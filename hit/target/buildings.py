from __future__ import annotations

import re
from pathlib import Path

import geopandas as gpd
import pandas as pd

_GBA_BASE = "s3://us-west-2.opendata.source.coop/tge-labs/globalbuildingatlas-lod1/"
_TILE_RE = re.compile(r"([ew])(\d{3})_([ns])(\d{2})_([ew])(\d{3})_([ns])(\d{2})\.parquet$")


def _gba_empty() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        columns=["height", "geometry"], geometry="geometry", crs="EPSG:4326"
    )


def _signed(hemi: str, value: str) -> float:
    return float(value) * (-1 if hemi in "ws" else 1)


def _gba_tiles_for_bbox(con, bbox: tuple[float, float, float, float]) -> list[str]:
    lon_min, lat_min, lon_max, lat_max = bbox
    paths = [r[0] for r in con.execute(f"SELECT file FROM glob('{_GBA_BASE}*.parquet')").fetchall()]
    hits = []
    for path in paths:
        m = _TILE_RE.search(path)
        if not m:
            continue
        w, s_, e, n = (
            _signed(m[1], m[2]), _signed(m[7], m[8]), _signed(m[5], m[6]), _signed(m[3], m[4]),
        )
        if w < lon_max and e > lon_min and s_ < lat_max and n > lat_min:
            hits.append(path)
    return hits


def fetch_gba_buildings(
    bbox: tuple[float, float, float, float],
    cache_path: Path,
    progress_cb: "Callable[[int, int], None] | None" = None,
) -> gpd.GeoDataFrame:
    """Fetch GBA LoD1 buildings for bbox from the Source Cooperative GeoParquet mirror.

    Returns GeoDataFrame in WGS84 with a `height` column. Cached to cache_path on success.
    """
    if cache_path.exists():
        return gpd.read_file(cache_path)

    try:
        import duckdb
    except ImportError as exc:
        raise ImportError("duckdb is required for building data: pip install duckdb") from exc

    xmin, ymin, xmax, ymax = bbox
    con = duckdb.connect()
    con.execute("INSTALL spatial; INSTALL httpfs;")
    con.execute("LOAD spatial; LOAD httpfs;")
    # Bucket name contains dots, which breaks virtual-host-style TLS.
    con.execute("SET s3_region='us-west-2'; SET s3_url_style='path';")

    tiles = _gba_tiles_for_bbox(con, bbox)
    frames = []
    for i, tile in enumerate(tiles):
        df = con.execute(
            f"""
            SELECT height, ST_AsText(geometry) AS wkt
            FROM read_parquet('{tile}')
            WHERE bbox.xmin <= {xmax} AND bbox.xmax >= {xmin}
              AND bbox.ymin <= {ymax} AND bbox.ymax >= {ymin}
              AND height IS NOT NULL
            """
        ).fetchdf()
        frames.append(df)
        if progress_cb is not None:
            progress_cb(i + 1, len(tiles))
    con.close()

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if df.empty:
        return _gba_empty()

    gdf = gpd.GeoDataFrame(
        {"height": df["height"]},
        geometry=gpd.GeoSeries.from_wkt(df["wkt"]),
        crs="EPSG:4326",
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(cache_path, driver="GPKG")
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
