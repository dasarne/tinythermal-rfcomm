#!/usr/bin/env python3
import json
from pathlib import Path

from PIL import Image, ImageDraw


WIDTH = 289
HEIGHT = 96
MM_WIDTH = 36.0
MM_HEIGHT = 12.0
X_DPI = WIDTH * 25.4 / MM_WIDTH
Y_DPI = HEIGHT * 25.4 / MM_HEIGHT


def save(img: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG", dpi=(X_DPI, Y_DPI))


def main() -> None:
    out_dir = Path("out/diagnostics/wrap_bands")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "wrap_bands_289x96.png"

    img = Image.new("L", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(img)
    draw.rectangle((1, 1, WIDTH - 2, HEIGHT - 2), outline=0, width=1)

    bands: list[dict[str, int]] = []
    left = 24
    right = 120

    # Each 8-row band gets a unique horizontal segment pattern.
    # If a band wraps into another zone on paper, the signature should stay recognizable.
    for band in range(12):
        y0 = band * 8
        y_mid = y0 + 4
        seg_len = 10 + (band * 4)
        gap = 6 + (band % 3)

        x = left
        segments = 1 + (band % 4)
        for idx in range(segments):
            x0 = x + idx * (seg_len + gap)
            x1 = min(x0 + seg_len, right)
            draw.line((x0, y_mid, x1, y_mid), fill=0, width=1)

        # One short vertical tick unique to the band.
        tick_x = 150 + (band * 9)
        draw.line((tick_x, y0 + 2, tick_x, min(y0 + 6, HEIGHT - 2)), fill=0, width=1)

        # Band index marker family on the right side:
        # 0 dots for band 0, 1 dot for band 1, ..., cycling every 4 bands.
        dots = band % 4
        for d in range(dots):
            dot_x = 228 + (d * 5)
            draw.rectangle((dot_x, y0 + 3, dot_x + 1, y0 + 4), fill=0)

        bands.append(
            {
                "band": band,
                "y_start": y0,
                "y_mid": y_mid,
                "segments": segments,
                "segment_len": seg_len,
                "tick_x": tick_x,
                "dots": dots,
            }
        )

    manifest = {
        "output": str(out_path),
        "purpose": "Identify whether vertical wrap follows 8-row band boundaries or a fixed partial-zone mapping.",
        "bands": bands,
    }

    save(img, out_path)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(out_path)


if __name__ == "__main__":
    main()
