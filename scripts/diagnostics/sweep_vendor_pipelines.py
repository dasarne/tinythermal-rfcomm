#!/usr/bin/env python3
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageChops

SCRIPT_DIR = Path(__file__).resolve().parent
PARENT_DIR = SCRIPT_DIR.parent
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))

from raster_btbuf import (
    _vendor_import_preprocess,
    btbuf_to_image,
    image_to_btbuf_with_canvas,
    load_template_geometry,
)


APP_PREVIEW_WIDTH = 384


def compare_images(ref: Image.Image, cand: Image.Image) -> dict[str, int]:
    ref_bw = ref.convert("1")
    cand_bw = cand.convert("1")
    diff = ImageChops.difference(ref_bw, cand_bw)
    ref_black = ref_bw.convert("L").histogram()[0]
    cand_black = cand_bw.convert("L").histogram()[0]
    return {
        "changed_pixels": int(sum(diff.histogram()[1:])),
        "black_pixel_delta": int(cand_black - ref_black),
    }


def load_vendor_preview(job_dir: Path) -> Image.Image:
    preview = job_dir / "render_lsb_inv0.png"
    if not preview.exists():
        raise SystemExit(f"vendor preview not found: {preview}")
    return Image.open(preview).convert("L")


def prepare_raw(src: Image.Image) -> Image.Image:
    return src.convert("L")


def prepare_splice_regular_single(src: Image.Image) -> Image.Image:
    work = src.convert("L")
    if work.width != APP_PREVIEW_WIDTH:
        scale = APP_PREVIEW_WIDTH / work.width
        work = work.resize(
            (APP_PREVIEW_WIDTH, max(1, int(round(work.height * scale)))),
            Image.Resampling.BICUBIC,
        )
    max_height = int((APP_PREVIEW_WIDTH * 300) / 48.0)
    if work.height > max_height:
        work = work.crop((0, 0, work.width, max_height))
    return work


def prepare_splice_free_single(src: Image.Image) -> Image.Image:
    work = src.convert("L")
    target_width = max(1, int(round((APP_PREVIEW_WIDTH / 2.0) - 40.0)))
    if work.width != target_width:
        scale = target_width / work.width
        work = work.resize(
            (target_width, max(1, int(round(work.height * scale)))),
            Image.Resampling.BICUBIC,
        )
    return work


def render_btbuf(
    image_path: Path,
    template_dump_dir: Path,
    template_job: int,
) -> tuple[bytes, dict[str, int]]:
    tgeom = load_template_geometry(template_dump_dir / f"job_{template_job:03d}")
    if tgeom is None:
        raise SystemExit("unable to load template geometry")
    canvas_width = int(tgeom["width"]) + int(tgeom.get("no_zero_index", 0))
    force_no_zero_index = int(tgeom.get("no_zero_index", 0))
    return image_to_btbuf_with_canvas(
        image_path,
        threshold=125,
        canvas_width=canvas_width,
        bytes_per_col=int(tgeom["bytes_per_col"]),
        svg_pixels_per_mm=8.0,
        no_scale=False,
        scale_to_canvas_width=False,
        force_no_zero_index=force_no_zero_index,
        scale_width_bias=0,
        scale_resample="lanczos",
        compat_raster_preset="vendor-like-t15",
        bbox_fit_mode="contain",
        bbox_align_x="center",
        bbox_align_y="center",
        bbox_inset_y=0,
        bbox_offset_y=0,
        raster_y_phase=0,
        template_btbuf=None,
        template_layout=None,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare plausible vendor image pipelines against a decoded vendor btbuf preview.")
    ap.add_argument("source_image", help="Source raster image to feed into candidate pipelines")
    ap.add_argument("--vendor-job-dir", required=True, help="Decoded vendor lzma_btbuf job directory containing render_lsb_inv0.png")
    ap.add_argument("--template-dump-dir", required=True, help="Decoded SPP dump dir used as the T15 template source")
    ap.add_argument("--template-job", type=int, default=1)
    ap.add_argument("--out-dir", default="", help="Output directory")
    args = ap.parse_args()

    ts = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else Path("out/vendor_pipeline_sweep") / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    src = Image.open(args.source_image).convert("L")
    vendor = load_vendor_preview(Path(args.vendor_job_dir))

    pipeline_images = {
        "raw": prepare_raw(src),
        "import_setup": _vendor_import_preprocess(src, use_dither=False),
        "import_setup_dither": _vendor_import_preprocess(src, use_dither=True),
        "splice_regular_single": prepare_splice_regular_single(src),
        "splice_free_single": prepare_splice_free_single(src),
    }

    report: dict[str, object] = {
        "source_image": args.source_image,
        "vendor_job_dir": args.vendor_job_dir,
        "template_dump_dir": args.template_dump_dir,
        "template_job": args.template_job,
        "results": {},
    }

    with tempfile.TemporaryDirectory(prefix="vendor-pipeline-sweep-", dir="/tmp") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        for name, im in pipeline_images.items():
            pipeline_dir = out_dir / name
            pipeline_dir.mkdir(parents=True, exist_ok=True)
            prepared_path = tmp_dir / f"{name}.png"
            im.save(prepared_path, format="PNG")
            im.save(pipeline_dir / "prepared.png", format="PNG")

            btbuf, geom = render_btbuf(prepared_path, Path(args.template_dump_dir), args.template_job)
            preview = btbuf_to_image(btbuf).convert("L")
            preview.save(pipeline_dir / "btbuf_preview.png", format="PNG")
            diff = ImageChops.difference(vendor.convert("1"), preview.convert("1")).convert("L")
            diff.save(pipeline_dir / "diff_vs_vendor.png", format="PNG")

            stats = compare_images(vendor, preview)
            result = {
                "prepared_size": [im.width, im.height],
                "geometry": geom,
                **stats,
            }
            report["results"][name] = result

    (out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(out_dir)


if __name__ == "__main__":
    main()
