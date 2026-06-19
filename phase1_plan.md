# Phase 1 build plan — Foundation

Test city: **Salvador, Brazil** (`BRA_salvador`, `heatwave_oct2023`)
Existing outputs in: `target-UMEP/data/BRA_salvador/heatwave_oct2023/`

---

## Goals

- `target_py` patched and vendored — reproducible in any environment
- TARGET pipeline extractable and testable from outside the notebooks
- Dockerfile builds and runs TARGET end-to-end
- TARGET runtime and memory profiled for a 1-week run
- `hit/` package skeleton in place with `hit/target/` working against Salvador

---

## Task 1 — Initialise the `hit` repo

```
/Users/martynclark/hit/
  hit/
    __init__.py
    config/
      __init__.py
      city.py          # CityConfig dataclass
    target/
      __init__.py
    era5/
      __init__.py
    cities/
      __init__.py
    exposure/
      __init__.py
    spatial/
      __init__.py
  tests/
  scripts/             # one-off profiling / validation scripts, not part of the package
  Dockerfile
  pyproject.toml       # or setup.py — whichever you prefer
  requirements.txt
```

Steps:
1. `git init` inside `/Users/martynclark/hit/`
2. Create the directory tree above with empty `__init__.py` files
3. Add `pyproject.toml` with package metadata and core dependencies (pinned versions — see Task 3)

---

## Task 2 — Vendor patched `target_py`

The installed `target_py` has four hand-edits (see spec). It must live in the repo, not pulled from PyPI.

Steps:
1. Copy the patched package from the conda env into the repo:
   ```bash
   cp -r ~/miniforge3/envs/target-umep/lib/python3.11/site-packages/target_py \
         /Users/martynclark/hit/vendor/target_py
   ```
2. Add `vendor/` to the repo. Update `pyproject.toml` to install from `vendor/target_py` locally rather than PyPI.
3. Verify the four patches are present:
   - `vendor/target_py/scripts/TbRurSolver.py` — `iterations = 100`
   - `vendor/target_py/scripts/UTCI.py` — `max(..., 1e-10)` clamp
   - `vendor/target_py/scripts/toolkit.py` — `mod_rslts[1:len(met_data_all)]`
   - `vendor/target_py/ui/utils.py` — uninitialised row guard + `%Y-%m-%d %H_%M_%S`
4. Delete all `.pyc` files from `vendor/target_py`

### Regression tests (`tests/test_target_patches.py`)

Write three targeted tests that would fail on a clean PyPI install:

| Test | What it catches |
|---|---|
| `test_tbRurSolver_iterations` | Read `iterations` from source; assert `== 100` |
| `test_utci_domain_guard` | Call the UTCI solver with inputs that produce a negative argument; assert no `ValueError` |
| `test_toolkit_slice` | Assert `mod_rslts[1:len(met_data_all)]` pattern is present (or construct a minimal run and check no `AttributeError` on trailing rows) |

---

## Task 3 — Dockerfile

Build this before anything else runs inside it. The image is pure Python + GDAL — no QGIS.

Key dependencies to pin (check exact versions from the `target-umep` conda env):
```
gdal
numpy
pandas
xarray
geopandas
rasterio / rioxarray
shapely
fiona
zarr
fsspec
requests
xclim          # pin minor version — breaking changes between releases
streamlit      # add later; pin here now
pydeck
plotly
```

Dockerfile approach:
- Base image: `python:3.11-slim` + system GDAL via `apt`, or `osgeo/gdal:ubuntu-small-3.x.x` as base
- Copy `vendor/target_py` and install locally
- Copy `hit/` package and install in editable mode
- `CMD ["python", "-m", "pytest", "tests/"]` for CI validation; override for Streamlit later

**Acceptance test:** run `docker build` cleanly, then inside the container:
```bash
python -c "from target_py import Target; print('ok')"
python -m pytest tests/test_target_patches.py
```

---

## Task 4 — Profile TARGET with Salvador

Run a **1-week slice** of the Salvador `heatwave_oct2023` run inside the Docker container. Pick 7 days from the existing `era5_met_forcing.csv`.

Measure:
- Wall-clock time (full run, not just model — include input prep and output write)
- Peak memory (`/usr/bin/time -v` or `memory_profiler`)
- Count of "No convergence: values too extreme" warnings (UTCI solver)

Record results in `scripts/profiling/salvador_1week_profile.txt`.

