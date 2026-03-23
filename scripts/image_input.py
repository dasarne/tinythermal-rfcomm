#!/usr/bin/env python3
import io
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image


def _parse_svg_length_mm(value: str) -> float | None:
    s = (value or "").strip().lower()
    if not s:
        return None
    try:
        if s.endswith("mm"):
            return float(s[:-2])
        if s.endswith("cm"):
            return float(s[:-2]) * 10.0
        if s.endswith("in"):
            return float(s[:-2]) * 25.4
        if s.endswith("px"):
            return float(s[:-2]) * 25.4 / 96.0
        return float(s) * 25.4 / 96.0
    except ValueError:
        return None


def get_svg_size_mm(src_path: Path) -> tuple[float, float] | None:
    try:
        root = ET.fromstring(src_path.read_text())
    except Exception:
        return None
    w = _parse_svg_length_mm(root.attrib.get("width", ""))
    h = _parse_svg_length_mm(root.attrib.get("height", ""))
    if w and h and w > 0 and h > 0:
        return (w, h)
    return None


def get_raster_size_mm(src_path: Path) -> tuple[float, float] | None:
    try:
        with Image.open(src_path) as im:
            dpi = im.info.get("dpi")
            if not dpi or len(dpi) < 2:
                return None
            xdpi = float(dpi[0])
            ydpi = float(dpi[1])
            if xdpi <= 0 or ydpi <= 0:
                return None
            width_mm = im.width * 25.4 / xdpi
            height_mm = im.height * 25.4 / ydpi
            if width_mm <= 0 or height_mm <= 0:
                return None
            return (width_mm, height_mm)
    except Exception:
        return None


def _render_svg_to_png_bytes(
    src_path: Path,
    width_px: int | None = None,
    height_px: int | None = None,
    renderer: str = "auto",
) -> bytes:
    errors = []

    renderers = ["rsvg", "magick"] if renderer == "auto" else [renderer]
    for current in renderers:
        if current == "rsvg":
            cmd = ["rsvg-convert", str(src_path), "-f", "png"]
            if width_px and width_px > 0:
                cmd.extend(["-w", str(width_px)])
            if height_px and height_px > 0:
                cmd.extend(["-h", str(height_px)])
        elif current == "magick":
            cmd = ["magick", str(src_path), "png:-"]
            if width_px and height_px and width_px > 0 and height_px > 0:
                cmd = ["magick", str(src_path), "-resize", f"{width_px}x{height_px}!", "png:-"]
        else:
            raise RuntimeError(f"unsupported SVG renderer: {current}")
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
        errors.append(f"{current}: {proc.stderr.decode(errors='ignore').strip()}")

    raise RuntimeError("unable to rasterize SVG; " + " | ".join(errors))


def _flatten_alpha_to_white(im: Image.Image) -> Image.Image:
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(bg, rgba).convert("RGB")
    return im.copy()


def rasterize_svg_to_image(src_path: Path, svg_pixels_per_mm: float = 8.0, renderer: str = "auto") -> Image.Image:
    size_mm = get_svg_size_mm(src_path)
    width_px = None
    height_px = None
    if size_mm is not None and svg_pixels_per_mm > 0:
        width_px = max(1, int(round(size_mm[0] * svg_pixels_per_mm)))
        height_px = max(1, int(round(size_mm[1] * svg_pixels_per_mm)))
    png_bytes = _render_svg_to_png_bytes(src_path, width_px=width_px, height_px=height_px, renderer=renderer)
    with Image.open(io.BytesIO(png_bytes)) as im:
        return _flatten_alpha_to_white(im)


def load_image_any(src_path: Path, svg_pixels_per_mm: float = 8.0) -> Image.Image:
    suffix = src_path.suffix.lower()
    if suffix == ".svg":
        return rasterize_svg_to_image(src_path, svg_pixels_per_mm=svg_pixels_per_mm)
    with Image.open(src_path) as im:
        return _flatten_alpha_to_white(im)
