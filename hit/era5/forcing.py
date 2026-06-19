from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

ARCO_ZARR_URL = "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"

_VARS = [
    "2m_temperature",
    "2m_dewpoint_temperature",
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
    "surface_pressure",
    "surface_solar_radiation_downwards",
    "surface_thermal_radiation_downwards",
]


def _open_arco() -> xr.Dataset:
    return xr.open_dataset(
        ARCO_ZARR_URL,
        chunks={},
        engine="zarr",
        storage_options=dict(token="anon"),
    )


def fetch_era5_forcing(
    centroid_lat: float,
    centroid_lon: float,
    date_start: str,
    date_end: str,
    cache_path: Path | None = None,
) -> Path:
    """Fetch 7-column ERA5 met forcing for TARGET and write it as CSV.

    Covers (date_start − 1 spinup day) through date_end at hourly resolution, UTC.
    Returns path to the CSV file.

    Required by TARGET config.ini as inpt_met_file with date_fmt=%d/%m/%Y %H:%M.
    """
    if cache_path and cache_path.exists():
        return cache_path

    spinup = (pd.Timestamp(date_start) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    lon_360 = centroid_lon % 360

    ds = _open_arco()

    raw = (
        ds[_VARS]
        .sel(time=slice(spinup, date_end))
        .sel(latitude=centroid_lat, longitude=lon_360, method="nearest")
        .squeeze(drop=True)
        .load()
    )

    ta = raw["2m_temperature"].values - 273.15
    td = raw["2m_dewpoint_temperature"].values - 273.15
    u  = raw["10m_u_component_of_wind"].values
    v  = raw["10m_v_component_of_wind"].values
    p  = raw["surface_pressure"].values / 100.0
    kd = np.clip(raw["surface_solar_radiation_downwards"].values / 3600.0, 0, None)
    ld = np.clip(raw["surface_thermal_radiation_downwards"].values / 3600.0, 0, None)

    rh = 100.0 * (
        np.exp(17.625 * td / (243.04 + td))
        / np.exp(17.625 * ta / (243.04 + ta))
    )
    rh = np.clip(rh, 0, 100)
    ws = np.sqrt(u**2 + v**2)

    times = pd.DatetimeIndex(raw.time.values)
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
