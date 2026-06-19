# CLAUDE.md — HIT project context

This file loads automatically when working in `/Users/martynclark/hit/`.
The reference notebook pipeline lives in `/Users/martynclark/target-UMEP/` — do not build there.

---

## What this project is

**HIT (Urban Heat Index Tool)** — a three-tab Streamlit web app for urban heat exposure screening, deployable on GCP. Packages the TARGET urban climate model pipeline into a web tool.

- Tab 1: City-level UTCI heat climatology (~25km, global city search via GHSL-UCDB)
- Tab 2: Neighbourhood-level TARGET modelling (200m, hourly)
- Tab 3: Future climate projections (city-level UTCI trends to 2100 under SSP2-4.5 and SSP3-7.0)

Full spec: `urban_climate_pipeline_spec_v2.md`
Phase 1 plan (complete): `phase1_plan.md`

---

## Build status

### Phase 1 — COMPLETE

- `vendor/target_py` — patched `target_py` vendored with `setup.py`; 4 patch regression tests pass
- `Dockerfile` — builds cleanly as `hit:phase1`; pure Python + GDAL, no QGIS
- `hit/config/city.py` — `CityConfig` dataclass; `SALVADOR` config (dates 2023-10-21 to 2023-10-27)
- `hit/target/run.py` + `hit/target/results.py` — TARGET pipeline wrapper
- `tests/test_target_patches.py` — 4 patch tests (pass locally and in Docker)
- `tests/test_target_parity.py` — 4 parity tests against Salvador reference outputs (all pass)
- TARGET runtime profiled: **~29 min for 1-week Salvador run on local MacBook**
- Async job queue confirmed required from day one

### Phase 2 — COMPLETE (Streamlit Tab 1, UTCI)

- `hit/cities/loader.py` — loads GHS-UCDB R2024A, strips BOM, reprojects Mollweide→WGS84, merges GHSL historical pop (2000–2025)
- `hit/cities/search.py` — city name search (case-insensitive substring, sorted by population)
- `hit/era5/baseline.py` — ECMWF ARCO UTCI Zarr fetch (hourly → daily max) with file-based caching
- `hit/era5/indices.py` — `tx90p_threshold`, `count_extreme_heat_days`, `heat_type`, `annual_utci_cycle`, `utci_category_days_annual`; plus `UTCI_CATEGORIES` and `HEAT_STRESS_CATEGORIES` constants
- `hit/era5/periods.py` — `suggest_periods` (rolling UTCI anomaly ranking vs 30-year baseline DOY means)
- `hit/exposure/__init__.py` — `person_days`, `population_weighted_heat_exposure`
- `app.py` — Streamlit three-tab app; Tab 1 fully implemented

### Phase 3 — COMPLETE (Streamlit Tab 2, Neighbourhood Analysis)

- `hit/jobs/queue.py` + `hit/jobs/worker.py` — async job queue (subprocess + SQLite); `submit_run`, `get_status`
- `hit/spatial/__init__.py` — `results_to_geodataframe`, `diurnal_stats`, `utci_diurnal_stats`, `uhi_series`
- `app.py` Tab 2 — full results display: spatial map (Folium, 200m grid coloured by peak UTCI/Tmrt/UHI), diurnal cycle charts (air temp + Tmrt bands), UTCI stress band diurnal cycle, UHI intensity time series
- Pre-staged run loading + live job progress polling with fallback to pre-staged demo data
- Tab 3 shows a clearly-labelled placeholder with synthetic illustrative charts

### Phase 8 — Neighbourhood exposure summaries (Brazil only, geobr)

**Dependency note:** bairro boundaries are only available for Brazil via geobr. No equivalent programmatic source exists for other countries yet. This phase is deferred until either (a) boundary sources are identified for additional countries, or (b) the tool's scope is confirmed as Brazil-only. For non-Brazilian cities Tab 2 will continue to show cell-level results only.

