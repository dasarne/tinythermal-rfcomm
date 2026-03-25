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


def base() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("L", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(img)
    draw.rectangle((1, 1, WIDTH - 2, HEIGHT - 2), outline=0, width=1)
    return img, draw


def make_wrap_probe() -> tuple[Image.Image, dict[str, object]]:
    img, draw = base()

    top_band_rows = [4, 8, 12]
    bottom_band_rows = [83, 87, 91]

    # Sparse unique structures at the top.
    for y in top_band_rows:
        draw.line((18, y, 90, y), fill=0, width=1)
    draw.line((54, 3, 54, 16), fill=0, width=1)

    # Different unique structures at the bottom so wrap becomes obvious.
    for y in bottom_band_rows:
        draw.line((180, y, 258, y), fill=0, width=1)
    draw.rectangle((208, 80, 224, 92), outline=0, width=1)
    draw.line((216, 80, 216, 92), fill=0, width=1)

    # Middle reference that should not participate in wrap.
    draw.line((120, 40, 168, 40), fill=0, width=1)
    draw.line((144, 32, 144, 48), fill=0, width=1)

    manifest = {
        "top_band_rows": top_band_rows,
        "bottom_band_rows": bottom_band_rows,
        "purpose": "Isolate vertical wrap by separating top-only and bottom-only structures.",
    }
    return img, manifest


def main() -> None:
    out_dir = Path("out/diagnostics/wrap")
    img, manifest = make_wrap_probe()
    out_path = out_dir / "wrap_probe_289x96.png"
    save(img, out_path)
    manifest["output"] = str(out_path)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(out_path)


if __name__ == "__main__":
    main()
