from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import streamlit as st
import xarray as xr


def peak_conditions(ds: xr.Dataset) -> pd.DataFrame:
    """Per-cell peak UTCI, peak Tmrt, and mean UHI for a TARGET results Dataset.

    UHI = Ta (urban air, ERA5-forced) - Tb_rur (rural background).
    Tmrt (mean radiant temperature) is used instead of Tac_can_roof, which is a
    surface-stability hybrid and not a human-experienced air temperature.
    Returns DataFrame with index 'cell' and columns: peak_utci, peak_tmrt, mean_uhi.
    """
    peak_utci = ds["UTCI"].where(ds["UTCI"] > -900).max(dim="time").values
    peak_tmrt = ds["Tmrt"].where(ds["Tmrt"] > -900).max(dim="time").values
    uhi       = (ds["Ta"] - ds["Tb_rur"]).where(ds["Ta"] > -900)
    mean_uhi  = uhi.mean(dim="time").values

    return pd.DataFrame(
        {"peak_utci": peak_utci, "peak_tmrt": peak_tmrt, "mean_uhi": mean_uhi},
        index=pd.Index(ds["cell"].values, name="cell"),
    )


@st.cache_data(show_spinner=False)
def results_to_geodataframe(ds: xr.Dataset, grid_path: Path) -> gpd.GeoDataFrame:
    """Join per-cell peak stats from a TARGET Dataset to grid polygon geometry.

    Returns a GeoDataFrame in EPSG:4326 with columns: peak_utci, peak_tmrt, mean_uhi.
    Grid rows are 0-indexed and correspond positionally to the Dataset cell coordinate.
    """
    grid = gpd.read_file(grid_path)
    peak_df = peak_conditions(ds).reset_index()
    grid = grid.copy()
    grid["_cell"] = range(len(grid))
    gdf = grid.merge(peak_df, left_on="_cell", right_on="cell", how="left")
    gdf = gdf.drop(columns=["_cell"])
    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")
    return gdf


def diurnal_stats(ds: xr.Dataset, utc_offset: float = 0.0) -> pd.DataFrame:
    """Hourly Tmrt distribution across ALL cells x ALL days, local time.

    Uses Tmrt (mean radiant temperature) rather than Tac_can_roof. Tac_can_roof is a
    plan-area-weighted blend of roof surface (~55-60 C) and canyon air used internally
    for Richardson-number stability — not a human-experienced air temperature.
    Tmrt captures the actual urban radiation environment and is spatially variable.

    Returns DataFrame: local_hour, tmrt_p10/p25/p50/p75/p90/max/mean, ta_mean.
    """
    times_local = pd.DatetimeIndex(ds["time"].values) + pd.Timedelta(hours=utc_offset)
    hours = times_local.hour

    tmrt = ds["Tmrt"].values                    # (time, cell)
    ta   = ds["Ta"].mean(dim="cell").values      # (time,) ERA5 background air temp

    rows = []
    for h in range(24):
        mask = hours == h
        if not mask.any():
            continue
        vals = tmrt[mask].ravel()
        vals = vals[np.isfinite(vals) & (vals > -900.0)]
        ta_h = ta[mask]
        ta_h = ta_h[np.isfinite(ta_h)]
        if len(vals) == 0:
            continue
        rows.append({
            "local_hour": h,
            "tmrt_p10":   float(np.percentile(vals, 10)),
            "tmrt_p25":   float(np.percentile(vals, 25)),
            "tmrt_p50":   float(np.percentile(vals, 50)),
            "tmrt_p75":   float(np.percentile(vals, 75)),
            "tmrt_p90":   float(np.percentile(vals, 90)),
            "tmrt_max":   float(np.max(vals)),
            "tmrt_mean":  float(np.mean(vals)),
            "ta_mean":    float(np.mean(ta_h)) if len(ta_h) else float("nan"),
        })
    return pd.DataFrame(rows)


def diurnal_means(ds: xr.Dataset, utc_offset: float = 0.0) -> pd.DataFrame:
    """Alias for diurnal_stats — kept for backward compatibility."""
    return diurnal_stats(ds, utc_offset)


def utci_diurnal_stats(ds: xr.Dataset, utc_offset: float = 0.0) -> pd.DataFrame:
    """Hourly UTCI distribution across ALL cells x ALL days, local time.

    Returns DataFrame: local_hour, utci_p10/p25/p50/p75/p90/max/mean.
    """
    times_local = pd.DatetimeIndex(ds["time"].values) + pd.Timedelta(hours=utc_offset)
    hours = times_local.hour
    utci  = ds["UTCI"].values  # (time, cell)

    rows = []
    for h in range(24):
        mask = hours == h
        if not mask.any():
            continue
        vals = utci[mask].ravel()
        vals = vals[np.isfinite(vals) & (vals > -900.0)]
        if len(vals) == 0:
            continue
        rows.append({
            "local_hour": h,
            "utci_p10":   float(np.percentile(vals, 10)),
            "utci_p25":   float(np.percentile(vals, 25)),
            "utci_p50":   float(np.percentile(vals, 50)),
            "utci_p75":   float(np.percentile(vals, 75)),
            "utci_p90":   float(np.percentile(vals, 90)),
            "utci_max":   float(np.max(vals)),
            "utci_mean":  float(np.mean(vals)),
        })
    return pd.DataFrame(rows)


def utci_diurnal_means(ds: xr.Dataset, utc_offset: float = 0.0) -> pd.DataFrame:
    """Alias for utci_diurnal_stats — kept for backward compatibility."""
    return utci_diurnal_stats(ds, utc_offset)


def uhi_series(ds: xr.Dataset, utc_offset: float = 0.0) -> pd.DataFrame:
    """Domain-mean UHI (Ta urban - Tb_rur rural) at each timestep, in local time.

    Uses Ta (ERA5 air temperature as modified by TARGET per cell) rather than
    Tac_can_roof, which is inflated by roof surface temperatures and is not an
    air temperature in the human-exposure sense.
    Returns DataFrame with columns: local_time, uhi.
    """
    times_local = pd.DatetimeIndex(ds["time"].values) + pd.Timedelta(hours=utc_offset)
    ta  = ds["Ta"].mean(dim="cell").values
    tb  = ds["Tb_rur"].mean(dim="cell").values
    return pd.DataFrame({"local_time": times_local, "uhi": ta - tb})
