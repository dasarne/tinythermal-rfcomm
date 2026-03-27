#!/usr/bin/env python3
import argparse
import copy
import glob
import json
import lzma
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from image_input import get_raster_size_mm, get_svg_size_mm, load_image_any


MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$", re.IGNORECASE)
DEFAULT_CONFIG = {
    "printer": {
        "mac": "",
        "auto_discover": True,
        "bt_preflight": False,
        "auto_scan_seconds": 4,
        "name_patterns": ["katasymbol", "t0"],
        "channel": 1,
        "channels": "1,2,3",
        "connect_timeout": 5.0,
        "recv_timeout": 0.2,
        "timing_scale": 0.001,
        "delay_ms": 30,
    },
    "template": {
        "dump_dir": "",
        "job": 5,
    },
    "image": {
        "threshold": 125,
        "prepare": True,
        "svg_pixels_per_mm": 8.0,
        "autocontrast": True,
        "crop_content": True,
        "despeckle": False,
        "rotate": "auto",
        "fit_mode": "shrink",
        "dither": "auto",
        "align": "center",
        "offset_x": 0,
        "offset_y": 0,
        "head_height": 0,
        "prepared_image_out": "",
    },
    "transfer": {
        "post_frames_after_aa10": 0,
        "lzma_encoder": "java",
        "compat_raster_preset": "decoded-template-bbox",
        "long_label_svg_preset": False,
        "long_label_bitmap_preset": False,
        "bbox_fit_mode": "contain",
        "bbox_align_x": "center",
        "bbox_align_y": "center",
        "bbox_inset_y": 4,
        "bbox_offset_y": 0,
        "raster_y_phase": 0,
        "scale_to_canvas_width": True,
        "use_template_nozero": True,
        "keep_template_aabb": False,
    },
    "output": {
        "out_dir": "out/replay_sender",
    },
}


def run(cmd: list[str]) -> int:
    proc = subprocess.run(cmd)
    return proc.returncode


