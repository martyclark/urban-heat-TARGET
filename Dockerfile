FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Vendored target_py installed first — must precede hit package install
COPY vendor/target_py /app/vendor/target_py
RUN pip install --no-cache-dir /app/vendor/target_py

# Pin all scientific packages to match the target-umep conda environment
RUN pip install --no-cache-dir \
    "numpy==2.4.6" \
    "pandas==3.0.3" \
    "xarray==2026.4.0" \
    "zarr==3.1.6" \
    "fsspec==2026.3.0" \
    "aiohttp==3.14.0" \
    "requests==2.34.2" \
    "scipy==1.17.1" \
    "tqdm==4.68.2"

# Geospatial stack — PyPI wheels bundle GDAL/GEOS/PROJ so no system installs needed
RUN pip install --no-cache-dir \
    "shapely==2.1.2" \
    "fiona==1.10.1" \
    "pyproj==3.7.1" \
    "rasterio==1.4.4" \
    "geopandas==1.1.3" \
    "rioxarray==0.19.0"

# Climate indices — pin minor version, breaking changes between releases
RUN pip install --no-cache-dir \
    "xclim>=0.52,<0.53"

# Web / visualisation (not needed for TARGET runs but included for the full app)
RUN pip install --no-cache-dir \
    "streamlit>=1.35" \
    "plotly>=5.22" \
    "pydeck>=0.9"

# hit package
COPY hit/ /app/hit/
COPY pyproject.toml /app/
RUN pip install --no-cache-dir -e ".[dev]"

COPY tests/ /app/tests/

CMD ["python", "-m", "pytest", "tests/", "-v"]
