# HIT — Urban Heat Index Tool

A Streamlit web application for urban heat exposure screening and neighbourhood-scale microclimate modelling. Packages the [TARGET](https://github.com/jixuan-chen/target) urban climate model into a deployable web tool, backed by global open datasets.

Built on the research pipeline at [target-UMEP](https://github.com/martyclark/target-UMEP).

---

## What it does

**Tab 1 — City Screening**
- Search 11,422 global urban centres (GHSL-UCDB R2024A)
- ERA5 UTCI climatology 1991–2025: annual cycle, heat stress day trends, population-weighted exposure
- Identifies candidate periods for detailed neighbourhood modelling

**Tab 2 — Neighbourhood Analysis**
- Generates a 200 m grid over the city; fetches building, road, and land cover data automatically
- Runs the TARGET urban microclimate model (~29 min for 1 week at ~14,000 cells)
- Outputs: spatial UTCI/Tmrt/UHI map, diurnal temperature and thermal stress cycles, UHI time series
- Downloads: modelled results (NetCDF) and input morphology data

**Tab 3 — Future Climate**  
Placeholder for NEX-GDDP-CMIP6 projections (SSP2-4.5 and SSP3-7.0, 1950–2100) — in development.

---

## Model architecture

The neighbourhood model is **TARGET** (Temperature of Air for Green/non-green urban Typologies), a single-layer urban canyon energy balance model.

- **Paper:** Broadbent et al. 2019, *Urban Climate* — [doi.org/10.1016/j.uclim.2018.11.002](https://doi.org/10.1016/j.uclim.2018.11.002)
- **UMEP documentation:** [umep-docs.readthedocs.io — TARGET processor](https://umep-docs.readthedocs.io/en/latest/processor/Urban%20Energy%20Balance%20TARGET.html)
- **UMEP project:** [umep-docs.readthedocs.io](https://umep-docs.readthedocs.io/)

TARGET is vendored in `vendor/target_py/` with patches for Python 3.11 compatibility and sentinel value handling.

---

## Data sources

| Layer | Source | Licence |
|---|---|---|
| Urban centre boundaries + population | [GHS-UCDB R2024A](https://ghsl.jrc.ec.europa.eu/ghs_ucdb2024.php) (JRC) | CC BY 4.0 |
| UTCI climatology (1940–present, ~25 km) | [ECMWF ARCO ERA5 derived UTCI](https://cds.climate.copernicus.eu/datasets/derived-utci-historical) | Copernicus CDS — free, licence acceptance required |
| Building footprints + heights | [Global Building Atlas (GBA) WFS](https://www.gba.ovgu.de/) — Zhu et al. 2025, ESSD | Open, no key needed |
| Road network | [Overture Maps Foundation](https://overturemaps.org/) transportation layer | ODbL |
| Land cover | [ESA WorldCover 2021](https://esa-worldcover.org/) 10 m | CC BY 4.0 |
| Future projections (planned) | [NEX-GDDP-CMIP6 v2](https://www.nasa.gov/nex-gddp/) (NASA) via [CarbonPlan Kerchunk](https://carbonplan.org/) | CMIP6 Terms of Use |

---

## Setup

### Prerequisites

- Python 3.11+
- GDAL/GEOS/PROJ (bundled via PyPI wheels — no system install needed)
- CDS API key — register and accept licence at [cds.climate.copernicus.eu](https://cds.climate.copernicus.eu/datasets/derived-utci-historical)
- GHSL-UCDB data (see below)

### Install

```bash
git clone https://github.com/martyclark/hit.git
cd hit

# Install vendored target_py first
pip install vendor/target_py

# Install hit package
pip install -e ".[dev]"
```

### GHSL-UCDB data

Download and extract the GeoPackage:

```bash
mkdir -p data/ghsl
curl -L "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_UCDB_GLOBE_R2024A/GHS_UCDB_GLOBE_R2024A/V1-1/GHS_UCDB_GLOBE_R2024A_V1_1.zip" \
     -o data/ghsl/GHS_UCDB_GLOBE_R2024A_V1_1.zip
unzip data/ghsl/GHS_UCDB_GLOBE_R2024A_V1_1.zip -d data/ghsl/
```

### CDS API key

Create `.streamlit/secrets.toml`:

```toml
CDS_API_KEY = "your-key-here"
```

Or set the environment variable: `export CDS_API_KEY=your-key-here`

### Run

```bash
streamlit run app.py
```

---

## Docker

```bash
docker build -t hit:latest .
docker run -p 8501:8501 \
  -e CDS_API_KEY=your-key-here \
  -v $(pwd)/data:/app/data \
  hit:latest
```

App available at `http://localhost:8501`.

---

## Tests

```bash
pytest tests/ -v
```

- `tests/test_target_patches.py` — 4 TARGET patch regression tests
- `tests/test_target_parity.py` — 4 parity tests against Salvador reference outputs

---

## Project structure

```
hit/
  config/       CityConfig dataclass
  target/       TARGET pipeline wrapper (run.py, results.py)
  cities/       GHSL-UCDB city search and loader
  era5/         UTCI Zarr retrieval, indices, period suggestion
  exposure/     Person-days and population-weighted exposure
  spatial/      Grid ops, diurnal stats, UHI series
  jobs/         Async job queue (subprocess + filesystem)
vendor/
  target_py/    Patched TARGET model (Python 3.11 compatible)
tests/
data/
  ghsl/         GHS-UCDB GeoPackage (download separately)
  era5/         Cached daily-max UTCI NetCDF per city
  target/       TARGET morphology and results per city
```

---

## Known limitations

- ERA5 meteorological forcing is applied at ~25 km resolution uniformly across the modelled grid — it does not capture mesoscale spatial variation within the city
- GBA building height coverage varies by region; cells with no data default to 8 m
- TARGET is a single-layer canyon model; tall or complex 3D building geometry is approximated via mean H and W parameters
- Neighbourhood runs take ~29 min for a 1-week period at ~14,000 cells on a 4-core machine — an async job queue is used; the browser polls for results
- Tab 3 (future projections) shows illustrative synthetic data only — real NEX-GDDP pipeline is in development
