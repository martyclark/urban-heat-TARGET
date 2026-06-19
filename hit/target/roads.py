from __future__ import annotations

import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely as _shapely
from shapely import wkt as shapely_wkt
from shapely.geometry import LineString
from shapely.ops import unary_union
from shapely.strtree import STRtree

_N_WORKERS = max(1, (os.cpu_count() or 4) - 1)

_OVERTURE_RELEASE = "2026-05-20.0"

_ROAD_WIDTHS: dict[str, float] = {
    "motorway": 12.0,
    "trunk": 10.0,
    "primary": 8.0,
    "secondary": 7.0,
    "tertiary": 6.0,
    "residential": 5.0,
    "service": 3.0,
    "unclassified": 5.0,
    "living_street": 4.0,
}

_EXCLUDE_CLASSES = {"footway", "steps", "path", "cycleway", "bridleway", "pedestrian"}
_DEFAULT_ROAD_WIDTH_M = 5.0
_TRANSECT_INTERVAL_M = 10
_TRANSECT_LENGTH_M = 80
_MIN_W_M = 3.0


def fetch_overture_roads(
    bbox: tuple[float, float, float, float],
    cache_path: Path,
    overture_release: str = _OVERTURE_RELEASE,
) -> gpd.GeoDataFrame:
    """Fetch road segments from Overture Maps S3 via DuckDB. Returns GeoDataFrame in WGS84."""
    if cache_path.exists():
        return gpd.read_file(cache_path)

    try:
        import duckdb
    except ImportError as exc:
        raise ImportError("duckdb is required for road data: pip install duckdb") from exc

    xmin, ymin, xmax, ymax = bbox
    query = f"""
    SELECT id, class, ST_AsText(geometry) AS wkt, width_rules
    FROM read_parquet(
        's3://overturemaps-us-west-2/release/{overture_release}/theme=transportation/type=segment/*',
        filename=true, hive_partitioning=1
    )
    WHERE bbox.xmin <= {xmax}
      AND bbox.xmax >= {xmin}
      AND bbox.ymin <= {ymax}
      AND bbox.ymax >= {ymin}
      AND subtype = 'road'
    """

    con = duckdb.connect()
    con.execute("INSTALL spatial; INSTALL httpfs;")
    con.execute("LOAD spatial; LOAD httpfs;")
    con.execute("SET s3_region='us-west-2';")
    con.execute("SET enable_object_cache=true;")
    result = con.execute(query).fetchdf()
    con.close()

    if result.empty:
        return gpd.GeoDataFrame(
            columns=["id", "class", "road_width_m", "geometry"],
            geometry="geometry",
            crs="EPSG:4326",
        )

    result = result[~result["class"].isin(_EXCLUDE_CLASSES)].copy()
    result["geometry"] = result["wkt"].apply(shapely_wkt.loads)
    gdf = gpd.GeoDataFrame(result.drop(columns=["wkt"]), geometry="geometry", crs="EPSG:4326")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(cache_path, driver="GPKG")
    return gdf


def _extract_width_from_rules(width_rules_val) -> float | None:
    if width_rules_val is None:
        return None
    try:
        if isinstance(width_rules_val, (list, np.ndarray)) and len(width_rules_val) > 0:
            first = width_rules_val[0]
            if isinstance(first, dict):
                value = first.get("value")
                unit = first.get("unit", "meters")
                if value is not None and str(unit).lower() in ("meters", "metres", "m"):
                    return float(value)
    except Exception:
        pass
    return None


def _canyon_width_at_point(
    pt, tangent_dx: float, tangent_dy: float, bldg_boundary
) -> float | None:
    mag = (tangent_dx**2 + tangent_dy**2) ** 0.5
    if mag < 1e-9:
        return None
    px, py = -tangent_dy / mag, tangent_dx / mag
    L = _TRANSECT_LENGTH_M
    left = LineString([(pt.x, pt.y), (pt.x + px * L, pt.y + py * L)])
    right = LineString([(pt.x, pt.y), (pt.x - px * L, pt.y - py * L)])
    li = left.intersection(bldg_boundary)
    ri = right.intersection(bldg_boundary)
    if li.is_empty or ri.is_empty:
        return None
    ld = min(pt.distance(p) for p in (li.geoms if hasattr(li, "geoms") else [li]))
    rd = min(pt.distance(p) for p in (ri.geoms if hasattr(ri, "geoms") else [ri]))
    return ld + rd


def _measure_canyon_width(centreline, bldg_boundary, fallback_w: float) -> float:
    seg_len = centreline.length
    if seg_len < 1.0:
        return fallback_w
    n = max(3, int(seg_len / _TRANSECT_INTERVAL_M))
    hits: list[float] = []
    for f in np.linspace(0.1, 0.9, n):
        d = f * seg_len
        pt = centreline.interpolate(d)
        pt_n = centreline.interpolate(min(d + 0.5, seg_len))
        pt_p = centreline.interpolate(max(d - 0.5, 0.0))
        w = _canyon_width_at_point(pt, pt_n.x - pt_p.x, pt_n.y - pt_p.y, bldg_boundary)
        if w is not None:
            hits.append(w)
    return float(np.median(hits)) if len(hits) >= 2 else fallback_w


