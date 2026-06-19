from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

JOBS_DIR = Path("data/jobs")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def _read_status(job_id: str) -> dict:
    p = _job_dir(job_id) / "status.json"
    if not p.exists():
        return {"status": "unknown"}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {"status": "unknown"}


def submit_run(
    city_config_dict: dict,
    data_dir: str,
    date_start: str,
    date_end: str,
) -> str:
    """Submit a TARGET run job. Returns job_id.

    city_config_dict: serialisable dict of CityConfig fields.
    data_dir: path to pre-staged morphology dir (grid.gpkg, target_landcover.csv).
    """
    job_id = uuid.uuid4().hex[:12]
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "city_config": city_config_dict,
        "data_dir": data_dir,
        "date_start": date_start,
        "date_end": date_end,
    }
    (job_dir / "config.json").write_text(json.dumps(config, indent=2))
    (job_dir / "status.json").write_text(json.dumps({
        "status": "pending",
        "job_id": job_id,
        "created_at": _now(),
        "started_at": None,
        "completed_at": None,
        "error": None,
        "output_path": None,
    }, indent=2))

    subprocess.Popen(
        [sys.executable, "-m", "hit.jobs.worker", job_id],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return job_id


def submit_prepare(
    city: dict,
    data_dir: str,
    local_crs: str,
) -> str:
    """Submit a morphology preparation job. Returns job_id.

    city: dict from UCDB search; geometry serialised to GeoJSON for JSON storage.
    """
    job_id = uuid.uuid4().hex[:12]
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    city_serialisable = {k: v for k, v in city.items() if k != "geometry"}
    geom = city.get("geometry")
    if geom is not None:
        city_serialisable["geometry"] = geom.__geo_interface__

    config = {
        "job_type": "prepare",
        "city": city_serialisable,
        "data_dir": data_dir,
        "local_crs": local_crs,
    }
    (job_dir / "config.json").write_text(json.dumps(config, indent=2))
    (job_dir / "status.json").write_text(json.dumps({
        "status": "pending",
        "job_type": "prepare",
        "job_id": job_id,
        "created_at": _now(),
        "started_at": None,
        "completed_at": None,
        "step": None,
        "error": None,
    }, indent=2))

    subprocess.Popen(
        [sys.executable, "-m", "hit.jobs.worker_prepare", job_id],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return job_id


def get_status(job_id: str) -> dict:
    status = _read_status(job_id)
    step_path = _job_dir(job_id) / "step.json"
    if step_path.exists():
        try:
            step_data = json.loads(step_path.read_text())
            # New format: per-step status dict written by prepare_city_morphology
            if any(k in step_data for k in ("grid", "buildings", "roads", "worldcover", "combine")):
                status["step_statuses"] = step_data
            # Legacy single-step format
            elif step_data.get("step"):
                status["step"] = step_data["step"]
        except Exception:
            pass
    return status


def list_jobs() -> list[dict]:
    if not JOBS_DIR.exists():
        return []
    jobs = []
    for d in sorted(JOBS_DIR.iterdir(), reverse=True):
        s = _read_status(d.name)
        s["job_id"] = d.name
        jobs.append(s)
    return jobs
