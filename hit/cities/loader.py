import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

UCDB_DIR  = Path(__file__).parents[2] / "data" / "ghsl"
UCDB_PATH = UCDB_DIR / "GHS_UCDB_GLOBE_R2024A.gpkg"

_UCDB_ZIP_URL = (
    "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/"
    "GHS_UCDB_GLOBE_R2024A/GHS_UCDB_GLOBE_R2024A/V1-1/"
    "GHS_UCDB_GLOBE_R2024A_V1_1.zip"
)


def _download_ucdb(dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / "GHS_UCDB_GLOBE_R2024A_V1_1.zip"
    print(f"Downloading GHSL-UCDB (~400 MB) to {zip_path} …")
    with requests.get(_UCDB_ZIP_URL, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(zip_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    print("Extracting …")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest_dir)
    zip_path.unlink()
    gpkg_files = sorted(dest_dir.rglob("*.gpkg"))
    if not gpkg_files:
        raise RuntimeError("No .gpkg found after extracting UCDB zip")
    gpkg = gpkg_files[0]
    if gpkg.parent != dest_dir:
        gpkg = gpkg.rename(dest_dir / gpkg.name)
    print(f"UCDB ready at {gpkg}")
    return gpkg

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
        if candidates:
            path = candidates[0]
        else:
            path = _download_ucdb(UCDB_DIR)

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
