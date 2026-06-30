from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_bounds
from rasterio.warp import transform_bounds


def gdf_to_geotiff(
    gdf: gpd.GeoDataFrame,
    column: str,
    out_path: Path,
    resolution_m: float = 200.0,
) -> dict:
    """Burn a GeoDataFrame column to a float32 GeoTIFF in Web Mercator.

    Returns dict with path, WGS84 bounds [W, S, E, N], vmin (p2), vmax (p98).
    """
    gdf_3857 = gdf[[column, "geometry"]].to_crs("EPSG:3857")
    minx, miny, maxx, maxy = gdf_3857.total_bounds
    minx -= resolution_m
    miny -= resolution_m
    maxx += resolution_m
    maxy += resolution_m

    width = max(1, int((maxx - minx) / resolution_m))
    height = max(1, int((maxy - miny) / resolution_m))
    transform = from_bounds(minx, miny, maxx, maxy, width, height)

    vals = gdf_3857[column].values.astype(np.float32)
    shapes = (
        (geom, float(val))
        for geom, val in zip(gdf_3857.geometry, vals)
        if np.isfinite(val)
    )
    burned = rasterize(
        shapes,
        out_shape=(height, width),
        transform=transform,
        fill=np.nan,
        dtype=np.float32,
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        out_path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype=np.float32,
        crs="EPSG:3857",
        transform=transform,
        nodata=float("nan"),
    ) as dst:
        dst.write(burned, 1)

    valid = burned[np.isfinite(burned)]
    vmin = float(np.percentile(valid, 2)) if len(valid) else 0.0
    vmax = float(np.percentile(valid, 98)) if len(valid) else 1.0

    bounds_4326 = list(transform_bounds("EPSG:3857", "EPSG:4326", minx, miny, maxx, maxy))
    return {"path": str(out_path), "bounds": bounds_4326, "vmin": vmin, "vmax": vmax}