Overlay TARGET 200m outputs onto official neighbourhood (bairro) boundaries and WorldPop population to produce per-neighbourhood heat exposure summaries and a priority quadrant diagram.

**Neighbourhood boundaries — Brazil (geobr)**
- Python package `geobr` (`pip install geobr`) wraps the IBGE spatial data API
- Geography: **bairros** via `geobr.read_neighborhood()` — official named neighbourhoods that residents and planners recognise (e.g. Barra, Itaigara, Liberdade in Salvador); large enough to aggregate ~10–50 TARGET cells each
- Filter by municipality code to the city of interest; geobr geometries are in WGS84
- Fall back to direct HTTP fetch of the underlying GeoPackage from geobr GitHub releases if the package call fails
- Cache to `data/boundaries/BRA/bairros_{municipality_code}.gpkg`
- For non-Brazilian cities: bairro boundaries are not available; Tab 2 shows cell-level map only

**Population — WorldPop**
- WorldPop 100m unconstrained UN-adjusted population counts, GeoTIFF per country per year
- Brazil: `bra_ppp_2020_UNadj.tif` — download on first use from `https://data.worldpop.org/GIS/Population/Global_2000_2020/2020/BRA/bra_ppp_2020_UNadj.tif`; cache to `data/worldpop/`
- Aggregate 100m pixels to bairro polygons via `rasterstats.zonal_stats` (sum)

**Heat metric**
Per-cell UTCI exceedance hours within the TARGET run window: count of hourly timesteps where UTCI exceeds each stress threshold (26, 32, 38, 46°C). Also compute nocturnal exceedance hours separately (20:00–06:00 local) — nights above threshold are the epidemiologically critical signal per the heat risk guidance above. Aggregate to bairros via `geopandas.sjoin` + mean across cells.

UI note: make clear this ranks spatial differentiation within the modelled period, not an annual burden estimate.

**New outputs in Tab 2**

*1. Bairro outlines on the existing 200m UTCI map*
Overlay bairro boundary polygons on the existing Folium spatial map (the one already showing peak UTCI / Tmrt / UHI per cell). Draw outlines only — no fill — so the 200m cell colours remain readable. Tooltip on each bairro shows name and mean UTCI.

*2. Neighbourhood summary table*
Below the map: bairro name, population (WorldPop), mean peak UTCI (°C), mean nocturnal exceedance hours above 32°C, exposure score (population × mean exceedance hours), rank. Sortable.

*3. Quadrant diagram — heat intensity vs. population*
Scatter plot: x-axis = mean UTCI exceedance hours above 32°C (heat intensity), y-axis = bairro population (WorldPop). One point per bairro, labelled by name. Median lines on both axes divide the plot into four quadrants:
- Top-right: **high heat + high population** — priority for intervention
- Top-left: high population, lower heat stress
- Bottom-right: high heat, lower population density
- Bottom-left: lower heat, lower population

Quadrant lines at median heat-hours and median population. Points sized by exposure score (population × heat-hours). No colour encoding needed — position and size carry the information.

*4. Ranked bar chart*
Horizontal bar: top-N bairros by exposure score (person-hours above 32°C threshold), so planners can see the absolute ranking alongside the quadrant.

**New module**
`hit/exposure/neighbourhoods.py`:
- `load_bairros(municipality_code)` → GeoDataFrame
- `compute_cell_exceedances(ds, utc_offset)` → GeoDataFrame with per-cell exceedance hour counts joined to cell geometries
- `aggregate_to_bairros(cell_exceedances_gdf, bairros_gdf)` → per-bairro mean exceedance hours
- `load_worldpop_population(bairros_gdf, tif_path)` → adds `population` column
- `neighbourhood_exposure(agg_df)` → adds `exposure_score` and `rank`

**Dependencies to add**: `geobr`, `rasterstats`

---

## Tab 1 design (confirmed)

