#!/usr/bin/env python3
import argparse
import glob
import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass
class PayloadFile:
    dump: str
    job: int
    idx: int
    cmd: str
    frame: int
    length: int
    path: str
    data: bytes


NAME_RE = re.compile(r"(\d{3})_(aabb|aad1)_f(\d+)_len(\d+)\.bin$")


def parse_payload_files(root: str) -> List[PayloadFile]:
    payloads: List[PayloadFile] = []
    for dump_dir in sorted(glob.glob(os.path.join(root, "dumpstate-*"))):
        if not os.path.isdir(dump_dir):
            continue
        dump = os.path.basename(dump_dir)
        for job_dir in sorted(glob.glob(os.path.join(dump_dir, "job_*"))):
            job_name = os.path.basename(job_dir)
            job = int(job_name.split("_")[-1])
            for p in sorted(glob.glob(os.path.join(job_dir, "*.bin"))):
                m = NAME_RE.search(os.path.basename(p))
                if not m:
                    continue
                idx = int(m.group(1))
                cmd = m.group(2)
                frame = int(m.group(3))
                length = int(m.group(4))
                data = open(p, "rb").read()
                payloads.append(
                    PayloadFile(
                        dump=dump,
                        job=job,
                        idx=idx,
                        cmd=cmd,
                        frame=frame,
                        length=length,
                        path=p,
                        data=data,
                    )
                )
    return payloads


def shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    ent = 0.0
    n = len(data)
    for c in counts:
        if c == 0:
            continue
        p = c / n
        ent -= p * math.log2(p)
    return ent


def diff_positions(a: bytes, b: bytes) -> List[int]:
    n = min(len(a), len(b))
    out = []
    for i in range(n):
        if a[i] != b[i]:
            out.append(i)
    return out


def contiguous_ranges(pos: List[int]) -> List[Tuple[int, int]]:
    if not pos:
        return []
    ranges: List[Tuple[int, int]] = []
    s = pos[0]
    prev = pos[0]
    for p in pos[1:]:
        if p == prev + 1:
            prev = p
            continue
        ranges.append((s, prev))
        s = p
        prev = p
    ranges.append((s, prev))
    return ranges


def decode_aabb_header(data: bytes) -> Dict[str, object]:
    return {
        "b0_1_hex": data[0:2].hex(),
        "chunk_index": data[2],
        "chunk_total": data[3],
        "b4_5_le": int.from_bytes(data[4:6], "little"),
        "b6_7_le": int.from_bytes(data[6:8], "little"),
        "b8_9_le": int.from_bytes(data[8:10], "little"),
        "b10_11_le": int.from_bytes(data[10:12], "little"),
        "b12_19_hex": data[12:20].hex(),
        "payload_prefix_32_hex": data[:32].hex(),
        "tail_entropy_bits_per_byte": round(shannon_entropy(data[20:]), 4),
    }


