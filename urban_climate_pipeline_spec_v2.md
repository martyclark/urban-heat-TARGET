# Urban Heat Screening Tool — Project Spec v2

## Project overview

A two-tab web application for urban heat exposure screening. The tool moves from city-level climatology (ERA5, ~25 km) to fine-resolution neighbourhood analysis (TARGET model, 200 m). The developer has a working pure-Python TARGET pipeline producing 200 m outputs for Adelaide and Salvador; the goal is to package this into a deployable web tool on GCP.

No QGIS. No Google Earth Engine. Pure Python throughout.

---

## Tab 1: City-level heat climatology

### What it does

1. User searches or browses the GHSL Urban Centres Database (GHS-UCDB) to select a city. GHSL-UCDB provides city boundaries, population estimates, and classification metadata for ~10,000 urban centres globally.
2. Pulls ERA5 T2m for the selected city bounding box via ARCO-ERA5 Zarr (anonymous, no credentials).
3. Computes a **30-year climatological baseline** (1991–2020, WMO standard period) and derives the **TX90p threshold** — the 90th percentile of daily maximum temperature for that city. TX90p is locally calibrated, so it is meaningful for tropical cities (chronic heat), arid cities (dry extreme heat), and temperate cities (episodic heat) alike.
4. Counts **extreme heat days** in a recent window (~2020–2025): days where Tmax exceeds the TX90p threshold.
5. Calculates **person-days of extreme heat** = city population (from GHSL-UCDB) × count of extreme heat days. This is a single summary metric of population heat burden, not a time series.
6. Presents the city's heat climatology — annual temperature cycle, distribution of extreme heat days, type of heat the city faces (chronic/persistent, episodic, seasonal) — to give the user insight before selecting a modelling period.
7. Suggests **1–2 candidate periods** for detailed TARGET modelling: the period(s) capturing the hottest part of a representative recent year. Framed as "candidate periods for detailed analysis," not as "heatwaves" (terminology that doesn't apply consistently across climate types).
8. User selects a period (default **1 week**; optional 2 or 4 weeks if compute budget allows) and confirms city details before proceeding to Tab 2.

### Key design notes

- **TX90p via xclim**, `bootstrap=False`. xclim handles the index computation; the 30-year baseline is the input.
- **Terminology**: avoid "heatwave." Cities vary — some face chronic tropical heat, some episodic events, some pronounced dry seasons. The tool characterises the type of heat rather than applying a single label.
- **Person-days** is a single number contextualising the population burden. It is not a trend chart.
- **Period length** drives TARGET compute cost. 1 week (168 hourly timesteps) is the default. 2 or 4 weeks should be user-selectable where runtime allows. This choice is surfaced in the UI before the Tab 2 run is triggered.
- ERA5 at ~25 km is appropriate for city-level climatology. The 30-year baseline fetch is a modest Zarr read — ARCO-ERA5 chunks this efficiently for point/bounding-box queries.

### Key data

- **GHSL-UCDB** — city boundaries, population, classification (open data, JRC)
- **ARCO-ERA5 Zarr** — T2m, 30-year baseline + recent window (anonymous access)
- **xclim** — TX90p index computation

---

## Tab 2: Neighbourhood-level TARGET analysis (200 m)

### What it does

1. Takes the city and candidate period selected in Tab 1.
2. Runs the existing TARGET pipeline at hourly timesteps and 200 m grid resolution.
3. Produces:
   - Intra-urban air temperature fields (Tac)
   - Surface temperature (Tsurf)
   - Mean radiant temperature (Tmrt) and thermal comfort (UTCI)
   - Urban heat island intensity (UHI)
   - Diurnal cycle profiles per neighbourhood
4. Overlays WorldPop 100 m population for neighbourhood-level exposure scoring.
5. Interactive map visualisation of 200 m outputs.

### Key data

- **TARGET model** (`target_py`, patched — see Package section below)
- **ERA5 forcing** via ARCO-ERA5 Zarr — same source as Tab 1
- **Urban morphology inputs** (per city, pre-staged):
  - Building height and roof fraction — GBA (Global Building Archive) via WFS
  - Road fraction and canyon width — OSM
  - Land cover — ESA WorldCover 2021 (direct download)
- **WorldPop 100 m** — neighbourhood population exposure

---

## Existing codebase

The current implementation is a suite of Jupyter notebooks (NB01–NB08) plus `city_config.py`:

| Notebook | Role |
|---|---|
| NB01 | Study area grid, coastal clipping via geoBoundaries ADM2 |
| NB01b | ERA5 anomaly ranking, candidate period selection |
| NB02 | ERA5 met forcing (ARCO-ERA5 Zarr, primary) |
| NB03 | Building data from GBA WFS |
| NB03b | Road data from OSM |
| NB04 | ESA WorldCover 2021 land cover |
| NB05 | Combine fractions into TARGET land cover input |
| NB06 | Run TARGET model |
| NB08 | Visualise results |

Pilot cities with fully processed outputs: **Adelaide** (AUS), **Salvador** (BRA). **Caceres** (BRA) in progress.

The `city_config.py` pattern (single config file, all notebooks import from it) is a real asset and should be generalised to a `CityConfig` dataclass in the `hit/` package.

### What is NOT in the existing codebase

The following are **net-new development**, not extractions from existing notebooks:

- GHSL-UCDB city search and population lookup
- 30-year ERA5 climatological baseline
- TX90p threshold calculation
- Person-days of extreme heat metric
- Candidate period suggestion logic
- Any web UI

This distinction matters for effort estimation. The Phase 1 parity exit criterion applies only to the TARGET pipeline, not to Tab 1 features.

---

## Package structure: `hit/`

```
hit/
  config/       # CityConfig dataclass; generalised city_config.py pattern
  cities/       # GHSL-UCDB search, city boundaries, population lookup
  era5/         # ARCO-ERA5 Zarr retrieval, 30-year baseline, TX90p, period suggestion
  target/       # TARGET pipeline wrapper (NOT "umep/") — wraps patched target_py
  exposure/     # Person-days calculation, WorldPop neighbourhood overlay
  spatial/      # Grid ops, raster aggregation, ADM2 coastal clipping
```

### Design discipline

Keep `hit/` functions **stateless at the interface** (explicit paths in, explicit artefacts out). The TARGET pipeline is inherently file-stateful (`target_runs/{site}/input|output/`), so "stateless" means no hidden global state — not pure functions. This keeps the package callable from Streamlit directly and from FastAPI routes later without backend changes.

---

## The TARGET model and `target_py`

TARGET (not UMEP) is the urban microclimate model. It is implemented in the `target_py` Python package, which has been **manually patched** to make it production-reliable. These patches must be vendored into the repo before any Docker build — a clean `pip install target_py` will produce a model that hangs or throws errors.

### Patches applied

| File | Change | Why |
|---|---|---|
| `scripts/TbRurSolver.py` | `iterations` 900000 → 100 | Binary bisection needs ~53 steps; 900k caused multi-minute hangs per non-converging timestep |
| `scripts/UTCI.py` | `max(..., 1e-10)` clamp before `pow(..., 0.25)` | Negative argument causes `ValueError` on coastal/ocean cells |
| `scripts/toolkit.py` | Slice `mod_rslts[1:len(met_data_all)]` | `self.nt > len(met_data_all)` left uninitialised trailing rows with date=0 (int, not datetime) |
| `ui/utils.py` | Skip uninitialised rows; `%Y-%m-%d %H_%M_%S` format | `AttributeError` on int date; `%F` not portable |

**Action required in Phase 1:** vendor the patched `target_py` into the repo (or maintain a pinned fork). Add a regression test that would catch the hang and domain-error cases.

---

## Frontend

**Primary: Streamlit.** Fastest path from notebooks to a functional web tool. Single Python container, no JavaScript build. Covers core requirements:
- `st.tabs` for two-tab layout
- City search via text input with autocomplete against GHSL-UCDB
- Time series and distribution charts via Plotly
- Interactive 200 m raster overlay via pydeck or folium
- Period length selector (1 / 2 / 4 weeks)

Adequate for practitioner use and stakeholder demos. Layout constraints and raster rendering are acceptable tradeoffs at this stage.

**Future: React + FastAPI.** If the tool moves to wider rollout (e.g. broader C40 deployment), a React frontend with Deck.gl or Mapbox GL JS gives full map control. The FastAPI backend wraps the same `hit/` package — no backend changes required for the frontend swap. Treat this as Phase 5 and only pursue if Streamlit proves insufficient.

---

## GCP deployment

### Architecture: Compute Engine VM + Docker

```
User (browser)
  |
  v
Compute Engine VM
  |
  +-- Docker container
  |     +-- Streamlit app
  |     +-- hit/ Python package
  |     +-- patched target_py (vendored)
  |     +-- Job queue worker (async TARGET runs)
  |
  +-- GCS
  |     +-- GHSL-UCDB dataset
  |     +-- WorldPop 100m tiles
  |     +-- Cached ERA5 climatologies (keyed by city+baseline)
  |     +-- Cached TARGET outputs (keyed by city+period)
  +-- Secret Manager (EDH_API_KEY if ERA5-Land option used)
```

**Cloud Run is not the right default for this workload.** TARGET runs are CPU/memory intensive, file-stateful, and can run up to 60 minutes. Cloud Run's max request timeout, cold-start penalty on a heavy scientific image, and stateless model all fight this design. A Compute Engine VM has no timeout constraints, no cold starts, and for a single-instance practitioner tool the cost difference is negligible.

Revisit Cloud Run only if genuine multi-user concurrency emerges and vertical scaling is insufficient.

### Async job queue (required from day one)

Even on a single VM, a long TARGET run must not block the Streamlit web thread. A lightweight job queue (e.g. a subprocess worker, RQ with Redis, or Celery) on the same VM allows:
- Immediate UI response when a run is triggered
- Status polling / progress indication in the Streamlit UI
- Queue management if multiple users submit runs

This is an architectural requirement, not a scaling nicety. Design it in Phase 1.

### Caching strategy

- **ERA5 climatologies** (30-year baseline, TX90p per city): computed once, cached in GCS keyed by `{city_id}_{baseline_period}`. Expensive to regenerate; effectively static.
- **TARGET outputs**: cached in GCS keyed by `{city_id}_{period_start}_{period_end}_{grid_hash}`. Pilot city outputs (Adelaide, Salvador) pre-computed and staged before deployment.
- Serve from cache by default. On-demand computation only for cache misses (new cities or new periods).

### Data staging (GCS)

Pre-stage before deployment:
- GHSL-UCDB (single GeoPackage/GeoJSON, ~50 MB)
- WorldPop 100 m tiles for pilot cities
- ERA5 climatologies for pilot cities
- TARGET outputs for pilot cities (Adelaide, Salvador; Caceres once complete)

Per-city morphology inputs (GBA buildings, OSM roads, WorldCover) are fetched on first run and cached locally on the VM.

### Key considerations

- **GEE is not used.** Do not build a GEE service-account auth workstream. The pipeline uses ARCO-ERA5 Zarr (anonymous), GBA WFS, OSM, and ESA WorldCover direct downloads.
- **`EDH_API_KEY`** (Destination Earth) is needed only if the ERA5-Land option (NB02b) is exposed in the UI. Store in Secret Manager if included.
- **CRS / UTC offset per city**: `LOCAL_CRS`, `UTC_OFFSET`, `BBOX` must be derived for any new city. Auto-derivation (nearest UTM zone, timezone polygon lookup) is a real sub-project — flag it explicitly in the implementation plan.
- **Docker image size**: no QGIS dependency. The image is pure Python + GDAL + scientific stack. This is substantially easier to build than the original spec implied.
- **Portability**: a working Docker container runs on any hosting. Relevant if the project outlives current funding (university infrastructure, partner organisations).

### Cost control

- ARCO-ERA5, GHSL-UCDB, WorldCover, OSM: open/free
- WorldPop: open data
- Main costs: Compute Engine VM (especially during TARGET runs), GCS storage
- Profile TARGET runtime and memory per city in Phase 1; set GCP budget alerts before deployment

---

## Build sequence

Backend-first. Each phase produces something testable.

### Phase 1: Foundation

**Goals:** Working `hit/` package; `target_py` reproducibility locked down; TARGET runtime profiled; architecture decisions confirmed.

1. Vendor patched `target_py` into the repo. Add regression tests covering the hang (TbRurSolver), domain-error (UTCI), and trailing-row (toolkit/utils) cases.
2. Profile TARGET runtime and memory end-to-end for Adelaide and Salvador. **This determines whether the async queue design is sufficient or whether more aggressive parallelisation is needed. Decide before Phase 2.**
3. Build Dockerfile. Pure Python + GDAL + scientific stack (no QGIS). Pin all versions — xclim has breaking changes between minor releases. All subsequent development happens inside this container.
4. Extract TARGET pipeline into `hit/target/`. Generalise `city_config.py` to `CityConfig` dataclass in `hit/config/`. Confirm numerical parity with existing notebook outputs for Adelaide and Salvador.
5. Implement `hit/cities/` — load GHSL-UCDB from GCS/local, search by name, return boundary and population.
6. Implement `hit/era5/` — ARCO-ERA5 Zarr retrieval, 30-year baseline, TX90p via xclim, extreme heat day count, person-days, candidate period suggestion.
7. Implement `hit/exposure/` — WorldPop overlay, neighbourhood exposure scoring.
8. Implement async job queue on the VM. Define the job interface (city + period in → TARGET outputs out).

**Exit criteria:**
- `hit/` package importable and runnable end-to-end from a script for Adelaide and Salvador
- TARGET outputs numerically identical to existing notebooks
- Dockerfile builds and runs cleanly
- TARGET runtime and memory profiled; async queue design confirmed

### Phase 2: Streamlit — Tab 1

**Goal:** Functional city screening UI running locally.

9. Build Streamlit Tab 1: GHSL-UCDB city search, heat climatology display (annual cycle, extreme day distribution, heat type characterisation), person-days summary, candidate period cards, period length selector (1 / 2 / 4 weeks).
10. Wire to `hit/cities/` and `hit/era5/` directly — no API layer needed yet.
11. Test with pilot cities locally.

**Exit criteria:** User can search for a city, understand its heat type, see the person-days burden, and select a candidate period.

### Phase 3: Streamlit — Tab 2 + job queue

**Goal:** Full two-tab workflow running locally.

12. Build Streamlit Tab 2: trigger TARGET run via job queue, progress indicator, 200 m raster map overlay (pydeck/folium), diurnal cycle charts, neighbourhood exposure.
13. Wire to `hit/target/` and `hit/exposure/` via the async job queue.
14. Test full flow: city search → period selection → TARGET run → neighbourhood results.

**Exit criteria:** End-to-end workflow running locally for pilot cities. Async queue handles a TARGET run without blocking the UI.

### Phase 4: Deployment

**Goal:** Tool accessible via URL, cached, cost-controlled.

15. Set up GCP infrastructure: Compute Engine VM, GCS buckets, Secret Manager, IAM.
16. Pre-stage GCS: GHSL-UCDB, WorldPop tiles, ERA5 climatologies and TARGET outputs for pilot cities.
17. Deploy Docker container to VM. Wire GCS cache reads/writes.
18. Test with pilot cities on deployed instance — results should serve from cache.
19. Test new-city flow: trigger on-demand ERA5 + TARGET run, confirm async queue handles it, result cached after first run.
20. Performance and cost review: per-city latency, GCP spend, caching effectiveness.

**Exit criteria:** Tool deployed. Pilot city results load from cache. New cities trigger on-demand computation via async queue.

### Phase 5 (future): React frontend

Only if Streamlit proves insufficient for the intended audience.

21. Stand up FastAPI wrapping `hit/` package (already built and tested — no backend changes needed).
22. Build React frontend with Deck.gl or Mapbox GL JS.
23. Split into two services (frontend + API).

---

## Open decisions (resolve before or during Phase 1)

| Decision | Options | When to decide |
|---|---|---|
| Auto-derive `LOCAL_CRS` / `UTC_OFFSET` per city | Timezone polygon lookup + nearest UTM zone; or require user input for new cities | Phase 1 — affects `hit/config/` design |
| ERA5-Land option in UI | Expose NB02b as an alternative forcing source, or ERA5 only | Phase 1 |
| Period length options | 1 week only, or 1/2/4 weeks user-selectable | Phase 2 UI design |
| Extreme heat threshold | TX90p only, or also offer absolute threshold (e.g. 35°C) for comparison | Phase 2 UI design |

---

## Notes for implementation

- **Model is TARGET, not UMEP.** TARGET is a physics-based urban microclimate model implemented in `target_py`. UMEP is a larger QGIS-dependent suite of which TARGET is one component; this pipeline does not use QGIS or any other UMEP component.
- **ERA5 source is ARCO-ERA5 Zarr** (anonymous, no credentials). ERA5-Land via Destination Earth (`EDH_API_KEY`) is an alternative option in NB02b only.
- **Patched `target_py` must be vendored.** A clean PyPI install will not work. See Package section above.
- **xclim TX90p/TN90p: `bootstrap=False`.** Preserve this parameter.
- **xarray for all multi-dimensional model output.** TARGET outputs a numpy structured array of shape `(timesteps, cells, 1)` — load via xarray, not pandas.
- **`ds.sizes['time']` not `ds.dims['time']`** — FutureWarning in newer xarray.
- **Coastal clipping via geoBoundaries ADM2** — clip grid to cells intersecting ADM2 polygons to remove ocean cells that cause UTCI convergence failures.
- **Clear `.pyc` cache** after any edit to installed package `.py` files: `find ~/miniforge3/envs/target-umep/lib/python3.11/site-packages/target_py -name "*.pyc" -delete`
