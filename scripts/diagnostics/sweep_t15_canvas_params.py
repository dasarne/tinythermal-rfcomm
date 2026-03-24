#!/usr/bin/env python3
import argparse
import json
import time
from pathlib import Path

from PIL import Image, ImageChops


def render_canvas(
    src: Image.Image,
    canvas_width: int,
    canvas_height: int,
    content_height: int,
    offset_x: int,
    offset_y: int,
    resample_name: str,
) -> Image.Image:
    resample = Image.Resampling.NEAREST if resample_name == "nearest" else Image.Resampling.LANCZOS
    scale = content_height / src.height if src.height > 0 else 1.0
    scaled_w = max(1, int(src.width * scale))
    scaled_h = max(1, int(src.height * scale))
    scaled = src.resize((scaled_w, scaled_h), resample)

    canvas = Image.new("L", (canvas_width, canvas_height), 255)
    left = ((canvas_width - scaled_w) // 2) + offset_x
    top = ((canvas_height - scaled_h) // 2) + offset_y
    left = min(max(left, 0), max(0, canvas_width - scaled_w))
    top = min(max(top, 0), max(0, canvas_height - scaled_h))
    canvas.paste(scaled, (left, top))
    return canvas


def compare(a: Image.Image, b: Image.Image) -> dict[str, int]:
    a1 = a.convert("1")
    b1 = b.convert("1")
    diff = ImageChops.difference(a1, b1)
    return {
        "changed_pixels": int(sum(diff.histogram()[1:])),
        "black_pixel_delta": int(b1.convert("L").histogram()[0] - a1.convert("L").histogram()[0]),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Sweep T15 pre-ImgConverter canvas placement parameters against a vendor btbuf preview.")
    ap.add_argument("source_image", help="source raster image")
    ap.add_argument("vendor_preview", help="vendor render_lsb_inv0.png")
    ap.add_argument("--canvas-width", type=int, default=312)
    ap.add_argument("--canvas-height", type=int, default=96)
    ap.add_argument("--height-min", type=int, default=84)
    ap.add_argument("--height-max", type=int, default=92)
    ap.add_argument("--dx-min", type=int, default=-6)
    ap.add_argument("--dx-max", type=int, default=6)
    ap.add_argument("--dy-min", type=int, default=-6)
    ap.add_argument("--dy-max", type=int, default=6)
    ap.add_argument("--out-dir", default="")
    args = ap.parse_args()

    ts = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else Path("out/t15_canvas_sweep") / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    src = Image.open(args.source_image).convert("L")
    vendor = Image.open(args.vendor_preview).convert("L")

    results = []
    best = None
    best_canvas = None
    for resample_name in ("nearest", "lanczos"):
        for content_height in range(args.height_min, args.height_max + 1):
            for dx in range(args.dx_min, args.dx_max + 1):
                for dy in range(args.dy_min, args.dy_max + 1):
                    canvas = render_canvas(
                        src,
                        args.canvas_width,
                        args.canvas_height,
                        content_height,
                        dx,
                        dy,
                        resample_name,
                    )
                    # emulate no_zero trim to 288/24 family
                    cropped = canvas.crop((24, 0, 24 + vendor.width, vendor.height))
                    stats = compare(vendor, cropped)
                    item = {
                        "resample": resample_name,
                        "content_height": content_height,
                        "offset_x": dx,
                        "offset_y": dy,
                        **stats,
                    }
                    results.append(item)
                    key = (item["changed_pixels"], abs(item["black_pixel_delta"]), content_height, abs(dx), abs(dy), resample_name)
                    if best is None or key < best[0]:
                        best = (key, item)
                        best_canvas = cropped.copy()

    results.sort(key=lambda r: (r["changed_pixels"], abs(r["black_pixel_delta"]), r["content_height"], abs(r["offset_x"]), abs(r["offset_y"]), r["resample"]))
    report = {
        "source_image": args.source_image,
        "vendor_preview": args.vendor_preview,
        "canvas_width": args.canvas_width,
        "canvas_height": args.canvas_height,
        "best": results[0] if results else None,
        "top20": results[:20],
    }
    if best_canvas is not None:
        best_canvas.save(out_dir / "best_canvas.png", format="PNG")
        ImageChops.difference(vendor.convert("1"), best_canvas.convert("1")).convert("L").save(out_dir / "best_diff.png", format="PNG")
    (out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(out_dir)


if __name__ == "__main__":
    main()
