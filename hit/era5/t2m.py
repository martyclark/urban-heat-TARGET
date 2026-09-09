from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests
import xarray as xr

_OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"


def fetch_t2m_monthly(
    centroid_lat: float,
    centroid_lon: float,
    year_start: int,
    year_end: int,
    cache_path: Path | None = None,
) -> xr.Dataset:
    """Fetch monthly T2m tmin/tavg/tmax (°C) at city centroid, ERA5 reanalysis via Open-Meteo.

    Monthly tmax/tmin are the mean of daily maxima/minima within each calendar month;
    tavg is the mean of daily means. A plain air-temperature sense check, distinct
    from UTCI — shows how the raw temperature record is trending over the period.
    """
    if cache_path and cache_path.exists():
        return xr.open_dataset(cache_path).load()

    resp = requests.get(
        _OPEN_METEO_URL,
        params={
            "latitude": centroid_lat,
            "longitude": centroid_lon,
            "start_date": f"{year_start}-01-01",
            "end_date": f"{year_end}-12-31",
            "daily": "temperature_2m_max,temperature_2m_min,temperature_2m_mean",
            "timezone": "UTC",
        },
        timeout=120,
    )
    resp.raise_for_status()
    daily = resp.json()["daily"]

    df = pd.DataFrame({
        "time": pd.DatetimeIndex(daily["time"]),
        "tmax": daily["temperature_2m_max"],
        "tmin": daily["temperature_2m_min"],
        "tavg": daily["temperature_2m_mean"],
    }).set_index("time")

    monthly = df.resample("MS").mean()

    ds = xr.Dataset(
        {
            "tmax": ("time", monthly["tmax"].values),
            "tmin": ("time", monthly["tmin"].values),
            "tavg": ("time", monthly["tavg"].values),
        },
        coords={"time": monthly.index.values},
    )
    ds["tmax"].attrs.update({"units": "degC", "long_name": "Monthly mean of daily maximum 2m temperature"})
    ds["tmin"].attrs.update({"units": "degC", "long_name": "Monthly mean of daily minimum 2m temperature"})
    ds["tavg"].attrs.update({"units": "degC", "long_name": "Monthly mean of daily mean 2m temperature"})
    ds.attrs["source"] = "ERA5 reanalysis via Open-Meteo archive API"

    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        ds.to_netcdf(cache_path)

    return ds
