from __future__ import annotations

import io
import json
from pathlib import Path

import mercantile
import numpy as np
import rasterio
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.warp import reproject

TILE_SIZE = 256
STATE_FILE = Path("data/tile_server_state.json")

# Yellow → orange → red → dark red — matches existing branca colormap in app.py
_COLOR_STOPS = [
    (0.0,  (255, 255, 178)),
    (0.25, (254, 204,  92)),
    (0.5,  (253, 141,  60)),
    (0.75, (240,  59,  32)),
    (1.0,  (189,   0,  38)),
]

app = FastAPI(title="HIT Tile Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _read_state() -> dict:
    if not STATE_FILE.exists():
        raise HTTPException(status_code=503, detail="No active tile data — run a TARGET simulation first")
    return json.loads(STATE_FILE.read_text())


def _interpolate_color(t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    for i in range(len(_COLOR_STOPS) - 1):
        t0, c0 = _COLOR_STOPS[i]
        t1, c1 = _COLOR_STOPS[i + 1]
        if t <= t1:
            f = (t - t0) / (t1 - t0)
            return (
                int(c0[0] + f * (c1[0] - c0[0])),
                int(c0[1] + f * (c1[1] - c0[1])),
                int(c0[2] + f * (c1[2] - c0[2])),
            )
    return _COLOR_STOPS[-1][1]


def _colorize(data: np.ndarray, vmin: float, vmax: float, opacity: float) -> np.ndarray:
    rgba = np.zeros((TILE_SIZE, TILE_SIZE, 4), dtype=np.uint8)
    valid = np.isfinite(data)
    if np.any(valid):
        t_vals = np.clip((data[valid] - vmin) / max(vmax - vmin, 1e-6), 0.0, 1.0)
        colors = np.array([_interpolate_color(float(v)) for v in t_vals], dtype=np.uint8)
        rgba[valid, :3] = colors
        rgba[valid, 3] = int(opacity * 255)
    return rgba


def _tile_dst_transform(z: int, x: int, y: int):
    bounds = mercantile.xy_bounds(mercantile.Tile(x, y, z))
    return from_bounds(bounds.left, bounds.bottom, bounds.right, bounds.top, TILE_SIZE, TILE_SIZE)


def _png_bytes(rgba: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG")
    return buf.getvalue()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/state")
def get_state():
    return _read_state()


@app.get("/tiles/{z}/{x}/{y}.png")
def serve_tile(z: int, x: int, y: int, opacity: float = 1.0):
    state = _read_state()
    tif_path = Path(state["path"])
    if not tif_path.exists():
        raise HTTPException(status_code=404, detail="Tile GeoTIFF not found")

    vmin = float(state["vmin"])
    vmax = float(state["vmax"])
    dst_transform = _tile_dst_transform(z, x, y)
    dst = np.full((TILE_SIZE, TILE_SIZE), np.nan, dtype=np.float32)

    with rasterio.open(tif_path) as src:
        reproject(
            source=rasterio.band(src, 1),
            destination=dst,
            src_nodata=src.nodata,
            dst_transform=dst_transform,
            dst_crs="EPSG:3857",
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )

    return Response(
        content=_png_bytes(_colorize(dst, vmin, vmax, min(1.0, max(0.0, opacity)))),
        media_type="image/png",
    )
