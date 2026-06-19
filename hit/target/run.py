import configparser
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import geopandas as gpd
import numpy as np

from hit.config.city import CityConfig

DATA_VARS = [
    "Ws", "Ta", "Ts_horz", "Tac_can_roof", "roofTsrfT",
    "Tmrt", "UTCI", "UTCI_cat", "httc_urb_new", "Tb_rur",
]

_DEFAULT_PARAMETERS = {
    "res":    {"value": 200},
    "karman": {"value": 0.41},
    "sb":     {"value": 5.67e-08},
    "alb": {"value": {
        "roof": 0.18, "wall": 0.18, "road": 0.12, "watr": 0.05,
        "conc": 0.15, "Veg": 0.20, "dry": 0.30, "irr": 0.20,
    }},
    "emis": {"value": {
        "roof": 0.90, "wall": 0.90, "road": 0.95, "watr": 0.97,
        "conc": 0.92, "Veg": 0.98, "dry": 0.93, "irr": 0.96,
    }},
    "rs": {"value": {
        "roof": -999.0, "wall": -999.0, "road": -999.0, "watr": 0.0,
        "conc": -999.0, "Veg": 40.0, "dry": -999.0, "irr": 40.0,
    }},
    "C": {"value": {
        "roof": 1.25e6, "wall": 1.25e6, "road": 1.94e6, "watr": 4.18e6,
        "conc": 2.11e6, "dry": 1.35e6, "irr": 2.19e6, "soilW": 3.03e6,
    }},
    "K": {"value": {
        "roof": 5e-8, "wall": 5e-8, "road": 3.8e-7, "watr": 1.4e-7,
        "conc": 7.2e-7, "dry": 2.1e-7, "irr": 4.2e-7, "soilW": 6.3e-7,
    }},
    "Ts": {"value": {
        "roof": 20.0, "wall": 20.0, "road": 20.0, "watr": 20.0,
        "conc": 20.0, "dry": 20.0, "irr": 20.0,
    }},
    "Tm": {"value": {
        "roof": 25.0, "wall": 25.0, "road": 26.0, "watr": 24.5,
        "conc": 26.0, "dry": 22.4, "irr": 21.5,
    }},
    "LUMPS1": {"value": {
        "roof": [0.12, 0.24, -4.5], "wall": [0.12, 0.24, -4.5],
        "road": [0.50, 0.28, -31.45], "conc": [0.61, 0.28, -23.9],
        "Veg": [0.11, 0.11, -12.3], "dry": [0.27, 0.33, -21.75],
        "irr": [0.32, 0.54, -27.4],
    }},
    "alphapm": {"value": {
        "roof": 0.0, "wall": 0.0, "road": 0.0, "conc": 0.0,
        "Veg": 1.2, "dry": 0.2, "irr": 1.2,
    }},
    "beta": {"value": {
        "roof": 3.0, "wall": 3.0, "road": 3.0, "conc": 3.0,
        "Veg": 3.0, "dry": 3.0, "irr": 3.0,
    }},
    "Kw":    {"value": 6.18e-7},
    "hv":    {"value": 0.0014},
    "betaW": {"value": 0.45},
    "zW":    {"value": 0.3},
    "NW":    {"value": "formula_(1.1925*$zW**(-0.424))"},
    "dW":    {"value": "formula_math.sqrt(2*$K_soilW/(2*math.pi / 86400))"},
    "ww":    {"value": "formula_(2*math.pi / 86400)"},
    "cp":    {"value": 0.001013},
    "cpair": {"value": 1004.67},
    "e":     {"value": 0.622},
    "pa":    {"value": 1.2},
    "Lv":    {"value": 2430000.0},
    "z_TaRef": {"value": 2.0},
    "z_URef":  {"value": 10.0},
    "z0m":     {"value": 0.1},
    "zavg":    {"value": 4.5},
}


