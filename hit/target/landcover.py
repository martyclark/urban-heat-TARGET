from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd

_FRAC_COLS = ["roof", "road", "veg", "dry", "irr", "watr", "conc"]
_TOLERANCE = 1e-6


def combine_fractions(
    grid_fids: pd.Series,
    buildings_df: pd.DataFrame,
    roads_df: pd.DataFrame,
    lc_df: pd.DataFrame,
    out_path: Path,
) -> pd.DataFrame:
    """Merge per-cell building, road and land cover data into TARGET landcover CSV.

    Computes concrete fraction as residual impervious (built_up - roof - road),
    normalises rows to sum to 1, and writes target_landcover.csv.

    Returns the final DataFrame.
    """
    df = pd.DataFrame({"FID": grid_fids})
    df = df.merge(buildings_df[["FID", "roof", "H"]], on="FID", how="left")
    df = df.merge(roads_df[["FID", "road", "W"]], on="FID", how="left")
    df = df.merge(lc_df[["FID", "veg", "dry", "irr", "watr", "built_up"]], on="FID", how="left")

    frac_input = ["roof", "road", "veg", "dry", "irr", "watr", "built_up"]
    df[frac_input] = df[frac_input].fillna(0.0)
    df["H"] = df["H"].fillna(0.0)
    df["W"] = df["W"].fillna(0.0)

    df["conc"] = (df["built_up"] - df["roof"] - df["road"]).clip(lower=0.0, upper=1.0)

    # Normalise rows that overflow due to data inconsistencies
    frac_cols = ["roof", "road", "veg", "dry", "irr", "watr", "conc"]
    row_sums = df[frac_cols].sum(axis=1)
    overflow = row_sums > (1.0 + _TOLERANCE)
    if overflow.any():
        warnings.warn(f"{overflow.sum()} cells have fraction totals > 1 — normalising.")
        df.loc[overflow, frac_cols] = df.loc[overflow, frac_cols].div(row_sums[overflow], axis=0)

    df[frac_cols] = df[frac_cols].clip(lower=0.0, upper=1.0)

    row_sums = df[frac_cols].sum(axis=1)
    bad_sum = (row_sums - 1.0).abs() > _TOLERANCE
    if bad_sum.any():
        df.loc[bad_sum, frac_cols] = df.loc[bad_sum, frac_cols].div(row_sums[bad_sum], axis=0)

    df = df.rename(columns={"veg": "Veg"})
    target_cols = ["FID", "roof", "road", "watr", "conc", "Veg", "dry", "irr", "H", "W"]
    df_out = df[target_cols].copy()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df_out.to_csv(out_path, index=False, float_format="%.6f")
    return df_out
