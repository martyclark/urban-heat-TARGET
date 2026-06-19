from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import geopandas as gpd

from hit.target.buildings import compute_roof_fraction, fetch_gba_buildings
from hit.target.grid import generate_grid
from hit.target.landcover import combine_fractions
from hit.target.roads import compute_road_fraction, fetch_overture_roads
from hit.target.worldcover import compute_landcover_fractions, fetch_worldcover

_WC_CACHE_DIR = Path("data/worldcover_cache")


def prepare_city_morphology(
    city: dict,
    data_dir: Path,
    local_crs: str,
    status_path: Path | None = None,
) -> None:
    """Run the full morphology preparation pipeline for a city.

    Produces grid.gpkg and target_landcover.csv in data_dir.
    Buildings, roads and WorldCover are fetched in parallel after the grid is ready.
    status_path (step.json) is written with per-step statuses for UI progress display.
    """
    _lock = threading.Lock()
    _statuses: dict[str, str] = {
        "grid":       "pending",
        "buildings":  "pending",
        "roads":      "pending",
        "worldcover": "pending",
        "combine":    "pending",
    }

    def _update(step: str, status: str) -> None:
        _statuses[step] = status
        if status_path is not None:
            with _lock:
                status_path.write_text(json.dumps(_statuses))

    geom = city.get("geometry")
    if geom is not None:
        lon_min, lat_min, lon_max, lat_max = geom.bounds
    else:
        lat = city["centroid_lat"]
        lon = city["centroid_lon"]
        lon_min, lat_min, lon_max, lat_max = lon - 0.15, lat - 0.15, lon + 0.15, lat + 0.15
    bbox = (lon_min, lat_min, lon_max, lat_max)

    _update("grid", "running")
    generate_grid(city=city, data_dir=data_dir, local_crs=local_crs)
    _update("grid", "complete")

    grid_wgs84 = gpd.read_file(data_dir / "grid.gpkg")
    if "FID" not in grid_wgs84.columns:
        grid_wgs84 = grid_wgs84.reset_index(names=["FID"])

    buildings_cache = data_dir / "gba_buildings.gpkg"
    roads_cache     = data_dir / "roads_raw.gpkg"

    _update("buildings",  "running")
    _update("roads",      "running")
    _update("worldcover", "running")

    def _fetch_buildings():
        from datetime import datetime, timezone
        with _lock:
            _statuses["buildings_started_at"] = datetime.now(timezone.utc).isoformat()
            if status_path is not None:
                status_path.write_text(json.dumps(_statuses))

        def _progress(fetched: int, total: int) -> None:
            with _lock:
                _statuses["buildings_fetched"] = fetched
                _statuses["buildings_total"] = total
                if status_path is not None:
                    status_path.write_text(json.dumps(_statuses))
        try:
            result = fetch_gba_buildings(bbox, buildings_cache, progress_cb=_progress)
            _update("buildings", "complete")
            return result
        except Exception:
            _update("buildings", "failed")
            raise

    def _fetch_roads():
        try:
            result = fetch_overture_roads(bbox, roads_cache)
            _update("roads", "complete")
            return result
        except Exception:
            _update("roads", "failed")
            raise

    def _fetch_worldcover():
        try:
            result = fetch_worldcover(bbox, _WC_CACHE_DIR)
            _update("worldcover", "complete")
            return result
        except Exception:
            _update("worldcover", "failed")
            raise

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_b = pool.submit(_fetch_buildings)
        f_r = pool.submit(_fetch_roads)
        f_w = pool.submit(_fetch_worldcover)
        fut_names = {f_b: "buildings", f_r: "roads", f_w: "worldcover"}

        errors: list[str] = []
        results: dict[str, object] = {}
        for fut in as_completed(fut_names):
            name = fut_names[fut]
            try:
                results[name] = fut.result()
            except Exception as exc:
                errors.append(f"{name}: {exc}")

    if errors:
        raise RuntimeError("Data fetch failed:\n" + "\n".join(errors))

    buildings_df = compute_roof_fraction(results["buildings"], grid_wgs84, local_crs)
    roads_df     = compute_road_fraction(results["roads"], results["buildings"], grid_wgs84, local_crs)
    lc_df        = compute_landcover_fractions(grid_wgs84, results["worldcover"])

    _update("combine", "running")
    combine_fractions(
        grid_fids=grid_wgs84["FID"],
        buildings_df=buildings_df,
        roads_df=roads_df,
        lc_df=lc_df,
        out_path=data_dir / "target_landcover.csv",
    )
    _update("combine", "complete")
