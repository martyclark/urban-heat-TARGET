from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
from pyproj import Transformer
from shapely.geometry import box


def generate_grid(
    city: dict,
    data_dir: Path,
    local_crs: str,
    resolution_m: int = 200,
    buffer_m: int = 500,
) -> Path:
    """Generate a 200 m TARGET grid for a city, saved as grid.gpkg.

    Clips to the UCDB urban centre polygon buffered by buffer_m. UCDB polygons
    are land-based by definition so no additional ocean/land masking is needed.
    Falls back to centroid ±0.15° bbox if no geometry is available.

    Returns path to saved grid.gpkg.
    """
    geom = city.get("geometry")
    if geom is not None:
        lon_min, lat_min, lon_max, lat_max = geom.bounds
    else:
        lat = city["centroid_lat"]
        lon = city["centroid_lon"]
        lon_min, lat_min, lon_max, lat_max = lon - 0.15, lat - 0.15, lon + 0.15, lat + 0.15

    transformer = Transformer.from_crs("EPSG:4326", local_crs, always_xy=True)
    x_min, y_min = transformer.transform(lon_min, lat_min)
    x_max, y_max = transformer.transform(lon_max, lat_max)

    res = resolution_m
    xs = np.arange(x_min, x_max, res)
    ys = np.arange(y_min, y_max, res)

    cells = [
        {"FID": fid, "geometry": box(x0, y0, x0 + res, y0 + res)}
        for fid, (x0, y0) in enumerate(
            (x0, y0) for x0 in xs for y0 in ys
        )
    ]
    if not cells:
        raise ValueError("No grid cells generated — check city bbox and resolution.")

    grid_proj = gpd.GeoDataFrame(cells, crs=local_crs)

    if geom is not None:
        ucdb_proj = gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326").to_crs(local_crs)
        clip_poly = ucdb_proj.geometry.union_all().buffer(buffer_m)
        grid_proj = grid_proj[grid_proj.geometry.intersects(clip_poly)].copy()
        grid_proj = grid_proj.reset_index(drop=True)
        grid_proj["FID"] = range(len(grid_proj))

    if len(grid_proj) == 0:
        raise ValueError("No grid cells remain after clipping — check city geometry.")

    grid_wgs84 = grid_proj.to_crs("EPSG:4326")
    data_dir.mkdir(parents=True, exist_ok=True)
    out_path = data_dir / "grid.gpkg"
    if not out_path.exists():
        grid_wgs84.to_file(out_path, driver="GPKG")
    return out_path
