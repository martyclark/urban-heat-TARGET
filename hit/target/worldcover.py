from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.merge import merge as rasterio_merge
from rasterio.windows import from_bounds as window_from_bounds
from rasterstats import zonal_stats

_WC_URL_TEMPLATE = (
    "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
    "ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
)

_ESA_TO_TARGET: dict[int, str] = {
    10: "veg", 20: "veg", 95: "veg",
    30: "dry", 60: "dry", 70: "dry", 100: "dry",
    40: "irr",
    80: "watr", 90: "watr",
    50: "built_up",
}
_TARGET_CODE = {"veg": 1, "dry": 2, "irr": 3, "watr": 4, "built_up": 5}
_TARGET_CLASSES = ["veg", "dry", "irr", "watr", "built_up"]


def _tile_id(lat_sw: int, lon_sw: int) -> str:
    lat_s = f"S{abs(lat_sw):02d}" if lat_sw < 0 else f"N{lat_sw:02d}"
    lon_s = f"W{abs(lon_sw):03d}" if lon_sw < 0 else f"E{lon_sw:03d}"
    return f"{lat_s}{lon_s}"


def fetch_worldcover(
    bbox: tuple[float, float, float, float],
    cache_dir: Path,
) -> Path:
    """Download ESA WorldCover 2021 for bbox via windowed S3 read, reclassify to TARGET codes.

    Returns path to reclassified GeoTIFF. Cached by bbox coords.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    lon_min, lat_min, lon_max, lat_max = bbox
    reclass_path = cache_dir / f"wc_{lon_min:.3f}_{lat_min:.3f}_{lon_max:.3f}_{lat_max:.3f}_reclass.tif"
    if reclass_path.exists():
        return reclass_path

    reclass_lut = np.full(256, _TARGET_CODE["dry"], dtype=np.uint8)
    reclass_lut[0] = 0
    for esa_code, target_class in _ESA_TO_TARGET.items():
        reclass_lut[esa_code] = _TARGET_CODE[target_class]

    lat_starts = range(math.floor(lat_min / 3) * 3, math.ceil(lat_max / 3) * 3, 3)
    lon_starts = range(math.floor(lon_min / 3) * 3, math.ceil(lon_max / 3) * 3, 3)
    tiles = [_tile_id(lat, lon) for lat in lat_starts for lon in lon_starts]

    tile_paths: list[Path] = []
    for tile in tiles:
        url = _WC_URL_TEMPLATE.format(tile=tile)
        vsicurl = f"/vsicurl/{url}"
        with rasterio.open(vsicurl) as src:
            win = window_from_bounds(lon_min, lat_min, lon_max, lat_max, src.transform)
            data = src.read(1, window=win)
            transform = src.window_transform(win)
            meta = src.meta.copy()
            meta.update({
                "driver": "GTiff", "compress": "lzw",
                "height": data.shape[0], "width": data.shape[1],
                "transform": transform,
            })
            tp = cache_dir / f"wc_tile_{tile}.tif"
            with rasterio.open(tp, "w", **meta) as dst:
                dst.write(data[np.newaxis, :, :])
            tile_paths.append(tp)

    if len(tile_paths) == 1:
        with rasterio.open(tile_paths[0]) as src:
            raw_data = src.read(1)
            raw_transform = src.transform
            raw_meta = src.meta.copy()
        tile_paths[0].unlink(missing_ok=True)
    else:
        src_files = [rasterio.open(p) for p in tile_paths]
        mosaic, mosaic_transform = rasterio_merge(src_files)
        raw_data = mosaic[0]
        raw_transform = mosaic_transform
        raw_meta = src_files[0].meta.copy()
        for s in src_files:
            s.close()
        for p in tile_paths:
            p.unlink(missing_ok=True)

    data_safe = np.clip(raw_data, 0, 255).astype(np.uint8)
    reclass_data = reclass_lut[data_safe]
    raw_meta.update({
        "dtype": "uint8", "nodata": 0, "compress": "lzw",
        "height": reclass_data.shape[0], "width": reclass_data.shape[1],
        "transform": raw_transform,
    })
    with rasterio.open(reclass_path, "w", **raw_meta) as dst:
        dst.write(reclass_data[np.newaxis, :, :])

    return reclass_path


def compute_landcover_fractions(
    grid_wgs84: gpd.GeoDataFrame,
    reclass_path: Path,
) -> pd.DataFrame:
    """Compute per-cell fraction of each TARGET land cover class via zonal statistics.

    Returns DataFrame with columns FID, veg, dry, irr, watr, built_up.
    """
    with rasterio.open(reclass_path) as src:
        reclass_arr = src.read(1)
        raster_meta = src.meta.copy()

    fractions: dict[str, np.ndarray] = {"FID": grid_wgs84["FID"].values}

    for class_name in _TARGET_CLASSES:
        code = _TARGET_CODE[class_name]
        binary = (reclass_arr == code).astype(np.uint8)
        tmp_meta = raster_meta.copy()
        tmp_meta.update({"dtype": "uint8", "nodata": 255})

        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp_f:
            tmp_path = tmp_f.name
        try:
            with rasterio.open(tmp_path, "w", **tmp_meta) as dst:
                dst.write(binary[np.newaxis, :, :])
            stats = zonal_stats(grid_wgs84, tmp_path, stats=["mean"], nodata=255)
            fractions[class_name] = np.clip(
                np.array([s["mean"] if s["mean"] is not None else 0.0 for s in stats]),
                0, 1,
            )
        finally:
            os.unlink(tmp_path)

    return pd.DataFrame(fractions)
