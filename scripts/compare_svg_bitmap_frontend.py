#!/usr/bin/env python3
import argparse
import json
import time
from pathlib import Path

from PIL import Image, ImageChops, ImageOps

from image_input import get_raster_size_mm, get_svg_size_mm, load_image_any, rasterize_svg_to_image
from katasymbol_print import choose_rotation_auto


def diff_stats(a: Image.Image, b: Image.Image) -> dict[str, int | list[int] | None]:
    d = ImageChops.difference(a, b)
    bbox = d.getbbox()
    hist = d.histogram()
    return {
        "changed_pixels": int(sum(hist[1:])),
        "diff_bbox": list(bbox) if bbox is not None else None,
    }


def normalize_grayscale(im: Image.Image, head_height: int, autocontrast: bool, rotate_mode: str) -> tuple[Image.Image, dict[str, object]]:
    rot = 0
    if rotate_mode == "auto":
        rot = choose_rotation_auto(im, head_height)
    else:
        rot = int(rotate_mode)
    if rot:
        im = im.rotate(rot, expand=True)

    g = im.convert("L")
    if autocontrast:
        g = ImageOps.autocontrast(g)
    if g.height > 0 and g.height != head_height:
        new_w = max(1, round(g.width * (head_height / g.height)))
        g = g.resize((new_w, head_height), Image.Resampling.LANCZOS)
    return g, {"rotation": rot, "size": [g.width, g.height]}


def save_image(im: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, format="PNG")


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare SVG frontend rasterization against a bitmap reference.")
    ap.add_argument("svg", help="SVG source")
    ap.add_argument("bitmap", help="Bitmap reference (PNG/JPG)")
    ap.add_argument("--out-dir", default="", help="Output directory")
    ap.add_argument("--svg-ppmm", type=float, default=8.0, help="Rasterization density for SVG in pixels/mm")
    ap.add_argument("--renderer", choices=["auto", "rsvg", "magick"], default="auto")
    ap.add_argument("--head-height", type=int, default=96, help="Target normalized height")
    ap.add_argument("--rotate", default="auto", choices=["auto", "0", "90", "180", "270"])
    ap.add_argument("--no-autocontrast", action="store_true")
    args = ap.parse_args()

    svg_path = Path(args.svg)
    bitmap_path = Path(args.bitmap)
    ts = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else Path("out/svg_frontend_compare") / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    svg_raw = rasterize_svg_to_image(svg_path, svg_pixels_per_mm=args.svg_ppmm, renderer=args.renderer)
    bitmap_raw = load_image_any(bitmap_path).convert("RGB")

    svg_gray_raw = svg_raw.convert("L")
    bitmap_gray_raw = bitmap_raw.convert("L")
    svg_norm, svg_norm_meta = normalize_grayscale(
        svg_raw,
        head_height=args.head_height,
        autocontrast=(not args.no_autocontrast),
        rotate_mode=args.rotate,
    )
    bitmap_norm, bitmap_norm_meta = normalize_grayscale(
        bitmap_raw,
        head_height=args.head_height,
        autocontrast=(not args.no_autocontrast),
        rotate_mode=args.rotate,
    )

    if svg_gray_raw.size == bitmap_gray_raw.size:
        raw_diff = diff_stats(svg_gray_raw, bitmap_gray_raw)
    else:
        raw_diff = {
            "changed_pixels": -1,
            "diff_bbox": None,
        }
    if svg_norm.size == bitmap_norm.size:
        norm_diff = diff_stats(svg_norm, bitmap_norm)
    else:
        raise SystemExit("normalized images unexpectedly differ in size")

    save_image(svg_gray_raw, out_dir / "svg_raster_gray.png")
    save_image(bitmap_gray_raw, out_dir / "bitmap_gray.png")
    save_image(svg_norm, out_dir / "svg_normalized_gray.png")
    save_image(bitmap_norm, out_dir / "bitmap_normalized_gray.png")

    if svg_gray_raw.size == bitmap_gray_raw.size:
        save_image(ImageChops.difference(svg_gray_raw, bitmap_gray_raw), out_dir / "diff_raw_gray.png")
    save_image(ImageChops.difference(svg_norm, bitmap_norm), out_dir / "diff_normalized_gray.png")

    report = {
        "svg": str(svg_path),
        "bitmap": str(bitmap_path),
        "svg_size_mm": list(get_svg_size_mm(svg_path)) if get_svg_size_mm(svg_path) else None,
        "bitmap_size_mm": list(get_raster_size_mm(bitmap_path)) if get_raster_size_mm(bitmap_path) else None,
        "svg_ppmm": args.svg_ppmm,
        "renderer": args.renderer,
        "head_height": args.head_height,
        "autocontrast": not args.no_autocontrast,
        "svg_raw": {"size": [svg_gray_raw.width, svg_gray_raw.height]},
        "bitmap_raw": {"size": [bitmap_gray_raw.width, bitmap_gray_raw.height]},
        "svg_normalized": svg_norm_meta,
        "bitmap_normalized": bitmap_norm_meta,
        "raw_diff": raw_diff,
        "normalized_diff": norm_diff,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(out_dir)


if __name__ == "__main__":
    main()