def build_report(payloads: List[PayloadFile]) -> Dict[str, object]:
    aad1 = [p for p in payloads if p.cmd == "aad1"]
    aabb = [p for p in payloads if p.cmd == "aabb"]

    aad1_hashes = sorted({hashlib.sha256(p.data).hexdigest() for p in aad1})
    aabb_hashes = sorted({hashlib.sha256(p.data).hexdigest() for p in aabb})

    aabb_items = []
    for p in aabb:
        h = hashlib.sha256(p.data).hexdigest()
        aabb_items.append(
            {
                "dump": p.dump,
                "job": p.job,
                "idx": p.idx,
                "frame": p.frame,
                "path": p.path,
                "sha256": h,
                "header": decode_aabb_header(p.data),
            }
        )

    # Canonical picks for useful diffs
    by_key: Dict[Tuple[str, int], List[PayloadFile]] = {}
    for p in aabb:
        by_key.setdefault((p.dump, p.job), []).append(p)

    # Expected jobs we care about
    a_single = by_key.get(("dumpstate-2026-03-15-21-19-38_A", 1), [])
    h_single = by_key.get(("dumpstate-2026-03-15-21-25-34_HELLO", 2), [])
    b_img = by_key.get(("dumpstate-2026-03-15-21-56-49_Bild", 5), [])

    comparisons = []

    def add_cmp(name: str, x: PayloadFile, y: PayloadFile) -> None:
        pos = diff_positions(x.data, y.data)
        comparisons.append(
            {
                "name": name,
                "left": x.path,
                "right": y.path,
                "different_bytes": len(pos),
                "same_bytes": min(len(x.data), len(y.data)) - len(pos),
                "different_ranges": contiguous_ranges(pos),
            }
        )

    if a_single and h_single:
        add_cmp("A_vs_HELLO", a_single[0], h_single[0])

    if len(b_img) >= 3:
        b_img_sorted = sorted(b_img, key=lambda p: p.idx)
        add_cmp("Bild_chunk0_vs_chunk1", b_img_sorted[0], b_img_sorted[1])
        add_cmp("Bild_chunk1_vs_chunk2", b_img_sorted[1], b_img_sorted[2])
        add_cmp("Bild_chunk0_vs_chunk2", b_img_sorted[0], b_img_sorted[2])

    return {
        "counts": {
            "payload_files_total": len(payloads),
            "aad1_count": len(aad1),
            "aabb_count": len(aabb),
            "aad1_unique_sha256": len(aad1_hashes),
            "aabb_unique_sha256": len(aabb_hashes),
        },
        "aad1_unique_sha256": aad1_hashes,
        "aabb_unique_sha256": aabb_hashes,
        "aabb_items": aabb_items,
        "comparisons": comparisons,
    }


def write_markdown(report: Dict[str, object], out_md: str) -> None:
    c = report["counts"]
    lines: List[str] = []
    lines.append("# Payload Analysis")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- payload files total: {c['payload_files_total']}")
    lines.append(f"- aad1 count: {c['aad1_count']}")
    lines.append(f"- aabb count: {c['aabb_count']}")
    lines.append(f"- aad1 unique sha256: {c['aad1_unique_sha256']}")
    lines.append(f"- aabb unique sha256: {c['aabb_unique_sha256']}")
    lines.append("")
    lines.append("## Key Findings")
    lines.append("- `aad1` payload is constant across all observed jobs (same two hashes recurring).")
    lines.append("- `aabb` carries job-specific payload data.")
    lines.append("- `aabb` byte `2` appears to be chunk index, byte `3` appears to be total chunks.")
    lines.append("- In single-chunk jobs (`A`, `HELLO`): header starts with `.. .. 00 01`.")
    lines.append("- In image job: observed three chunks with headers `.. .. 00 03`, `.. .. 01 03`, `.. .. 02 03`.")
    lines.append("")
    lines.append("## AABB Headers")
    for item in report["aabb_items"]:
        h = item["header"]
        lines.append(
            f"- {item['dump']} job_{item['job']:03d} idx={item['idx']} sha={item['sha256'][:16]} "
            f"chunk={h['chunk_index']}/{h['chunk_total']} "
            f"b4_5={h['b4_5_le']} b6_7={h['b6_7_le']} b8_9={h['b8_9_le']} b10_11={h['b10_11_le']} "
            f"entropy_tail={h['tail_entropy_bits_per_byte']}"
        )
    lines.append("")
    lines.append("## Comparisons")
    for cmp_item in report["comparisons"]:
        lines.append(
            f"- {cmp_item['name']}: different_bytes={cmp_item['different_bytes']} same_bytes={cmp_item['same_bytes']} "
            f"ranges={cmp_item['different_ranges'][:8]}"
        )
    lines.append("")
    with open(out_md, "w") as f:
        f.write("\n".join(lines) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Analyze extracted aad1/aabb payloads.")
    ap.add_argument("--decode-root", default="out/decode", help="Root directory from decode_spp.py")
    ap.add_argument("--out-dir", default="out/analysis", help="Output directory")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    payloads = parse_payload_files(args.decode_root)
    report = build_report(payloads)

    out_json = os.path.join(args.out_dir, "payload_report.json")
    out_md = os.path.join(args.out_dir, "payload_report.md")
    with open(out_json, "w") as f:
        json.dump(report, f, indent=2)
    write_markdown(report, out_md)

    print(out_json)
    print(out_md)


if __name__ == "__main__":
    main()