- User searches GHSL-UCDB for a city → gets boundary + population
- Pull UTCI (hourly) from ECMWF ARCO Zarr for 1991–2025; resample to daily max; cache as NetCDF
- Baseline 1991–2020; recent window 2021–2025
- Annual UTCI cycle (monthly mean daily max, baseline)
- Heat type classification: chronic / seasonal / episodic (based on UTCI seasonality range)
- **UTCI stress category evolution** (stacked bar, days/year in moderate/strong/very strong/extreme heat stress, 1991–2025)
- **Population-weighted exposure** at GHSL 5-year snapshots (2000–2025): person-days per stress category
- Suggest 1–2 **candidate periods** for TARGET modelling (hottest rolling window by UTCI anomaly, 2021–2025)
- Period length selector: 1 week default, 2 or 4 weeks optional
- Do NOT use the word "heatwave"

---

## Tab 3 design (planned)

Two clearly-labelled, non-joined data series — observed and projected are never stitched into a single continuous line:

- **Observed** (ERA5, 1991–2025): already shown in Tab 1; not repeated here
- **Projected** (NEX-GDDP, 1950–2100): self-consistent — historical and future both from the same model world

Charts planned:
- Multi-model ensemble heat stress days per year (1950–2100): historical median line + future ribbon (10th–90th %ile) for SSP2-4.5 and SSP3-7.0; vertical marker at present
- Stress category breakdown at key horizons (2050, 2075, 2100) by scenario: stacked bar per UTCI category

Do NOT use the word "heatwave". Do NOT join ERA5 and NEX-GDDP series into a single time axis.

---

## UTCI data source (Tab 1 — historical, observed)

**ECMWF ARCO UTCI Zarr** (authenticated, HTTPS):
```
https://arco.datastores.ecmwf.int/cadl-arco-geo-004/arco/derived_utci_historical/all/geoChunked.zarr
```

- Variable: `utci`, units: `degK` (subtract 273.15 for °C)
- Dimensions: `(time, latitude, longitude)`; time is hourly ("hours since 1970-01-01")
- Chunk shape: `(67584, 4, 4)` — ~7.7 years × 4×4 spatial; geo-chunked for efficient point time-series
- Longitude: 0–360 (use `centroid_lon % 360`)
- Latitude: 90°N to –60°S (601 points at 0.25°)
- Coverage: 1940–present; ~25 MB download per point for 35 years, cached as ~50 KB daily NetCDF
- Requires `CDS_API_KEY` — set in `.streamlit/secrets.toml` (local) or Secret Manager env var (GCP)
- Licence must be accepted at `cds.climate.copernicus.eu/datasets/derived-utci-historical`

### UTCI stress categories (Bröde et al. 2012 / ISO 15743)

| Category | UTCI range |
|---|---|
| Extreme cold stress | < –40°C |
| Very strong cold stress | –40 to –27°C |
| Strong cold stress | –27 to –13°C |
| Moderate cold stress | –13 to 0°C |
| Slight cold stress | 0 to 9°C |
| No thermal stress | 9 to 26°C |
| **Moderate heat stress** | **26 to 32°C** |
| **Strong heat stress** | **32 to 38°C** |
| **Very strong heat stress** | **38 to 46°C** |
| **Extreme heat stress** | **> 46°C** |

### Known limitation — pre-computed statistics not yet available via Zarr

The `derived-utci-historical` dataset includes pre-computed yearly statistics (days per year per stress category: `utci_days_above_X_daymax`, `utci_days_in_range_9_26_daymax`, etc.) and daily max statistics (`utci_max_of_daymax`). These would eliminate the need for the hourly → daily resample and `utci_category_days_annual` computation.

**Current state:** only `universal_thermal_climate_index_yearly_statistics` has a geoChunked Zarr path (`derived_utci_historical/universal_thermal_climate_index_yearly_statistics/geoChunked.zarr`) but it was not accessible at time of writing. Daily statistics are only available via `cdsapi` file download (global gridded NetCDF), which is less efficient than the point-query Zarr.

