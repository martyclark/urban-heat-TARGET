from __future__ import annotations

import math
import time
from pathlib import Path

import fiona
import fiona.crs
import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import shape

_GBA_WFS_URL = "https://tubvsig-so2sat-vm1.srv.mwn.de/geoserver/ows"
_GBA_LAYER = "global3D:lod1_global"
_GBA_PAGE_SIZE = 50000
_GBA_TILE_DEG = 0.10   # ~11 km; keeps per-tile WFS responses fast
_GBA_HITS_TIMEOUT = 60  # seconds — used only for the full-bbox preflight
_GBA_PAGE_TIMEOUT = 120  # seconds — per page within a tile


def _gba_empty() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        columns=["height", "geometry"], geometry="geometry", crs="EPSG:4326"
    )


def _tile_bboxes(
    lon_min: float, lat_min: float, lon_max: float, lat_max: float, tile_deg: float
) -> list[tuple[float, float, float, float]]:
    tiles = []
    lon = lon_min
    while lon < lon_max:
        lat = lat_min
        while lat < lat_max:
            tiles.append((
                round(lon, 6), round(lat, 6),
                round(min(lon + tile_deg, lon_max), 6),
                round(min(lat + tile_deg, lat_max), 6),
            ))
            lat = round(lat + tile_deg, 10)
        lon = round(lon + tile_deg, 10)
    return tiles


def _check_gba_hits(session: requests.Session, bbox_str: str, timeout: int) -> int | None:
    """Return feature count for bbox, or None if server doesn't respond in time."""
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": _GBA_LAYER, "bbox": bbox_str,
        "outputFormat": "application/json", "resultType": "hits",
    }
    try:
        resp = session.get(_GBA_WFS_URL, params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return int(data.get("numberMatched") or data.get("totalFeatures") or 0)
    except Exception:
        return None


def _fetch_tile(
    session: requests.Session,
    bbox_str: str,
    sink: "fiona.Collection",
) -> int:
    """Fetch all pages for one tile bbox, writing directly to sink. Returns feature count."""
    start_index = 0
    total: int | None = None
    written = 0

    while True:
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": _GBA_LAYER, "bbox": bbox_str,
            "outputFormat": "application/json",
            "count": _GBA_PAGE_SIZE, "startIndex": start_index,
            "sortBy": "height",
        }
        for attempt in range(3):
            try:
                resp = session.get(
                    _GBA_WFS_URL, params=params, timeout=_GBA_PAGE_TIMEOUT,
                    headers={"Accept-Encoding": "gzip, deflate"},
                )
                resp.raise_for_status()
                break
            except requests.RequestException as exc:
                if attempt == 2:
                    raise RuntimeError(f"GBA WFS tile {bbox_str} failed: {exc}") from exc
                time.sleep(5 * (attempt + 1))

        data = resp.json()
        batch = data.get("features", [])
        if total is None:
            total = data.get("numberMatched") or data.get("totalFeatures") or 0

        for f in batch:
            geom = f.get("geometry")
            if not geom:
                continue
            sink.write({
                "geometry": geom,
                "properties": {"height": f["properties"].get("height")},
            })
            written += 1

        start_index += len(batch)
        if not batch or (total and start_index >= total):
            break

    return written


def fetch_gba_buildings(
    bbox: tuple[float, float, float, float],
    cache_path: Path,
    progress_cb: "Callable[[int, int], None] | None" = None,
) -> gpd.GeoDataFrame:
    """Fetch GBA LoD1 buildings via WFS for bbox. Returns GeoDataFrame in WGS84.

    Tiles the bbox into _GBA_TILE_DEG-degree cells so that each WFS request is
    small enough to respond promptly regardless of city size. Results written
    page-by-page to a temporary GeoPackage so memory stays bounded.
    Cached to cache_path on success.
    """
    if cache_path.exists():
        return gpd.read_file(cache_path)

    lon_min, lat_min, lon_max, lat_max = bbox
    full_bbox_str = f"{lon_min},{lat_min},{lon_max},{lat_max},EPSG:4326"
    session = requests.Session()

    # Full-bbox hits check: fast-fail only on a definitive 0; timeout → proceed.
    hits = _check_gba_hits(session, full_bbox_str, timeout=_GBA_HITS_TIMEOUT)
    if hits == 0:
        return _gba_empty()

    tiles = _tile_bboxes(lon_min, lat_min, lon_max, lat_max, _GBA_TILE_DEG)
    n_tiles = len(tiles)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(".tmp.gpkg")
    if tmp_path.exists():
        tmp_path.unlink()

    _schema = {
        "geometry": "MultiPolygon",
        "properties": {"height": "float"},
    }
    _crs = fiona.crs.from_epsg(3857)

    total_written = 0

    with fiona.open(tmp_path, "w", driver="GPKG", schema=_schema, crs=_crs) as sink:
        for i, (tlon_min, tlat_min, tlon_max, tlat_max) in enumerate(tiles):
            tile_bbox_str = f"{tlon_min},{tlat_min},{tlon_max},{tlat_max},EPSG:4326"
            n = _fetch_tile(session, tile_bbox_str, sink)
            total_written += n
            if progress_cb is not None:
                progress_cb(i + 1, n_tiles)

    if total_written == 0:
        tmp_path.unlink(missing_ok=True)
        return _gba_empty()

    gdf = gpd.read_file(tmp_path).to_crs("EPSG:4326")
    gdf.to_file(cache_path, driver="GPKG")
    tmp_path.unlink(missing_ok=True)
    return gdf


def compute_roof_fraction(
    buildings_wgs84: gpd.GeoDataFrame,
    grid_wgs84: gpd.GeoDataFrame,
    local_crs: str,
) -> pd.DataFrame:
    """Compute roof fraction and area-weighted mean building height per cell.

    Returns DataFrame with columns FID, roof, H.
    """
    grid_proj = grid_wgs84.to_crs(local_crs)
    cell_area_m2 = float(grid_proj.geometry.area.iloc[0])

    if buildings_wgs84.empty:
        return pd.DataFrame({"FID": grid_proj["FID"].values, "roof": 0.0, "H": 0.0})

    buildings_proj = buildings_wgs84.to_crs(local_crs)

    clipped = gpd.overlay(
        buildings_proj[["geometry", "height"]],
        grid_proj[["FID", "geometry"]],
        how="intersection",
        keep_geom_type=False,
    )
    clipped["clip_area_m2"] = clipped.geometry.area

    roof_by_cell = (
        clipped.groupby("FID")["clip_area_m2"]
        .sum()
        .rename("total_footprint_m2")
        .reset_index()
    )
    roof_by_cell["roof"] = (roof_by_cell["total_footprint_m2"] / cell_area_m2).clip(0, 1)

    clipped_h = clipped[clipped["height"].notna() & (clipped["height"] > 0)].copy()
    clipped_h["area_x_h"] = clipped_h["clip_area_m2"] * clipped_h["height"]
    height_by_cell = (
        clipped_h.groupby("FID")
        .agg(area_x_h=("area_x_h", "sum"), weighted_area=("clip_area_m2", "sum"))
        .reset_index()
    )
    height_by_cell["H"] = height_by_cell["area_x_h"] / height_by_cell["weighted_area"]

    result = grid_proj[["FID"]].merge(roof_by_cell[["FID", "roof"]], on="FID", how="left")
    result["roof"] = result["roof"].fillna(0.0)
    result = result.merge(height_by_cell[["FID", "H"]], on="FID", how="left")
    result["H"] = result["H"].fillna(0.0)
    return result[["FID", "roof", "H"]]
