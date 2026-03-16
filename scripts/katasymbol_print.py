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

from PIL import Image


MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$", re.IGNORECASE)
DEFAULT_CONFIG = {
    "printer": {
        "mac": "",
        "auto_discover": True,
        "auto_scan_seconds": 4,
        "name_patterns": ["katasymbol", "t0"],
        "channel": 1,
        "channels": "1,2,3",
        "connect_timeout": 5.0,
        "recv_timeout": 0.2,
        "timing_scale": 1.0,
        "delay_ms": 20,
    },
    "template": {
        "dump_dir": "",
        "job": 5,
    },
    "image": {
        "threshold": 125,
        "prepare": True,
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
        "post_frames_after_aa10": 12,
        "lzma_encoder": "python",
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


def find_latest_template_dump(default_root: Path) -> Path | None:
    # Pick dump dir that has decoded job payloads with at least one aabb file.
    best: tuple[float, Path] | None = None
    patt = str(default_root / "dumpstate-*" / "job_*" / "*_aabb_*.bin")
    for p in glob.glob(patt):
        fp = Path(p)
        dump_dir = fp.parent.parent
        try:
            mtime = fp.stat().st_mtime
        except OSError:
            continue
        if best is None or mtime > best[0]:
            best = (mtime, dump_dir)
    return best[1] if best else None


def pick_template_job(dump_dir: Path, requested_job: int) -> int:
    job_dirs = sorted(dump_dir.glob("job_*"))
    if not job_dirs:
        raise SystemExit(f"no job_* dirs in template dump: {dump_dir}")
    with_aabb = []
    for jd in job_dirs:
        if list(jd.glob("*_aabb_*.bin")):
            with_aabb.append(jd)
    if not with_aabb:
        raise SystemExit(f"no *_aabb_*.bin payloads in template dump: {dump_dir}")
    max_job = max(int(jd.name.split("_")[-1]) for jd in with_aabb)
    if requested_job > 0 and any(int(jd.name.split("_")[-1]) == requested_job for jd in with_aabb):
        return requested_job
    print(f"auto template job: {max_job}")
    return max_job


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


def prepare_image(
    src_path: Path,
    out_path: Path,
    head_height: int,
    rotate_mode: str,
    fit_mode: str,
    dither_mode: str,
    threshold: int,
    align: str,
    offset_x: int,
    offset_y: int,
) -> dict:
    im0 = Image.open(src_path)
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

    # final canvas: dynamic length (width), fixed head height
    content_w, content_h = bw.size
    canvas_w = content_w + max(0, offset_x)
    canvas_h = head_height
    canvas = Image.new("1", (canvas_w, canvas_h), 1)  # white

    if align == "center":
        y = (canvas_h - content_h) // 2 + offset_y
        x = offset_x
    elif align == "top":
        y = 0 + offset_y
        x = offset_x
    else:  # bottom
        y = (canvas_h - content_h) + offset_y
        x = offset_x
    y = max(0, min(canvas_h - content_h, y))
    x = max(0, x)
    canvas.paste(bw, (x, y))

    # save as JPEG (requested), but from 8-bit for compatibility
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("L").save(out_path, format="JPEG", quality=100, subsampling=0, optimize=False)

    return {
        "src_size": [im0.width, im0.height],
        "src_bw": src_bw,
        "rotation": rot,
        "prepared_size": [canvas.width, canvas.height],
        "content_size": [content_w, content_h],
        "dither_used": mode,
        "fit_mode": fit_mode,
        "align": align,
        "offset_x": offset_x,
        "offset_y": offset_y,
        "head_height": head_height,
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
        description="Katasymbol print CLI with image preprocessing, config defaults and Bluetooth auto-discovery."
    )
    ap.add_argument("image", help="Input image file (PNG/JPG)")
    ap.add_argument("--config", default="", help="Path to config JSON (default: .katasymbol_print.json)")
    ap.add_argument("--init-config", action="store_true", help="Create default config if missing and exit")
    ap.add_argument("--print-config", action="store_true", help="Print effective config and exit")
    ap.add_argument("--mac", default="", help="Printer MAC address (optional with auto-discovery)")
    ap.add_argument(
        "--printer-name-pattern",
        action="append",
        default=[],
        help="Additional case-insensitive substring used for auto-discovery (repeatable)",
    )
    ap.add_argument(
        "--no-bt-preflight",
        action="store_true",
        help="Disable Bluetooth preflight (power on / scan / l2ping wakeup)",
    )
    ap.add_argument("--no-auto-discover", action="store_true", help="Disable Bluetooth printer auto-discovery")
    ap.add_argument(
        "--template-dump-dir",
        default="",
        help="Template dump directory with summary.json/messages.csv/job_XXX (default: auto-latest)",
    )
    ap.add_argument("--template-job", type=int, default=None, help="Template job index (1-based)")
    ap.add_argument("--out-dir", default="", help="Output directory for logs/artifacts")
    ap.add_argument("--channel", type=int, default=None, help="Preferred RFCOMM channel")
    ap.add_argument("--channels", default="", help="Fallback channel list")
    ap.add_argument("--threshold", type=int, default=None, help="Binarization threshold")
    ap.add_argument("--connect-timeout", type=float, default=None)
    ap.add_argument("--recv-timeout", type=float, default=None)
    ap.add_argument("--timing-scale", type=float, default=None)
    ap.add_argument("--delay-ms", type=int, default=None)
    ap.add_argument("--post-frames-after-aa10", type=int, default=None)
    ap.add_argument("--lzma-encoder", choices=["python", "xz"], default="")
    ap.add_argument(
        "--no-scale-to-canvas-width",
        action="store_true",
        help="Disable scaling image to full template width",
    )
    ap.add_argument(
        "--no-use-template-nozero",
        action="store_true",
        help="Disable forcing template no_zero_index behavior",
    )
    ap.add_argument("--dry-run", action="store_true", help="Build payload/artifacts only, do not send")
    ap.add_argument(
        "--keep-template-aabb",
        action="store_true",
        help="Diagnostic mode: send captured template aabb instead of generated image payload",
    )
    # Preprocessing controls
    ap.add_argument("--no-prepare", action="store_true", help="Disable preprocessing pipeline")
    ap.add_argument(
        "--rotate",
        default="",
        choices=["", "auto", "0", "90", "180", "270"],
        help="Rotation before scaling",
    )
    ap.add_argument(
        "--fit-mode",
        default="",
        choices=["", "shrink", "fit", "stretch"],
        help="How to adapt image to fixed print-head height",
    )
    ap.add_argument(
        "--dither",
        default="",
        choices=["", "auto", "threshold", "floyd", "ordered"],
        help="B/W conversion mode (auto: threshold for strict BW, Floyd for gray/color)",
    )
    ap.add_argument("--align", default="", choices=["", "center", "top", "bottom"], help="Vertical alignment")
    ap.add_argument("--offset-x", type=int, default=None, help="Horizontal offset in pixels (adds white left padding)")
    ap.add_argument("--offset-y", type=int, default=None, help="Vertical offset in pixels")
    ap.add_argument(
        "--head-height",
        type=int,
        default=None,
        help="Fixed print-head height in pixels (0: infer from template)",
    )
    ap.add_argument(
        "--prepared-image-out",
        default="",
        help="Optional explicit path for prepared JPEG (default: out/prepared_images/<timestamp>.jpg)",
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
    head_height_cfg = int(cfg_get(cfg, "image.head_height"))
    head_height = args.head_height if args.head_height is not None else head_height_cfg
    prepared_image_out_cfg = str(cfg_get(cfg, "image.prepared_image_out")).strip()
    prepared_image_out = args.prepared_image_out.strip() or prepared_image_out_cfg

    scale_to_canvas_width = bool(cfg_get(cfg, "transfer.scale_to_canvas_width")) and (not args.no_scale_to_canvas_width)
    use_template_nozero = bool(cfg_get(cfg, "transfer.use_template_nozero")) and (not args.no_use_template_nozero)
    keep_template_aabb = bool(cfg_get(cfg, "transfer.keep_template_aabb")) or args.keep_template_aabb
    prepare_enabled = bool(cfg_get(cfg, "image.prepare")) and (not args.no_prepare)
    auto_discover_enabled = bool(cfg_get(cfg, "printer.auto_discover")) and (not args.no_auto_discover)

    auto_mode = False
    if template_dump_arg:
        template_dump_dir = Path(template_dump_arg)
    elif template_dump_cfg:
        template_dump_dir = Path(template_dump_cfg)
    else:
        auto = find_latest_template_dump(Path("out/decode"))
        if auto is None:
            raise SystemExit("no template dump found in out/decode (run decode_spp.py first)")
        template_dump_dir = auto
        auto_mode = True
        print(f"auto template dump: {template_dump_dir}")
    if not template_dump_dir.exists():
        raise SystemExit(f"template dump dir not found: {template_dump_dir}")

    if auto_mode:
        template_job = pick_template_job(template_dump_dir, template_job)

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
            prep_out = Path("out/prepared_images") / f"prepared_{ts}.jpg"
        prep_meta = prepare_image(
            src_path=Path(args.image),
            out_path=prep_out,
            head_height=head_height,
            rotate_mode=rotate,
            fit_mode=fit_mode,
            dither_mode=dither,
            threshold=threshold,
            align=align,
            offset_x=offset_x,
            offset_y=offset_y,
        )
        image_for_sender = prep_out
        print(f"prepared image: {image_for_sender}")

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
    if mac and (not args.dry_run) and (not args.no_bt_preflight):
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
    ]
    if mac:
        cmd.extend(["--mac", mac])
    if scale_to_canvas_width:
        cmd.append("--scale-to-canvas-width")
    if use_template_nozero:
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
