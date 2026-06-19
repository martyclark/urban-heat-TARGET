from collections import OrderedDict

import xarray as xr
import xclim.core.calendar
import xclim.indices

# UTCI thermal stress categories per Bröde et al. (2012) / ISO 15743
# Bounds are (lower_inclusive, upper_exclusive); None = unbounded
UTCI_CATEGORIES: OrderedDict[str, tuple] = OrderedDict([
    ("extreme_cold",      (None, -40)),
    ("very_strong_cold",  (-40,  -27)),
    ("strong_cold",       (-27,  -13)),
    ("moderate_cold",     (-13,    0)),
    ("slight_cold",       (  0,    9)),
    ("no_stress",         (  9,   26)),
    ("moderate_heat",     ( 26,   32)),
    ("strong_heat",       ( 32,   38)),
    ("very_strong_heat",  ( 38,   46)),
    ("extreme_heat",      ( 46, None)),
])

HEAT_STRESS_CATEGORIES = ["moderate_heat", "strong_heat", "very_strong_heat", "extreme_heat"]


def tx90p_threshold(utci_baseline: xr.DataArray, window: int = 5) -> xr.DataArray:
    """90th-percentile threshold of daily UTCI by DOY, computed from baseline period.

    Uses a ±window-day centred window to increase sample size per DOY.
    """
    da = utci_baseline.copy()
    da.attrs["units"] = "degC"
    p = xclim.core.calendar.percentile_doy(da, window=window, per=90)
    # Squeeze out percentiles dim (present when per is scalar in some xclim builds)
    if "percentiles" in p.dims:
        p = p.sel(percentiles=90)
    return p


def count_extreme_heat_days(
    utci_recent: xr.DataArray,
    t90p: xr.DataArray,
    freq: str = "YS",
) -> xr.DataArray:
    """Days per year where daily UTCI max exceeds the UTCI90 threshold."""
    da = utci_recent.copy()
    da.attrs["units"] = "degC"
    # Second param renamed t90p→tasmax_per in xclim 0.53+; pass positionally for compat
    return xclim.indices.tx90p(da, t90p, freq=freq, bootstrap=False)


def heat_type(utci_baseline: xr.DataArray) -> str:
    """Classify city heat regime: 'chronic', 'seasonal', or 'episodic'.

    chronic  — low seasonality (<6°C): tropical and coastal cities with persistent heat
    seasonal — high seasonality (>16°C): hot-cool seasonal swing
    episodic — moderate seasonality: temperate cities with intermittent heat events
    """
    monthly     = utci_baseline.resample(time="MS").mean()
    clim        = monthly.groupby("time.month").mean()
    seasonality = float(clim.max() - clim.min())

    if seasonality < 6.0:
        return "chronic"
    if seasonality > 16.0:
        return "seasonal"
    return "episodic"


def annual_utci_cycle(utci_baseline: xr.DataArray) -> xr.DataArray:
    """Climatological monthly mean daily-max UTCI over the baseline period (month 1–12)."""
    return utci_baseline.groupby("time.month").mean()


def utci_category_days_annual(utci_daily: xr.DataArray) -> xr.Dataset:
    """Days per year in each of the 10 UTCI thermal stress categories."""
    result = {}
    for name, (lo, hi) in UTCI_CATEGORIES.items():
        mask = xr.ones_like(utci_daily, dtype=bool)
        if lo is not None:
            mask = mask & (utci_daily >= lo)
        if hi is not None:
            mask = mask & (utci_daily < hi)
        result[name] = mask.resample(time="YS").sum()
    return xr.Dataset(result)