**Future fix:** when the yearly statistics Zarr becomes reliably accessible, replace `utci_category_days_annual()` in `hit/era5/indices.py` with a direct read of pre-computed category day counts. This removes the hourly Zarr dependency for everything except period suggestion (which needs daily time-series to locate the hottest rolling window within a year).

---

## NEX-GDDP-CMIP6 v2 data source (Tab 3 — future projections)

**NASA Earth eXchange Global Daily Downscaled Projections v2** (May 2025):

- Variables: `tasmax`, `tas`, `tasmin`, `hurs`, `huss`, `pr`, `sfcWind`, `rsds`, `rlds` — all daily
- Spatial: 0.25° × 0.25°, global (90°N to 60°S)
- Temporal: daily 1950–2014 (historical) + 2015–2100 (future)
- Scenarios: SSP1-2.6, SSP2-4.5, SSP3-7.0, SSP3-7.0 · 35 CMIP6 GCMs
- Access: public AWS S3 (`s3://nex-gddp-cmip6/`), no authentication
- Kerchunk reference catalog: `s3://carbonplan-share/nasa-nex-reference/reference_catalog_nested.csv`
  - Open any model/scenario/variable as virtual Zarr: `xr.open_dataset(url, engine="kerchunk", storage_options={"remote_protocol": "s3", "anon": True})`
  - Generated by CarbonPlan; covers full 35.6 TB archive in ~290 MB of reference files
- Licence: CMIP6 Terms of Use (open for scientific/research use); code CC0

**UTCI computation from NEX-GDDP:** derived from `tasmax` + `huss` + `sfcWind` + `rsds` + `rlds`
using `pythermalcomfort`. Daily-mean inputs give an approximation of daily-maximum UTCI;
this is standard practice in climate impacts literature for projection work.

**Historical/future join:** ERA5 UTCI (Tab 1) and NEX-GDDP UTCI (Tab 3) must NEVER be
joined into a single time series — different source data, different computation. Tab 3 uses
NEX-GDDP for both its historical baseline (1950–2014) and future (2015–2100) so the
series is internally consistent.

**Pre-computation pipeline architecture:**
NEX-GDDP native chunks are full global grids. The efficient access pattern is one batch pass
extracting all 11,422 GHSL-UCDB city points simultaneously per year/variable/model/scenario,
rather than per-city on-demand. Annual UTCI stress category counts are cached to GCS.
Tab 3 at runtime reads only from the GCS cache — no raw NEX-GDDP access at request time.

---

## GHSL-UCDB (Phase 2 prerequisite — already downloaded)

Using **GHS-UCDB R2024A** (11,422 urban centres globally).

File: `data/ghsl/GHS_UCDB_GLOBE_R2024A.gpkg` (extracted from ZIP)
Download ZIP from: `https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_UCDB_GLOBE_R2024A/GHS_UCDB_GLOBE_R2024A/V1-1/GHS_UCDB_GLOBE_R2024A_V1_1.zip`

Layers used:
- `GHS_UCDB_THEME_GENERAL_CHARACTERISTICS_GLOBE_R2024A` → city name (`GC_UCN_MAI_2025`), country (`GC_CNT_GAD_2025`, full name not ISO), geometry (Mollweide → reprojected to WGS84)
- `GHS_UCDB_THEME_GHSL_GLOBE_R2024A` → historical population `GH_POP_TOT_YYYY` (2000, 2005, 2010, 2015, 2020, 2025)

Normalised column names in loader output: `name`, `country`, `population` (2025), `pop_2000`…`pop_2025`, `geometry` (WGS84)

Note: all column names and string values have a Unicode BOM (`﻿`) prefix in the raw file — stripped by `_strip_bom()` in `loader.py`.

---

## Key technical decisions