def compute_road_fraction(
    roads_wgs84: gpd.GeoDataFrame,
    buildings_wgs84: gpd.GeoDataFrame,
    grid_wgs84: gpd.GeoDataFrame,
    local_crs: str,
) -> pd.DataFrame:
    """Compute road fraction and canyon width per cell.

    Returns DataFrame with columns FID, road, W.
    """
    grid_proj = grid_wgs84.to_crs(local_crs)
    cell_area_m2 = float(grid_proj.geometry.area.iloc[0])

    if roads_wgs84.empty:
        fallback_w = _DEFAULT_ROAD_WIDTH_M
        return pd.DataFrame({
            "FID": grid_proj["FID"].values,
            "road": 0.0,
            "W": fallback_w,
        })

    roads_proj = roads_wgs84.to_crs(local_crs).copy()
    roads_proj["width_from_rules"] = roads_proj["width_rules"].apply(_extract_width_from_rules)
    roads_proj["road_class"] = roads_proj["class"].fillna("unclassified")
    roads_proj["width_default"] = roads_proj["road_class"].map(_ROAD_WIDTHS).fillna(_DEFAULT_ROAD_WIDTH_M)
    roads_proj["road_width_m"] = roads_proj["width_from_rules"].combine_first(roads_proj["width_default"])

    roads_proj["centreline"] = roads_proj["geometry"].copy()
    roads_proj["geometry"] = roads_proj.apply(
        lambda r: r.geometry.buffer(r["road_width_m"] / 2.0), axis=1
    )
    roads_proj = roads_proj[roads_proj.geometry.is_valid & ~roads_proj.geometry.is_empty]

    # Road fraction
    roads_clipped = gpd.overlay(
        roads_proj[["geometry", "road_width_m", "road_class"]],
        grid_proj[["FID", "geometry"]],
        how="intersection",
        keep_geom_type=False,
    )

    has_buildings = not buildings_wgs84.empty
    if has_buildings:
        buildings_proj = buildings_wgs84.to_crs(local_crs)
        buildings_proj = buildings_proj[buildings_proj.geometry.is_valid].copy()
        roads_clipped = gpd.overlay(
            roads_clipped,
            buildings_proj[["geometry"]],
            how="difference",
            keep_geom_type=False,
        )
        roads_clipped = roads_clipped[
            roads_clipped.geometry.is_valid & ~roads_clipped.geometry.is_empty
        ]

    roads_dissolved = roads_clipped[["FID", "geometry"]].dissolve(by="FID").reset_index()
    roads_dissolved["road_area_m2"] = roads_dissolved.geometry.area
    grid_road = grid_proj[["FID"]].merge(roads_dissolved[["FID", "road_area_m2"]], on="FID", how="left")
    grid_road["road_area_m2"] = grid_road["road_area_m2"].fillna(0.0)
    grid_road["road"] = (grid_road["road_area_m2"] / cell_area_m2).clip(0, 1)

    # Canyon width via perpendicular transects (vectorised gather + threaded compute)
    fallback_global = float(roads_proj["road_width_m"].mean())

    centreline_arr = roads_proj["centreline"].values
    width_arr = roads_proj["road_width_m"].values
    fid_arr = grid_proj["FID"].values
    cell_geom_arr = grid_proj.geometry.values

    road_tree = STRtree(centreline_arr)
    cell_idxs, road_idxs = road_tree.query(cell_geom_arr, predicate="intersects")

    cell_to_roads: dict[int, list[int]] = defaultdict(list)
    for ci, ri in zip(cell_idxs, road_idxs):
        cell_to_roads[ci].append(ri)

    if has_buildings:
        bldg_geom_arr = buildings_proj.geometry.values
        bldg_tree = STRtree(bldg_geom_arr)
        buffered_cells = _shapely.buffer(cell_geom_arr, _TRANSECT_LENGTH_M)
        bcell_idxs, bldg_idxs = bldg_tree.query(buffered_cells)
        cell_to_bldgs: dict[int, list[int]] = defaultdict(list)
        for ci, bi in zip(bcell_idxs, bldg_idxs):
            cell_to_bldgs[ci].append(bi)
    else:
        bldg_geom_arr = np.array([], dtype=object)
        cell_to_bldgs = {}

    def _cell_canyon_w(ci: int) -> tuple[int, float]:
        local_centrelines = centreline_arr[cell_to_roads[ci]]
        local_widths = width_arr[cell_to_roads[ci]]
        bldg_idx = cell_to_bldgs.get(ci, [])
        local_bldg_geoms = bldg_geom_arr[bldg_idx] if bldg_idx else []

        if len(local_bldg_geoms) == 0:
            return fid_arr[ci], float(np.mean(local_widths))

        local_boundary = unary_union(local_bldg_geoms).boundary
        seg_ws, seg_lens = [], []
        for centreline, width in zip(local_centrelines, local_widths):
            w = _measure_canyon_width(centreline, local_boundary, width)
            seg_ws.append(w)
            seg_lens.append(centreline.length)

        w_val = float(np.average(seg_ws, weights=seg_lens)) if seg_ws else fallback_global
        return fid_arr[ci], w_val

    W_per_cell: dict[int, float] = {}
    with ThreadPoolExecutor(max_workers=_N_WORKERS) as pool:
        for fid, w in pool.map(_cell_canyon_w, cell_to_roads.keys()):
            W_per_cell[fid] = w

    W_series = pd.Series(W_per_cell, name="W").rename_axis("FID").reset_index()
    grid_road = grid_road.merge(W_series, on="FID", how="left")
    grid_road["W"] = grid_road["W"].fillna(fallback_global).clip(lower=_MIN_W_M)

    return grid_road[["FID", "road", "W"]]
