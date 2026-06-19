from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class CityConfig:
    city_name: str
    country_iso: str
    bbox: tuple[float, float, float, float]  # (lon_min, lat_min, lon_max, lat_max)
    local_crs: str
    utc_offset: float
    run_name: str
    date_start: str  # "YYYY-MM-DD"
    date_end: str    # "YYYY-MM-DD"

    @property
    def city_id(self) -> str:
        return f"{self.country_iso}_{self.city_name.lower()}"

    @property
    def data_dir_name(self) -> str:
        return f"{self.country_iso}_{self.city_name}"


def city_config_from_ucdb(
    city: dict,
    date_start: str,
    date_end: str,
    run_name: str,
) -> CityConfig:
    """Build a CityConfig from a UCDB search result dict.

    local_crs derived from nearest UTM zone; utc_offset from centroid longitude (lon/15,
    rounded to nearest whole hour — adequate for scheduling, TARGET is UTC-agnostic anyway).
    """
    lat = city["centroid_lat"]
    lon = city["centroid_lon"]

    # Nearest UTM zone (WGS84)
    zone = math.floor((lon + 180) / 6) + 1
    hemisphere = "N" if lat >= 0 else "S"
    epsg = 32600 + zone if hemisphere == "N" else 32700 + zone
    local_crs = f"EPSG:{epsg}"

    utc_offset = round(lon / 15.0)

    country_iso = city.get("country_iso3", city["country"][:3].upper())

    return CityConfig(
        city_name=city["name"],
        country_iso=country_iso,
        bbox=(
            city.get("lon_min", lon - 0.15),
            city.get("lat_min", lat - 0.15),
            city.get("lon_max", lon + 0.15),
            city.get("lat_max", lat + 0.15),
        ),
        local_crs=local_crs,
        utc_offset=float(utc_offset),
        run_name=run_name,
        date_start=date_start,
        date_end=date_end,
    )


SALVADOR = CityConfig(
    city_name="Salvador",
    country_iso="BRA",
    bbox=(-38.60, -13.05, -38.30, -12.80),
    local_crs="EPSG:31985",
    utc_offset=-3.0,
    run_name="heatwave_oct2023",
    date_start="2023-10-21",
    date_end="2023-10-27",
)
