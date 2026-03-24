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


def draw_border(draw: ImageDraw.ImageDraw) -> None:
    draw.rectangle((1, 1, WIDTH - 2, HEIGHT - 2), outline=0, width=1)


def img_base() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("L", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(img)
    draw_border(draw)
    return img, draw


def horizontals_only() -> Image.Image:
    img, draw = img_base()
    for y in (4, 8, 12, 16, 48, 80):
        draw.line((1, y, WIDTH - 2, y), fill=0, width=1)
    for x in range(1, WIDTH - 1, 32):
        draw.line((x, 2, x, 56), fill=0, width=1)
    return img


def boxes_only() -> Image.Image:
    img, draw = img_base()
    boxes = [
        (24, 8, 76, 44),
        (118, 8, 170, 44),
        (212, 8, 264, 44),
        (71, 50, 123, 86),
        (165, 50, 217, 86),
    ]
    for x0, y0, x1, y1 in boxes:
        draw.rectangle((x0, y0, x1, y1), outline=0, width=1)
    draw.rectangle((129, 28, 159, 58), outline=0, width=1)
    draw.line((144, 28, 144, 58), fill=0, width=1)
    draw.line((129, 43, 159, 43), fill=0, width=1)
    return img


def horizontals_plus_boxes() -> Image.Image:
    img = horizontals_only()
    draw = ImageDraw.Draw(img)
    draw.rectangle((129, 28, 159, 58), outline=0, width=1)
    draw.line((144, 28, 144, 58), fill=0, width=1)
    draw.line((129, 43, 159, 43), fill=0, width=1)
    for x in (65, 145, 225):
        draw.rectangle((x - 12, 6, x + 12, 18), fill=0)
    return img


def stroke_weights() -> Image.Image:
    img, draw = img_base()
    rows = [(8, 1), (20, 2), (36, 3), (56, 4)]
    for y, width in rows:
        draw.line((16, y, WIDTH - 16, y), fill=0, width=width)
        draw.rectangle((48, y + 6, 96, y + 6 + width), outline=0, width=width)
        draw.line((160, y - 4, 160, y + 16), fill=0, width=width)
    return img


def main() -> None:
    out_dir = Path("out/diagnostics/deductive")
    files = {
        "horizontals_only": horizontals_only(),
        "boxes_only": boxes_only(),
        "horizontals_plus_boxes": horizontals_plus_boxes(),
        "stroke_weights": stroke_weights(),
    }
    manifest: dict[str, object] = {
        "width": WIDTH,
        "height": HEIGHT,
        "files": {},
    }
    for name, img in files.items():
        path = out_dir / f"{name}_{WIDTH}x{HEIGHT}.png"
        save(img, path)
        manifest["files"][name] = str(path)
        print(path)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