**Decision gate:** based on results, confirm or revise the async queue design before Phase 2:
- Under 10 min → synchronous is borderline viable but async queue still recommended
- 10–30 min → async queue required; `concurrent.futures` + SQLite job table is sufficient
- Over 30 min → async queue required; assess whether parallelisation across cells is worth pursuing

---

## Task 5 — `hit/config/` — CityConfig dataclass

Generalise `city_config.py` from `target-UMEP` into a typed dataclass.

File: `hit/config/city.py`

```python
from dataclasses import dataclass

@dataclass
class CityConfig:
    city_name: str
    country_iso: str
    bbox: tuple[float, float, float, float]  # (lon_min, lat_min, lon_max, lat_max)
    local_crs: str                            # e.g. "EPSG:31985"
    utc_offset: float
    run_name: str
    date_start: str                           # "YYYY-MM-DD"
    date_end: str                             # "YYYY-MM-DD"

    @property
    def city_id(self) -> str:
        return f"{self.country_iso}_{self.city_name.lower()}"
```

Salvador config for tests:
```python
SALVADOR = CityConfig(
    city_name="Salvador",
    country_iso="BRA",
    bbox=(-38.60, -13.05, -38.30, -12.80),
    local_crs="EPSG:31985",
    utc_offset=-3.0,
    run_name="heatwave_oct2023",
    date_start="2023-10-14",
    date_end="2023-10-20",
)
```

---

## Task 6 — `hit/target/` — TARGET pipeline wrapper

Extract the core logic from NB05 + NB06 into callable functions. No web dependencies. Inputs and outputs are explicit paths.

Key functions to expose:

```python
# hit/target/run.py
def prepare_inputs(config: CityConfig, data_dir: Path) -> Path:
    """Combine fractions and write TARGET input files to target_runs/{site}/input/.
    Returns path to the input directory."""

def run_target(config: CityConfig, input_dir: Path, output_dir: Path) -> Path:
    """Run TARGET model. Returns path to target_results.nc."""

def load_results(results_nc: Path) -> xr.Dataset:
    """Load TARGET numpy output into xarray Dataset."""
```

**Parity test (`tests/test_target_parity.py`):**

Load the existing Salvador `target_results.nc` from `target-UMEP/data/BRA_salvador/heatwave_oct2023/`. Run `hit/target/` against the same inputs. Assert key variables (Tac, UTCI) match to within floating-point tolerance.

```python
def test_salvador_tac_parity():
    reference = xr.open_dataset("path/to/reference/target_results.nc")
    result = run_pipeline(SALVADOR, ...)
    xr.testing.assert_allclose(result["Tac"], reference["Tac"], atol=1e-4)
```

---

## Phase 1 exit criteria

- [ ] `vendor/target_py` in repo; three patch regression tests pass
- [ ] `docker build` succeeds cleanly
- [ ] TARGET runs inside container for a 1-week Salvador slice; runtime and memory recorded
- [ ] `CityConfig` dataclass defined and importable
- [ ] `hit/target/` produces numerically identical outputs to existing Salvador notebooks
- [ ] Async queue design confirmed based on profiling results

---

## What Phase 1 does NOT include

- GHSL-UCDB integration (Phase 1, Task 5 in spec — starts after TARGET is stable)
- ERA5 30-year baseline / TX90p (Phase 1, Tasks 6–7 in spec)
- Any Streamlit UI
- Adelaide parity test (do Salvador first; Adelaide is a quick addition once the pattern is established)

---

## Phase 2 prerequisites — data downloads

### GHSL Urban Centres Database (GHS-UCDB)

Required before `hit/cities/` can be built. Download from:
https://human-settlement.emergency.copernicus.eu/download.php?ds=ucdb

Download the **GHS-UCDB R2019A** GeoPackage (the `.gpkg` file — ~100 MB).

Place at: `hit/data/ghsl/GHS_STAT_UCDB2015MT_GLOBE_R2019A_V1_2.gpkg`
(or equivalent filename — adjust the path constant in `hit/cities/loader.py` if the filename differs)

Key attributes used:
- `UC_NM_MN` — city name
- `CTR_MN_ISO` — ISO3 country code
- `P15` — population estimate 2015 (used for person-days calculation)
- `geometry` — urban centre polygon boundary

This file is static open data (JRC/Copernicus). For GCP deployment, stage it in GCS so the VM can load it without a local copy.
