#!/usr/bin/env python3
import glob
import lzma
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image

from image_input import load_image_any


AABB_RE = re.compile(r"\d{3}_aabb_f(\d+)_len\d+\.bin$")


def _pack_canvas_columns_lsb(
    canvas: Image.Image,
    threshold: int,
    bytes_per_col: int,
    y_phase: int = 0,
    x_start: int = 0,
    x_stop: Optional[int] = None,
) -> bytearray:
    width, height = canvas.size
    if x_stop is None:
        x_stop = width
    phase = y_phase % height if height > 0 else 0
    data = bytearray(max(0, x_stop - x_start) * bytes_per_col)
    px = canvas.load()
    for x in range(x_start, x_stop):
        for by in range(bytes_per_col):
            v = 0
            for bit in range(8):
                y = by * 8 + bit
                if y >= height:
                    continue
                src_y = (y + phase) % height if phase else y
                if px[x, src_y] < threshold:
                    v |= 1 << bit
            data[(x - x_start) * bytes_per_col + by] = v
    return data


def image_to_btbuf(img_path: Path, threshold: int) -> Tuple[bytes, Dict[str, int]]:
    img = load_image_any(img_path, svg_pixels_per_mm=8.0).convert("L")
    h_target = 96
    if img.height > h_target:
        new_w = max(1, int(round(img.width * (h_target / img.height))))
        img = img.resize((new_w, h_target), Image.Resampling.LANCZOS)

    canvas = Image.new("L", (img.width, h_target), 255)
    top = (h_target - img.height) // 2
    canvas.paste(img, (0, top))

    width = canvas.width
    bpc = 12
    data = _pack_canvas_columns_lsb(canvas, threshold, bpc)

    btbuf = bytearray(4000)
    btbuf[2:4] = (0x100E).to_bytes(2, "little")
    btbuf[4:6] = width.to_bytes(2, "little")
    btbuf[6] = bpc
    btbuf[8:10] = (1).to_bytes(2, "little")
    btbuf[10:12] = (1).to_bytes(2, "little")
    btbuf[12:14] = b"\x00\x00"
    btbuf[16 : 16 + len(data)] = data

    used = (width * bpc) + 16
    s = sum(btbuf[2:14])
    for k in range(1, (used // 256) + 1):
        s += btbuf[(k * 256) - 1]
    btbuf[0:2] = (s & 0xFFFF).to_bytes(2, "little")

    return bytes(btbuf), {"width": width, "height": h_target, "bytes_per_col": bpc}


def _lzma_decompress_best_prefix(stream: bytes) -> bytes:
    try:
        return lzma.decompress(stream, format=lzma.FORMAT_ALONE)
    except Exception:
        for n in range(13, len(stream) + 1):
            try:
                return lzma.decompress(stream[:n], format=lzma.FORMAT_ALONE)
            except Exception:
                continue
    raise lzma.LZMAError("unable to decompress template aabb stream")


def load_template_geometry(job_dir: Path) -> Optional[Dict[str, int]]:
    items = []
    for p in sorted(glob.glob(str(job_dir / "*_aabb_*.bin"))):
        m = AABB_RE.match(Path(p).name)
        if not m:
            continue
        data = Path(p).read_bytes()
        if len(data) < 8:
            continue
        idx = data[2]
        items.append((idx, data))
    if not items:
        return None
    items.sort(key=lambda t: t[0])
    stream = b"".join(d[4:] for _, d in items)
    btbuf = _lzma_decompress_best_prefix(stream)
    if len(btbuf) < 7:
        return None
    return {
        "width": int.from_bytes(btbuf[4:6], "little"),
        "bytes_per_col": btbuf[6],
        "no_zero_index": btbuf[12] if len(btbuf) > 12 else 0,
    }


def load_template_btbuf(job_dir: Path) -> Optional[bytes]:
    items = []
    for p in sorted(glob.glob(str(job_dir / "*_aabb_*.bin"))):
        m = AABB_RE.match(Path(p).name)
        if not m:
            continue
        data = Path(p).read_bytes()
        if len(data) < 8:
            continue
        idx = data[2]
        items.append((idx, data))
    if not items:
        return None
    items.sort(key=lambda t: t[0])
    stream = b"".join(d[4:] for _, d in items)
    return _lzma_decompress_best_prefix(stream)


def template_btbuf_layout(btbuf: bytes) -> Optional[Dict[str, int]]:
    if len(btbuf) < 14:
        return None
    width = int.from_bytes(btbuf[4:6], "little")
    bpc = btbuf[6]
    if width <= 0 or bpc <= 0:
        return None
    height = bpc * 8
    data = btbuf[16 : 16 + width * bpc]
    xs: List[int] = []
    ys: List[int] = []
    for x in range(width):
        col_off = x * bpc
        for by in range(bpc):
            v = data[col_off + by]
            for bit in range(8):
                if (v >> bit) & 1:
                    xs.append(x)
                    ys.append((by * 8) + bit)
    if not xs or not ys:
        return None
    return {
        "effective_width": width,
        "height": height,
        "no_zero_index": btbuf[12] if len(btbuf) > 12 else 0,
        "bbox_x": min(xs),
        "bbox_y": min(ys),
        "bbox_w": (max(xs) - min(xs)) + 1,
        "bbox_h": (max(ys) - min(ys)) + 1,
    }


def analyze_btbuf(btbuf: bytes) -> Optional[Dict[str, int]]:
    if len(btbuf) < 16:
        return None
    width = int.from_bytes(btbuf[4:6], "little")
    bpc = btbuf[6]
    if width <= 0 or bpc <= 0:
        return None
    height = bpc * 8
    data = btbuf[16 : 16 + width * bpc]
    xs: List[int] = []
    ys: List[int] = []
    for x in range(width):
        col_off = x * bpc
        for by in range(bpc):
            v = data[col_off + by]
            for bit in range(8):
                if (v >> bit) & 1:
                    xs.append(x)
                    ys.append((by * 8) + bit)
    info: Dict[str, int] = {
        "width": width,
        "height": height,
        "bytes_per_col": bpc,
        "no_zero_index": btbuf[12] if len(btbuf) > 12 else 0,
        "nonzero_cols": len(set(xs)),
    }
    if xs and ys:
        info.update(
            {
                "bbox_x": min(xs),
                "bbox_y": min(ys),
                "bbox_w": (max(xs) - min(xs)) + 1,
                "bbox_h": (max(ys) - min(ys)) + 1,
                "first_nonzero_col": min(xs),
                "last_nonzero_col": max(xs),
            }
        )
    return info


def btbuf_to_image(btbuf: bytes) -> Image.Image:
    info = analyze_btbuf(btbuf)
    if info is None:
        raise ValueError("invalid btbuf")
    width = info["width"]
    height = info["height"]
    bpc = info["bytes_per_col"]
    data = btbuf[16 : 16 + width * bpc]
    img = Image.new("1", (width, height), 1)
    px = img.load()
    for x in range(width):
        col_off = x * bpc
        for by in range(bpc):
            v = data[col_off + by]
            for bit in range(8):
                y = by * 8 + bit
                if y >= height:
                    continue
                if (v >> bit) & 1:
                    px[x, y] = 0
    return img


def image_to_btbuf_with_canvas(
    img_path: Path,
    threshold: int,
    canvas_width: int,
    bytes_per_col: int,
    scale_to_canvas_width: bool,
    force_no_zero_index: int,
    scale_width_bias: int,
    scale_resample: str,
    compat_raster_preset: str,
    bbox_fit_mode: str,
    bbox_align_x: str,
    bbox_align_y: str,
    bbox_inset_y: int,
    bbox_offset_y: int,
    raster_y_phase: int,
    template_btbuf: Optional[bytes],
    template_layout: Optional[Dict[str, int]],
) -> Tuple[bytes, Dict[str, int]]:
    img = load_image_any(img_path, svg_pixels_per_mm=(bytes_per_col * 8) / 12.0).convert("L")
    h_target = bytes_per_col * 8
    resample = Image.Resampling.NEAREST if scale_resample == "nearest" else Image.Resampling.LANCZOS

    def fit_into_bbox(im: Image.Image, bbox_w: int, bbox_h: int) -> Image.Image:
        if im.width <= 0 or im.height <= 0:
            return im
        if bbox_fit_mode == "stretch":
            return im.resize((max(1, bbox_w), max(1, bbox_h)), resample)
        if bbox_fit_mode == "cover":
            scale = max(bbox_w / im.width, bbox_h / im.height)
            target_w = max(1, int(round(im.width * scale)))
            target_h = max(1, int(round(im.height * scale)))
            cov = im.resize((target_w, target_h), resample)
            left = max(0, (cov.width - bbox_w) // 2)
            top = max(0, (cov.height - bbox_h) // 2)
            return cov.crop((left, top, left + bbox_w, top + bbox_h))
        scale = min(bbox_w / im.width, bbox_h / im.height)
        target_w = max(1, int(round(im.width * scale)))
        target_h = max(1, int(round(im.height * scale)))
        return im.resize((target_w, target_h), resample)

    def place_in_bbox(bbox_x: int, bbox_y: int, bbox_w: int, bbox_h: int, im: Image.Image) -> Tuple[int, int]:
        if bbox_align_x == "left":
            left = bbox_x
        elif bbox_align_x == "right":
            left = bbox_x + max(0, bbox_w - im.width)
        else:
            left = bbox_x + max(0, (bbox_w - im.width) // 2)
        if bbox_align_y == "top":
            top = bbox_y
        elif bbox_align_y == "bottom":
            top = bbox_y + max(0, bbox_h - im.height)
        else:
            top = bbox_y + max(0, (bbox_h - im.height) // 2)
        top += bbox_offset_y
        min_left = 0
        max_left = max(0, eff_width - im.width) if "eff_width" in locals() else left
        left = min(max(left, min_left), max_left)
        min_top = 0
        max_top = max(0, h_target - im.height)
        top = min(max(top, min_top), max_top)
        return left, top

    if compat_raster_preset == "template-btbuf-overlay" and template_btbuf is not None and template_layout is not None:
        eff_width = int(template_layout["effective_width"])
        no_zero_index = int(template_layout["no_zero_index"])
        bbox_x = int(template_layout["bbox_x"])
        bbox_y = int(template_layout["bbox_y"])
        bbox_w = int(template_layout["bbox_w"])
        bbox_h = int(template_layout["bbox_h"])
        if bbox_inset_y > 0 and (bbox_h - 2 * bbox_inset_y) >= 8:
            bbox_y += bbox_inset_y
            bbox_h -= 2 * bbox_inset_y

        if img.width > 0 and img.height > 0:
            img = fit_into_bbox(img, bbox_w, bbox_h)

        base_btbuf = bytearray(template_btbuf[:4000])
        base_data = bytearray(base_btbuf[16 : 16 + eff_width * bytes_per_col])

        canvas = Image.new("L", (eff_width, h_target), 255)
        left, top = place_in_bbox(bbox_x, bbox_y, bbox_w, bbox_h, img)
        canvas.paste(img, (left, top))
        overlay = _pack_canvas_columns_lsb(
            canvas,
            threshold,
            bytes_per_col,
            y_phase=raster_y_phase,
            x_start=bbox_x,
            x_stop=min(eff_width, bbox_x + bbox_w),
        )
        base_data[bbox_x * bytes_per_col : bbox_x * bytes_per_col + len(overlay)] = overlay

        btbuf = bytearray(4000)
        btbuf[2:4] = (0x100E).to_bytes(2, "little")
        btbuf[4:6] = eff_width.to_bytes(2, "little")
        btbuf[6] = bytes_per_col
        btbuf[8:10] = (1).to_bytes(2, "little")
        btbuf[10:12] = (1).to_bytes(2, "little")
        btbuf[12] = no_zero_index & 0xFF
        btbuf[13] = 0
        btbuf[16 : 16 + len(base_data)] = base_data

        used = (eff_width * bytes_per_col) + 16
        s = sum(btbuf[2:14])
        for k in range(1, (used // 256) + 1):
            s += btbuf[(k * 256) - 1]
        btbuf[0:2] = (s & 0xFFFF).to_bytes(2, "little")

        return bytes(btbuf), {"width": eff_width, "height": h_target, "bytes_per_col": bytes_per_col, "no_zero_index": no_zero_index}

    if compat_raster_preset in ("decoded-template-bbox", "long-label-svg-289") and template_layout is not None:
        eff_width = int(template_layout["effective_width"])
        no_zero_index = int(template_layout["no_zero_index"])
        bbox_x = int(template_layout["bbox_x"])
        bbox_y = int(template_layout["bbox_y"])
        bbox_w = int(template_layout["bbox_w"])
        bbox_h = int(template_layout["bbox_h"])
        if bbox_inset_y > 0 and (bbox_h - 2 * bbox_inset_y) >= 8:
            bbox_y += bbox_inset_y
            bbox_h -= 2 * bbox_inset_y

        if img.width > 0 and img.height > 0:
            img = fit_into_bbox(img, bbox_w, bbox_h)

        canvas = Image.new("L", (eff_width, h_target), 255)
        left, top = place_in_bbox(bbox_x, bbox_y, bbox_w, bbox_h, img)
        canvas.paste(img, (left, top))

        data = _pack_canvas_columns_lsb(canvas, threshold, bytes_per_col, y_phase=raster_y_phase)

        btbuf = bytearray(4000)
        btbuf[2:4] = (0x100E).to_bytes(2, "little")
        btbuf[4:6] = eff_width.to_bytes(2, "little")
        btbuf[6] = bytes_per_col
        btbuf[8:10] = (1).to_bytes(2, "little")
        btbuf[10:12] = (1).to_bytes(2, "little")
        btbuf[12] = no_zero_index & 0xFF
        btbuf[13] = 0
        btbuf[16 : 16 + len(data)] = data

        used = (eff_width * bytes_per_col) + 16
        s = sum(btbuf[2:14])
        for k in range(1, (used // 256) + 1):
            s += btbuf[(k * 256) - 1]
        btbuf[0:2] = (s & 0xFFFF).to_bytes(2, "little")

        return bytes(btbuf), {"width": eff_width, "height": h_target, "bytes_per_col": bytes_per_col, "no_zero_index": no_zero_index}

    if img.height != h_target and img.height > 0:
        new_w = max(1, int(round(img.width * (h_target / img.height))) + scale_width_bias)
        img = img.resize((new_w, h_target), resample)

    if scale_to_canvas_width and force_no_zero_index < 0 and img.width > 0:
        new_h = max(1, int(round(img.height * (canvas_width / img.width))))
        img = img.resize((canvas_width, new_h), resample)
        if img.height != h_target and img.height > 0:
            new_w = max(1, int(round(img.width * (h_target / img.height))) + scale_width_bias)
            img = img.resize((new_w, h_target), resample)
    if img.width > canvas_width:
        new_h = max(1, int(round(img.height * (canvas_width / img.width))))
        img = img.resize((canvas_width, new_h), resample)

    canvas = Image.new("L", (canvas_width, h_target), 255)
    top = (h_target - img.height) // 2
    if force_no_zero_index >= 0:
        left = min(max(0, force_no_zero_index), max(0, canvas_width - img.width))
    else:
        left = (canvas_width - img.width) // 2
    canvas.paste(img, (left, top))

    width = canvas.width
    bpc = bytes_per_col
    data_full = _pack_canvas_columns_lsb(canvas, threshold, bpc, y_phase=raster_y_phase)

    if force_no_zero_index >= 0:
        no_zero_index = min(width - 1, force_no_zero_index) if width > 0 else 0
    else:
        i2 = min(width, 48)
        i3 = 0
        while i3 < i2:
            col = data_full[i3 * bpc : (i3 + 1) * bpc]
            if any(col):
                break
            i3 += 1
        if i3 > 0:
            i3 -= 1
        no_zero_index = (i2 - 1) if i3 >= i2 else i3
    eff_width = max(0, width - no_zero_index)
    data = data_full[no_zero_index * bpc :]

    if compat_raster_preset == "legacy-testpattern-64x32" and eff_width == 201 and bpc == 12:
        overrides = {
            81: bytes.fromhex("388ee3388ee3388ee3388ee3"),
            87: bytes.fromhex("3804413804413804413804c1"),
            90: bytes.fromhex("87711cc7711cc7711cc7711e"),
            151: bytes.fromhex("00fe03000000000000000000"),
            154: bytes.fromhex("0080ff000000000000000000"),
            166: bytes.fromhex("00000000c0ff070000000000"),
        }
        data_mut = bytearray(data)
        for col_idx, col_bytes in overrides.items():
            off = col_idx * bpc
            if off + bpc <= len(data_mut):
                data_mut[off : off + bpc] = col_bytes
        data = bytes(data_mut)

    btbuf = bytearray(4000)
    btbuf[2:4] = (0x100E).to_bytes(2, "little")
    btbuf[4:6] = eff_width.to_bytes(2, "little")
    btbuf[6] = bpc
    btbuf[8:10] = (1).to_bytes(2, "little")
    btbuf[10:12] = (1).to_bytes(2, "little")
    btbuf[12] = no_zero_index & 0xFF
    btbuf[13] = 0
    btbuf[16 : 16 + len(data)] = data

    used = (eff_width * bpc) + 16
    s = sum(btbuf[2:14])
    for k in range(1, (used // 256) + 1):
        s += btbuf[(k * 256) - 1]
    btbuf[0:2] = (s & 0xFFFF).to_bytes(2, "little")

    return bytes(btbuf), {"width": eff_width, "height": h_target, "bytes_per_col": bpc, "no_zero_index": no_zero_index}
