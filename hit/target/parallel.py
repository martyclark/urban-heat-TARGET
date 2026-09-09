"""Parallel TARGET runner — splits cells across N worker processes."""
from __future__ import annotations

import dataclasses
import multiprocessing
import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

# Below this cell count, the Salvador benchmark (13,981 cells serial in 29 min)
# shows serial is fast enough that per-chunk process/merge overhead isn't worth it.
PARALLEL_CELL_THRESHOLD = 15_000


def _chunk_worker(args: tuple) -> tuple[int, str, str | None]:
    """Run TARGET for one cell chunk. Returns (chunk_idx, npy_path, error)."""
    chunk_idx, config_ini_str = args
    try:
        from hit.target.run import run_target
        npy = run_target(Path(config_ini_str), progress=False)
        return chunk_idx, str(npy), None
    except Exception as exc:
        return chunk_idx, "", str(exc)


def run_target_parallel(
    config,
    data_dir: Path,
    run_dir: Path,
    target_work_dir: Path,
    n_workers: int | None = None,
) -> Path:
    """Run TARGET in parallel by splitting cells across N processes.

    Each worker gets an equal share of the landcover CSV; results are merged
    back along the cell axis. The sequential fallback in worker.py is untouched.
    """
    from hit.target.run import prepare_inputs

    if n_workers is None:
        # On Apple Silicon, cap at 4 to use performance cores only —
        # efficiency cores are ~2-3x slower and would bottleneck the merge.
        n_workers = min(multiprocessing.cpu_count() // 2, 4)

    lc = pd.read_csv(data_dir / "target_landcover.csv")
    chunk_size = -(-len(lc) // n_workers)  # ceiling division
    chunks = [lc.iloc[i:i + chunk_size] for i in range(0, len(lc), chunk_size)]
    n_workers = len(chunks)  # may be less than requested if lc is small

    # Build one TARGET working dir per chunk
    chunk_ini_paths: list[tuple[int, str]] = []
    for i, chunk in enumerate(chunks):
        chunk_data = target_work_dir / f"chunk_{i:03d}_data"
        chunk_data.mkdir(parents=True, exist_ok=True)

        chunk_lc = chunk_data / "target_landcover.csv"
        chunk.to_csv(chunk_lc, index=False)

        # grid.gpkg needed only for domainDim in config.ini (not used in simulation)
        src_grid = data_dir / "grid.gpkg"
        dst_grid = chunk_data / "grid.gpkg"
        if not dst_grid.exists():
            shutil.copy2(src_grid, dst_grid)

        chunk_work = target_work_dir / f"chunk_{i:03d}_run"
        chunk_work.mkdir(parents=True, exist_ok=True)

        chunk_config = dataclasses.replace(
            config, city_name=f"{config.city_name}_c{i:03d}"
        )
        ini = prepare_inputs(
            config=chunk_config,
            data_dir=chunk_data,
            run_dir=run_dir,
            target_work_dir=chunk_work,
        )
        chunk_ini_paths.append((i, str(ini)))

    # Run all chunks in parallel
    results: dict[int, Path] = {}
    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(_chunk_worker, args): args[0] for args in chunk_ini_paths}
        for future in as_completed(futures):
            chunk_idx, npy_path, error = future.result()
            if error:
                raise RuntimeError(f"TARGET chunk {chunk_idx} failed: {error}")
            results[chunk_idx] = Path(npy_path)

    # Merge in order along cell axis (dim 1)
    arrays = [np.load(results[i], allow_pickle=True) for i in range(n_workers)]
    merged = np.concatenate(arrays, axis=1)

    out_path = target_work_dir / f"{config.run_name}.npy"
    np.save(out_path, merged)
    return out_path
