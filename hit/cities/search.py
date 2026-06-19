from __future__ import annotations

import math

import geopandas as gpd

from .loader import _POP_YEARS, load_ucdb

_DB: gpd.GeoDataFrame | None = None


def _get_db() -> gpd.GeoDataFrame:
    global _DB
    if _DB is None:
        _DB = load_ucdb()
    return _DB


def search_cities(
    query: str,
    country: str | None = None,
    n: int = 10,
    db: gpd.GeoDataFrame | None = None,
) -> list[dict]:
    """Return up to n cities whose name contains `query` (case-insensitive)."""
    if db is None:
        db = _get_db()

    mask = db["name"].str.contains(query.strip(), case=False, na=False, regex=False)
    if country:
        mask &= db["country"].str.contains(country.strip(), case=False, na=False, regex=False)

    matches = db[mask].sort_values("population", ascending=False).head(n)
    return [_to_record(row) for _, row in matches.iterrows()]


def _to_record(row) -> dict:
    centroid = row.geometry.centroid
    pop = row.get("population")

    record: dict = {
        "name": row["name"],
        "country": row["country"],
        "population": int(round(pop)) if pop and not (isinstance(pop, float) and math.isnan(pop)) else None,
        "centroid_lat": centroid.y,
        "centroid_lon": centroid.x,
        "geometry": row.geometry,
    }

    for yr in _POP_YEARS:
        col = f"pop_{yr}"
        val = row.get(col)
        record[col] = (
            int(round(val)) if val and not (isinstance(val, float) and math.isnan(val)) else None
        )

    return record
