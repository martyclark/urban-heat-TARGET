import numpy as np
import pandas as pd
import xarray as xr


def suggest_periods(
    utci_recent: xr.DataArray,
    utci_baseline: xr.DataArray,
    window_days: int = 7,
    top_n: int = 2,
    min_gap_days: int = 14,
) -> list[dict]:
    """Return top-n hottest windows in recent UTCI, ranked by anomaly vs baseline DOY climatology.

    Anomaly = rolling-mean UTCI deviation above the 30-year baseline mean for that DOY.
    Candidate windows are separated by at least min_gap_days to avoid overlapping events.
    """
    baseline_clim = utci_baseline.groupby("time.dayofyear").mean("time")
    anom          = utci_recent.groupby("time.dayofyear") - baseline_clim
    rolling_anom  = anom.rolling(time=window_days, min_periods=window_days).mean()

    scores      = rolling_anom.values.copy()
    times       = pd.DatetimeIndex(rolling_anom.time.values)
    scores_work = np.where(np.isnan(scores), -np.inf, scores)

    candidates: list[dict] = []
    for _ in range(top_n):
        peak_idx = int(np.argmax(scores_work))
        if scores_work[peak_idx] == -np.inf:
            break

        end_date   = times[peak_idx]
        start_date = end_date - pd.Timedelta(days=window_days - 1)

        period = utci_recent.sel(time=slice(str(start_date.date()), str(end_date.date())))
        mean_utci = float(period.mean()) if period.sizes.get("time", 0) > 0 else float("nan")

        candidates.append({
            "rank":       len(candidates) + 1,
            "date_start": start_date.strftime("%Y-%m-%d"),
            "date_end":   end_date.strftime("%Y-%m-%d"),
            "mean_utci":  mean_utci,
            "anomaly":    float(scores_work[peak_idx]),
        })

        lo = max(0, peak_idx - min_gap_days)
        hi = min(len(scores_work), peak_idx + min_gap_days + 1)
        scores_work[lo:hi] = -np.inf

    return candidates
