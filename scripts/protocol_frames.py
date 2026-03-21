#!/usr/bin/env python3
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple


BIG_RE = re.compile(r"\d{3}_(aad1|aabb)_f(\d+)_len\d+\.bin$")


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
