"""HIT — Urban Heat Index Tool"""
from __future__ import annotations

import dataclasses
import json
import io
import math
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
import streamlit as st
import xarray as xr

from hit.cities.loader import UCDB_DIR, load_ucdb
from hit.cities.search import search_cities
from hit.config.city import city_config_from_ucdb
from hit.era5.baseline import _open_utci_store, fetch_utci_daily
from hit.era5.indices import (
    HEAT_STRESS_CATEGORIES,
    annual_utci_cycle,
    heat_type,
    tx90p_threshold,
    utci_category_days_annual,
)
from hit.era5.periods import suggest_periods
from hit.exposure import population_weighted_heat_exposure
from hit.jobs.queue import JOBS_DIR, get_status, list_jobs, submit_prepare, submit_run
from hit.spatial import diurnal_stats, results_to_geodataframe, uhi_series, utci_diurnal_stats

FULL_START     = 1991
FULL_END       = 2025
BASELINE_START = 1991
BASELINE_END   = 2020

ERA5_CACHE_DIR  = Path("data/era5")
TARGET_DATA_DIR = Path("data/target")

HEAT_CATEGORY_COLORS = {
    "moderate_heat":    "#f4a460",
    "strong_heat":      "#e87722",
    "very_strong_heat": "#d62728",
    "extreme_heat":     "#7b0000",
}

HEAT_CATEGORY_LABELS = {
    "moderate_heat":    "Moderate (26–32°C)",
    "strong_heat":      "Strong (32–38°C)",
    "very_strong_heat": "Very strong (38–46°C)",
    "extreme_heat":     "Extreme (>46°C)",
}

HEAT_TYPE_LABELS = {
    "chronic":  "Chronic — persistently high temperatures year-round",
    "seasonal": "Seasonal — pronounced hot season with cooler periods",
    "episodic": "Episodic — moderate baseline with intermittent extreme events",
}

MAP_OPTIONS = {
    "Peak UTCI (°C)":         "peak_utci",
    "Peak Radiant Temp Tmrt (°C)": "peak_tmrt",
    "Mean UHI (°C)":          "mean_uhi",
}

st.set_page_config(
    page_title="HIT — Urban Heat Index Tool",
    page_icon="🌡",
    layout="wide",
)


# ── Cached loaders ───────────────────────────────────────────────────────────

@st.cache_resource(show_spinner="Loading city database...")
def _load_ucdb():
    return load_ucdb()


@st.cache_resource(show_spinner="Connecting to UTCI data store...")
def _get_utci_store():
    return _open_utci_store()


@st.cache_data(show_spinner=False)
def _fetch_utci_daily_cached(lat: float, lon: float, year_start: int, year_end: int, slug: str):
    cache_path = ERA5_CACHE_DIR / slug / f"utci_daily_{year_start}_{year_end}.nc"
    return fetch_utci_daily(lat, lon, year_start, year_end, cache_path=cache_path, store=_get_utci_store())


@st.cache_data(show_spinner="Loading TARGET results…")
def _load_results_cached(nc_path: str) -> xr.Dataset:
    return xr.open_dataset(nc_path).load()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _city_slug(city: dict) -> str:
    country = city["country"].lower().replace(" ", "_")[:10]
    name    = city["name"].lower().replace(" ", "_")[:20]
    return f"{country}_{name}"


def _city_id(city: dict) -> str:
    country_iso = city["country"][:3].upper()
    city_name   = city["name"].lower().replace(" ", "_")
    return f"{country_iso}_{city_name}"


def _fmt_pop(n: int | None) -> str:
    if n is None:
        return "N/A"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def _city_boundary_html(geojson_str: str, lon_min: float, lat_min: float, lon_max: float, lat_max: float) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<link href="https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.css" rel="stylesheet">
<script src="https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.js"></script>
<style>html,body,#map{{height:100%;margin:0;padding:0;}}</style>
</head><body>
<div id="map"></div>
<script>
const map = new maplibregl.Map({{
  container: 'map',
  style: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
  bounds: [{lon_min},{lat_min},{lon_max},{lat_max}],
  fitBoundsOptions: {{padding: 40}},
}});
map.on('load', () => {{
  map.addSource('city', {{type:'geojson', data:{geojson_str}}});
  map.addLayer({{id:'city-fill', type:'fill', source:'city',
    paint:{{'fill-color':'#1f77b4','fill-opacity':0.2}}}});
  map.addLayer({{id:'city-line', type:'line', source:'city',
    paint:{{'line-color':'#1f77b4','line-width':2}}}});
}});
</script>
</body></html>"""


def _results_map_html(lat: float, lon: float, meta: dict, label: str, opacity: float) -> str:
    import time as _t
    bounds = meta["bounds"]  # [W, S, E, N]
    vmin, vmax = meta["vmin"], meta["vmax"]
    cache_bust = int(_t.time())
    tile_url = f"http://localhost:8502/tiles/{{z}}/{{x}}/{{y}}.png?opacity={opacity}&v={cache_bust}"
    stops = [
        {"offset": "0%",   "color": "#ffffb2"},
        {"offset": "25%",  "color": "#fecc5c"},
        {"offset": "50%",  "color": "#fd8d3c"},
        {"offset": "75%",  "color": "#f03b20"},
        {"offset": "100%", "color": "#bd0026"},
    ]
    legend_css = "".join(f"{s['color']} {s['offset']}," for s in stops).rstrip(",")
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<link href="https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.css" rel="stylesheet">
<script src="https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.js"></script>
<style>
html,body,#map{{height:100%;margin:0;padding:0;}}
#legend{{position:absolute;bottom:24px;left:10px;background:rgba(255,255,255,0.9);
  padding:8px 10px;border-radius:4px;font:12px/1.4 sans-serif;min-width:140px;}}
#legend .bar{{height:10px;background:linear-gradient(to right,{legend_css});border-radius:2px;margin:4px 0;}}
#legend .ends{{display:flex;justify-content:space-between;}}
</style>
</head><body>
<div id="map"></div>
<div id="legend">
  <b>{label}</b>
  <div class="bar"></div>
  <div class="ends"><span>{vmin:.1f}</span><span>{vmax:.1f}</span></div>
</div>
<script>
const map = new maplibregl.Map({{
  container: 'map',
  style: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
  bounds: [{bounds[0]},{bounds[1]},{bounds[2]},{bounds[3]}],
  fitBoundsOptions: {{padding: 20}},
}});
map.on('load', () => {{
  map.addSource('heat', {{type:'raster', tiles:['{tile_url}'], tileSize:256, attribution:'TARGET model'}});
  map.addLayer({{id:'heat-layer', type:'raster', source:'heat',
    paint:{{'raster-opacity':{opacity}}}}});
  map.addControl(new maplibregl.NavigationControl(), 'top-right');
}});
</script>
</body></html>"""


