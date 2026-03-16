#!/usr/bin/env python3
import argparse
import glob
import json
import lzma
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

    @property
    def payload(self) -> bytes:
        # Inner 506-byte frame without leading AA BB, as captured in decode_spp output.
        return self.data[4:]


def load_job_chunks(job_dir: str) -> List[Chunk]:
    out: List[Chunk] = []
    for p in sorted(glob.glob(os.path.join(job_dir, "*_aabb_*.bin"))):
        m = NAME_RE.search(os.path.basename(p))
        if not m:
            continue
        out.append(
            Chunk(
                path=p,
                idx_file=int(m.group(1)),
                frame=int(m.group(2)),
                length=int(m.group(3)),
                data=open(p, "rb").read(),
            )
        )
    return out


def decompress_lzma_alone_best(stream: bytes) -> Tuple[bytes, int]:
    # First try full stream.
    try:
        return lzma.decompress(stream, format=lzma.FORMAT_ALONE), len(stream)
    except Exception:
        pass

    # Fallback: find the shortest prefix that decodes.
    for n in range(13, len(stream) + 1):
        try:
            return lzma.decompress(stream[:n], format=lzma.FORMAT_ALONE), n
        except Exception:
            continue
    raise lzma.LZMAError("could not decode LZMA stream from any prefix")


def parse_btbuf_header(buf: bytes) -> Dict[str, int]:
    if len(buf) < 14:
        raise ValueError("buffer too short for btbuf header")
    return {
        "checksum_le": int.from_bytes(buf[0:2], "little"),
        "page_flags_le": int.from_bytes(buf[2:4], "little"),
        "columns_le": int.from_bytes(buf[4:6], "little"),
        "bytes_per_col": buf[6],
        "left_margin_le": int.from_bytes(buf[8:10], "little"),
        "right_margin_le": int.from_bytes(buf[10:12], "little"),
    }


def render_col_major_to_pbm(
    data: bytes, width: int, bytes_per_col: int, lsb_first: bool, invert: bool, out_pbm: str
) -> Tuple[int, int]:
    if width <= 0 or bytes_per_col <= 0:
        raise ValueError("invalid geometry")
    height = bytes_per_col * 8
    row_bytes = (width + 7) // 8
    raster = bytearray(row_bytes * height)

    for x in range(width):
        col_off = x * bytes_per_col
        for by in range(bytes_per_col):
            v = data[col_off + by]
            for bit in range(8):
                y = (by * 8) + bit
                b = (v >> bit) & 1 if lsb_first else (v >> (7 - bit)) & 1
                if invert:
                    b ^= 1
                if b:
                    pos = (y * row_bytes) + (x // 8)
                    raster[pos] |= 1 << (7 - (x % 8))

    with open(out_pbm, "wb") as f:
        f.write(f"P4\n{width} {height}\n".encode("ascii"))
        f.write(raster)
    return width, height


def maybe_convert_png(pbm_path: str) -> Optional[str]:
    png_path = pbm_path[:-4] + ".png"
    try:
        subprocess.run(["magick", pbm_path, png_path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return png_path
    except Exception:
        return None


def process_job(job_dir: str, out_root: str) -> Dict[str, object]:
    chunks = load_job_chunks(job_dir)
    if not chunks:
        return {"job_dir": job_dir, "status": "skip_no_aabb"}

    chunks = sorted(chunks, key=lambda c: (c.chunk_index, c.frame, c.idx_file))
    payload_cat = b"".join(ch.payload for ch in chunks)

    job_name = job_dir.strip("/").replace("/", "__")
    out_dir = os.path.join(out_root, job_name)
    os.makedirs(out_dir, exist_ok=True)

    result: Dict[str, object] = {
        "job_dir": job_dir,
        "out_dir": out_dir,
        "status": "ok",
        "chunks": [
            {
                "path": ch.path,
                "chunk_index": ch.chunk_index,
                "chunk_total": ch.chunk_total,
                "payload_len": len(ch.payload),
                "header8_hex": ch.data[:8].hex(),
            }
            for ch in chunks
        ],
        "lzma_concat_len": len(payload_cat),
    }

    with open(os.path.join(out_dir, "lzma_concat.bin"), "wb") as f:
        f.write(payload_cat)

    try:
        btbuf, used_len = decompress_lzma_alone_best(payload_cat)
    except Exception as e:
        result["status"] = "decode_error"
        result["error"] = str(e)
        return result

    with open(os.path.join(out_dir, "btbuf.bin"), "wb") as f:
        f.write(btbuf)

    result["lzma_used_len"] = used_len
    result["btbuf_len"] = len(btbuf)
    result["btbuf_prefix32_hex"] = btbuf[:32].hex()

    try:
        hdr = parse_btbuf_header(btbuf)
    except Exception as e:
        result["status"] = "parse_error"
        result["error"] = str(e)
        return result

    result["header"] = hdr

    width = hdr["columns_le"]
    bpc = hdr["bytes_per_col"]
    data_avail = max(0, len(btbuf) - 14)
    data_need = width * bpc
    data_len = min(data_avail, data_need)
    data = btbuf[14 : 14 + data_len]
    result["data_need"] = data_need
    result["data_avail"] = data_avail
    result["data_used"] = data_len

    # If stream is shorter than expected, zero-pad to maintain geometry.
    if data_len < data_need:
        data = data + (b"\x00" * (data_need - data_len))

    renders = []
    for bit_order in ("msb", "lsb"):
        for invert in (0, 1):
            base = f"render_{bit_order}_inv{invert}"
            pbm_path = os.path.join(out_dir, base + ".pbm")
            w, h = render_col_major_to_pbm(
                data=data,
                width=width,
                bytes_per_col=bpc,
                lsb_first=(bit_order == "lsb"),
                invert=bool(invert),
                out_pbm=pbm_path,
            )
            png_path = maybe_convert_png(pbm_path)
            renders.append(
                {
                    "name": base,
                    "bit_order": bit_order,
                    "invert": bool(invert),
                    "width": w,
                    "height": h,
                    "pbm": pbm_path,
                    "png": png_path,
                }
            )
    result["renders"] = renders
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Decode Katasymbol aabb payloads into LZMA btbuf and raster renders.")
    ap.add_argument("--decode-root", default="out/decode")
    ap.add_argument("--out-dir", default="out/btbuf_decode")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    job_dirs = sorted(glob.glob(os.path.join(args.decode_root, "dumpstate-*", "job_*")))
    results = [process_job(job_dir, args.out_dir) for job_dir in job_dirs]
    summary = {
        "decode_root": args.decode_root,
        "out_dir": args.out_dir,
        "jobs_total": len(job_dirs),
        "jobs_processed": len([r for r in results if r.get("status") == "ok"]),
        "results": results,
    }

    out_json = os.path.join(args.out_dir, "summary.json")
    with open(out_json, "w") as f:
        json.dump(summary, f, indent=2)
    print(out_json)


if __name__ == "__main__":
    main()
