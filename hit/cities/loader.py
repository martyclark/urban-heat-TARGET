from pathlib import Path

import geopandas as gpd
import pandas as pd

UCDB_DIR  = Path(__file__).parents[2] / "data" / "ghsl"
UCDB_PATH = UCDB_DIR / "GHS_UCDB_GLOBE_R2024A.gpkg"

_LAYER_GEN  = "GHS_UCDB_THEME_GENERAL_CHARACTERISTICS_GLOBE_R2024A"
_LAYER_GHSL = "GHS_UCDB_THEME_GHSL_GLOBE_R2024A"

_POP_YEARS = [2000, 2005, 2010, 2015, 2020, 2025]
_POP_COLS  = {yr: f"GH_POP_TOT_{yr}" for yr in _POP_YEARS}


def _strip_bom(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Strip Unicode BOM (﻿) embedded by JRC in column names and string values."""
    gdf.columns = [c.lstrip("﻿") for c in gdf.columns]
    for col in gdf.select_dtypes(include=["object", "string"]).columns:
        try:
            gdf[col] = gdf[col].str.lstrip("﻿")
        except AttributeError:
            pass
    return gdf


def load_ucdb(path: Path = UCDB_PATH) -> gpd.GeoDataFrame:
    """Load GHS-UCDB R2024A and return a normalised GeoDataFrame.

    Columns: name, country, population (2025), pop_2000…pop_2025, geometry (WGS84).
    """
    if not path.exists():
        candidates = sorted(UCDB_DIR.glob("*.gpkg"))
        if not candidates:
            raise FileNotFoundError(
                "GHSL-UCDB file not found. "
                "Download GHS_UCDB_GLOBE_R2024A_V1_1.zip from "
                "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/"
                "GHS_UCDB_GLOBE_R2024A/GHS_UCDB_GLOBE_R2024A/V1-1/GHS_UCDB_GLOBE_R2024A_V1_1.zip "
                f"and extract the .gpkg into {UCDB_DIR}/"
            )
        path = candidates[0]

    gen = _strip_bom(gpd.read_file(path, layer=_LAYER_GEN))
    gen = gen[["ID_UC_G0", "GC_UCN_MAI_2025", "GC_CNT_GAD_2025", "geometry"]].rename(
        columns={"GC_UCN_MAI_2025": "name", "GC_CNT_GAD_2025": "country"}
    )

    ghsl = _strip_bom(gpd.read_file(path, layer=_LAYER_GHSL))
    pop_cols_avail = {yr: col for yr, col in _POP_COLS.items() if col in ghsl.columns}
    ghsl = ghsl[["ID_UC_G0"] + list(pop_cols_avail.values())].copy()
    rename_map = {col: f"pop_{yr}" for yr, col in pop_cols_avail.items()}
    ghsl = ghsl.rename(columns=rename_map)

    merged = gen.merge(ghsl, on="ID_UC_G0", how="left")
    merged = merged.drop(columns=["ID_UC_G0"])

    # Reproject Mollweide → WGS84 so centroid.y / centroid.x give lat/lon
    merged = merged.to_crs("EPSG:4326")

    # Main population = 2025 estimate
    merged["population"] = merged.get("pop_2025", None)

    return merged