def prepare_inputs(
    config: CityConfig,
    data_dir: Path,
    run_dir: Path,
    target_work_dir: Path,
    parameters: dict | None = None,
    timestep_min: int = 60,
    mod_ldwn: str = "N",
) -> Path:
    """Set up TARGET input directory for a city run.

    Copies met forcing and land cover into the TARGET directory tree,
    computes grid dimensions from grid.gpkg, and writes config.ini and
    parameters.json.

    Returns the path to config.ini.
    """
    site_dir = target_work_dir / config.city_name.lower()
    (site_dir / "input" / "MET").mkdir(parents=True, exist_ok=True)
    (site_dir / "input" / "LC").mkdir(parents=True, exist_ok=True)
    (site_dir / "output").mkdir(parents=True, exist_ok=True)

    met_src = run_dir / "era5_met_forcing.csv"
    lc_src = data_dir / "target_landcover.csv"
    if not met_src.exists():
        raise FileNotFoundError(f"Met forcing not found: {met_src}")
    if not lc_src.exists():
        raise FileNotFoundError(f"Land cover not found: {lc_src}")

    shutil.copy2(met_src, site_dir / "input" / "MET" / "met_forcing.csv")
    shutil.copy2(lc_src, site_dir / "input" / "LC" / "landcover.csv")

    grid_path = data_dir / "grid.gpkg"
    if not grid_path.exists():
        raise FileNotFoundError(f"Grid not found: {grid_path}")

    grid_proj = gpd.read_file(grid_path).to_crs(config.local_crs)
    centroids = grid_proj.geometry.centroid
    unique_x = np.unique(np.round(centroids.x.values, -1))
    unique_y = np.unique(np.round(centroids.y.values, -1))
    n_cols, n_rows = len(unique_x), len(unique_y)

    grid_wgs84 = grid_proj.to_crs("EPSG:4326")
    lon_min, lat_min, lon_max, lat_max = grid_wgs84.total_bounds
    lon_resolution = (lon_max - lon_min) / n_cols
    lat_resolution = (lat_max - lat_min) / n_rows
    centroid_lat = (lat_min + lat_max) / 2.0

    dt_start = datetime.strptime(config.date_start, "%Y-%m-%d")
    dt_end = datetime.strptime(config.date_end, "%Y-%m-%d")
    dt_spinup = dt_start - timedelta(days=1)

    def _fmt(dt, hour=0):
        return f"{dt.year},{dt.month},{dt.day},{hour}"

    config_content = (
        f"[DEFAULT]\n"
        f"work_dir={target_work_dir.resolve()}\n"
        f"para_json_path={site_dir.resolve()}/parameters.json\n"
        f"site_name={config.city_name.lower()}\n"
        f"run_name={config.run_name}\n"
        f"inpt_met_file=met_forcing.csv\n"
        f"inpt_lc_file=landcover.csv\n"
        f"date_fmt=%d/%m/%Y %H:%M\n"
        f"timestep={timestep_min}\n"
        f"mod_ldwn={mod_ldwn}\n"
        f"include roofs=Y\n"
        f"lat={centroid_lat:.6f}\n"
        f"domainDim={n_rows},{n_cols}\n"
        f"latEdge={lat_max:.6f}\n"
        f"lonEdge={lon_min:.6f}\n"
        f"latResolution={lat_resolution:.6f}\n"
        f"lonResolution={lon_resolution:.6f}\n"
        f"z_URef=10.0\n"
        f"zavg=4.5\n"
        f"date1a={_fmt(dt_spinup)}\n"
        f"date1={_fmt(dt_start)}\n"
        f"date2={_fmt(dt_end, hour=23)}\n"
    )

    config_ini_path = site_dir / "config.ini"
    config_ini_path.write_text(config_content)

    params = parameters or _DEFAULT_PARAMETERS
    params_path = site_dir / "parameters.json"
    with params_path.open("w") as f:
        json.dump(params, f, indent=2)

    return config_ini_path


def run_target(config_ini_path: Path, progress: bool = True) -> Path:
    """Run the TARGET simulation.

    Returns the path to the output .npy file.
    """
    from target_py import Target

    tar = Target(str(config_ini_path), progress=progress)
    tar.load_config()
    tar.run_simulation(save_csv=True)
    tar.save_simulation_parameters()

    # Derive output path from config
    import configparser
    cfg = configparser.ConfigParser()
    cfg.read(config_ini_path)
    work_dir = Path(cfg["DEFAULT"]["work_dir"])
    site_name = cfg["DEFAULT"]["site_name"]
    run_name = cfg["DEFAULT"]["run_name"]

    output_path = work_dir / site_name / "output" / f"{run_name}.npy"
    if not output_path.exists():
        raise RuntimeError(f"TARGET completed but output not found: {output_path}")

    return output_path
