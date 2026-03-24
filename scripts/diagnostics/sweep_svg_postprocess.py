#!/usr/bin/env python3
import argparse
import json
import sys
import time
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageOps

SCRIPT_DIR = Path(__file__).resolve().parent
PARENT_DIR = SCRIPT_DIR.parent
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))

from compare_svg_bitmap_frontend import normalize_grayscale, save_image
from image_input import load_image_any, rasterize_svg_to_image


def parse_number_list(raw: str, caster):
    return [caster(part.strip()) for part in raw.split(",") if part.strip()]


def threshold_to_bw(im: Image.Image, threshold: int) -> Image.Image:
    return im.point(lambda p: 0 if p < threshold else 255, mode="L").convert("L")


def diff_stats(a: Image.Image, b: Image.Image) -> dict[str, int | list[int] | None]:
    diff = ImageChops.difference(a, b)
    bbox = diff.getbbox()
    return {
        "changed_pixels": int(sum(diff.histogram()[1:])),
        "diff_bbox": list(bbox) if bbox is not None else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Sweep SVG grayscale postprocessing against a bitmap reference.")
    ap.add_argument("svg", help="SVG source")
    ap.add_argument("bitmap", help="Bitmap reference (PNG/JPG)")
    ap.add_argument("--out-dir", default="", help="Output directory")
    ap.add_argument("--renderer", choices=["rsvg", "magick"], default="rsvg")
    ap.add_argument("--svg-ppmm", type=float, default=12.0)
    ap.add_argument("--head-height", type=int, default=96)
    ap.add_argument("--rotate", default="auto", choices=["auto", "0", "90", "180", "270"])
    ap.add_argument("--no-autocontrast", action="store_true")
    ap.add_argument("--bitmap-threshold", type=int, default=230)
    ap.add_argument("--svg-thresholds", default="180,190,200,210,220,230,240,245")
    ap.add_argument("--blur-radii", default="0,0.3,0.6,0.9,1.2")
    args = ap.parse_args()

    ts = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else Path("out/svg_postprocess_sweep") / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    autocontrast = not args.no_autocontrast
    svg_raw = rasterize_svg_to_image(Path(args.svg), svg_pixels_per_mm=args.svg_ppmm, renderer=args.renderer)
    bitmap_raw = load_image_any(Path(args.bitmap)).convert("RGB")

    svg_norm, svg_meta = normalize_grayscale(
        svg_raw,
        head_height=args.head_height,
        autocontrast=autocontrast,
        rotate_mode=args.rotate,
    )
    bitmap_norm, bitmap_meta = normalize_grayscale(
        bitmap_raw,
        head_height=args.head_height,
        autocontrast=autocontrast,
        rotate_mode=args.rotate,
    )
    if svg_norm.size != bitmap_norm.size:
        raise SystemExit("normalized SVG and bitmap sizes differ unexpectedly")

    bitmap_bw = threshold_to_bw(bitmap_norm, args.bitmap_threshold)
    save_image(svg_norm, out_dir / "svg_normalized_gray.png")
    save_image(bitmap_norm, out_dir / "bitmap_normalized_gray.png")
    save_image(bitmap_bw, out_dir / "bitmap_reference_bw.png")

    results = []
    thresholds = parse_number_list(args.svg_thresholds, int)
    blur_radii = parse_number_list(args.blur_radii, float)
    best = None

    for blur_radius in blur_radii:
        if blur_radius > 0:
            svg_gray = svg_norm.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        else:
            svg_gray = svg_norm.copy()
        for threshold in thresholds:
            svg_bw = threshold_to_bw(svg_gray, threshold)
            stats = diff_stats(svg_bw, bitmap_bw)
            black_svg = int(svg_bw.histogram()[0])
            black_bitmap = int(bitmap_bw.histogram()[0])
            item = {
                "blur_radius": blur_radius,
                "threshold": threshold,
                "changed_pixels": int(stats["changed_pixels"]),
                "diff_bbox": stats["diff_bbox"],
                "svg_black_pixels": black_svg,
                "bitmap_black_pixels": black_bitmap,
                "black_pixel_delta": black_svg - black_bitmap,
            }
            results.append(item)
            key = (item["changed_pixels"], abs(item["black_pixel_delta"]), blur_radius, threshold)
            if best is None or key < best[0]:
                best = (key, item, svg_gray.copy(), svg_bw.copy())

    if best is None:
        raise SystemExit("no sweep results generated")

    _, best_item, best_gray, best_bw = best
    save_image(best_gray, out_dir / "best_svg_gray.png")
    save_image(best_bw, out_dir / "best_svg_bw.png")
    save_image(ImageChops.difference(best_bw, bitmap_bw), out_dir / "best_diff_bw.png")

    results_sorted = sorted(results, key=lambda item: (item["changed_pixels"], abs(item["black_pixel_delta"]), item["blur_radius"], item["threshold"]))
    report = {
        "svg": args.svg,
        "bitmap": args.bitmap,
        "renderer": args.renderer,
        "svg_ppmm": args.svg_ppmm,
        "head_height": args.head_height,
        "autocontrast": autocontrast,
        "bitmap_threshold": args.bitmap_threshold,
        "svg_normalized": svg_meta,
        "bitmap_normalized": bitmap_meta,
        "best": best_item,
        "results": results_sorted,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(out_dir)


if __name__ == "__main__":
    main()
