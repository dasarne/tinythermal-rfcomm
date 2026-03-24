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


def make_grid() -> Image.Image:
    img = Image.new("L", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(img)
    draw.rectangle((1, 1, WIDTH - 2, HEIGHT - 2), outline=0, width=1)

    x0 = 18
    segment_len = 44
    col_gap = 18
    row_gap = 6
    top0 = 8
    left0 = 18

    # Columns vary line count: 2, 3, 5
    line_counts = [2, 3, 5]
    # Rows vary spacing: 2, 3, 4 pixels
    spacings = [2, 3, 4]

    cells = []
    for r, spacing in enumerate(spacings):
        for c, count in enumerate(line_counts):
            cell_x = left0 + c * (segment_len + col_gap)
            cell_y = top0 + r * (18 + row_gap)
            for i in range(count):
                y = cell_y + i * spacing
                draw.line((cell_x, y, cell_x + segment_len, y), fill=0, width=1)
            # small label tick underneath each cell for orientation
            draw.line((cell_x + (segment_len // 2), cell_y + 10, cell_x + (segment_len // 2), cell_y + 14), fill=0, width=1)
            cells.append(
                {
                    "x": cell_x,
                    "y": cell_y,
                    "line_count": count,
                    "spacing": spacing,
                }
            )

    # Lower reference line for wrap visibility.
    draw.line((1, 80, 262, 80), fill=0, width=1)
    return img, cells


def main() -> None:
    out_dir = Path("out/diagnostics/parallel_grid")
    img, cells = make_grid()
    out_path = out_dir / "parallel_line_grid_289x96.png"
    save(img, out_path)
    manifest = {
        "output": str(out_path),
        "columns_line_counts": [2, 3, 5],
        "rows_spacings": [2, 3, 4],
        "cells": cells,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(out_path)


if __name__ == "__main__":
    main()