- **Model is TARGET** (via patched `target_py`), not UMEP. No QGIS anywhere.
- **GEE is not used.** Data sources: ECMWF ARCO UTCI Zarr (authenticated), GBA WFS, OSM, ESA WorldCover direct download.
- **ERA5-Land** (Destination Earth, `EDH_API_KEY`) is NB02b only — not the default.
- **Compute Engine VM** for GCP deployment (not Cloud Run — TARGET runtime too long).
- **Async job queue required** from day one — even on single VM.
- **Streamlit first**, React/FastAPI only if wider rollout requires it.
- xclim TX90p: always use `bootstrap=False`. Second param of `tx90p` renamed `t90p`→`tasmax_per` in xclim 0.53+; pass positionally to stay compatible.
- xarray for all multi-dimensional output — not pandas.
- `ds.sizes['time']` not `ds.dims['time']` (FutureWarning in newer xarray).

---

## Package structure

```
hit/
  config/     CityConfig dataclass
  target/     TARGET pipeline wrapper (run.py, results.py)
  cities/     GHSL-UCDB search and city lookup
  era5/       UTCI Zarr retrieval, baseline, UTCI90, category indices, period suggestion
  exposure/   Person-days, population-weighted heat exposure, WorldPop overlay
  spatial/    Grid ops, raster aggregation
vendor/
  target_py/  Patched target_py with setup.py
tests/
  test_target_patches.py
  test_target_parity.py
data/
  ghsl/       GHS-UCDB GeoPackage (download separately)
  era5/       Cached daily-max UTCI NetCDF per city (utci_daily_1991_2025.nc)
.streamlit/
  secrets.toml  CDS_API_KEY (gitignored)
Dockerfile
pyproject.toml
```

---

## Future features (not yet built)

### Phase 4 — COMPLETE (new-city grid generation + morphology pipeline)

- `hit/target/grid.py` — `generate_grid()`: 200m grid from UCDB polygon + 500m buffer + Natural Earth land clip (no ADM2 fetch)
- `hit/target/buildings.py` — `fetch_gba_buildings()` (GBA WFS, paginated), `compute_roof_fraction()` (roof fraction + area-weighted height H per cell)
- `hit/target/roads.py` — `fetch_overture_roads()` (DuckDB → Overture Maps S3), `compute_road_fraction()` (road fraction + canyon width W via perpendicular transects)
- `hit/target/worldcover.py` — `fetch_worldcover()` (ESA WorldCover 2021, windowed S3 read, reclassified to TARGET codes), `compute_landcover_fractions()` (zonal stats via rasterstats)
- `hit/target/landcover.py` — `combine_fractions()`: merges building/road/worldcover into `target_landcover.csv` (FID, roof, road, watr, conc, Veg, dry, irr, H, W); normalises to sum=1
- `hit/target/prepare.py` — `prepare_city_morphology()`: orchestrates all five steps with step-name updates for progress display
- `hit/jobs/worker_prepare.py` — async prepare worker (same pattern as TARGET run worker); writes step name to `step.json` for polling
- `hit/jobs/queue.py` — `submit_prepare()`: spawns prepare worker; `city["geometry"]` serialised to GeoJSON for JSON storage
- `app.py` Tab 2 — "Prepare city data" button shown when `grid.gpkg` absent; polls prepare job with per-step progress labels; falls through to existing results display once morphology is ready
- **New deps**: `rasterstats`, `duckdb` (added to `pyproject.toml`)

#### User-supplied city boundary (shapefile upload)
Allow users to upload a shapefile (or GeoJSON/GeoPackage) of an **official city boundary** via `st.file_uploader` in Tab 2. This would replace or supplement the UCDB polygon as the study area mask for grid generation. Useful when:
- The official municipal boundary differs significantly from the GHSL urban footprint
- The user has a custom study area (e.g. a specific district or corridor)
- The city is too new or small to appear in UCDB R2024A

Implementation notes:
- Accept `.zip` (shapefile bundle), `.geojson`, or `.gpkg`; read with `geopandas.read_file`
- Reproject to WGS84 and dissolve to a single polygon
- Store in `st.session_state["custom_boundary"]`; Tab 2 grid generation checks this before falling back to UCDB polygon
- Validate: geometry must be a Polygon/MultiPolygon; warn if area > threshold (very large areas will produce too many 200 m cells)

