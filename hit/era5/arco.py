from pathlib import Path

import xarray as xr

ZARR_URL = "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"


def fetch_t2m(
    centroid_lat: float,
    centroid_lon: float,
    year_start: int,
    year_end: int,
    cache_path: Path | None = None,
) -> xr.DataArray:
    """Fetch ERA5 T2m (°C) at city centroid for the given year range.

    Longitude in degrees east (-180 to 180); internally converted to 0–360 for ARCO-ERA5.
    Result is cached to cache_path (NetCDF) if provided.
    """
    if cache_path and cache_path.exists():
        return xr.open_dataarray(cache_path).load()

    centroid_lon_360 = centroid_lon % 360

    ds = xr.open_dataset(
        ZARR_URL,
        chunks={},
        engine="zarr",
        storage_options=dict(token="anon"),
    )

    t2m = (
        ds["2m_temperature"]
        .sel(time=slice(f"{year_start}-01-01", f"{year_end}-12-31"))
        .sel(latitude=centroid_lat, longitude=centroid_lon_360, method="nearest")
        .load()
    ) - 273.15

    t2m.name = "t2m"
    t2m.attrs["units"] = "degC"
    t2m.attrs["long_name"] = "2 metre temperature"

    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        t2m.to_netcdf(cache_path)

    return t2m
