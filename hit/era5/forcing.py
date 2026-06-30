from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# Open-Meteo ERA5 reanalysis — free, no auth, returns point JSON in seconds
_OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"

_OM_VARS = [
    "temperature_2m",
    "dewpoint_2m",
    "wind_speed_10m",
    "surface_pressure",
    "shortwave_radiation",       # Kd — solar downwelling (W/m²)
    "terrestrial_radiation",     # Ld — longwave downwelling (W/m²)
]


def fetch_era5_forcing(
    centroid_lat: float,
    centroid_lon: float,
    date_start: str,
    date_end: str,
    cache_path: Path | None = None,
) -> Path:
    """Fetch 7-column ERA5 met forcing for TARGET and write it as CSV.

    Covers (date_start − 1 spinup day) through date_end at hourly resolution, UTC.
    Uses Open-Meteo ERA5 reanalysis API — single HTTP request, no authentication.
    Returns path to the CSV file.
    """
    if cache_path and cache_path.exists():
        return cache_path

    spinup = (pd.Timestamp(date_start) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    resp = requests.get(
        _OPEN_METEO_URL,
        params={
            "latitude": centroid_lat,
            "longitude": centroid_lon,
            "start_date": spinup,
            "end_date": date_end,
            "hourly": ",".join(_OM_VARS),
            "timezone": "UTC",
            "wind_speed_unit": "ms",
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()["hourly"]

    times = pd.DatetimeIndex(data["time"])
    ta  = np.array(data["temperature_2m"], dtype=float)
    td  = np.array(data["dewpoint_2m"],    dtype=float)
    ws  = np.array(data["wind_speed_10m"], dtype=float)
    p   = np.array(data["surface_pressure"], dtype=float)
    kd  = np.clip(np.array(data["shortwave_radiation"],  dtype=float), 0, None)
    ld  = np.clip(np.array(data["terrestrial_radiation"], dtype=float), 0, None)

    rh = 100.0 * (
        np.exp(17.625 * td / (243.04 + td))
        / np.exp(17.625 * ta / (243.04 + ta))
    )
    rh = np.clip(rh, 0, 100)

    df = pd.DataFrame({
        "datetime": times.strftime("%d/%m/%Y %H:%M"),
        "Ta":  np.round(ta, 6),
        "RH":  np.round(rh, 6),
        "WS":  np.round(ws, 6),
        "P":   np.round(p,  6),
        "Kd":  np.round(kd, 6),
        "Ld":  np.round(ld, 6),
    })

    out = cache_path or Path(f"era5_met_forcing_{date_start}_{date_end}.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    return out
