import os
from pathlib import Path

import xarray as xr

UTCI_ZARR_URL = (
    "https://arco.datastores.ecmwf.int/cadl-arco-geo-004/arco"
    "/derived_utci_historical/all/geoChunked.zarr"
)


def _get_token() -> str:
    token = os.environ.get("CDS_API_KEY")
    if not token:
        try:
            import streamlit as st
            token = st.secrets.get("CDS_API_KEY")
        except Exception:
            pass
    if not token:
        raise EnvironmentError(
            "CDS_API_KEY not found. Set it in .streamlit/secrets.toml or as an env var."
        )
    return token


def _open_utci_store() -> xr.Dataset:
    token = _get_token()
    return xr.open_zarr(
        UTCI_ZARR_URL,
        consolidated=True,
        storage_options={"headers": {"Authorization": f"Bearer {token}"}},
    )


def fetch_utci_daily(
    centroid_lat: float,
    centroid_lon: float,
    year_start: int,
    year_end: int,
    cache_path: Path | None = None,
    store: xr.Dataset | None = None,
) -> xr.DataArray:
    """Fetch UTCI daily maximum (°C) at city centroid from the ECMWF ARCO UTCI Zarr store.

    The store contains hourly UTCI (variable: 'utci', units: degK) geo-chunked for
    efficient point time-series queries. Chunks are ~7.7 years × 4×4 spatial, so a
    35-year point query downloads ~25 MB once; the daily-max cache is ~50 KB.
    Requires CDS_API_KEY env var (licence must be accepted at cds.climate.copernicus.eu).
    """
    if cache_path and cache_path.exists():
        return xr.open_dataarray(cache_path).load()

    ds = store if store is not None else _open_utci_store()

    # Longitude in this store is 0–360
    lon_query = centroid_lon % 360

    utci_hourly = (
        ds["utci"]
        .sel(time=slice(f"{year_start}-01-01", f"{year_end}-12-31"))
        .sel(latitude=centroid_lat, longitude=lon_query, method="nearest")
        .squeeze(drop=True)
        .load()
    ) - 273.15  # degK → °C

    utci_daily = utci_hourly.resample(time="1D").max()
    utci_daily.name = "utci_max"
    utci_daily.attrs.update({
        "units": "degC",
        "long_name": "Universal Thermal Climate Index daily maximum",
        "source": "ERA5-derived UTCI via ECMWF ARCO geoChunked Zarr (derived-utci-historical)",
    })

    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        utci_daily.to_netcdf(cache_path)

    return utci_daily
