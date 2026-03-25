#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parent
PARENT_DIR = SCRIPT_DIR.parent
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))

from raster_btbuf import analyze_btbuf


def reverse_bits(v: int) -> int:
    out = 0
    for i in range(8):
        out = (out << 1) | ((v >> i) & 1)
    return out


def render_variant(
    btbuf: bytes,
    data_offset: int,
    bit_order: str = "lsb",
    y_shift: int = 0,
    byte_row_shift: int = 0,
) -> Image.Image:
    info = analyze_btbuf(btbuf, data_offset=data_offset)
    if info is None:
        raise ValueError("invalid btbuf")
    width = info["width"]
    height = info["height"]
    bpc = info["bytes_per_col"]
    data = btbuf[data_offset : data_offset + width * bpc]
    img = Image.new("1", (width, height), 1)
    px = img.load()

    row_shift = y_shift % height if height > 0 else 0
    byte_shift = byte_row_shift % bpc if bpc > 0 else 0

    for x in range(width):
        col_off = x * bpc
        col = list(data[col_off : col_off + bpc])
        if bit_order == "msb":
            col = [reverse_bits(v) for v in col]
        if byte_shift:
            col = col[byte_shift:] + col[:byte_shift]
        for by, v in enumerate(col):
            for bit in range(8):
                y = (by * 8) + bit
                if y >= height:
                    continue
                if (v >> bit) & 1:
                    yy = (y + row_shift) % height if row_shift else y
                    px[x, yy] = 0
    return img


def main() -> None:
    ap = argparse.ArgumentParser(description="Render btbuf using multiple vertical/bit-order interpretations.")
    ap.add_argument("btbuf", help="btbuf.bin")
    ap.add_argument("--out-dir", default="", help="Output directory")
    ap.add_argument("--max-y-shift", type=int, default=15)
    ap.add_argument("--include-msb", action="store_true")
    ap.add_argument("--include-byte-row-shifts", action="store_true")
    ap.add_argument("--data-offset", type=int, default=16)
    args = ap.parse_args()

    btbuf = Path(args.btbuf).read_bytes()
    info = analyze_btbuf(btbuf, data_offset=args.data_offset)
    if info is None:
        raise SystemExit("invalid btbuf")

    out_dir = Path(args.out_dir) if args.out_dir else Path("out/render_btbuf_variants")
    out_dir.mkdir(parents=True, exist_ok=True)

    modes = ["lsb"]
    if args.include_msb:
        modes.append("msb")
    byte_shifts = [0]
    if args.include_byte_row_shifts:
        byte_shifts = list(range(info["bytes_per_col"]))

    manifest: dict[str, object] = {
        "btbuf": args.btbuf,
        "width": info["width"],
        "height": info["height"],
        "bytes_per_col": info["bytes_per_col"],
        "data_offset": args.data_offset,
        "variants": [],
    }

    for mode in modes:
        for byte_shift in byte_shifts:
            for y_shift in range(args.max_y_shift + 1):
                name = f"{mode}_y{y_shift:02d}_byte{byte_shift:02d}.png"
                img = render_variant(btbuf, data_offset=args.data_offset, bit_order=mode, y_shift=y_shift, byte_row_shift=byte_shift)
                img.convert("L").save(out_dir / name, format="PNG")
                manifest["variants"].append(
                    {
                        "name": name,
                        "bit_order": mode,
                        "y_shift": y_shift,
                        "byte_row_shift": byte_shift,
                    }
                )

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(out_dir)


if __name__ == "__main__":
    main()