def run_capture(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def default_config_path() -> Path:
    env_path = os.environ.get("KATASYMBOL_PRINT_CONFIG", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return Path(".katasymbol_print.json")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def ensure_config_exists(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
    print(f"created default config: {path}")


def load_config(path: Path) -> dict[str, Any]:
    ensure_config_exists(path)
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON in config {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SystemExit(f"config root must be object: {path}")
    return deep_merge(DEFAULT_CONFIG, raw)


def cfg_get(cfg: dict[str, Any], dotted_key: str) -> Any:
    cur: Any = cfg
    for part in dotted_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise SystemExit(f"missing config key: {dotted_key}")
        cur = cur[part]
    return cur


def _list_template_candidates(default_root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for dump_dir in sorted(default_root.glob("dumpstate-*")):
        for job_dir in sorted(dump_dir.glob("job_*")):
            aabb_files = sorted(job_dir.glob("*_aabb_*.bin"))
            if not aabb_files:
                continue
            job = int(job_dir.name.split("_")[-1])
            geom = load_template_geometry(dump_dir, job)
            try:
                mtime = max(p.stat().st_mtime for p in aabb_files)
            except OSError:
                mtime = 0.0
            out.append(
                {
                    "dump_dir": dump_dir,
                    "job": job,
                    "width": int(geom["width"]),
                    "bytes_per_col": int(geom["bytes_per_col"]),
                    "no_zero_index": int(geom["no_zero_index"]),
                    "mtime": mtime,
                }
            )
    return out


def find_auto_template(default_root: Path, prefer_long: bool) -> tuple[Path, int] | None:
    candidates = _list_template_candidates(default_root)
    if not candidates:
        return None

    def score(c: dict[str, Any]) -> tuple[int, int, float]:
        width = int(c["width"])
        nozero = int(c["no_zero_index"])
        dump_name = str(c["dump_dir"].name).lower()
        s = 0
        if prefer_long:
            if width == 289 and nozero == 23:
                s += 100
            if "inkscapetest" in dump_name:
                s += 20
            if width == 209 and nozero == 23:
                s += 5
        else:
            if width == 209 and nozero == 23:
                s += 100
            if "ref_pattern" in dump_name:
                s += 20
            if width == 201 and nozero in (8, 23):
                s += 5
        if width == 332:
            s -= 50
        return (s, width, float(c["mtime"]))

    best = max(candidates, key=score)
    return Path(best["dump_dir"]), int(best["job"])


def load_template_geometry(dump_dir: Path, template_job: int) -> dict:
    job_dir = dump_dir / f"job_{template_job:03d}"
    aabb_files = sorted(job_dir.glob("*_aabb_*.bin"))
    if not aabb_files:
        # fallback sane defaults
        return {"width": 201, "bytes_per_col": 12, "no_zero_index": 0}
    chunks = []
    for p in aabb_files:
        d = p.read_bytes()
        if len(d) < 8:
            continue
        chunks.append((d[2], d[4:]))  # idx, payload body
    if not chunks:
        return {"width": 201, "bytes_per_col": 12, "no_zero_index": 0}
    chunks.sort(key=lambda t: t[0])
    stream = b"".join(c[1] for c in chunks)
    btbuf = None
    try:
        btbuf = lzma.decompress(stream, format=lzma.FORMAT_ALONE)
    except Exception:
        # some captures have zero padding; find decodable prefix
        for n in range(13, len(stream) + 1):
            try:
                btbuf = lzma.decompress(stream[:n], format=lzma.FORMAT_ALONE)
                break
            except Exception:
                continue
    if not btbuf or len(btbuf) < 13:
        return {"width": 201, "bytes_per_col": 12, "no_zero_index": 0}
    return {
        "width": int.from_bytes(btbuf[4:6], "little"),
        "bytes_per_col": int(btbuf[6]),
        "no_zero_index": int(btbuf[12]),
    }


def is_strict_bw(im: Image.Image) -> bool:
    if im.mode == "1":
        return True
    g = im.convert("L")
    hist = g.histogram()
    for i, n in enumerate(hist):
        if n and i not in (0, 255):
            return False
    return True


def choose_rotation_auto(im: Image.Image, head_height: int) -> int:
    # Keep long edge in print-length direction (x-axis).
    # Landscape stays as-is, portrait rotates to landscape.
    if im.width >= im.height:
        return 0
    return 90


def is_auto_long_label_size_mm_candidate(width_mm: float, height_mm: float) -> bool:
    # Conservative heuristic for the currently validated physical long-label case.
    # Keep this size-based so both raster inputs (via DPI) and SVG inputs (via mm)
    # can share the same everyday auto-selection path.
    if height_mm <= 0:
        return False
    aspect = width_mm / height_mm
    if aspect < 2.0:
        return False
    if not (10.0 <= height_mm <= 14.5):
        return False
    if width_mm < 24.0:
        return False
    return True


def is_auto_long_label_bitmap_candidate(src_path: Path) -> bool:
    if src_path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        return False
    size_mm = get_raster_size_mm(src_path)
    if size_mm is None:
        return False
    return is_auto_long_label_size_mm_candidate(*size_mm)


def is_auto_long_label_svg_candidate(src_path: Path) -> bool:
    if src_path.suffix.lower() != ".svg":
        return False
    size_mm = get_svg_size_mm(src_path)
    if size_mm is None:
        return False
    return is_auto_long_label_size_mm_candidate(*size_mm)


def despeckle_bw(bw: Image.Image, min_neighbors: int = 2) -> Image.Image:
    src = bw.convert("1")
    w, h = src.size
    in_px = src.load()
    out = src.copy()
    out_px = out.load()
    for y in range(h):
        for x in range(w):
            if in_px[x, y] != 0:
                continue
            neighbors = 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx = x + dx
                    ny = y + dy
                    if nx < 0 or ny < 0 or nx >= w or ny >= h:
                        continue
                    if in_px[nx, ny] == 0:
                        neighbors += 1
            if neighbors < min_neighbors:
                out_px[x, y] = 255
    return out


def prepare_image(
    src_path: Path,
    out_path: Path,
    head_height: int,
    svg_pixels_per_mm: float,
    autocontrast: bool,
    crop_content: bool,
    despeckle: bool,
    rotate_mode: str,
    fit_mode: str,
    dither_mode: str,
    threshold: int,
    align: str,
    offset_x: int,
    offset_y: int,
) -> dict:
    im0 = load_image_any(src_path, svg_pixels_per_mm=svg_pixels_per_mm)
    src_bw = is_strict_bw(im0)
    im = im0.convert("RGB")

    rot = 0
    if rotate_mode == "auto":
        rot = choose_rotation_auto(im, head_height)
    else:
        rot = int(rotate_mode)
    if rot:
        im = im.rotate(rot, expand=True)

    # grayscale workspace
    g = im.convert("L")
    if autocontrast:
        g = ImageOps.autocontrast(g)

    # vertical fit to print head
    if fit_mode == "shrink":
        if g.height > head_height:
            nw = max(1, round(g.width * (head_height / g.height)))
            g = g.resize((nw, head_height), Image.Resampling.LANCZOS)
    elif fit_mode == "fit":
        if g.height != head_height:
            nw = max(1, round(g.width * (head_height / g.height)))
            g = g.resize((nw, head_height), Image.Resampling.LANCZOS)
    elif fit_mode == "stretch":
        if g.height != head_height:
            g = g.resize((g.width, head_height), Image.Resampling.LANCZOS)

    # map to bw
    if dither_mode == "auto":
        mode = "threshold" if src_bw else "floyd"
    else:
        mode = dither_mode
    if mode == "threshold":
        bw = g.point(lambda p: 0 if p < threshold else 255, mode="L").convert("1", dither=Image.Dither.NONE)
    elif mode == "floyd":
        bw = g.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    elif mode == "ordered":
        bw = g.convert("1", dither=Image.Dither.ORDERED)
    else:
        raise SystemExit(f"unsupported dither mode: {mode}")

    if despeckle:
        bw = despeckle_bw(bw)

    crop_bbox = None
    if crop_content:
        inv = ImageOps.invert(bw.convert("L"))
        crop_bbox = inv.getbbox()
        if crop_bbox is not None:
            bw = bw.crop(crop_bbox)

    # Do not force a full head-height canvas here. The sender performs the final
    # 96-dot placement and scaling. Saving an already padded 96px-high image
    # prevents the sender from widening small source images like the known-good
    # 64x32 test pattern.
    content_w, content_h = bw.size
    pad_left = max(0, offset_x)
    pad_top = max(0, offset_y)
    pad_bottom = max(0, -offset_y)
    canvas_w = content_w + pad_left
    canvas_h = content_h + pad_top + pad_bottom
    canvas = Image.new("1", (canvas_w, canvas_h), 1)  # white
    canvas.paste(bw, (pad_left, pad_top))

    lossless_preferred = (src_path.suffix.lower() in (".svg", ".png")) or src_bw or (mode == "threshold")

    if not out_path.suffix:
        out_path = out_path.with_suffix(".png" if lossless_preferred else ".jpg")
    elif lossless_preferred and out_path.suffix.lower() in (".jpg", ".jpeg"):
        out_path = out_path.with_suffix(".png")

    # Save line art and SVG-derived images losslessly to avoid JPEG artifacts.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.suffix.lower() == ".png":
        canvas.convert("L").save(out_path, format="PNG")
    else:
        canvas.convert("L").save(out_path, format="JPEG", quality=100, subsampling=0, optimize=False)

    return {
        "prepared_image": str(out_path),
        "src_size": [im0.width, im0.height],
        "src_size_mm": list(get_svg_size_mm(src_path)) if src_path.suffix.lower() == ".svg" and get_svg_size_mm(src_path) else None,
        "src_bw": src_bw,
        "rotation": rot,
        "autocontrast": autocontrast,
        "crop_content": crop_content,
        "despeckle": despeckle,
        "crop_bbox": list(crop_bbox) if crop_bbox is not None else None,
        "prepared_size": [canvas.width, canvas.height],
        "content_size": [content_w, content_h],
        "dither_used": mode,
        "fit_mode": fit_mode,
        "align": align,
        "offset_x": offset_x,
        "offset_y": offset_y,
        "head_height": head_height,
        "svg_pixels_per_mm": svg_pixels_per_mm,
    }


def parse_bluetoothctl_devices(output: str) -> list[tuple[str, str]]:
    devices: list[tuple[str, str]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("Device "):
            continue
        parts = line.split(maxsplit=2)
        if len(parts) < 3:
            continue
        mac = parts[1].upper()
        name = parts[2].strip()
        if MAC_RE.match(mac):
            devices.append((mac, name))
    return devices


def bluetooth_info(mac: str) -> str:
    rc, out, err = run_capture(["bluetoothctl", "info", mac])
    if rc != 0:
        return out + "\n" + err
    return out


def maybe_scan_for_devices(seconds: int) -> str:
    if seconds <= 0:
        return ""
    rc, out, err = run_capture(["bluetoothctl", "--timeout", str(seconds), "scan", "on"])
    return (out or "") + ("\n" + err if err else "")


def parse_scan_output_for_devices(output: str) -> list[tuple[str, str]]:
    devices: dict[str, str] = {}
    for line in output.splitlines():
        line = line.strip()
        if "Device " not in line:
            continue
        m = re.search(r"Device\s+(([0-9A-F]{2}:){5}[0-9A-F]{2})\s+(.+)$", line, flags=re.IGNORECASE)
        if not m:
            continue
        mac = m.group(1).upper()
        name = m.group(3).strip()
        if MAC_RE.match(mac):
            devices[mac] = name
    return sorted([(mac, name) for mac, name in devices.items()], key=lambda t: t[0])


def bluetooth_preflight(mac: str, scan_seconds: int) -> None:
    # Best effort only: keep runtime behavior deterministic even if commands fail.
    run_capture(["bluetoothctl", "power", "on"])
    info = bluetooth_info(mac)
    if "RSSI:" not in info and scan_seconds > 0:
        maybe_scan_for_devices(scan_seconds)
    if shutil.which("l2ping"):
        run_capture(["l2ping", "-c", "1", mac])


def score_device(name: str, info_text: str, patterns: list[str]) -> int:
    score = 0
    name_l = name.lower()
    if any(p and p in name_l for p in patterns):
        score += 100
    if "Paired: yes" in info_text:
        score += 30
    if "Trusted: yes" in info_text:
        score += 20
    if "UUID: Serial Port" in info_text:
        score += 20
    if "Vendor specific" in info_text:
        score += 10
    m = re.search(r"RSSI:\s+0x[0-9a-fA-F]+\s+\((-?\d+)\)", info_text)
    if m:
        # less negative RSSI should be preferred
        score += max(-100, int(m.group(1)))
    return score


def discover_printer_mac(cfg: dict[str, Any], cli_patterns: list[str]) -> tuple[str, str]:
    # Under sudo/non-interactive shells adapters may appear powered off; try to power on first.
    run_capture(["bluetoothctl", "power", "on"])
    rc, out, err = run_capture(["bluetoothctl", "devices"])
    if rc != 0:
        raise SystemExit(f"bluetoothctl devices failed:\n{err or out}")
    devices = parse_bluetoothctl_devices(out)
    if not devices:
        scan_out = maybe_scan_for_devices(int(cfg_get(cfg, "printer.auto_scan_seconds")))
        rc, out, err = run_capture(["bluetoothctl", "devices"])
        if rc != 0:
            raise SystemExit(f"bluetoothctl devices failed after scan:\n{err or out}")
        devices = parse_bluetoothctl_devices(out)
        if not devices:
            devices = parse_scan_output_for_devices(scan_out)
    if not devices:
        raise SystemExit("no Bluetooth devices found; pair/trust the printer first")

    pattern_cfg = [str(x).lower() for x in cfg_get(cfg, "printer.name_patterns")]
    patterns = [p for p in pattern_cfg + [p.lower() for p in cli_patterns] if p]

    scored: list[tuple[int, str, str]] = []
    for mac, name in devices:
        info = bluetooth_info(mac)
        score = score_device(name, info, patterns)
        scored.append((score, mac, name))
    scored.sort(reverse=True)
    best_score, best_mac, best_name = scored[0]
    if best_score < 0:
        raise SystemExit("unable to identify a suitable printer automatically; set printer.mac in config or pass --mac")
    return best_mac, best_name


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Katasymbol print CLI with image preprocessing, known-good defaults and Bluetooth auto-discovery.",
        epilog=(
            "Typical use:\n"
            "  sudo python3 scripts/katasymbol_print.py image.png\n"
            "Slow diagnostic dry-run:\n"
            "  python3 scripts/katasymbol_print.py image.png --slow --dry-run"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    ap.add_argument("image", help="Input image file (PNG/JPG/SVG)")

    basic = ap.add_argument_group("Basic Workflow")
    basic.add_argument("--mac", default="", help="Printer MAC address (optional with auto-discovery)")
    basic.add_argument("--dry-run", action="store_true", help="Build payload/artifacts only, do not send")
    basic.add_argument(
        "--prepare-only",
        action="store_true",
        help="Run image preprocessing only, write prepared image + metadata, then exit",
    )
    basic.add_argument(
        "--safe",
        action="store_true",
        help="Deprecated alias for the conservative default transport mode",
    )
    basic.add_argument(
        "--slow",
        action="store_true",
        help="Fallback timing mode using original template pacing",
    )
    basic.add_argument(
        "--aggressive",
        action="store_true",
        help="Riskier transport mode with extra post-trigger frames and shorter inter-frame delay",
    )
    basic.add_argument(
        "--long-label-svg",
        action="store_true",
        help="Force the validated long SVG label preset based on InkscapeTest2/job_002",
    )
    basic.add_argument(
        "--long-label-bitmap",
        action="store_true",
        help="Use the current best long bitmap label preset based on InkscapeTest2/job_002",
    )
    basic.add_argument(
        "--diagnostic-bitmap-passthrough",
        action="store_true",
        help="Use a narrow diagnostic long-bitmap path for already prepared black/white raster test images",
    )
    basic.add_argument(
        "--t-experimental",
        action="store_true",
        help="Enable the current experimental T-fix candidate for long-label PNG/SVG paths",
    )

    image_group = ap.add_argument_group("Image Preparation")
    image_group.add_argument("--no-prepare", action="store_true", help="Disable preprocessing pipeline")
    image_group.add_argument("--autocontrast", action="store_true", help="Enable grayscale autocontrast before binarization")
    image_group.add_argument("--no-autocontrast", action="store_true", help="Disable grayscale autocontrast before binarization")
    image_group.add_argument("--crop-content", action="store_true", help="Crop white margins during preprocessing")
    image_group.add_argument("--no-crop-content", action="store_true", help="Keep original white margins during preprocessing")
    image_group.add_argument("--despeckle", action="store_true", help="Remove isolated black speckles after binarization")
    image_group.add_argument("--no-despeckle", action="store_true", help="Disable removal of isolated black speckles after binarization")
    image_group.add_argument(
        "--rotate",
        default="",
        choices=["", "auto", "0", "90", "180", "270"],
        help="Rotation before scaling",
    )
    image_group.add_argument(
        "--fit-mode",
        default="",
        choices=["", "shrink", "fit", "stretch"],
        help="How to adapt image to fixed print-head height",
    )
    image_group.add_argument(
        "--dither",
        default="",
        choices=["", "auto", "threshold", "floyd", "ordered"],
        help="B/W conversion mode (auto: threshold for strict BW, Floyd for gray/color)",
    )
    image_group.add_argument("--align", default="", choices=["", "center", "top", "bottom"], help="Vertical alignment")
    image_group.add_argument("--offset-x", type=int, default=None, help="Horizontal offset in pixels (adds white left padding)")
    image_group.add_argument("--offset-y", type=int, default=None, help="Vertical offset in pixels")
    image_group.add_argument(
        "--head-height",
        type=int,
        default=None,
        help="Fixed print-head height in pixels (0: infer from template)",
    )
    image_group.add_argument(
        "--prepared-image-out",
        default="",
        help="Optional explicit path for prepared JPEG (default: out/prepared_images/<timestamp>.jpg)",
    )
    image_group.add_argument(
        "--svg-pixels-per-mm",
        type=float,
        default=None,
        help="SVG rasterization density in pixels/mm before preprocessing/sender placement",
    )
    image_group.add_argument(
        "--no-scale",
        action="store_true",
        help="Keep the input at its current physical/pixel size instead of fitting it to the long-label renderer geometry",
    )

    connection = ap.add_argument_group("Connection And Template")
    connection.add_argument("--config", default="", help="Path to config JSON (default: .katasymbol_print.json)")
    connection.add_argument("--init-config", action="store_true", help="Create default config if missing and exit")
    connection.add_argument("--print-config", action="store_true", help="Print effective config and exit")
    connection.add_argument(
        "--printer-name-pattern",
        action="append",
        default=[],
        help="Additional case-insensitive substring used for auto-discovery (repeatable)",
    )
    connection.add_argument(
        "--bt-preflight",
        action="store_true",
        help="Enable Bluetooth preflight (power on / scan / l2ping wakeup)",
    )
    connection.add_argument(
        "--no-bt-preflight",
        action="store_true",
        help="Deprecated alias for disabling Bluetooth preflight",
    )
    connection.add_argument("--no-auto-discover", action="store_true", help="Disable Bluetooth printer auto-discovery")
    connection.add_argument(
        "--template-dump-dir",
        default="",
        help="Template dump directory with summary.json/messages.csv/job_XXX (default: auto-latest)",
    )
    connection.add_argument("--template-job", type=int, default=None, help="Template job index (1-based)")
    connection.add_argument("--out-dir", default="", help="Output directory for logs/artifacts")
    connection.add_argument("--channel", type=int, default=None, help="Preferred RFCOMM channel")
    connection.add_argument("--channels", default="", help="Fallback channel list")

    advanced = ap.add_argument_group("Advanced Transfer")
    advanced.add_argument("--threshold", type=int, default=None, help="Binarization threshold")
    advanced.add_argument("--connect-timeout", type=float, default=None)
    advanced.add_argument("--recv-timeout", type=float, default=None)
    advanced.add_argument("--timing-scale", type=float, default=None)
    advanced.add_argument("--delay-ms", type=int, default=None)
    advanced.add_argument("--post-frames-after-aa10", type=int, default=None)
    advanced.add_argument("--lzma-encoder", choices=["python", "xz", "java"], default="")

    experimental = ap.add_argument_group("Experimental / Reverse Engineering")
    experimental.add_argument("--canvas-width", type=int, default=None, help="Override btbuf width before trimming")
    experimental.add_argument("--force-no-zero-index", type=int, default=None, help="Override btbuf trim start / btbuf[12]")
    experimental.add_argument("--scale-width-bias", type=int, default=0, help="Adjust width after aspect-ratio scaling to head height")
    experimental.add_argument("--scale-resample", choices=["lanczos", "nearest"], default="lanczos", help="Resampling kernel used during sender-side scaling")
    experimental.add_argument(
        "--bbox-fit-mode",
        choices=["", "contain", "cover", "stretch"],
        default="",
        help="How decoded-template/template overlay presets fit content into the template bbox",
    )
    experimental.add_argument("--bbox-align-x", choices=["", "left", "center", "right"], default="", help="Horizontal placement inside template bbox")
    experimental.add_argument("--bbox-align-y", choices=["", "top", "center", "bottom"], default="", help="Vertical placement inside template bbox")
    experimental.add_argument(
        "--bbox-inset-y",
        type=int,
        default=None,
        help="Vertical safety inset in pixels for template-bbox presets",
    )
    experimental.add_argument(
        "--bbox-offset-y",
        type=int,
        default=None,
        help="Vertical placement offset in pixels for template-bbox presets (positive moves down)",
    )
    experimental.add_argument(
        "--raster-y-phase",
        type=int,
        default=None,
        help="Cyclic vertical phase shift applied during raster packing",
    )
    experimental.add_argument(
        "--compat-raster-preset",
        choices=["", "legacy-testpattern-64x32", "decoded-template-bbox", "template-btbuf-overlay", "long-label-svg-289", "vendor-like-t15", "vendor-like-t15-import", "vendor-like-t15-import-dither"],
        default="",
        help="Raster compatibility preset for known test cases",
    )
    experimental.add_argument(
        "--no-scale-to-canvas-width",
        action="store_true",
        help="Disable scaling image to full template width",
    )
    experimental.add_argument(
        "--no-use-template-nozero",
        action="store_true",
        help="Disable forcing template no_zero_index behavior",
    )
    experimental.add_argument(
        "--keep-template-aabb",
        action="store_true",
        help="Diagnostic mode: send captured template aabb instead of generated image payload",
    )
    args = ap.parse_args()

    cfg_path = Path(args.config).expanduser() if args.config.strip() else default_config_path()
    cfg = load_config(cfg_path)
    if args.init_config:
        print(cfg_path)
        return
    if args.print_config:
        print(json.dumps(cfg, indent=2))
        return

    if not Path(args.image).exists():
        raise SystemExit(f"image not found: {args.image}")

    template_dump_cfg = str(cfg_get(cfg, "template.dump_dir")).strip()
    template_dump_arg = args.template_dump_dir.strip()
    template_job = args.template_job if args.template_job is not None else int(cfg_get(cfg, "template.job"))
    out_dir = args.out_dir or str(cfg_get(cfg, "output.out_dir"))
    channel = args.channel if args.channel is not None else int(cfg_get(cfg, "printer.channel"))
    channels = args.channels or str(cfg_get(cfg, "printer.channels"))
    threshold = args.threshold if args.threshold is not None else int(cfg_get(cfg, "image.threshold"))
    canvas_width = args.canvas_width
    force_no_zero_index = args.force_no_zero_index
    scale_width_bias = args.scale_width_bias
    scale_resample = args.scale_resample
    compat_raster_preset = args.compat_raster_preset or str(cfg_get(cfg, "transfer.compat_raster_preset"))
    bbox_fit_mode = args.bbox_fit_mode or str(cfg_get(cfg, "transfer.bbox_fit_mode"))
    bbox_align_x = args.bbox_align_x or str(cfg_get(cfg, "transfer.bbox_align_x"))
    bbox_align_y = args.bbox_align_y or str(cfg_get(cfg, "transfer.bbox_align_y"))
    bbox_inset_y = args.bbox_inset_y if args.bbox_inset_y is not None else int(cfg_get(cfg, "transfer.bbox_inset_y"))
    bbox_offset_y = args.bbox_offset_y if args.bbox_offset_y is not None else int(cfg_get(cfg, "transfer.bbox_offset_y"))
    raster_y_phase = args.raster_y_phase if args.raster_y_phase is not None else int(cfg_get(cfg, "transfer.raster_y_phase"))
    connect_timeout = args.connect_timeout if args.connect_timeout is not None else float(cfg_get(cfg, "printer.connect_timeout"))
    recv_timeout = args.recv_timeout if args.recv_timeout is not None else float(cfg_get(cfg, "printer.recv_timeout"))
    timing_scale = args.timing_scale if args.timing_scale is not None else float(cfg_get(cfg, "printer.timing_scale"))
    delay_ms = args.delay_ms if args.delay_ms is not None else int(cfg_get(cfg, "printer.delay_ms"))
    post_frames_after_aa10 = (
        args.post_frames_after_aa10
        if args.post_frames_after_aa10 is not None
        else int(cfg_get(cfg, "transfer.post_frames_after_aa10"))
    )
    lzma_encoder = args.lzma_encoder or str(cfg_get(cfg, "transfer.lzma_encoder"))

    rotate = args.rotate or str(cfg_get(cfg, "image.rotate"))
    fit_mode = args.fit_mode or str(cfg_get(cfg, "image.fit_mode"))
    dither = args.dither or str(cfg_get(cfg, "image.dither"))
    align = args.align or str(cfg_get(cfg, "image.align"))
    offset_x = args.offset_x if args.offset_x is not None else int(cfg_get(cfg, "image.offset_x"))
    offset_y = args.offset_y if args.offset_y is not None else int(cfg_get(cfg, "image.offset_y"))
    autocontrast = bool(cfg_get(cfg, "image.autocontrast"))
    if args.autocontrast:
        autocontrast = True
    if args.no_autocontrast:
        autocontrast = False
    crop_content = bool(cfg_get(cfg, "image.crop_content"))
    if args.crop_content:
        crop_content = True
    if args.no_crop_content:
        crop_content = False
    despeckle = bool(cfg_get(cfg, "image.despeckle"))
    if args.despeckle:
        despeckle = True
    if args.no_despeckle:
        despeckle = False
    head_height_cfg = int(cfg_get(cfg, "image.head_height"))
    head_height = args.head_height if args.head_height is not None else head_height_cfg
    svg_pixels_per_mm = (
        args.svg_pixels_per_mm if args.svg_pixels_per_mm is not None else float(cfg_get(cfg, "image.svg_pixels_per_mm"))
    )
    no_scale = args.no_scale
    prepared_image_out_cfg = str(cfg_get(cfg, "image.prepared_image_out")).strip()
    prepared_image_out = args.prepared_image_out.strip() or prepared_image_out_cfg

    scale_to_canvas_width = bool(cfg_get(cfg, "transfer.scale_to_canvas_width")) and (not args.no_scale_to_canvas_width)
    use_template_nozero = bool(cfg_get(cfg, "transfer.use_template_nozero")) and (not args.no_use_template_nozero)
    keep_template_aabb = bool(cfg_get(cfg, "transfer.keep_template_aabb")) or args.keep_template_aabb
    long_label_svg = bool(cfg_get(cfg, "transfer.long_label_svg_preset")) or args.long_label_svg
    long_label_bitmap = bool(cfg_get(cfg, "transfer.long_label_bitmap_preset")) or args.long_label_bitmap
    diagnostic_bitmap_passthrough = args.diagnostic_bitmap_passthrough
    t_experimental = args.t_experimental
    explicit_long_label_controls = bool(
        args.long_label_svg
        or args.long_label_bitmap
        or args.diagnostic_bitmap_passthrough
        or args.t_experimental
        or args.template_dump_dir
        or args.template_job is not None
        or args.compat_raster_preset
        or args.bbox_fit_mode
        or args.bbox_align_x
        or args.bbox_align_y
        or args.bbox_inset_y is not None
        or args.bbox_offset_y is not None
        or args.raster_y_phase is not None
    )
    prepare_enabled = bool(cfg_get(cfg, "image.prepare")) and (not args.no_prepare)
    auto_discover_enabled = bool(cfg_get(cfg, "printer.auto_discover")) and (not args.no_auto_discover)
    bt_preflight_enabled = bool(cfg_get(cfg, "printer.bt_preflight"))
    if args.bt_preflight:
        bt_preflight_enabled = True
    if args.no_bt_preflight:
        bt_preflight_enabled = False

    if args.slow:
        timing_scale = 1.0
    if args.aggressive:
        if post_frames_after_aa10 <= 0:
            post_frames_after_aa10 = 12
        if delay_ms >= 30:
            delay_ms = 20

    if (not explicit_long_label_controls) and (not long_label_svg) and (not long_label_bitmap):
        src_path = Path(args.image)
        if is_auto_long_label_bitmap_candidate(src_path):
            long_label_bitmap = True
            print("auto long-label bitmap preset")
        elif is_auto_long_label_svg_candidate(src_path):
            long_label_svg = True
            print("auto long-label svg preset")

    if t_experimental and (not long_label_svg) and (not long_label_bitmap):
        src_path = Path(args.image)
        if is_auto_long_label_bitmap_candidate(src_path):
            long_label_bitmap = True
            print("auto long-label bitmap preset")
        elif is_auto_long_label_svg_candidate(src_path):
            long_label_svg = True
            print("auto long-label svg preset")

    if long_label_svg:
        template_dump_cfg = "out/decode/dumpstate-2026-03-21-21-32-39-InkscapeTest2"
        template_dump_arg = template_dump_cfg
        template_job = 2
        compat_raster_preset = "vendor-like-t15"
        scale_resample = "nearest"
        bbox_fit_mode = "contain"
        bbox_align_x = "center"
        bbox_align_y = "center"
        bbox_inset_y = 0
        bbox_offset_y = 0
        raster_y_phase = 0
        dither = "threshold"
        threshold = 230
        svg_pixels_per_mm = 12.0
        offset_y = 0
        prepare_enabled = False
        if no_scale and args.svg_pixels_per_mm is None:
            # In no-scale mode, interpret SVG document units at printer density
            # instead of the higher comparison density used by the validated
            # vendor-nearer reference path.
            svg_pixels_per_mm = 8.0

    if long_label_bitmap:
        template_dump_cfg = "out/decode/dumpstate-2026-03-21-21-32-39-InkscapeTest2"
        template_dump_arg = template_dump_cfg
        template_job = 2
        compat_raster_preset = "vendor-like-t15"
        scale_resample = "nearest"
        bbox_fit_mode = "contain"
        bbox_align_x = "center"
        bbox_align_y = "center"
        bbox_inset_y = 0
        bbox_offset_y = 0
        raster_y_phase = 0
        dither = "threshold"
        threshold = 230
        offset_y = 0
        prepare_enabled = False

    if diagnostic_bitmap_passthrough:
        template_dump_cfg = "out/decode/dumpstate-2026-03-23-21-40-19"
        template_dump_arg = template_dump_cfg
        template_job = 1
        compat_raster_preset = "decoded-template-bbox"
        bbox_fit_mode = "stretch"
        bbox_align_x = "left"
        bbox_align_y = "top"
        bbox_inset_y = 4
        bbox_offset_y = 0
        raster_y_phase = 0
        dither = "threshold"
        threshold = 230
        offset_y = 0
        prepare_enabled = False
        print("diagnostic bitmap passthrough preset")

    if t_experimental and (long_label_svg or long_label_bitmap):
        # This currently aliases the validated vendor-nearer long-label path.
        print("experimental T preset")

    auto_mode = False
    if template_dump_arg:
        template_dump_dir = Path(template_dump_arg)
    elif template_dump_cfg:
        template_dump_dir = Path(template_dump_cfg)
    else:
        auto = find_auto_template(Path("out/decode"), prefer_long=long_label_bitmap or long_label_svg)
        if auto is None:
            raise SystemExit("no template dump found in out/decode (run decode_spp.py first)")
        template_dump_dir, template_job = auto
        print(f"auto template dump: {template_dump_dir}")
        print(f"auto template job: {template_job}")
        auto_mode = True
    if not template_dump_dir.exists():
        raise SystemExit(f"template dump dir not found: {template_dump_dir}")

    geom = load_template_geometry(template_dump_dir, template_job)
    if head_height is None or head_height <= 0:
        head_height = int(geom["bytes_per_col"]) * 8

    image_for_sender = Path(args.image)
    prep_meta = None
    if prepare_enabled:
        if prepared_image_out:
            prep_out = Path(prepared_image_out)
        else:
            ts = time.strftime("%Y%m%d-%H%M%S")
            src_suffix = Path(args.image).suffix.lower()
            prep_ext = ".png" if src_suffix in (".svg", ".png") else ".jpg"
            prep_out = Path("out/prepared_images") / f"prepared_{ts}{prep_ext}"
        prep_meta = prepare_image(
            src_path=Path(args.image),
            out_path=prep_out,
            head_height=head_height,
            svg_pixels_per_mm=svg_pixels_per_mm,
            autocontrast=autocontrast,
            crop_content=crop_content,
            despeckle=despeckle,
            rotate_mode=rotate,
            fit_mode=fit_mode,
            dither_mode=dither,
            threshold=threshold,
            align=align,
            offset_x=offset_x,
            offset_y=offset_y,
        )
        image_for_sender = Path(prep_meta["prepared_image"])
        print(f"prepared image: {image_for_sender}")
    elif args.prepare_only:
        raise SystemExit("--prepare-only requires preprocessing (do not combine with --no-prepare)")

    if args.prepare_only:
        if prep_meta is not None:
            meta_out = Path(out_dir) / "_last_prepare_meta.json"
            meta_out.parent.mkdir(parents=True, exist_ok=True)
            meta_out.write_text(json.dumps(prep_meta, indent=2))
            print(f"prepare meta: {meta_out}")
        return

    mac = args.mac.strip().upper()
    if not mac:
        cfg_mac = str(cfg_get(cfg, "printer.mac")).strip().upper()
        if cfg_mac:
            mac = cfg_mac
    if mac and not MAC_RE.match(mac):
        raise SystemExit(f"invalid MAC format: {mac}")
    if not mac and (not args.dry_run):
        if auto_discover_enabled:
            mac, dev_name = discover_printer_mac(cfg, args.printer_name_pattern)
            print(f"auto printer: {dev_name} ({mac})")
        else:
            raise SystemExit("--mac required with --send (auto-discovery disabled)")
    if mac and (not args.dry_run) and bt_preflight_enabled:
        bluetooth_preflight(mac, int(cfg_get(cfg, "printer.auto_scan_seconds")))

    if not args.dry_run and os.geteuid() != 0:
        print("warning: non-root run may fail to open RFCOMM socket; retry with sudo", file=sys.stderr)

    replay = Path(__file__).resolve().parent / "replay_sender.py"
    cmd = [
        sys.executable,
        str(replay),
        "--template-dump-dir",
        str(template_dump_dir),
        "--template-job",
        str(template_job),
        "--image",
        str(image_for_sender),
        "--out-dir",
        out_dir,
        "--threshold",
        str(threshold),
        "--channel",
        str(channel),
        "--channels",
        channels,
        "--connect-timeout",
        str(connect_timeout),
        "--recv-timeout",
        str(recv_timeout),
        "--timing-scale",
        str(timing_scale),
        "--delay-ms",
        str(delay_ms),
        "--post-frames-after-aa10",
        str(post_frames_after_aa10),
        "--lzma-encoder",
        lzma_encoder,
        "--svg-pixels-per-mm",
        str(svg_pixels_per_mm),
    ]
    if canvas_width is not None:
        cmd.extend(["--canvas-width", str(canvas_width)])
    if force_no_zero_index is not None:
        cmd.extend(["--force-no-zero-index", str(force_no_zero_index)])
    if scale_width_bias:
        cmd.extend(["--scale-width-bias", str(scale_width_bias)])
    if scale_resample != "lanczos":
        cmd.extend(["--scale-resample", scale_resample])
    if compat_raster_preset:
        cmd.extend(["--compat-raster-preset", compat_raster_preset])
    if bbox_fit_mode and bbox_fit_mode != "contain":
        cmd.extend(["--bbox-fit-mode", bbox_fit_mode])
    if bbox_align_x and bbox_align_x != "center":
        cmd.extend(["--bbox-align-x", bbox_align_x])
    if bbox_align_y and bbox_align_y != "center":
        cmd.extend(["--bbox-align-y", bbox_align_y])
    if bbox_inset_y:
        cmd.extend(["--bbox-inset-y", str(bbox_inset_y)])
    if bbox_offset_y:
        cmd.extend(["--bbox-offset-y", str(bbox_offset_y)])
    if raster_y_phase:
        cmd.extend(["--raster-y-phase", str(raster_y_phase)])
    if no_scale:
        cmd.append("--no-scale")
    if mac:
        cmd.extend(["--mac", mac])
    if scale_to_canvas_width:
        cmd.append("--scale-to-canvas-width")
    if use_template_nozero and (not no_scale):
        cmd.append("--use-template-nozero")
    if not args.dry_run:
        cmd.append("--send")
    if keep_template_aabb:
        cmd.append("--keep-template-aabb")

    rc = run(cmd)
    if prep_meta is not None:
        meta_out = Path(out_dir) / "_last_prepare_meta.json"
        meta_out.parent.mkdir(parents=True, exist_ok=True)
        meta_out.write_text(json.dumps(prep_meta, indent=2))
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
