#!/usr/bin/env python3
import argparse
import csv
import glob
import json
import lzma
import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image


BIG_RE = re.compile(r"\d{3}_(aad1|aabb)_f(\d+)_len\d+\.bin$")
AABB_RE = re.compile(r"\d{3}_aabb_f(\d+)_len\d+\.bin$")


@dataclass
class OutMsg:
    frame_start: int
    time_start: float
    cmd_hex: str
    payload: bytes


def build_1001(cmd_hex: str, payload: bytes) -> bytes:
    cmd = int(cmd_hex, 16)
    length = 4 + len(payload)
    return (
        b"\x7e\x5a"
        + length.to_bytes(2, "little")
        + b"\x10\x01"
        + cmd.to_bytes(2, "big")
        + payload
    )


def build_1002_aabb(payload_504: bytes) -> bytes:
    if len(payload_504) != 504:
        raise ValueError("aabb payload must be 504 bytes")
    length = 0x01FC
    return b"\x7e\x5a" + length.to_bytes(2, "little") + b"\x10\x02\xaa\xbb" + payload_504


def checksum_le(data: bytes) -> bytes:
    s = sum(data) & 0xFFFF
    return s.to_bytes(2, "little")


def payload_start_trans(frame_size: int, frame_count: int) -> bytes:
    tail = b"\x00\x01" + frame_size.to_bytes(2, "little") + frame_count.to_bytes(2, "little")
    return checksum_le(tail) + tail


def load_template_outgoing(job_dir: Path, summary_path: Path, messages_csv: Path, job_index: int) -> List[OutMsg]:
    s = json.loads(summary_path.read_text())
    jobs = s["jobs"]
    if job_index < 1 or job_index > len(jobs):
        raise ValueError(f"job_index out of range: 1..{len(jobs)}")
    jr = jobs[job_index - 1]
    f0, f1 = int(jr["frame_start"]), int(jr["frame_end"])

    big_by_frame: Dict[Tuple[str, int], bytes] = {}
    for p in sorted(job_dir.glob("*_aa*_f*_len*.bin")):
        m = BIG_RE.match(p.name)
        if not m:
            continue
        cmd = m.group(1)
        fr = int(m.group(2))
        big_by_frame[(cmd, fr)] = p.read_bytes()

    out: List[OutMsg] = []
    with messages_csv.open() as f:
        rd = csv.DictReader(f)
        for r in rd:
            fr = int(r["frame_start"])
            if fr < f0 or fr > f1:
                continue
            cmd = r["cmd"].lower()
            if not cmd.startswith("aa"):
                continue
            plen = int(r["payload_len"])
            if cmd in ("aad1", "aabb"):
                key = (cmd, fr)
                if key not in big_by_frame:
                    raise ValueError(f"missing {cmd} payload for frame {fr} in {job_dir}")
                payload = big_by_frame[key]
            else:
                px = bytes.fromhex(r["payload_prefix_hex"])
                if len(px) < plen:
                    raise ValueError(f"short prefix for frame {fr} cmd {cmd}: need {plen}, have {len(px)}")
                payload = px[:plen]
            out.append(OutMsg(frame_start=fr, time_start=float(r["time_start"]), cmd_hex=cmd, payload=payload))
    return out