### Phase 5 — Tab 3: Future Climate Projections (NEX-GDDP-CMIP6)

- Data source: **NEX-GDDP-CMIP6 v2** (NASA/CarbonPlan), 35 CMIP6 models, 0.25°, daily
- Historical: 1950–2014 · Future: 2015–2100 under SSP2-4.5 and SSP3-7.0
- Kerchunk reference files at `s3://carbonplan-share/nasa-nex-reference/reference_catalog_nested.csv` — public, no auth
- UTCI derived from `tasmax`, `huss`, `sfcWind`, `rsds`, `rlds` via `pythermalcomfort`
- Pre-computation pipeline: one global batch pass extracts all GHSL-UCDB city points simultaneously; annual category day counts cached to GCS
- Tab 3 at runtime reads only from the GCS cache — no raw NEX-GDDP access at request time
- See NEX-GDDP data source section below for full spec

### Phase 6 — GCP deployment

- Compute Engine VM (not Cloud Run — TARGET ~29 min too long for Cloud Run timeout)
- Secret Manager for `CDS_API_KEY`
- Cloud Storage bucket for ERA5 daily-max cache and TARGET results
- Startup script to launch Streamlit + job worker on VM boot

---

## Heat risk index — variable selection guidance

Derived from spatial CV analysis of TARGET outputs for Salvador, Brazil (Oct 2023, 13,981 cells, 1-week run).

### Use nocturnal Tmin, not daytime Tmax, as the primary heat variable

Nighttime minimum air temperature (`Ta` daily min, 20:00–06:00 window) is the epidemiologically critical metric for heat risk. Consecutive nights above ~25°C prevent physiological recovery, and the deficit compounds across multi-day events. The Salvador run showed mean Tmin rising from 21°C (Oct 20) to 25°C (Oct 26), with 42–61% of cells exceeding the 25°C recovery threshold on the final two nights.

Nocturnal UTCI minimum has **CV ~9–14%** vs. daytime UTCI CV of **3–6%** — nights are more spatially differentiated than days, making Tmin a stronger discriminator for mapping intra-urban risk.

### Spatial variability summary (Salvador reference run)

| Variable | Aggregation | CV range | Notes |
|---|---|---|---|
| UTCI | daily max | 3.5–6.5% | Low spatial signal; ~13°C peak-to-peak range |
| UTCI | nocturnal min | 9–14% | ~2× daytime; much better discriminator |
| Ta | daily max | 4.7–6.3% | Mirrors UTCI |
| Ta | nocturnal min | 3–10% | Higher on synoptically active nights |
| Tmrt | daily max | 1.8–7.8% | Captures urban geometry; use alongside UTCI |
| Ws | daily max | ~57% | Dominant source of spatial contrast |

### Recommended variable set for a heat risk index

- **Heat layer:** nocturnal Tmin (or nocturnal UTCI min) — highest spatial CV, epidemiologically justified
- **Geometry modifier:** Tmrt — adds daytime sky-view/shading signal not captured in Ta alone
- **Cooling potential:** wind speed — CV ~57%, far more spatially variable than thermal variables; frame as a risk modifier (low wind = reduced cooling = higher risk) rather than a direct heat metric
- **City-level severity:** fraction of cells with Tmin ≥ 25°C per night, accumulated over consecutive nights

### Aggregation note

Aggregating cell values to neighbourhood means/medians will reduce CV (smoothing within-neighbourhood variation). This is appropriate when matching to vulnerability/exposure data at census-tract level. The spatial heat signal is genuine but moderate — in a composite risk index, **exposure and vulnerability layers will drive most spatial variation**; the heat layer shifts overall severity rather than dominating the spatial pattern.

---

## Behaviour preferences

- No comments explaining what code does — only non-obvious WHY
- No trailing summaries after completing tasks
- No physics changes without explicit approval
- xarray for multi-dimensional model output
- Test city for all development: **Salvador, Brazil** (`BRA_salvador`, `heatwave_oct2023`)