def _zip_files(named_paths: dict[str, Path]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, path in named_paths.items():
            if path.exists():
                zf.write(path, arcname)
    return buf.getvalue()


def _estimate_grid_cells(city: dict) -> int:
    """Rough cell count estimate from UCDB polygon bounds at 200 m resolution.

    Uses a 0.75 fill factor (UCDB polygons are not rectangular) and the
    500 m buffer added by generate_grid. Good enough for threshold warnings.
    """
    geom = city.get("geometry")
    lat  = city["centroid_lat"]
    cos_lat = abs(math.cos(math.radians(lat)))
    if geom is not None:
        lon_min, lat_min, lon_max, lat_max = geom.bounds
        w_m = (lon_max - lon_min) * 111_000 * cos_lat
        h_m = (lat_max - lat_min) * 111_000
        area_m2 = w_m * h_m * 0.75
    else:
        area_m2 = (0.3 * 111_000) ** 2
    return max(1, int(area_m2 / (200 * 200)))


def _grid_size_warning(n_cells: int) -> tuple[str, str] | None:
    """Return (prep_estimate, run_estimate) warning strings, or None if city is small."""
    if n_cells > 40_000:
        return (
            f"~{n_cells:,} estimated cells — **very large city**. "
            "Morphology preparation will take **20–40 min** (spatial overlay across a large grid). ",
            f"~{n_cells:,} cells — TARGET run will take **90–150 min** (runtime scales with cell count).",
        )
    if n_cells > 15_000:
        return (
            f"~{n_cells:,} estimated cells — **large city**. "
            "Morphology preparation will take **10–20 min**.",
            f"~{n_cells:,} cells — TARGET run will take **45–90 min** (runtime scales with cell count).",
        )
    return None


_PREP_STEPS = [
    ("grid",       "Generate 200 m grid"),
    ("buildings",  "Fetch building data (GBA WFS)"),
    ("roads",      "Fetch road data (Overture Maps)"),
    ("worldcover", "Fetch land cover (ESA WorldCover)"),
    ("combine",    "Combine land cover fractions"),
]

_RUN_STEPS = [
    ("forcing", "Fetch ERA5 meteorological forcing"),
    ("prepare", "Prepare TARGET model inputs"),
    ("run",     "Run TARGET simulation"),
    ("save",    "Save results as NetCDF"),
]


def _render_step_checklist(
    steps: list[tuple[str, str]],
    current_step: str | None,
    overall_status: str,
    elapsed: str,
    step_statuses: dict[str, str] | None = None,
) -> None:
    """Render a step checklist.

    If step_statuses is provided (prep jobs with parallel steps), each step is
    rendered from its individual status. Otherwise current_step drives sequential logic.
    """
    lines = []
    if step_statuses:
        for key, label in steps:
            step_st = step_statuses.get(key, "pending")
            if step_st == "complete":
                lines.append(f"✅ {label}")
            elif step_st == "running":
                detail = elapsed
                if key == "buildings":
                    fetched = step_statuses.get("buildings_fetched", 0)
                    total   = step_statuses.get("buildings_total", 0)
                    if total and fetched:
                        pct = fetched / total
                        detail = f"{fetched:,} / {total:,} features ({pct:.0%})"
                        started = step_statuses.get("buildings_started_at")
                        if pct > 0.02 and started:
                            from datetime import datetime, timezone as _tz
                            elapsed_s = (datetime.now(_tz.utc) - datetime.fromisoformat(started)).total_seconds()
                            remaining = int(elapsed_s / pct - elapsed_s)
                            detail += f" — ~{remaining // 60}m remaining"
                lines.append(f"🔄 **{label}** — {detail}")
            elif step_st == "failed":
                lines.append(f"❌ **{label}** — failed")
            else:
                lines.append(f"⬜ {label}")
    else:
        step_keys = [k for k, _ in steps]
        if overall_status == "complete":
            current_idx = len(steps)
        elif current_step in step_keys:
            current_idx = step_keys.index(current_step)
        else:
            current_idx = 0
        for i, (_, label) in enumerate(steps):
            if i < current_idx:
                lines.append(f"✅ {label}")
            elif i == current_idx:
                lines.append(f"🔄 **{label}** — {elapsed} elapsed")
            else:
                lines.append(f"⬜ {label}")

    st.markdown("  \n".join(lines))


def _elapsed(iso_ts: str | None) -> str:
    if not iso_ts:
        return "starting…"
    delta = datetime.now(timezone.utc) - datetime.fromisoformat(iso_ts)
    m, s = divmod(int(delta.total_seconds()), 60)
    return f"{m}m {s}s"


@st.fragment(run_every=5)
def _prep_poll_fragment(prep_job_id: str, city_name: str) -> None:
    info = get_status(prep_job_id)
    status = info.get("status")
    st.info(f"Preparing morphology data for **{city_name}**")
    _render_step_checklist(
        _PREP_STEPS,
        current_step=None,
        overall_status=status,
        elapsed=_elapsed(info.get("started_at") or info.get("created_at")),
        step_statuses=info.get("step_statuses"),
    )
    if status not in ("pending", "running"):
        st.rerun(scope="app")


@st.fragment(run_every=5)
def _run_poll_fragment(job_id: str, date_start: str, date_end: str) -> None:
    info = get_status(job_id)
    status = info.get("status")
    st.info(f"Running TARGET analysis for **{date_start} → {date_end}**")
    _render_step_checklist(
        _RUN_STEPS,
        current_step=info.get("step"),
        overall_status=status,
        elapsed=_elapsed(info.get("started_at") or info.get("created_at")),
    )
    if status not in ("pending", "running"):
        st.rerun(scope="app")


# ── Layout ───────────────────────────────────────────────────────────────────

st.title("HIT — Urban Heat Index Tool")
tab1, tab2, tab3 = st.tabs(["City Screening", "Neighbourhood Analysis", "Future Climate"])

# ── Tab 1 ────────────────────────────────────────────────────────────────────

with tab1:
    st.header("City Screening")

    try:
        with st.spinner("Loading city database (downloading on first run, ~400 MB)…"):
            db = _load_ucdb()
    except Exception as exc:
        st.error(f"Failed to load city database: {exc}")
        st.stop()

    countries = sorted(db["country"].dropna().unique().tolist())
    country_sentinel = "— select a country —"
    country_choice = st.selectbox("Country", [country_sentinel] + countries, key="country_sel")

    city: dict | None = None

    if country_choice != country_sentinel:
        city_results = search_cities("", country=country_choice, n=1000, db=db)
        city_labels  = [f"{r['name']}  ({_fmt_pop(r['population'])})" for r in city_results]
        city_sentinel = "— select a city —"
        city_choice  = st.selectbox(
            "City",
            [city_sentinel] + city_labels,
            key=f"city_sel_{country_choice}",
        )
        if city_choice != city_sentinel:
            city = city_results[city_labels.index(city_choice)]

    if city:
        slug = _city_slug(city)
        lat  = city["centroid_lat"]
        lon  = city["centroid_lon"]

        # ── City summary ─────────────────────────────────────────────────────
        st.subheader(f"{city['name']}, {city['country']}")
        c1, c2, c3 = st.columns(3)
        c1.metric("Population 2025 (GHSL)", _fmt_pop(city["population"]))
        c2.metric("Latitude",  f"{lat:.4f}°")
        c3.metric("Longitude", f"{lon:.4f}°")

        # ── City map ─────────────────────────────────────────────────────────
        geom = city.get("geometry")
        if geom is not None:
            try:
                lon_min, lat_min, lon_max, lat_max = geom.bounds
                geojson_str = json.dumps(geom.__geo_interface__)
                st.components.v1.html(_city_boundary_html(geojson_str, lon_min, lat_min, lon_max, lat_max), height=340)
            except Exception:
                pass

        # Population trend (2000–2025)
        pop_years = [yr for yr in [2000, 2005, 2010, 2015, 2020, 2025] if city.get(f"pop_{yr}")]
        if len(pop_years) > 1:
            fig_pop = go.Figure(go.Scatter(
                x=pop_years,
                y=[city[f"pop_{yr}"] for yr in pop_years],
                mode="lines+markers",
                line=dict(color="#1f77b4"),
            ))
            fig_pop.update_layout(
                title="Population trend (GHSL)",
                yaxis_title="Population",
                height=220,
                margin=dict(t=36, b=16, l=40, r=16),
                xaxis=dict(tickvals=pop_years),
            )
            st.plotly_chart(fig_pop, use_container_width=True)

        # ── Existing results indicator ────────────────────────────────────────
        _t1_morph_dir = TARGET_DATA_DIR / _city_id(city)
        _available_runs: dict[str, Path] = {
            p.parent.name: p
            for p in sorted(_t1_morph_dir.glob("*/target_results.nc"))
        }
        for _j in list_jobs():
            if _j.get("status") != "complete" or not _j.get("output_path"):
                continue
            _nc = Path(_j["output_path"])
            if not _nc.exists():
                continue
            try:
                _cfg = json.loads((JOBS_DIR / _j["job_id"] / "config.json").read_text())
            except Exception:
                continue
            if Path(_cfg.get("data_dir", "")) != _t1_morph_dir:
                continue
            _slug = f"{_cfg['date_start']}_{_cfg['date_end']}"
            if _slug not in _available_runs:
                _available_runs[_slug] = _nc

        if _available_runs:
            with st.container(border=True):
                n = len(_available_runs)
                st.markdown(f"**Neighbourhood analysis results available** — {n} period{'s' if n > 1 else ''}")
                _run_cols = st.columns(min(n, 3))
                for _i, (_slug, _nc_path) in enumerate(_available_runs.items()):
                    _ds, _de = _slug[:10], _slug[11:] if len(_slug) > 10 else ""
                    with _run_cols[_i % len(_run_cols)]:
                        st.caption(f"{_ds} → {_de}")
                        if st.button("View in Tab 2 →", key=f"t1_view_{_i}"):
                            _period = {"date_start": _ds, "date_end": _de,
                                       "rank": "completed", "mean_utci": None, "anomaly": None}
                            st.session_state["analysis"] = {"city": city, "period": _period}
                            st.session_state.pop("job_id", None)
                            st.rerun()

        # ── Period length selector ────────────────────────────────────────────
        period_weeks = st.radio(
            "Analysis period length",
            [1, 2, 4],
            index=0,
            format_func=lambda w: f"{w} week{'s' if w > 1 else ''}",
            horizontal=True,
        )
        window_days = period_weeks * 7

        # ── Single UTCI fetch covering full range ─────────────────────────────
        try:
            with st.spinner(f"Loading UTCI {FULL_START}–{FULL_END}…"):
                utci_full = _fetch_utci_daily_cached(lat, lon, FULL_START, FULL_END, slug)
        except EnvironmentError:
            st.warning(
                "**CDS API key required for UTCI climate data.**\n\n"
                "Register at [cds.climate.copernicus.eu](https://cds.climate.copernicus.eu) "
                "and accept the [dataset licence](https://cds.climate.copernicus.eu/datasets/derived-utci-historical), "
                "then add your key to `.streamlit/secrets.toml`:\n\n"
                "```toml\nCDS_API_KEY = \"your-key-here\"\n```\n\n"
                "Or set it as an environment variable: `export CDS_API_KEY=your-key-here`"
            )
            st.stop()

        utci_base = utci_full.sel(time=slice(f"{BASELINE_START}-01-01", f"{BASELINE_END}-12-31"))
        utci_rec  = utci_full.sel(time=slice(f"{BASELINE_END + 1}-01-01", f"{FULL_END}-12-31"))

        t90p     = tx90p_threshold(utci_base)
        regime   = heat_type(utci_base)
        cycle    = annual_utci_cycle(utci_base)
        cat_days = utci_category_days_annual(utci_full)
        cands    = suggest_periods(utci_rec, utci_base, window_days=window_days)

        # ── ERA5 data download ────────────────────────────────────────────────
        _era5_cache = ERA5_CACHE_DIR / slug / f"utci_daily_{FULL_START}_{FULL_END}.nc"
        if _era5_cache.exists():
            st.download_button(
                "Download ERA5 UTCI data (.nc)",
                data=_era5_cache.read_bytes(),
                file_name=f"{slug}_utci_daily_{FULL_START}_{FULL_END}.nc",
                mime="application/octet-stream",
                help="Daily maximum UTCI NetCDF for this city, 1991–2025, derived from ECMWF ARCO ERA5.",
            )

        # ── Annual UTCI cycle ─────────────────────────────────────────────────
        st.subheader(f"Thermal Stress Climatology  ({BASELINE_START}–{BASELINE_END} baseline)")
        st.markdown(f"**Heat type:** {HEAT_TYPE_LABELS[regime]}")

        months     = list(range(1, 13))
        month_abbr = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        utci_vals  = [float(cycle.sel(month=m)) for m in months]
        t90p_mean  = float(t90p.mean())

        fig_cycle = go.Figure()
        fig_cycle.add_trace(go.Scatter(
            x=month_abbr,
            y=utci_vals,
            mode="lines+markers",
            name="Mean daily UTCI max",
            line=dict(color="#d62728", width=2),
        ))
        fig_cycle.add_hline(
            y=t90p_mean,
            line_dash="dash",
            line_color="#aaa",
            annotation_text=f"UTCI90 ≈ {t90p_mean:.1f}°C",
            annotation_position="bottom right",
        )
        for label, threshold in [("Moderate stress", 26), ("Strong stress", 32),
                                  ("Very strong stress", 38)]:
            fig_cycle.add_hline(
                y=threshold,
                line_dash="dot",
                line_color="#ccc",
                line_width=1,
                annotation_text=label,
                annotation_position="top left",
                annotation_font_size=10,
            )
        fig_cycle.update_layout(
            title="Annual UTCI cycle (climatological monthly mean daily maximum)",
            yaxis_title="UTCI (°C equivalent)",
            xaxis_title=None,
            height=340,
            margin=dict(t=40, b=20, l=40, r=20),
            showlegend=False,
        )
        st.plotly_chart(fig_cycle, use_container_width=True)
        with st.expander("About this chart"):
            st.markdown(
                f"""
**What it shows:** Monthly mean of daily maximum UTCI over the {BASELINE_START}–{BASELINE_END}
baseline period at this city's centroid. The dashed line is UTCI90 — the 90th percentile of
daily UTCI maxima over the baseline — used to classify the city's heat regime.

**Heat type** is derived from the seasonal amplitude of the UTCI cycle:
*Chronic* cities show high UTCI year-round; *seasonal* cities have a pronounced hot season;
*episodic* cities have a moderate baseline with intermittent peaks.

**Data source:** ECMWF ARCO ERA5 derived UTCI, accessed via authenticated Zarr store at ~25 km
resolution (single grid cell at the city centroid).
Licence acceptance required at [cds.climate.copernicus.eu](https://cds.climate.copernicus.eu/datasets/derived-utci-historical).

**UTCI standard:** Bröde et al. 2012 / ISO 15743 — a physiologically equivalent temperature
combining air temperature, radiation, wind, and humidity.

**Limitations:** ERA5 represents the large-scale background (~25 km grid cell). It does not
resolve intra-urban variation or the urban heat island. Use the Neighbourhood Analysis tab
for 200 m resolution results.
"""
            )

        # ── UTCI stress category evolution ────────────────────────────────────
        st.subheader(f"Heat Stress Days per Year  ({FULL_START}–{FULL_END})")

        cat_years = [int(y) for y in cat_days.time.dt.year.values]
        cat_vals  = {
            cat: np.array([int(cat_days[cat].sel(time=f"{y}-01-01").item()) for y in cat_years])
            for cat in HEAT_STRESS_CATEGORIES
        }

        fig_cat = go.Figure()
        for cat in HEAT_STRESS_CATEGORIES:
            fig_cat.add_trace(go.Bar(
                x=cat_years,
                y=cat_vals[cat],
                name=HEAT_CATEGORY_LABELS[cat],
                marker_color=HEAT_CATEGORY_COLORS[cat],
            ))

        # OLS trend lines at cumulative stack heights (one per category boundary)
        x_arr      = np.array(cat_years, dtype=float)
        cumulative = np.zeros(len(cat_years))
        for cat in HEAT_STRESS_CATEGORIES:
            cumulative = cumulative + cat_vals[cat]
            slope, intercept = np.polyfit(x_arr, cumulative, 1)
            trend = slope * x_arr + intercept
            direction = "▲" if slope > 0 else "▼"
            fig_cat.add_trace(go.Scatter(
                x=cat_years,
                y=trend.tolist(),
                mode="lines",
                name=f"{HEAT_CATEGORY_LABELS[cat]} trend ({direction}{abs(slope):.1f} d/yr)",
                line=dict(color=HEAT_CATEGORY_COLORS[cat], width=2, dash="dot"),
                showlegend=True,
            ))

        fig_cat.update_layout(
            barmode="stack",
            yaxis_title="Days per year",
            xaxis=dict(tickvals=cat_years[::2]),
            height=380,
            margin=dict(t=20, b=20, l=40, r=20),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig_cat, use_container_width=True)
        with st.expander("About this chart"):
            st.markdown(
                f"""
**What it shows:** Annual count of days where ERA5 daily maximum UTCI exceeds each stress
threshold, {FULL_START}–{FULL_END}. Dotted trend lines are ordinary least squares (OLS) linear
regressions on the cumulative heat stress day count — they show the direction of change, not
statistical significance.

**UTCI stress thresholds** (Bröde et al. 2012 / ISO 15743):

| Category | UTCI range |
|---|---|
| Moderate heat stress | 26–32°C |
| Strong heat stress | 32–38°C |
| Very strong heat stress | 38–46°C |
| Extreme heat stress | >46°C |

**Data source:** ECMWF ARCO ERA5 derived UTCI —
[cds.climate.copernicus.eu](https://cds.climate.copernicus.eu/datasets/derived-utci-historical)

**Limitations:** ERA5 ~25 km resolution; does not capture UHI or intra-urban variation.
Year-to-year variability reflects large-scale climate, not local conditions.
"""
            )

        # ── Population-weighted exposure at 5-year snapshots ──────────────────
        st.subheader("Population-Weighted Heat Stress Exposure")

        pop_snapshots = {
            yr: city[f"pop_{yr}"]
            for yr in [2000, 2005, 2010, 2015, 2020, 2025]
            if city.get(f"pop_{yr}")
        }

        if pop_snapshots:
            exposure_df = population_weighted_heat_exposure(
                cat_days, pop_snapshots, HEAT_STRESS_CATEGORIES
            )
            if not exposure_df.empty:
                fig_exp = go.Figure()
                for cat in HEAT_STRESS_CATEGORIES:
                    df_cat = exposure_df[exposure_df["category"] == cat]
                    fig_exp.add_trace(go.Bar(
                        x=df_cat["year"],
                        y=df_cat["person_days"],
                        name=HEAT_CATEGORY_LABELS[cat],
                        marker_color=HEAT_CATEGORY_COLORS[cat],
                    ))
                fig_exp.update_layout(
                    barmode="stack",
                    yaxis_title="Person-days of heat stress",
                    xaxis=dict(tickvals=sorted(pop_snapshots.keys())),
                    height=320,
                    margin=dict(t=20, b=20, l=40, r=20),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                )
                st.plotly_chart(fig_exp, use_container_width=True)
                with st.expander("About this chart"):
                    st.markdown(
                        """
**What it shows:** Annual heat stress days (ERA5 UTCI, city centroid) multiplied by the
city population at each GHSL 5-year snapshot. This gives a combined measure of how many
people are exposed to each heat stress level and for how long.

**Population data source:** GHS-UCDB R2024A, GHSL population theme — satellite-derived
estimates at 5-year intervals (2000–2025).
[ghsl.jrc.ec.europa.eu](https://ghsl.jrc.ec.europa.eu/ghs_ucdb2024.php)

**Assumptions:**
- The ERA5 city-centroid UTCI is applied uniformly to the entire population.
- Population figures are totals for the GHSL urban footprint, not administrative boundaries.
- Does not account for indoor time, adaptive capacity, or spatial variation within the city.

**Limitations:** This is a screening metric — it shows relative exposure across cities and
over time, not absolute health burden.
"""
                    )

        # ── Candidate periods ─────────────────────────────────────────────────
        st.subheader("Candidate Periods for Detailed Analysis")

        active_analysis = st.session_state.get("analysis", {})
        active_city     = active_analysis.get("city", {})
        active_period   = active_analysis.get("period", {})

        def _already_selected(date_s: str) -> bool:
            return (
                active_period.get("date_start") == date_s
                and active_city.get("name") == city["name"]
            )

        def _period_button(label: str, period: dict, key: str) -> None:
            if _already_selected(period["date_start"]):
                st.success("Selected →")
            elif st.button(label, key=key):
                st.session_state["analysis"] = {"city": city, "period": period}
                st.session_state.pop("job_id", None)
                st.rerun()

        # UTCI-ranked candidates
        if cands:
            cols = st.columns(len(cands))
            for i, (col, c) in enumerate(zip(cols, cands)):
                with col:
                    st.markdown(
                        f"**Rank {c['rank']}**  \n"
                        f"{c['date_start']} → {c['date_end']}  \n"
                        f"Mean UTCI: **{c['mean_utci']:.1f}°C**  \n"
                        f"Anomaly: **{c['anomaly']:+.1f}°C**"
                    )
                    _period_button("Use for analysis →", c, key=f"use_period_{i}")
        else:
            st.info("No candidate periods found in the recent data window.")
        with st.expander("About candidate periods"):
            st.markdown(
                f"""
**How periods are selected:** All rolling windows of the chosen length (1, 2, or 4 weeks)
within the 2021–{FULL_END} recent period are ranked by mean UTCI anomaly — the difference
between the window's mean daily UTCI maximum and the climatological mean for those calendar
days over the {BASELINE_START}–{BASELINE_END} baseline.

The top-ranked window is the hottest relative to what is normal for that time of year at
this city. This makes it a useful input for neighbourhood-scale modelling, where the aim
is to understand how the city performs under representative recent heat conditions.

**Data source:** ECMWF ARCO ERA5 derived UTCI —
[cds.climate.copernicus.eu](https://cds.climate.copernicus.eu/datasets/derived-utci-historical)

**Note:** Periods are identified at ERA5 resolution (~25 km). Finer-scale meteorological
variability is captured in the neighbourhood model (Tab 2).
"""
            )


# ── Tab 2 ────────────────────────────────────────────────────────────────────

with tab2:
    st.header("Neighbourhood Analysis")

    analysis = st.session_state.get("analysis")

    if not analysis:
        st.info(
            "Select a city in the City Screening tab, then click "
            "**Use for analysis →** on a candidate period."
        )
    else:
        t2_city   = analysis["city"]
        t2_period = analysis["period"]
        t2_lat    = t2_city["centroid_lat"]
        t2_lon    = t2_city["centroid_lon"]
        t2_utc    = round(t2_lon / 15.0)
        date_start = t2_period["date_start"]
        date_end   = t2_period["date_end"]
        city_id    = _city_id(t2_city)

        # Header row
        hcol1, hcol2 = st.columns([5, 1])
        with hcol1:
            st.subheader(f"{t2_city['name']}, {t2_city['country']}")
            st.markdown(f"Analysis period: **{date_start}** → **{date_end}**")
        with hcol2:
            if st.button("Change", key="clear_analysis"):
                del st.session_state["analysis"]
                st.session_state.pop("job_id", None)
                st.rerun()

        # Morphology check
        morph_dir = TARGET_DATA_DIR / city_id
        grid_path = morph_dir / "grid.gpkg"
        lc_path   = morph_dir / "target_landcover.csv"

        _prep_id_file = morph_dir / "prep_job_id.txt"
        prep_job_id   = st.session_state.get("prep_job_id")
        if not prep_job_id and _prep_id_file.exists():
            prep_job_id = _prep_id_file.read_text().strip()
            st.session_state["prep_job_id"] = prep_job_id
        prep_job_st   = get_status(prep_job_id).get("status") if prep_job_id else None

        if not lc_path.exists() and prep_job_st not in ("pending", "running", "complete"):
            n_cells   = _estimate_grid_cells(t2_city)
            size_warn = _grid_size_warning(n_cells)
            if size_warn:
                st.warning(
                    f"No morphology data for **{t2_city['name']}**. "
                    f"{size_warn[0]}"
                )
            else:
                st.info(
                    f"No morphology data for **{t2_city['name']}** (~{n_cells:,} estimated cells). "
                    "Generating it takes roughly 10–15 minutes."
                )
            if st.button("Prepare city data", type="primary", key="prep_btn"):
                from hit.config.city import city_config_from_ucdb
                cc = city_config_from_ucdb(t2_city, date_start, date_end, "prep")
                new_prep_id = submit_prepare(
                    city=t2_city,
                    data_dir=str(morph_dir),
                    local_crs=cc.local_crs,
                )
                morph_dir.mkdir(parents=True, exist_ok=True)
                _prep_id_file.write_text(new_prep_id)
                st.session_state["prep_job_id"] = new_prep_id
                st.rerun()

        elif prep_job_st in ("pending", "running"):
            _prep_poll_fragment(prep_job_id, t2_city["name"])

        elif prep_job_st == "failed":
            prep_info   = get_status(prep_job_id)
            _prep_err   = prep_info.get("error", "")
            _step_stats = prep_info.get("step_statuses") or {}
            _failed     = [label for key, label in _PREP_STEPS if _step_stats.get(key) == "failed"]

            if "GBA WFS" in _prep_err:
                st.error(
                    "**Building data unavailable** — the GBA server is temporarily down. "
                    "This is a third-party academic server with intermittent availability. "
                    "Please try again in a few minutes."
                )
            elif _failed:
                st.error(
                    f"Preparation failed at: **{', '.join(_failed)}**  \n"
                    f"{_prep_err}  \n\n"
                    "Steps that completed are cached and will be skipped on retry."
                )
            else:
                st.error(f"Morphology preparation failed: {_prep_err}")
            _render_step_checklist(
                _PREP_STEPS,
                current_step=None,
                overall_status="failed",
                elapsed="",
                step_statuses=_step_stats or None,
            )
            if st.button("Retry preparation", key="retry_prep"):
                st.session_state.pop("prep_job_id", None)
                st.rerun()

        if not lc_path.exists():
            pass  # waiting for prep or no prep started — UI handled above
        else:
            period_slug = f"{date_start}_{date_end}"
            job_id      = st.session_state.get("job_id")

            # ── Morphology download ───────────────────────────────────────────
            with st.expander("Download input data", expanded=True):
                st.caption("Grid, land cover, buildings and road data for this city.")
                st.download_button(
                    "Download morphology inputs (.zip)",
                    data=_zip_files({
                        "grid.gpkg":            morph_dir / "grid.gpkg",
                        "target_landcover.csv": morph_dir / "target_landcover.csv",
                        "gba_buildings.gpkg":   morph_dir / "gba_buildings.gpkg",
                        "roads_raw.gpkg":       morph_dir / "roads_raw.gpkg",
                    }),
                    file_name=f"{city_id}_morphology.zip",
                    mime="application/zip",
                    key="dl_morph",
                )

            # All pre-staged runs available for this city (morph_dir + completed jobs)
            all_prestaged = {
                p.parent.name: p
                for p in sorted(morph_dir.glob("*/target_results.nc"))
            }
            for _j in list_jobs():
                if _j.get("status") != "complete" or not _j.get("output_path"):
                    continue
                _nc = Path(_j["output_path"])
                if not _nc.exists():
                    continue
                try:
                    _cfg = json.loads((JOBS_DIR / _j["job_id"] / "config.json").read_text())
                except Exception:
                    continue
                if Path(_cfg.get("data_dir", "")) != morph_dir:
                    continue
                _slug = f"{_cfg['date_start']}_{_cfg['date_end']}"
                if _slug not in all_prestaged:
                    all_prestaged[_slug] = _nc

            ds            = None
            result_source = ""
            show_run_btn  = False

            job_st = get_status(job_id).get("status") if job_id else None

            # ── Job complete ──────────────────────────────────────────────────
            if job_st == "complete":
                import shutil
                output_path = get_status(job_id)["output_path"]
                dest = morph_dir / period_slug / "target_results.nc"
                if not dest.exists() and Path(output_path).exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(output_path, dest)
                    all_prestaged[period_slug] = dest
                ds = _load_results_cached(output_path)
                result_source = f"Run completed: {date_start} → {date_end}"

            # ── Job in progress — poll and offer demo data ────────────────────
            elif job_st in ("pending", "running"):
                other = {k: v for k, v in all_prestaged.items() if k != period_slug}
                if other:
                    with st.expander("View pre-staged results while waiting", expanded=True):
                        demo_choice = st.selectbox("Period:", list(other.keys()), key="demo_choice_wait")
                        ds = _load_results_cached(str(other[demo_choice]))
                        result_source = f"Pre-staged: {demo_choice}"
                _run_poll_fragment(job_id, date_start, date_end)

            # ── Job failed ────────────────────────────────────────────────────
            elif job_st == "failed":
                st.error(f"Analysis failed: {get_status(job_id).get('error')}")
                if st.button("Retry", key="retry_job"):
                    st.session_state.pop("job_id", None)
                    st.rerun()

            # ── No active job — check pre-staged ─────────────────────────────
            else:
                if period_slug in all_prestaged:
                    ds = _load_results_cached(str(all_prestaged[period_slug]))
                    result_source = f"Pre-staged: {date_start} → {date_end}"
                else:
                    show_run_btn = True
                    other = {k: v for k, v in all_prestaged.items() if k != period_slug}
                    if other:
                        with st.expander(
                            f"Pre-staged runs available for {t2_city['name']} — click to view",
                            expanded=True,
                        ):
                            demo_choice = st.selectbox("Period:", list(other.keys()), key="demo_choice")
                            ds = _load_results_cached(str(other[demo_choice]))
                            result_source = f"Pre-staged: {demo_choice}"
                    else:
                        st.info(
                            "No pre-staged results for this period.  \n"
                            "Running TARGET takes approximately **29 minutes** for a 1-week period."
                        )

            if show_run_btn:
                n_cells   = _estimate_grid_cells(t2_city)
                size_warn = _grid_size_warning(n_cells)
                if size_warn:
                    st.warning(size_warn[1])
                if st.button("Run Neighbourhood Analysis", type="primary", key="run_job"):
                    cc = city_config_from_ucdb(t2_city, date_start, date_end, period_slug)
                    new_job_id = submit_run(
                        city_config_dict=dataclasses.asdict(cc),
                        data_dir=str(morph_dir),
                        date_start=date_start,
                        date_end=date_end,
                    )
                    st.session_state["job_id"] = new_job_id
                    st.rerun()

            # ── Results display ───────────────────────────────────────────────
            if ds is not None:
                st.caption(result_source)

                # ── Results download ──────────────────────────────────────────
                result_files: dict[str, Path] = {}
                job_info = get_status(job_id) if job_id else {}
                if job_info.get("output_path"):
                    from hit.jobs.queue import JOBS_DIR
                    result_files["target_results.nc"]  = Path(job_info["output_path"])
                    result_files["era5_met_forcing.csv"] = JOBS_DIR / job_id / "era5_met_forcing.csv"
                else:
                    # Pre-staged: find the nc that was loaded
                    for nc_path in morph_dir.glob("*/target_results.nc"):
                        if nc_path.parent.name == period_slug:
                            result_files["target_results.nc"] = nc_path
                if result_files:
                    with st.expander("Download results", expanded=True):
                        st.caption("TARGET output NetCDF" + (" and ERA5 forcing CSV" if "era5_met_forcing.csv" in result_files else "") + ".")
                        st.download_button(
                            "Download TARGET results (.zip)",
                            data=_zip_files(result_files),
                            file_name=f"{city_id}_{period_slug}_results.zip",
                            mime="application/zip",
                            key="dl_results",
                        )

                # Summary metrics
                peak_utci_val = float(ds["UTCI"].max())
                peak_tmrt_val = float(ds["Tmrt"].max())
                uhi_vals      = (ds["Ta"] - ds["Tb_rur"]).mean(dim="cell")

                # Local-time mask for daytime (07:00–20:00)
                local_hours = (
                    (ds["time"].values.astype("datetime64[h]").astype(int) + t2_utc) % 24
                )
                daytime_mask = (local_hours >= 7) & (local_hours < 20)
                mean_day_uhi = float(uhi_vals.values[daytime_mask].mean())
                peak_uhi_idx  = int(uhi_vals.values.argmax())
                peak_uhi_hour = int(local_hours[peak_uhi_idx])
                peak_uhi_val  = float(uhi_vals.values[peak_uhi_idx])

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Peak UTCI",         f"{peak_utci_val:.1f}°C")
                m2.metric("Peak Tmrt",          f"{peak_tmrt_val:.1f}°C")
                m3.metric("Mean daytime UHI",   f"{mean_day_uhi:.2f}°C")
                m4.metric("Peak UHI",           f"{peak_uhi_val:.2f}°C  (at {peak_uhi_hour:02d}:00 local)")

                # ── Map ───────────────────────────────────────────────────────
                st.subheader("200 m Grid — Spatial Results")
                map_choice = st.radio(
                    "Show:",
                    list(MAP_OPTIONS.keys()),
                    horizontal=True,
                    key="map_choice",
                )
                map_col = MAP_OPTIONS[map_choice]

                _mc_left, _mc_right = st.columns([1, 3])
                show_heat_layer = _mc_left.checkbox("Show heat map layer", value=True, key="map_toggle")
                heat_opacity    = _mc_right.slider(
                    "Layer opacity", 0.1, 1.0, 0.75, 0.05,
                    key="map_opacity", disabled=not show_heat_layer,
                )

                with st.spinner("Building map…"):
                    from hit.spatial.rasterise import gdf_to_geotiff
                    gdf = results_to_geodataframe(ds, grid_path)
                    tif_path = Path(f"data/tile_cache/{_city_slug(t2_city)}_{map_col}.tif")
                    meta = gdf_to_geotiff(gdf, map_col, tif_path)
                    STATE_FILE = Path("data/tile_server_state.json")
                    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                    STATE_FILE.write_text(json.dumps(meta))

                _tile_opacity = heat_opacity if show_heat_layer else 0.0
                st.components.v1.html(
                    _results_map_html(t2_lat, t2_lon, meta, map_choice, _tile_opacity),
                    height=540,
                )
                with st.expander("About this map"):
                    st.markdown(
                        """
**What it shows:** TARGET model output on a 200 m grid covering the city's GHSL urban
footprint plus a 500 m buffer. Each cell is coloured by the peak value of the selected
variable across the analysis period.

**Variables:**
- **Peak UTCI** — maximum Universal Thermal Climate Index across the modelled period;
  the primary outdoor thermal comfort metric combining air temperature, radiation, wind,
  and humidity (Bröde et al. 2012 / ISO 15743)
- **Peak Tmrt** — maximum mean radiant temperature; captures the radiation load on a
  standing person from sun, sky, and surrounding surfaces; high spatial variability
  driven by building shade and sky-view factor
- **Mean UHI** — mean urban–rural air temperature difference (Ta urban − Tb rural)
  across the period; positive = urban is warmer than surrounding rural area

**Model:** TARGET (Temperature of Air for Green/non-green urban Typologies)
Broadbent et al. 2019, *Urban Climate* — [doi.org/10.1016/j.uclim.2018.11.002](https://doi.org/10.1016/j.uclim.2018.11.002)
UMEP documentation: [umep-docs.readthedocs.io — TARGET processor](https://umep-docs.readthedocs.io/en/latest/processor/Urban%20Energy%20Balance%20TARGET.html)

**Input data sources:**
| Layer | Source |
|---|---|
| Building morphology (roof fraction, height H) | [Global Building Atlas (GBA) WFS](https://www.gba.ovgu.de/) — Zhu et al. 2025, ESSD |
| Road network (road fraction, canyon width W) | [Overture Maps Foundation](https://overturemaps.org/) transportation layer |
| Land cover (vegetation, water, impervious) | [ESA WorldCover 2021](https://esa-worldcover.org/) 10 m, reclassified to TARGET codes |
| Meteorological forcing | [ECMWF ARCO ERA5](https://cds.climate.copernicus.eu/datasets/derived-utci-historical) at city centroid (~25 km) |
| Urban footprint | [GHS-UCDB R2024A](https://ghsl.jrc.ec.europa.eu/ghs_ucdb2024.php) |

**Limitations:**
- TARGET is a single-layer urban canyon model; complex 3D building geometry is
  parameterised via mean height (H) and canyon width (W) ratios
- GBA building heights may be incomplete for some cities (default 8 m applied to cells
  with no data)
- ERA5 meteorological forcing is applied uniformly across the grid (~25 km background)
- Vegetation cooling is parameterised, not explicitly resolved at tree scale
"""
                    )

                # ── Diurnal cycle — air temp & radiant environment ────────────
                st.subheader("Diurnal Cycle — Air & Radiant Temperature")
                diurnal = diurnal_stats(ds, utc_offset=float(t2_utc))
                hours_fmt = [f"{h:02d}:00" for h in diurnal["local_hour"]]

                fig_diurnal = go.Figure()
                # Outer band: p10–p90 Tmrt
                fig_diurnal.add_trace(go.Scatter(
                    x=hours_fmt, y=diurnal["tmrt_p90"].tolist(),
                    mode="lines", line=dict(width=0), showlegend=False,
                ))
                fig_diurnal.add_trace(go.Scatter(
                    x=hours_fmt, y=diurnal["tmrt_p10"].tolist(),
                    fill="tonexty", mode="lines", line=dict(width=0),
                    fillcolor="rgba(214,39,40,0.10)", name="Tmrt p10–p90",
                ))
                # Inner band: IQR Tmrt
                fig_diurnal.add_trace(go.Scatter(
                    x=hours_fmt, y=diurnal["tmrt_p75"].tolist(),
                    mode="lines", line=dict(width=0), showlegend=False,
                ))
                fig_diurnal.add_trace(go.Scatter(
                    x=hours_fmt, y=diurnal["tmrt_p25"].tolist(),
                    fill="tonexty", mode="lines", line=dict(width=0),
                    fillcolor="rgba(214,39,40,0.20)", name="Tmrt IQR (p25–p75)",
                ))
                # Median Tmrt
                fig_diurnal.add_trace(go.Scatter(
                    x=hours_fmt, y=diurnal["tmrt_p50"].tolist(),
                    mode="lines", name="Median Tmrt (mean radiant temp)",
                    line=dict(color="#d62728", width=2),
                ))
                # Max Tmrt
                fig_diurnal.add_trace(go.Scatter(
                    x=hours_fmt, y=diurnal["tmrt_max"].tolist(),
                    mode="lines", name="Max Tmrt (hottest cell)",
                    line=dict(color="#8c0000", width=1.5, dash="dot"),
                ))
                # ERA5 background air temp
                fig_diurnal.add_trace(go.Scatter(
                    x=hours_fmt, y=diurnal["ta_mean"].tolist(),
                    mode="lines", name="Background air temp (Ta, ERA5)",
                    line=dict(color="#1f77b4", width=2, dash="dash"),
                ))
                fig_diurnal.update_layout(
                    yaxis_title="Temperature (°C)",
                    xaxis_title="Local time",
                    height=340,
                    margin=dict(t=20, b=20, l=40, r=20),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                )
                st.plotly_chart(fig_diurnal, use_container_width=True)
                with st.expander("About this chart"):
                    st.markdown(
                        """
**What it shows:** How air temperature (Ta) and mean radiant temperature (Tmrt) vary
through the day, aggregated across all 200 m cells and all days in the modelled period.

- **Ta (blue dashed):** ERA5 background air temperature — the same meteorological signal
  applied to every cell in the grid (~25 km resolution forcing)
- **Tmrt (red lines and bands):** TARGET-modelled mean radiant temperature — spatially
  variable because it depends on each cell's sky-view factor, building shade geometry,
  and surface type. Bands show the spread across cells:
  IQR (p25–p75, darker) and p10–p90 (lighter)

Tmrt > Ta around solar noon is normal and expected — the gap reflects solar and longwave
radiation loading on a standing person, which can add 20–40°C equivalent to air
temperature alone.

**Model:** TARGET — [umep-docs.readthedocs.io](https://umep-docs.readthedocs.io/en/latest/processor/Urban%20Energy%20Balance%20TARGET.html)
**Forcing:** ECMWF ARCO ERA5 — [cds.climate.copernicus.eu](https://cds.climate.copernicus.eu/datasets/derived-utci-historical)
"""
                    )

                # ── UTCI diurnal cycle ───────────────────────────────────────
                st.subheader("Diurnal Cycle of Thermal Stress (UTCI)")
                utci_diurnal = utci_diurnal_stats(ds, utc_offset=float(t2_utc))
                hours_fmt_u  = [f"{h:02d}:00" for h in utci_diurnal["local_hour"]]

                fig_utci_diurnal = go.Figure()
                # Stress category background bands
                for band_y0, band_y1, band_color, band_label in [
                    (26, 32, "rgba(244,164, 96,0.15)", "Moderate"),
                    (32, 38, "rgba(232,119, 34,0.15)", "Strong"),
                    (38, 46, "rgba(214, 39, 40,0.15)", "Very strong"),
                    (46, 60, "rgba(123,  0,  0,0.15)", "Extreme"),
                ]:
                    fig_utci_diurnal.add_hrect(
                        y0=band_y0, y1=band_y1,
                        fillcolor=band_color,
                        line_width=0,
                        annotation_text=band_label,
                        annotation_position="top right",
                        annotation_font_size=10,
                    )
                # Outer band: p10–p90
                fig_utci_diurnal.add_trace(go.Scatter(
                    x=hours_fmt_u, y=utci_diurnal["utci_p90"].tolist(),
                    mode="lines", line=dict(width=0), showlegend=False,
                ))
                fig_utci_diurnal.add_trace(go.Scatter(
                    x=hours_fmt_u, y=utci_diurnal["utci_p10"].tolist(),
                    fill="tonexty", mode="lines", line=dict(width=0),
                    fillcolor="rgba(214,39,40,0.10)", name="p10–p90 UTCI",
                ))
                # Inner band: IQR (p25–p75)
                fig_utci_diurnal.add_trace(go.Scatter(
                    x=hours_fmt_u, y=utci_diurnal["utci_p75"].tolist(),
                    mode="lines", line=dict(width=0), showlegend=False,
                ))
                fig_utci_diurnal.add_trace(go.Scatter(
                    x=hours_fmt_u, y=utci_diurnal["utci_p25"].tolist(),
                    fill="tonexty", mode="lines", line=dict(width=0),
                    fillcolor="rgba(214,39,40,0.20)", name="IQR UTCI (p25–p75)",
                ))
                # Median
                fig_utci_diurnal.add_trace(go.Scatter(
                    x=hours_fmt_u, y=utci_diurnal["utci_p50"].tolist(),
                    mode="lines", name="Median UTCI",
                    line=dict(color="#d62728", width=2),
                ))
                # Max (hottest cell at each hour)
                fig_utci_diurnal.add_trace(go.Scatter(
                    x=hours_fmt_u, y=utci_diurnal["utci_max"].tolist(),
                    mode="lines", name="Max UTCI (hottest cell)",
                    line=dict(color="#8c0000", width=1.5, dash="dot"),
                ))
                fig_utci_diurnal.update_layout(
                    yaxis_title="UTCI (°C equivalent)",
                    xaxis_title="Local time",
                    height=340,
                    margin=dict(t=20, b=20, l=40, r=20),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                )
                st.plotly_chart(fig_utci_diurnal, use_container_width=True)
                with st.expander("About this chart"):
                    st.markdown(
                        """
**What it shows:** Hourly UTCI across all 200 m cells and all days in the modelled period.
The median line shows the typical thermal stress level across the city at each hour;
bands show spatial spread (IQR p25–p75 and p10–p90).

**UTCI** (Universal Thermal Climate Index) is computed within TARGET from modelled Tmrt
combined with ERA5 air temperature, wind speed, and humidity. It represents outdoor
thermal exposure for a standing adult.

**Stress categories** (Bröde et al. 2012 / ISO 15743):

| Band | Range |
|---|---|
| Moderate heat stress | 26–32°C |
| Strong heat stress | 32–38°C |
| Very strong heat stress | 38–46°C |
| Extreme heat stress | >46°C |

**Limitations:** UTCI here represents outdoor exposure only — not indoor conditions,
acclimatisation, or individual vulnerability. The p10–p90 spread reflects spatial
variation across the 200 m grid, not uncertainty in the model.

**Model:** TARGET — [umep-docs.readthedocs.io](https://umep-docs.readthedocs.io/en/latest/processor/Urban%20Energy%20Balance%20TARGET.html)
"""
                    )

                # ── UHI intensity ─────────────────────────────────────────────
                st.subheader("Urban Heat Island Intensity")
                uhi_df = uhi_series(ds, utc_offset=float(t2_utc))

                fig_uhi = go.Figure()
                fig_uhi.add_trace(go.Scatter(
                    x=uhi_df["local_time"],
                    y=uhi_df["uhi"],
                    mode="lines",
                    name="UHI (Ta urban − Tb rural)",
                    line=dict(color="#e87722", width=2),
                    fill="tozeroy",
                    fillcolor="rgba(232,119,34,0.15)",
                ))
                fig_uhi.add_hline(y=0, line_color="#999", line_width=1)
                fig_uhi.update_layout(
                    yaxis_title="UHI intensity (°C)",
                    xaxis_title="Local time",
                    height=280,
                    margin=dict(t=20, b=20, l=40, r=20),
                    showlegend=False,
                )
                st.plotly_chart(fig_uhi, use_container_width=True)
                with st.expander("About this chart"):
                    st.markdown(
                        """
**What it shows:** Urban Heat Island (UHI) intensity through the day — the difference
between the mean urban air temperature (Ta, averaged across all modelled cells) and the
rural background temperature (Tb_rural from TARGET's forcing).

Positive values mean the urban area is warmer than the surrounding countryside;
negative values (Urban Cool Island) can occur in heavily irrigated or vegetated cities
and are physically realistic, not a model error.

The UHI is driven by differences in surface energy balance between urban and rural land:
reduced evapotranspiration, increased heat storage in built materials, reduced sky-view
factor, and anthropogenic heat all tend to elevate urban temperatures — particularly
at night when rural areas cool faster.

**Model:** TARGET — [umep-docs.readthedocs.io](https://umep-docs.readthedocs.io/en/latest/processor/Urban%20Energy%20Balance%20TARGET.html)
Paper: Broadbent et al. 2019, *Urban Climate* — [doi.org/10.1016/j.uclim.2018.11.002](https://doi.org/10.1016/j.uclim.2018.11.002)

**Limitations:** The rural reference is the ERA5 background air temperature — a large-scale
(~25 km) signal, not a measured rural station. For cities with strong land-use heterogeneity
in the surrounding region, this may over- or underestimate the true UHI.
"""
                    )

# ── Tab 3 ────────────────────────────────────────────────────────────────────

with tab3:
    st.header("Future Climate Projections")
    st.caption(
        "Planned: NEX-GDDP-CMIP6 v2 · 35 CMIP6 models · "
        "SSP2-4.5 and SSP3-7.0 · 1950–2100"
    )

    st.info(
        "**This feature is under development.** "
        "Charts below illustrate the planned layout using synthetic data — "
        "not real projections for any city."
    )

    if city:
        st.subheader(f"{city['name']}, {city['country']}")

    with st.expander("How this will work", expanded=False):
        st.markdown(
            """
**Data source — NEX-GDDP-CMIP6 v2 (NASA / CarbonPlan)**
- 35 CMIP6 models, bias-corrected and spatially downscaled to 0.25° (~25 km)
- Historical: 1950–2014 · Future: 2015–2100 under SSP2-4.5 and SSP3-7.0
- Accessed via CarbonPlan Kerchunk reference files at
  `s3://carbonplan-share/nasa-nex-reference/` — no authentication required

**UTCI computation**
UTCI will be derived from five daily NEX-GDDP variables (`tasmax`, `huss`,
`sfcWind`, `rsds`, `rlds`) using `pythermalcomfort`. Daily-mean inputs are used
rather than simultaneous hourly values — a standard approximation for climate
projection work.

**Two separate, non-joined data series**
- *Observed* (ERA5, 1991–2025): shown in the City Screening tab
- *Projected* (NEX-GDDP, 1950–2100): shown here; baseline and future
  scenarios are derived from the same data source so the series is
  internally consistent

**Global pre-computation pipeline**
Because NEX-GDDP chunks cover the full global grid, the efficient approach
is a single batch pass extracting all 11,422 GHSL-UCDB city points
simultaneously. Annual UTCI stress category counts (per model, per scenario)
are then stored in GCS. Results are served instantly from cache with no
on-demand computation at runtime.
            """
        )

    # ── Illustrative chart 1: ensemble time series ────────────────────────────
    st.subheader("Heat Stress Days per Year — 1950 to 2100  (illustrative)")
    st.caption("Synthetic data · illustrates planned layout only")

    rng = np.random.default_rng(42)

    hist_years = np.arange(1950, 2015)
    proj_years = np.arange(2015, 2101)

    trend_hist = 90 + (hist_years - 1950) * 0.6
    hist_vals  = trend_hist + rng.normal(0, 6, len(hist_years))

    ssp245_med = trend_hist[-1] + (proj_years - 2015) * 1.0
    ssp245_lo  = ssp245_med - 12 - (proj_years - 2015) * 0.15
    ssp245_hi  = ssp245_med + 12 + (proj_years - 2015) * 0.15

    ssp370_med = trend_hist[-1] + (proj_years - 2015) * 1.8
    ssp370_lo  = ssp370_med - 18 - (proj_years - 2015) * 0.20
    ssp370_hi  = ssp370_med + 18 + (proj_years - 2015) * 0.20

    fig_proj = go.Figure()

    fig_proj.add_trace(go.Scatter(
        x=hist_years.tolist(), y=hist_vals.tolist(),
        mode="lines", name="Historical (model ensemble median)",
        line=dict(color="#555", width=1.5),
    ))

    for med, lo, hi, color, label in [
        (ssp370_med, ssp370_lo, ssp370_hi, "#d62728", "SSP3-7.0"),
        (ssp245_med, ssp245_lo, ssp245_hi, "#e87722", "SSP2-4.5"),
    ]:
        fig_proj.add_trace(go.Scatter(
            x=proj_years.tolist(), y=hi.tolist(),
            mode="lines", line=dict(width=0), showlegend=False,
        ))
        fig_proj.add_trace(go.Scatter(
            x=proj_years.tolist(), y=lo.tolist(),
            fill="tonexty", mode="lines", line=dict(width=0),
            fillcolor=f"rgba({int(color[1:3],16)},{int(color[3:5],16)},{int(color[5:],16)},0.15)",
            name=f"{label} model spread (10th–90th %ile)",
        ))
        fig_proj.add_trace(go.Scatter(
            x=proj_years.tolist(), y=med.tolist(),
            mode="lines", name=f"{label} ensemble median",
            line=dict(color=color, width=2),
        ))

    fig_proj.add_vline(
        x=2025, line_dash="dash", line_color="#aaa",
        annotation_text="Present", annotation_position="top left",
    )
    fig_proj.update_layout(
        yaxis_title="Heat stress days per year (UTCI ≥ 26°C)",
        xaxis=dict(range=[1950, 2100]),
        height=400,
        margin=dict(t=20, b=20, l=40, r=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig_proj, use_container_width=True)

    # ── Illustrative chart 2: category breakdown at key horizons ─────────────
    st.subheader("Stress Category Breakdown at Key Horizons  (illustrative)")
    st.caption("Synthetic data · illustrates planned layout only")

    horizons  = ["2025\nbaseline", "2050\nSSP2-4.5", "2050\nSSP3-7.0",
                 "2075\nSSP2-4.5", "2075\nSSP3-7.0", "2100\nSSP2-4.5", "2100\nSSP3-7.0"]
    cat_synth = {
        "moderate_heat":    [90,  95, 100,  98, 108,  95, 110],
        "strong_heat":      [40,  50,  62,  60,  85,  65, 100],
        "very_strong_heat": [15,  22,  35,  30,  55,  35,  72],
        "extreme_heat":     [ 2,   5,  10,   8,  22,  12,  38],
    }

    fig_horiz = go.Figure()
    for cat in HEAT_STRESS_CATEGORIES:
        fig_horiz.add_trace(go.Bar(
            x=horizons,
            y=cat_synth[cat],
            name=HEAT_CATEGORY_LABELS[cat],
            marker_color=HEAT_CATEGORY_COLORS[cat],
        ))
    fig_horiz.update_layout(
        barmode="stack",
        yaxis_title="Days per year",
        height=360,
        margin=dict(t=20, b=20, l=40, r=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig_horiz, use_container_width=True)