def image_to_btbuf(img_path: Path, threshold: int) -> Tuple[bytes, Dict[str, int]]:
    img = Image.open(img_path).convert("L")
    h_target = 96
    if img.height > h_target:
        new_w = max(1, int(round(img.width * (h_target / img.height))))
        img = img.resize((new_w, h_target), Image.Resampling.LANCZOS)

    canvas = Image.new("L", (img.width, h_target), 255)
    top = (h_target - img.height) // 2
    canvas.paste(img, (0, top))

    width = canvas.width
    bpc = 12
    data = bytearray(width * bpc)
    px = canvas.load()
    for x in range(width):
        for by in range(bpc):
            v = 0
            for bit in range(8):
                y = by * 8 + bit
                if y >= h_target:
                    continue
                # LSB-first bit packing, black pixel => 1
                if px[x, y] < threshold:
                    v |= 1 << bit
            data[x * bpc + by] = v

    btbuf = bytearray(4000)
    btbuf[2:4] = (0x100E).to_bytes(2, "little")
    btbuf[4:6] = width.to_bytes(2, "little")
    btbuf[6] = bpc
    btbuf[8:10] = (1).to_bytes(2, "little")
    btbuf[10:12] = (1).to_bytes(2, "little")
    btbuf[12:14] = b"\x00\x00"
    btbuf[14 : 14 + len(data)] = data

    used = (width * bpc) + 14
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


