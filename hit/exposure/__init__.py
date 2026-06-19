from __future__ import annotations

import pandas as pd
import xarray as xr


def person_days(population: int, extreme_days_total: int) -> int:
    """Total person-days of extreme heat: population × count of days exceeding UTCI90."""
    return population * extreme_days_total


def population_weighted_heat_exposure(
    category_days: xr.Dataset,
    pop_snapshots: dict[int, int | None],
    categories: list[str],
) -> pd.DataFrame:
    """Person-days per heat stress category at population snapshot years.

    category_days:  xr.Dataset with one variable per UTCI category, yearly time dim
    pop_snapshots:  {year: population} from GHSL 5-year data (None values skipped)
    categories:     ordered list of category names to include (e.g. HEAT_STRESS_CATEGORIES)

    Returns a DataFrame with columns [year, category, person_days].
    """
    rows = []
    for year, pop in sorted(pop_snapshots.items()):
        if pop is None:
            continue
        year_str = f"{year}-01-01"
        for cat in categories:
            if cat not in category_days:
                continue
            try:
                days = int(category_days[cat].sel(time=year_str).item())
            except KeyError:
                continue
            rows.append({"year": year, "category": cat, "person_days": days * pop})
    return pd.DataFrame(rows)
