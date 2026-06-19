"""Morphology preparation worker — run as: python -m hit.jobs.worker_prepare <job_id>"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import shape


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_status(job_dir: Path, status: dict) -> None:
    (job_dir / "status.json").write_text(json.dumps(status, indent=2))


def run(job_id: str) -> None:
    from hit.jobs.queue import JOBS_DIR
    from hit.target.prepare import prepare_city_morphology

    job_dir = JOBS_DIR / job_id
    status_path = job_dir / "status.json"
    step_path = job_dir / "step.json"

    status = json.loads(status_path.read_text())
    status["status"] = "running"
    status["started_at"] = _now()
    status["step"] = "grid"
    _write_status(job_dir, status)

    try:
        cfg = json.loads((job_dir / "config.json").read_text())

        city = cfg["city"]
        # Deserialise geometry from GeoJSON if present
        if city.get("geometry") and isinstance(city["geometry"], dict):
            city["geometry"] = shape(city["geometry"])

        data_dir = Path(cfg["data_dir"])
        local_crs = cfg["local_crs"]

        prepare_city_morphology(
            city=city,
            data_dir=data_dir,
            local_crs=local_crs,
            status_path=step_path,
        )

        status["status"] = "complete"
        status["completed_at"] = _now()
        status["step"] = "complete"

    except Exception as exc:
        status["status"] = "failed"
        status["completed_at"] = _now()
        status["error"] = str(exc)
        status["step"] = None

    _write_status(job_dir, status)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m hit.jobs.worker_prepare <job_id>", file=sys.stderr)
        sys.exit(1)
    run(sys.argv[1])
