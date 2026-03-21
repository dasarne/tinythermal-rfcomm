#!/usr/bin/env python3
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List


def trimmed_capture_lzma(capture_aabb: Path) -> bytes:
    data = capture_aabb.read_bytes()[4:]
    while data and data[-1] == 0:
        data = data[:-1]
    return data


def compare_bytes(data: bytes, ref: bytes) -> Dict[str, int]:
    first_diff = min(len(data), len(ref))
    overlap_equal = 0
    for i, (a, b) in enumerate(zip(data, ref)):
        if a == b:
            overlap_equal += 1
            continue
        first_diff = i
        break
    else:
        first_diff = min(len(data), len(ref))
    return {
        "len": len(data),
        "ref_len": len(ref),
        "first_diff": first_diff,
        "overlap_equal_bytes": overlap_equal,
        "tail_delta": len(data) - len(ref),
    }


def run_case(
    replay_sender: Path,
    template_dump_dir: Path,
    image: Path,
    encoder: str,
    compat_raster_preset: str,
    out_root: Path,
) -> Path:
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", image.stem)
    case_out_root = out_root / f"{safe_stem}_{encoder}"
    case_out_root.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(replay_sender),
        "--template-dump-dir",
        str(template_dump_dir),
        "--template-job",
        "1",
        "--image",
        str(image),
        "--out-dir",
        str(case_out_root),
        "--compat-raster-preset",
        compat_raster_preset,
        "--lzma-encoder",
        encoder,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"run failed for {image.name}/{encoder}: {proc.stderr or proc.stdout}")
    run_dir = case_out_root / proc.stdout.strip().splitlines()[-1].split("/")[-1]
    if not run_dir.exists():
        raise RuntimeError(f"missing run dir for {image.name}/{encoder}: {run_dir}")
    return run_dir


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--images",
        nargs="+",
        default=[
            "out/test_images/template_bbox_from_btbuf_lsb16.png",
            "out/test_images/ref_pattern_201x96.png",
            "test_pattern_64x32.png",
            "test.jpg",
        ],
    )
    ap.add_argument("--encoders", nargs="+", default=["python", "xz", "java"])
    ap.add_argument("--reference-encoder", default="java")
    ap.add_argument(
        "--template-dump-dir",
        default="out/decode/dumpstate-2026-03-16-16-45-48_ref_pattern",
    )
    ap.add_argument("--compat-raster-preset", default="decoded-template-bbox")
    ap.add_argument(
        "--capture-aabb",
        default="out/decode/dumpstate-2026-03-16-16-45-48_ref_pattern/job_001/002_aabb_f001145_len0504.bin",
    )
    ap.add_argument("--out-dir", default="out/encoder_matrix")
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    replay_sender = Path(__file__).resolve().parent / "replay_sender.py"
    template_dump_dir = repo_root / args.template_dump_dir
    capture_aabb = repo_root / args.capture_aabb
    out_root = repo_root / args.out_dir / time.strftime("%Y%m%d-%H%M%S")
    out_root.mkdir(parents=True, exist_ok=True)

    capture_lz = trimmed_capture_lzma(capture_aabb)
    summary: Dict[str, Dict[str, Dict[str, int]]] = {
        "_meta": {
            "capture_aabb": str(capture_aabb),
            "capture_lzma_len": len(capture_lz),
            "template_dump_dir": str(template_dump_dir),
            "compat_raster_preset": args.compat_raster_preset,
            "reference_encoder": args.reference_encoder,
        }
    }

    for image_str in args.images:
        image = repo_root / image_str
        image_key = image_str
        summary[image_key] = {}
        case_data: Dict[str, bytes] = {}
        for encoder in args.encoders:
            run_dir = run_case(
                replay_sender=replay_sender,
                template_dump_dir=template_dump_dir,
                image=image,
                encoder=encoder,
                compat_raster_preset=args.compat_raster_preset,
                out_root=out_root,
            )
            lz = (run_dir / "lzma.bin").read_bytes()
            case_data[encoder] = lz
            meta = json.loads((run_dir / "meta.json").read_text())
            result = compare_bytes(lz, capture_lz)
            result["run_dir"] = str(run_dir)
            result["lzma_len_meta"] = int(meta.get("lzma_len", len(lz)))
            result["aabb_chunks"] = int(meta.get("aabb_chunks", 0))
            result["frames_total"] = int(meta.get("frames_total", 0))
            summary[image_key][encoder] = result
        if args.reference_encoder in case_data:
            ref = case_data[args.reference_encoder]
            for encoder, lz in case_data.items():
                ref_cmp = compare_bytes(lz, ref)
                summary[image_key][encoder]["reference_encoder"] = args.reference_encoder
                summary[image_key][encoder]["vs_reference_first_diff"] = ref_cmp["first_diff"]
                summary[image_key][encoder]["vs_reference_overlap_equal_bytes"] = ref_cmp["overlap_equal_bytes"]
                summary[image_key][encoder]["vs_reference_tail_delta"] = ref_cmp["tail_delta"]

    summary_path = out_root / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print(summary_path)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
