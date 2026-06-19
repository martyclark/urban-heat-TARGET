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
    "rioxarray==0.19.0" \
    "rasterstats>=0.19" \
    "duckdb>=0.10"

# Climate indices — pin minor version, breaking changes between releases
RUN pip install --no-cache-dir \
    "xclim>=0.52,<0.53"

# Web / visualisation
RUN pip install --no-cache-dir \
    "streamlit>=1.35" \
    "plotly>=5.22" \
    "pydeck>=0.9" \
    "folium>=0.17" \
    "streamlit-folium>=0.22" \
    "branca>=0.7"

# GCS access
RUN pip install --no-cache-dir \
    "gcsfs>=2024.2"

# hit package
COPY hit/ /app/hit/
COPY pyproject.toml /app/
RUN pip install --no-cache-dir -e ".[dev]"

COPY app.py /app/
COPY tests/ /app/tests/

# Mount point for GCS bucket (populated at runtime via gcsfuse volume mount)
RUN mkdir -p /app/data

EXPOSE 8501

CMD ["streamlit", "run", "app.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true"]