def image_to_btbuf_with_canvas(
    img_path: Path,
    threshold: int,
    canvas_width: int,
    bytes_per_col: int,
    scale_to_canvas_width: bool,
    force_no_zero_index: int,
) -> Tuple[bytes, Dict[str, int]]:
    img = Image.open(img_path).convert("L")
    h_target = bytes_per_col * 8

    # Fit image into requested canvas while preserving aspect ratio.
    # In template-nozero mode we keep native width (no forced stretch), because
    # the template often encodes a specific left-offset behavior.
    if scale_to_canvas_width and force_no_zero_index < 0 and img.width > 0:
        new_h = max(1, int(round(img.height * (canvas_width / img.width))))
        img = img.resize((canvas_width, new_h), Image.Resampling.LANCZOS)
    if img.height > h_target:
        new_w = max(1, int(round(img.width * (h_target / img.height))))
        img = img.resize((new_w, h_target), Image.Resampling.LANCZOS)
    if img.width > canvas_width:
        new_h = max(1, int(round(img.height * (canvas_width / img.width))))
        img = img.resize((canvas_width, new_h), Image.Resampling.LANCZOS)

    canvas = Image.new("L", (canvas_width, h_target), 255)
    top = (h_target - img.height) // 2
    if force_no_zero_index >= 0:
        # Emulate app's first-page trim behavior: first black col tends to be at
        # no_zero_index + 1, so transferred stream starts with one blank column.
        left = min(max(0, force_no_zero_index + 1), max(0, canvas_width - img.width))
    else:
        left = (canvas_width - img.width) // 2
    canvas.paste(img, (left, top))

    width = canvas.width
    bpc = bytes_per_col
    data_full = bytearray(width * bpc)
    px = canvas.load()
    for x in range(width):
        for by in range(bpc):
            v = 0
            for bit in range(8):
                y = by * 8 + bit
                if y >= h_target:
                    continue
                if px[x, y] < threshold:
                    v |= 1 << bit
            data_full[x * bpc + by] = v

    # T15-like behavior: report leading blank columns via btbuf[12] and trim.
    if force_no_zero_index >= 0:
        no_zero_index = min(width - 1, force_no_zero_index) if width > 0 else 0
    else:
        # APK behavior (GetNoZeroIndex): scan first i2 cols for first non-zero column,
        # then back off by one column when possible.
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

    btbuf = bytearray(4000)
    btbuf[2:4] = (0x100E).to_bytes(2, "little")
    btbuf[4:6] = eff_width.to_bytes(2, "little")
    btbuf[6] = bpc
    btbuf[8:10] = (1).to_bytes(2, "little")
    btbuf[10:12] = (1).to_bytes(2, "little")
    btbuf[12] = no_zero_index & 0xFF
    btbuf[13] = 0
    btbuf[14 : 14 + len(data)] = data

    used = (eff_width * bpc) + 14
    s = sum(btbuf[2:14])
    for k in range(1, (used // 256) + 1):
        s += btbuf[(k * 256) - 1]
    btbuf[0:2] = (s & 0xFFFF).to_bytes(2, "little")

    return bytes(btbuf), {"width": eff_width, "height": h_target, "bytes_per_col": bpc, "no_zero_index": no_zero_index}


def btbuf_to_aabb_payloads(btbuf: bytes) -> Tuple[bytes, List[bytes]]:
    # Match app encoder defaults (LZMA "alone" with 0x5d + dict 0x2000).
    lz = lzma.compress(
        btbuf,
        format=lzma.FORMAT_ALONE,
        filters=[
            {
                "id": lzma.FILTER_LZMA1,
                "dict_size": 8192,
                "lc": 3,
                "lp": 0,
                "pb": 2,
                "mode": lzma.MODE_NORMAL,
                "nice_len": 128,
                "mf": lzma.MF_BT4,
            }
        ],
    )
    chunks = (len(lz) + 499) // 500
    out: List[bytes] = []
    for idx in range(chunks):
        part = lz[idx * 500 : (idx + 1) * 500]
        payload = bytearray(504)
        payload[2] = idx & 0xFF
        payload[3] = chunks & 0xFF
        payload[4 : 4 + len(part)] = part
        csum = sum(payload[2:504]) & 0xFFFF
        payload[0:2] = csum.to_bytes(2, "little")
        out.append(bytes(payload))
    return lz, out


def btbuf_to_aabb_payloads_xz(btbuf: bytes) -> Tuple[bytes, List[bytes]]:
    # Alternative encoder path via xz, sometimes closer to embedded decoders.
    cmd = [
        "xz",
        "--format=lzma",
        "--stdout",
        "--lzma1=dict=8KiB,lc=3,lp=0,pb=2,mode=normal,nice=128,mf=bt4",
    ]
    proc = subprocess.run(cmd, input=btbuf, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"xz compression failed: {proc.stderr.decode(errors='ignore')}")
    lz = proc.stdout
    chunks = (len(lz) + 499) // 500
    out: List[bytes] = []
    for idx in range(chunks):
        part = lz[idx * 500 : (idx + 1) * 500]
        payload = bytearray(504)
        payload[2] = idx & 0xFF
        payload[3] = chunks & 0xFF
        payload[4 : 4 + len(part)] = part
        csum = sum(payload[2:504]) & 0xFFFF
        payload[0:2] = csum.to_bytes(2, "little")
        out.append(bytes(payload))
    return lz, out


def materialize_frames(
    template: List[OutMsg],
    aabb_payloads: List[bytes],
    stop_after_aa10: bool,
    post_frames_after_aa10: int,
    keep_template_aabb: bool,
) -> List[Tuple[str, bytes, float]]:
    frames: List[Tuple[str, bytes, float]] = []
    inserted = False
    stop = False
    post_left = 0
    for m in template:
        if stop:
            break
        if m.cmd_hex == "aa5c":
            p = payload_start_trans(512, len(aabb_payloads))
            frames.append((m.cmd_hex, build_1001("aa5c", p), m.time_start))
            continue
        if m.cmd_hex == "aabb":
            if keep_template_aabb:
                frames.append((m.cmd_hex, build_1002_aabb(m.payload), m.time_start))
                inserted = True
            elif not inserted:
                for pl in aabb_payloads:
                    frames.append(("aabb", build_1002_aabb(pl), m.time_start))
                inserted = True
            continue
        frames.append((m.cmd_hex, build_1001(m.cmd_hex, m.payload), m.time_start))
        if stop_after_aa10 and m.cmd_hex == "aa10":
            post_left = max(0, post_frames_after_aa10)
            if post_left == 0:
                stop = True
            continue
        if stop_after_aa10 and post_left > 0:
            post_left -= 1
            if post_left == 0:
                stop = True
    return frames


def send_frames(
    mac: str,
    channel: int,
    frames: List[Tuple[str, bytes, float]],
    connect_timeout_s: float,
    recv_timeout_s: float,
    delay_ms: int,
    timing_scale: float,
) -> List[Dict[str, object]]:
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    sock.settimeout(connect_timeout_s)
    sock.connect((mac, channel))
    sock.settimeout(recv_timeout_s)
    events: List[Dict[str, object]] = []
    try:
        for i, (cmd, fr, _ts) in enumerate(frames):
            try:
                sock.sendall(fr)
            except Exception as e:
                events.append(
                    {
                        "index": i,
                        "cmd": cmd,
                        "tx_len": len(fr),
                        "rx_hex": "",
                        "error": f"send:{type(e).__name__}:{e}",
                    }
                )
                raise
            rec_hex = ""
            try:
                rec = sock.recv(2048)
                rec_hex = rec.hex()
                if rec == b"":
                    events.append(
                        {
                            "index": i,
                            "cmd": cmd,
                            "tx_len": len(fr),
                            "rx_hex": "",
                            "error": "recv:eof",
                        }
                    )
                    raise OSError("remote closed RFCOMM socket")
            except socket.timeout:
                rec_hex = ""
            events.append({"index": i, "cmd": cmd, "tx_len": len(fr), "rx_hex": rec_hex})
            sleep_s = 0.0
            if i + 1 < len(frames):
                dt = max(0.0, (frames[i + 1][2] - _ts) * timing_scale)
                sleep_s = dt
            if sleep_s <= 0.0 and delay_ms > 0:
                sleep_s = delay_ms / 1000.0
            if sleep_s > 0.0:
                time.sleep(sleep_s)
    finally:
        sock.close()
    return events


def send_frames_try_channels(
    mac: str,
    channels: List[int],
    frames: List[Tuple[str, bytes, float]],
    connect_timeout_s: float,
    recv_timeout_s: float,
    delay_ms: int,
    timing_scale: float,
) -> Tuple[int, List[Dict[str, object]]]:
    last_err: Optional[Exception] = None
    for ch in channels:
        try:
            events = send_frames(mac, ch, frames, connect_timeout_s, recv_timeout_s, delay_ms, timing_scale)
            return ch, events
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    raise RuntimeError("no channel to try")


def main() -> None:
    ap = argparse.ArgumentParser(description="Replay Katasymbol outgoing print sequence with swapped aabb payload from PNG.")
    ap.add_argument("--template-dump-dir", required=True, help="e.g. out/decode/dumpstate-2026-03-15-21-56-49_Bild")
    ap.add_argument("--template-job", type=int, default=5, help="1-based job index in summary.json")
    ap.add_argument("--image", required=True, help="input PNG/JPG to print")
    ap.add_argument("--threshold", type=int, default=125, help="binarization threshold (0..255)")
    ap.add_argument("--out-dir", default="out/replay_sender")
    ap.add_argument("--send", action="store_true", help="actually send over RFCOMM")
    ap.add_argument("--mac", default="", help="printer bluetooth MAC, required with --send")
    ap.add_argument("--channel", type=int, default=1, help="RFCOMM channel")
    ap.add_argument(
        "--channels",
        default="",
        help="optional fallback channels list, e.g. 1,2,3,4 (tries until connect succeeds)",
    )
    ap.add_argument("--connect-timeout", type=float, default=5.0, help="RFCOMM connect timeout seconds")
    ap.add_argument("--recv-timeout", type=float, default=0.12, help="per-frame recv timeout seconds")
    ap.add_argument("--delay-ms", type=int, default=20, help="inter-frame delay")
    ap.add_argument("--timing-scale", type=float, default=1.0, help="scale factor for template inter-frame timing")
    ap.add_argument("--full-sequence", action="store_true", help="send full captured aa* sequence (default: stop after aa10)")
    ap.add_argument(
        "--post-frames-after-aa10",
        type=int,
        default=0,
        help="when not using --full-sequence, also send N template frames after aa10",
    )
    ap.add_argument("--canvas-width", type=int, default=0, help="force btbuf width; 0 = auto from template aabb")
    ap.add_argument("--bytes-per-col", type=int, default=0, help="force btbuf bytes_per_col; 0 = auto from template")
    ap.add_argument(
        "--scale-to-canvas-width",
        action="store_true",
        help="scale input image to full canvas width before vertical fit",
    )
    ap.add_argument(
        "--use-template-nozero",
        action="store_true",
        help="force btbuf[12] from template job and use template full width (template width + template nozero)",
    )
    ap.add_argument(
        "--keep-template-aabb",
        action="store_true",
        help="send original captured aabb payload(s) from template job instead of generated payload",
    )
    ap.add_argument(
        "--lzma-encoder",
        choices=["python", "xz"],
        default="python",
        help="LZMA encoder backend for generated payload",
    )
    args = ap.parse_args()

    dump_dir = Path(args.template_dump_dir)
    summary_path = dump_dir / "summary.json"
    messages_csv = dump_dir / "messages.csv"
    job_dir = dump_dir / f"job_{args.template_job:03d}"
    if not summary_path.exists() or not messages_csv.exists() or not job_dir.exists():
        raise SystemExit("template paths missing (need summary.json, messages.csv, job_XXX)")

    template = load_template_outgoing(job_dir, summary_path, messages_csv, args.template_job)
    tgeom = load_template_geometry(job_dir)
    canvas_width = args.canvas_width
    bpc = args.bytes_per_col
    if canvas_width <= 0:
        if tgeom and args.use_template_nozero:
            canvas_width = int(tgeom["width"]) + int(tgeom.get("no_zero_index", 0))
        else:
            canvas_width = tgeom["width"] if tgeom else 201
    if bpc <= 0:
        bpc = tgeom["bytes_per_col"] if tgeom else 12
    force_no_zero_index = int(tgeom.get("no_zero_index", 0)) if (tgeom and args.use_template_nozero) else -1
    btbuf, geom = image_to_btbuf_with_canvas(
        Path(args.image),
        args.threshold,
        canvas_width,
        bpc,
        scale_to_canvas_width=args.scale_to_canvas_width,
        force_no_zero_index=force_no_zero_index,
    )
    if args.lzma_encoder == "xz":
        lz, aabb = btbuf_to_aabb_payloads_xz(btbuf)
    else:
        lz, aabb = btbuf_to_aabb_payloads(btbuf)
    frames = materialize_frames(
        template,
        aabb,
        stop_after_aa10=(not args.full_sequence),
        post_frames_after_aa10=args.post_frames_after_aa10,
        keep_template_aabb=args.keep_template_aabb,
    )

    ts = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir) / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "btbuf.bin").write_bytes(btbuf)
    (out_dir / "lzma.bin").write_bytes(lz)
    for i, p in enumerate(aabb):
        (out_dir / f"aabb_{i:03d}.bin").write_bytes(p)
    with (out_dir / "frames.bin").open("wb") as f:
        for _, fr, _ts in frames:
            f.write(fr)

    meta = {
        "template_dump_dir": str(dump_dir),
        "template_job": args.template_job,
        "image": args.image,
        "geometry": geom,
        "lzma_len": len(lz),
        "aabb_chunks": len(aabb),
        "frames_total": len(frames),
        "frames": [{"index": i, "cmd": c, "len": len(fr)} for i, (c, fr, _ts) in enumerate(frames)],
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    if args.send:
        if not args.mac:
            raise SystemExit("--mac required with --send")
        channels = [args.channel]
        if args.channels.strip():
            channels = [int(x.strip()) for x in args.channels.split(",") if x.strip()]
        events: List[Dict[str, object]] = []
        try:
            used_ch, events = send_frames_try_channels(
                args.mac,
                channels,
                frames,
                args.connect_timeout,
                args.recv_timeout,
                args.delay_ms,
                args.timing_scale,
            )
            meta["send_channel"] = used_ch
        finally:
            (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
            (out_dir / "send_log.json").write_text(json.dumps(events, indent=2))

    print(out_dir)


if __name__ == "__main__":
    main()
