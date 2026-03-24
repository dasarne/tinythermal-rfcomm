#!/usr/bin/env python3
import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Dict, List

from encoder_backends import encode_btbuf
from protocol_frames import OutMsg, load_template_outgoing, materialize_frames
from raster_btbuf import (
    analyze_btbuf,
    btbuf_to_image,
    image_to_btbuf,
    image_to_btbuf_with_canvas,
    load_template_btbuf,
    load_template_geometry,
    template_btbuf_layout,
)
from rfcomm_transport import send_frames_try_channels

def main() -> None:
    ap = argparse.ArgumentParser(description="Replay Katasymbol outgoing print sequence with swapped aabb payload from PNG.")
    ap.add_argument("--template-dump-dir", required=True, help="e.g. out/decode/dumpstate-2026-03-15-21-56-49_Bild")
    ap.add_argument("--template-job", type=int, default=5, help="1-based job index in summary.json")
    ap.add_argument("--image", required=True, help="input PNG/JPG to print")
    ap.add_argument("--threshold", type=int, default=125, help="binarization threshold (0..255)")
    ap.add_argument("--out-dir", default="out/replay_sender")
    ap.add_argument("--send", action="store_true", help="actually send over RFCOMM")
    ap.add_argument("--mac", default="", help="printer bluetooth MAC, required with --send")
    ap.add_argument("--channel", type=int, default=1, help="RFCOMM channel")
    ap.add_argument(
        "--channels",
        default="",
        help="optional fallback channels list, e.g. 1,2,3,4 (tries until connect succeeds)",
    )
    ap.add_argument("--connect-timeout", type=float, default=5.0, help="RFCOMM connect timeout seconds")
    ap.add_argument("--recv-timeout", type=float, default=0.12, help="per-frame recv timeout seconds")
    ap.add_argument("--delay-ms", type=int, default=20, help="inter-frame delay")
    ap.add_argument("--timing-scale", type=float, default=1.0, help="scale factor for template inter-frame timing")
    ap.add_argument("--full-sequence", action="store_true", help="send full captured aa* sequence (default: stop after aa10)")
    ap.add_argument(
        "--post-frames-after-aa10",
        type=int,
        default=0,
        help="when not using --full-sequence, also send N template frames after aa10",
    )
    ap.add_argument("--canvas-width", type=int, default=0, help="force btbuf width; 0 = auto from template aabb")
    ap.add_argument("--bytes-per-col", type=int, default=0, help="force btbuf bytes_per_col; 0 = auto from template")
    ap.add_argument(
        "--force-no-zero-index",
        type=int,
        default=None,
        help="override btbuf[12] / trim start column; default: auto or template-derived behavior",
    )
    ap.add_argument(
        "--scale-width-bias",
        type=int,
        default=0,
        help="adjust width after aspect-ratio scaling to head height; useful for compatibility tests",
    )
    ap.add_argument(
        "--scale-resample",
        choices=["lanczos", "nearest"],
        default="lanczos",
        help="resampling kernel used during sender-side scaling",
    )
    ap.add_argument(
        "--compat-raster-preset",
        choices=["", "legacy-testpattern-64x32", "decoded-template-bbox", "template-btbuf-overlay", "long-label-svg-289", "vendor-like-t15", "vendor-like-t15-import", "vendor-like-t15-import-dither"],
        default="",
        help="experimental raster compatibility preset for known test cases",
    )
    ap.add_argument(
        "--bbox-fit-mode",
        choices=["contain", "cover", "stretch"],
        default="contain",
        help="how template-bbox presets fit content into the template bbox",
    )
    ap.add_argument("--bbox-align-x", choices=["left", "center", "right"], default="center", help="horizontal placement inside template bbox")
    ap.add_argument("--bbox-align-y", choices=["top", "center", "bottom"], default="center", help="vertical placement inside template bbox")
    ap.add_argument(
        "--bbox-inset-y",
        type=int,
        default=0,
        help="vertical safety inset in pixels for template-bbox presets",
    )
    ap.add_argument(
        "--bbox-offset-y",
        type=int,
        default=0,
        help="vertical placement offset in pixels for template-bbox presets",
    )
    ap.add_argument(
        "--raster-y-phase",
        type=int,
        default=0,
        help="cyclic vertical phase shift applied during raster packing",
    )
    ap.add_argument(
        "--svg-pixels-per-mm",
        type=float,
        default=8.0,
        help="SVG rasterization density in pixels/mm before sender-side placement",
    )
    ap.add_argument(
        "--scale-to-canvas-width",
        action="store_true",
        help="scale input image to full canvas width before vertical fit",
    )
    ap.add_argument(
        "--use-template-nozero",
        action="store_true",
        help="force btbuf[12] from template job and use template full width (template width + template nozero)",
    )
    ap.add_argument(
        "--keep-template-aabb",
        action="store_true",
        help="send original captured aabb payload(s) from template job instead of generated payload",
    )
    ap.add_argument(
        "--lzma-encoder",
        choices=["python", "xz", "java"],
        default="java",
        help="LZMA encoder backend for generated payload",
    )
    args = ap.parse_args()

    dump_dir = Path(args.template_dump_dir)
    summary_path = dump_dir / "summary.json"
    messages_csv = dump_dir / "messages.csv"
    job_dir = dump_dir / f"job_{args.template_job:03d}"
    if not summary_path.exists() or not messages_csv.exists() or not job_dir.exists():
        raise SystemExit("template paths missing (need summary.json, messages.csv, job_XXX)")

    template = load_template_outgoing(job_dir, summary_path, messages_csv, args.template_job)
    tgeom = load_template_geometry(job_dir)
    template_btbuf = load_template_btbuf(job_dir)
    template_layout = template_btbuf_layout(template_btbuf) if template_btbuf is not None else None
    canvas_width = args.canvas_width
    bpc = args.bytes_per_col
    if canvas_width <= 0:
        if tgeom and args.use_template_nozero:
            canvas_width = int(tgeom["width"]) + int(tgeom.get("no_zero_index", 0))
        else:
            canvas_width = tgeom["width"] if tgeom else 201
    if bpc <= 0:
        bpc = tgeom["bytes_per_col"] if tgeom else 12
    if args.force_no_zero_index is not None:
        force_no_zero_index = args.force_no_zero_index
    else:
        force_no_zero_index = int(tgeom.get("no_zero_index", 0)) if (tgeom and args.use_template_nozero) else -1
    btbuf, geom = image_to_btbuf_with_canvas(
        Path(args.image),
        args.threshold,
        canvas_width,
        bpc,
        svg_pixels_per_mm=args.svg_pixels_per_mm,
        scale_to_canvas_width=args.scale_to_canvas_width,
        force_no_zero_index=force_no_zero_index,
        scale_width_bias=args.scale_width_bias,
        scale_resample=args.scale_resample,
        compat_raster_preset=args.compat_raster_preset,
        bbox_fit_mode=args.bbox_fit_mode,
        bbox_align_x=args.bbox_align_x,
        bbox_align_y=args.bbox_align_y,
        bbox_inset_y=args.bbox_inset_y,
        bbox_offset_y=args.bbox_offset_y,
        raster_y_phase=args.raster_y_phase,
        template_btbuf=template_btbuf,
        template_layout=template_layout,
    )
    repo_root = Path(__file__).resolve().parent.parent
    lz, aabb = encode_btbuf(btbuf, args.lzma_encoder, repo_root)
    frames = materialize_frames(
        template,
        aabb,
        stop_after_aa10=(not args.full_sequence),
        post_frames_after_aa10=args.post_frames_after_aa10,
        keep_template_aabb=args.keep_template_aabb,
    )

    ts = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir) / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "btbuf.bin").write_bytes(btbuf)
    (out_dir / "lzma.bin").write_bytes(lz)
    bt_preview = btbuf_to_image(btbuf)
    bt_preview.convert("L").save(out_dir / "btbuf_preview.png", format="PNG")
    bt_info = analyze_btbuf(btbuf) or {}
    if {"bbox_x", "bbox_y", "bbox_w", "bbox_h"} <= set(bt_info):
        bbox = (
            int(bt_info["bbox_x"]),
            int(bt_info["bbox_y"]),
            int(bt_info["bbox_x"] + bt_info["bbox_w"]),
            int(bt_info["bbox_y"] + bt_info["bbox_h"]),
        )
        bt_preview.crop(bbox).convert("L").save(out_dir / "btbuf_preview_cropped.png", format="PNG")
    for i, p in enumerate(aabb):
        (out_dir / f"aabb_{i:03d}.bin").write_bytes(p)
    with (out_dir / "frames.bin").open("wb") as f:
        for _, fr, _ts in frames:
            f.write(fr)

    meta = {
        "template_dump_dir": str(dump_dir),
        "template_job": args.template_job,
        "image": args.image,
        "geometry": geom,
        "btbuf_analysis": bt_info,
        "lzma_len": len(lz),
        "aabb_chunks": len(aabb),
        "frames_total": len(frames),
        "frames": [{"index": i, "cmd": c, "len": len(fr)} for i, (c, fr, _ts) in enumerate(frames)],
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    if args.send:
        if not args.mac:
            raise SystemExit("--mac required with --send")
        channels = [args.channel]
        if args.channels.strip():
            channels = [int(x.strip()) for x in args.channels.split(",") if x.strip()]
        events: List[Dict[str, object]] = []
        try:
            used_ch, events = send_frames_try_channels(
                args.mac,
                channels,
                frames,
                args.connect_timeout,
                args.recv_timeout,
                args.delay_ms,
                args.timing_scale,
            )
            meta["send_channel"] = used_ch
        finally:
            (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
            (out_dir / "send_log.json").write_text(json.dumps(events, indent=2))

    print(out_dir)


if __name__ == "__main__":
    main()
