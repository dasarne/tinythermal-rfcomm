#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


def parse_btbuf(btbuf: bytes) -> dict[str, int]:
    if len(btbuf) < 16:
        raise ValueError("btbuf too short")
    width = int.from_bytes(btbuf[4:6], "little")
    bytes_per_col = btbuf[6]
    if width <= 0 or bytes_per_col <= 0:
        raise ValueError("invalid btbuf geometry")
    return {
        "checksum": int.from_bytes(btbuf[0:2], "little"),
        "page_flags": int.from_bytes(btbuf[2:4], "little"),
        "width": width,
        "bytes_per_col": bytes_per_col,
        "left_margin": int.from_bytes(btbuf[8:10], "little"),
        "right_margin": int.from_bytes(btbuf[10:12], "little"),
        "no_zero_index": btbuf[12],
    }


def column_bytes(btbuf: bytes, x: int, width: int, bytes_per_col: int) -> bytes:
    if x < 0 or x >= width:
        raise IndexError(f"column out of range: {x}")
    start = 16 + (x * bytes_per_col)
    end = start + bytes_per_col
    return btbuf[start:end]


def column_black_rows(col: bytes) -> list[int]:
    rows: list[int] = []
    for by, value in enumerate(col):
        for bit in range(8):
            if (value >> bit) & 1:
                rows.append((by * 8) + bit)
    return rows


def inspect_columns(btbuf: bytes, x_start: int, x_stop: int) -> dict[str, object]:
    hdr = parse_btbuf(btbuf)
    width = hdr["width"]
    bytes_per_col = hdr["bytes_per_col"]
    items = []
    for x in range(max(0, x_start), min(width, x_stop)):
        col = column_bytes(btbuf, x, width, bytes_per_col)
        items.append(
            {
                "x": x,
                "bytes_hex": col.hex(),
                "black_rows": column_black_rows(col),
            }
        )
    return {
        "header": hdr,
        "x_start": max(0, x_start),
        "x_stop": min(width, x_stop),
        "columns": items,
    }


def compare_columns(ref_btbuf: bytes, cand_btbuf: bytes, x_start: int, x_stop: int) -> dict[str, object]:
    ref_hdr = parse_btbuf(ref_btbuf)
    cand_hdr = parse_btbuf(cand_btbuf)
    x0 = max(0, x_start)
    x1 = min(ref_hdr["width"], cand_hdr["width"], x_stop)
    bytes_per_col = min(ref_hdr["bytes_per_col"], cand_hdr["bytes_per_col"])
    diffs = []
    for x in range(x0, x1):
        ref_col = column_bytes(ref_btbuf, x, ref_hdr["width"], ref_hdr["bytes_per_col"])[:bytes_per_col]
        cand_col = column_bytes(cand_btbuf, x, cand_hdr["width"], cand_hdr["bytes_per_col"])[:bytes_per_col]
        if ref_col == cand_col:
            continue
        byte_diffs = []
        for idx, (rb, cb) in enumerate(zip(ref_col, cand_col)):
            if rb != cb:
                byte_diffs.append(
                    {
                        "byte_index": idx,
                        "ref": f"{rb:02x}",
                        "cand": f"{cb:02x}",
                    }
                )
        diffs.append(
            {
                "x": x,
                "ref_hex": ref_col.hex(),
                "cand_hex": cand_col.hex(),
                "ref_black_rows": column_black_rows(ref_col),
                "cand_black_rows": column_black_rows(cand_col),
                "byte_diffs": byte_diffs,
            }
        )
    return {
        "reference_header": ref_hdr,
        "candidate_header": cand_hdr,
        "x_start": x0,
        "x_stop": x1,
        "diff_columns": diffs,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Inspect or compare raw btbuf columns.")
    ap.add_argument("btbuf", help="Primary btbuf.bin path")
    ap.add_argument("--compare", default="", help="Optional second btbuf.bin path")
    ap.add_argument("--x-start", type=int, default=0)
    ap.add_argument("--x-stop", type=int, default=32)
    ap.add_argument("--out", default="", help="Optional JSON output path")
    args = ap.parse_args()

    btbuf = Path(args.btbuf).read_bytes()
    if args.compare:
        data = compare_columns(btbuf, Path(args.compare).read_bytes(), args.x_start, args.x_stop)
    else:
        data = inspect_columns(btbuf, args.x_start, args.x_stop)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(data, indent=2) + "\n")
        print(out_path)
    else:
        print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
