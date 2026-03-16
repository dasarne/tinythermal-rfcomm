#!/usr/bin/env python3
import argparse
import glob
import json
import math
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


NAME_RE = re.compile(r"(\d{3})_aabb_f(\d+)_len(\d+)\.bin$")


@dataclass
class Chunk:
    path: str
    idx_file: int
    frame: int
    length: int
    data: bytes

    @property
    def chunk_index(self) -> int:
        return self.data[2]

    @property
    def chunk_total(self) -> int:
        return self.data[3]


def bit_reverse_table() -> bytes:
    out = bytearray(256)
    for i in range(256):
        b = i
        r = 0
        for _ in range(8):
            r = (r << 1) | (b & 1)
            b >>= 1
        out[i] = r
    return bytes(out)


REV = bit_reverse_table()


def load_job_chunks(job_dir: str) -> List[Chunk]:
    chunks: List[Chunk] = []
    for p in sorted(glob.glob(os.path.join(job_dir, "*_aabb_*.bin"))):
        m = NAME_RE.search(os.path.basename(p))
        if not m:
            continue
        idx_file = int(m.group(1))
        frame = int(m.group(2))
        length = int(m.group(3))
        data = open(p, "rb").read()
        chunks.append(Chunk(path=p, idx_file=idx_file, frame=frame, length=length, data=data))
    return chunks


def find_target_job(decode_root: str) -> Optional[str]:
    best: Tuple[int, str] = (-1, "")
    for dump_dir in sorted(glob.glob(os.path.join(decode_root, "dumpstate-*"))):
        for job_dir in sorted(glob.glob(os.path.join(dump_dir, "job_*"))):
            n = len(glob.glob(os.path.join(job_dir, "*_aabb_*.bin")))
            if n > best[0]:
                best = (n, job_dir)
    return best[1] if best[0] > 0 else None


def ordered_chunks(chunks: List[Chunk]) -> List[Chunk]:
    return sorted(chunks, key=lambda c: (c.chunk_index, c.frame))


def make_stream(chunks: List[Chunk], skip_per_chunk: int) -> bytes:
    parts = []
    for ch in chunks:
        skip = min(skip_per_chunk, len(ch.data))
        parts.append(ch.data[skip:])
    return b"".join(parts)


def apply_bit_order(data: bytes, lsb_first: bool) -> bytes:
    if not lsb_first:
        return data
    return bytes(REV[b] for b in data)


def to_pbm_bytes(data: bytes, width: int, lsb_first: bool, invert: bool, max_height: int) -> Tuple[int, bytes]:
    if width % 8 != 0:
        raise ValueError("width must be multiple of 8")
    row_bytes = width // 8
    usable_rows = len(data) // row_bytes
    height = min(usable_rows, max_height)
    payload = bytearray(data[: height * row_bytes])
    if lsb_first:
        payload = bytearray(apply_bit_order(payload, True))
    if invert:
        for i in range(len(payload)):
            payload[i] ^= 0xFF
    return height, bytes(payload)


def write_pbm(path: str, width: int, height: int, payload: bytes) -> None:
    with open(path, "wb") as f:
        f.write(f"P4\n{width} {height}\n".encode("ascii"))
        f.write(payload)


def maybe_convert_png(pbm_path: str) -> Optional[str]:
    png_path = pbm_path[:-4] + ".png"
    try:
        subprocess.run(["magick", pbm_path, png_path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return png_path
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Render guessed raster candidates from aabb payloads.")
    ap.add_argument("--decode-root", default="out/decode")
    ap.add_argument("--job-dir", default="", help="Optional explicit job dir (e.g. out/decode/.../job_005)")
    ap.add_argument("--out-dir", default="out/raster_guess")
    ap.add_argument("--max-height", type=int, default=512)
    args = ap.parse_args()

    if args.job_dir:
        job_dir = args.job_dir
    else:
        job_dir = find_target_job(args.decode_root)
        if not job_dir:
            raise SystemExit("No job with aabb payloads found.")

    chunks = load_job_chunks(job_dir)
    if not chunks:
        raise SystemExit(f"No aabb chunks in {job_dir}")
    chunks = ordered_chunks(chunks)

    os.makedirs(args.out_dir, exist_ok=True)
    job_name = job_dir.strip("/").replace("/", "__")
    out_job_dir = os.path.join(args.out_dir, job_name)
    os.makedirs(out_job_dir, exist_ok=True)

    widths = [32, 48, 64, 72, 96, 128, 192, 256, 320, 384]
    skips = [4, 8, 12, 16, 20, 24, 32]
    offsets = [0, 1, 2, 3, 4, 8, 12, 16]

    index: Dict[str, object] = {
        "job_dir": job_dir,
        "chunks": [
            {
                "path": c.path,
                "chunk_index": c.chunk_index,
                "chunk_total": c.chunk_total,
                "len": len(c.data),
                "header16_hex": c.data[:16].hex(),
            }
            for c in chunks
        ],
        "candidates": [],
    }

    candidate_no = 0
    for skip in skips:
        base = make_stream(chunks, skip_per_chunk=skip)
        for offset in offsets:
            if offset >= len(base):
                continue
            stream = base[offset:]
            for width in widths:
                if width % 8 != 0:
                    continue
                for lsb_first in (False, True):
                    for invert in (False, True):
                        height, payload = to_pbm_bytes(
                            stream, width=width, lsb_first=lsb_first, invert=invert, max_height=args.max_height
                        )
                        if height < 8:
                            continue
                        base_name = (
                            f"cand_{candidate_no:04d}_skip{skip:02d}_off{offset:02d}_w{width:03d}"
                            f"_bit{'lsb' if lsb_first else 'msb'}_inv{int(invert)}"
                        )
                        pbm_path = os.path.join(out_job_dir, base_name + ".pbm")
                        write_pbm(pbm_path, width, height, payload)
                        png_path = maybe_convert_png(pbm_path)
                        index["candidates"].append(
                            {
                                "name": base_name,
                                "pbm": pbm_path,
                                "png": png_path,
                                "skip_per_chunk": skip,
                                "stream_offset": offset,
                                "width": width,
                                "height": height,
                                "bit_order": "lsb" if lsb_first else "msb",
                                "invert": invert,
                            }
                        )
                        candidate_no += 1

    with open(os.path.join(out_job_dir, "index.json"), "w") as f:
        json.dump(index, f, indent=2)

    # Also make a tiny markdown index for quick browsing.
    md_path = os.path.join(out_job_dir, "index.md")
    with open(md_path, "w") as f:
        f.write("# Raster Guess Candidates\n\n")
        f.write(f"- source job: `{job_dir}`\n")
        f.write(f"- chunks: {len(chunks)}\n")
        f.write(f"- candidates: {len(index['candidates'])}\n\n")
        f.write("## Chunk Headers\n")
        for ch in index["chunks"]:
            f.write(
                f"- idx {ch['chunk_index']}/{ch['chunk_total']} len={ch['len']} "
                f"header16={ch['header16_hex']}\n"
            )
        f.write("\n## Candidates (first 80)\n")
        for c in index["candidates"][:80]:
            f.write(
                f"- {c['name']} -> {os.path.basename(c['png'] or c['pbm'])}, "
                f"{c['width']}x{c['height']}, skip={c['skip_per_chunk']}, off={c['stream_offset']}, "
                f"{c['bit_order']}, inv={int(c['invert'])}\n"
            )

    print(out_job_dir)
    print(md_path)


if __name__ == "__main__":
    main()
