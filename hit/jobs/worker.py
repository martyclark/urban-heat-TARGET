"""TARGET job worker — run as: python -m hit.jobs.worker <job_id>"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_status(job_dir: Path, status: dict) -> None:
    (job_dir / "status.json").write_text(json.dumps(status, indent=2))


def run(job_id: str) -> None:
    from hit.config.city import CityConfig
    from hit.era5.forcing import fetch_era5_forcing
    from hit.jobs.queue import JOBS_DIR
    from hit.target.results import load_results
    from hit.target.run import prepare_inputs, run_target

    job_dir = JOBS_DIR / job_id
    status_path = job_dir / "status.json"

    status = json.loads(status_path.read_text())
    status["status"] = "running"
    status["started_at"] = _now()
    status["step"] = "forcing"
    _write_status(job_dir, status)

    try:
        cfg_raw = json.loads((job_dir / "config.json").read_text())

        cc_fields = cfg_raw["city_config"]
        config = CityConfig(
            city_name  = cc_fields["city_name"],
            country_iso= cc_fields["country_iso"],
            bbox       = tuple(cc_fields["bbox"]),
            local_crs  = cc_fields["local_crs"],
            utc_offset = cc_fields["utc_offset"],
            run_name   = cc_fields["run_name"],
            date_start = cc_fields["date_start"],
            date_end   = cc_fields["date_end"],
        )

        data_dir   = Path(cfg_raw["data_dir"])
        date_start = cfg_raw["date_start"]
        date_end   = cfg_raw["date_end"]

        forcing_path = job_dir / "era5_met_forcing.csv"
        fetch_era5_forcing(
            centroid_lat=float((config.bbox[1] + config.bbox[3]) / 2),
            centroid_lon=float((config.bbox[0] + config.bbox[2]) / 2),
            date_start=date_start,
            date_end=date_end,
            cache_path=forcing_path,
        )

        status["step"] = "prepare"
        _write_status(job_dir, status)
        target_work_dir = job_dir / "target_runs"
        config_ini = prepare_inputs(
            config=config,
            data_dir=data_dir,
            run_dir=job_dir,
            target_work_dir=target_work_dir,
        )

        status["step"] = "run"
        _write_status(job_dir, status)
        output_npy = run_target(config_ini, progress=False)

        status["step"] = "save"
        _write_status(job_dir, status)
        ds = load_results(output_npy)
        result_nc = job_dir / "target_results.nc"
        ds.to_netcdf(result_nc)

        status["status"]       = "complete"
        status["completed_at"] = _now()
        status["step"]         = "complete"
        status["output_path"]  = str(result_nc)

    except Exception as exc:
        status["status"]       = "failed"
        status["completed_at"] = _now()
        status["error"]        = str(exc)

    _write_status(job_dir, status)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m hit.jobs.worker <job_id>", file=sys.stderr)
        sys.exit(1)
    run(sys.argv[1])
