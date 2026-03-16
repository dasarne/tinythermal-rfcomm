#!/usr/bin/env python3
import argparse
import binascii
import csv
import hashlib
import json
import os
import struct
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


MAGIC = b"\x7e\x5a"


@dataclass
class Chunk:
    frame_no: int
    time_rel: float
    data: bytes


@dataclass
class Message:
    frame_start: int
    frame_end: int
    time_start: float
    time_end: float
    raw: bytes

    @property
    def length_field(self) -> int:
        return struct.unpack_from("<H", self.raw, 2)[0]

    @property
    def channel(self) -> int:
        return struct.unpack_from(">H", self.raw, 4)[0]

    @property
    def cmd(self) -> int:
        return struct.unpack_from(">H", self.raw, 6)[0]

    @property
    def payload(self) -> bytes:
        return self.raw[8:]

    @property
    def cmd_hex(self) -> str:
        return f"{self.cmd:04x}"

    @property
    def channel_hex(self) -> str:
        return f"{self.channel:04x}"


def run_tshark_extract(btsnoop_path: str) -> List[Chunk]:
    cmd = [
        "tshark",
        "-r",
        btsnoop_path,
        "-Y",
        "btspp.data",
        "-T",
        "fields",
        "-e",
        "frame.number",
        "-e",
        "frame.time_relative",
        "-e",
        "btspp.data",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    chunks: List[Chunk] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        frame_s, time_s, data_hex = parts[0], parts[1], parts[2].strip()
        if not data_hex:
            continue
        try:
            data = bytes.fromhex(data_hex)
        except ValueError:
            continue
        chunks.append(Chunk(frame_no=int(frame_s), time_rel=float(time_s), data=data))
    return chunks


def reassemble_messages(chunks: List[Chunk]) -> List[Message]:
    messages: List[Message] = []
    current = bytearray()
    current_need: Optional[int] = None
    frame_start = -1
    time_start = 0.0

    def flush_complete(frame_no: int, time_rel: float) -> None:
        nonlocal current, current_need, frame_start, time_start
        if current_need is None or len(current) < current_need:
            return
        raw = bytes(current[:current_need])
        msg = Message(
            frame_start=frame_start,
            frame_end=frame_no,
            time_start=time_start,
            time_end=time_rel,
            raw=raw,
        )
        messages.append(msg)
        rest = current[current_need:]
        current = bytearray(rest)
        current_need = None
        frame_start = -1
        time_start = 0.0

    i = 0
    while i < len(chunks):
        ch = chunks[i]
        data = ch.data
        j = 0
        while j < len(data):
            if current_need is None:
                idx = data.find(MAGIC, j)
                if idx < 0:
                    break
                if len(data) - idx < 4:
                    current = bytearray(data[idx:])
                    frame_start = ch.frame_no
                    time_start = ch.time_rel
                    j = len(data)
                    continue
                length_field = struct.unpack_from("<H", data, idx + 2)[0]
                current_need = 4 + length_field
                current = bytearray(data[idx:])
                frame_start = ch.frame_no
                time_start = ch.time_rel
                j = len(data)
                flush_complete(ch.frame_no, ch.time_rel)
            else:
                needed = current_need - len(current)
                take = min(needed, len(data) - j)
                current.extend(data[j : j + take])
                j += take
                flush_complete(ch.frame_no, ch.time_rel)
                if current_need is None and j < len(data):
                    continue
        i += 1
    return messages


def split_jobs(messages: List[Message], gap_seconds: float) -> List[List[Message]]:
    if not messages:
        return []
    jobs: List[List[Message]] = [[messages[0]]]
    for m in messages[1:]:
        prev = jobs[-1][-1]
        if m.time_start - prev.time_end > gap_seconds:
            jobs.append([m])
        else:
            jobs[-1].append(m)
    return jobs


def summarize_job(job: List[Message]) -> Dict[str, object]:
    counts: Dict[str, int] = {}
    for m in job:
        counts[m.cmd_hex] = counts.get(m.cmd_hex, 0) + 1

    big_payloads = []
    for m in job:
        if m.cmd_hex not in {"aabb", "aad1"}:
            continue
        payload = m.payload
        big_payloads.append(
            {
                "cmd": m.cmd_hex,
                "frame_start": m.frame_start,
                "frame_end": m.frame_end,
                "time_start": m.time_start,
                "time_end": m.time_end,
                "payload_len": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "prefix_hex": payload[:32].hex(),
            }
        )

    return {
        "frame_start": job[0].frame_start,
        "frame_end": job[-1].frame_end,
        "time_start": job[0].time_start,
        "time_end": job[-1].time_end,
        "message_count": len(job),
        "cmd_counts": dict(sorted(counts.items())),
        "big_payloads": big_payloads,
    }


def dump_job_payloads(job: List[Message], out_dir: str, job_idx: int) -> None:
    job_dir = os.path.join(out_dir, f"job_{job_idx:03d}")
    os.makedirs(job_dir, exist_ok=True)
    n = 0
    for m in job:
        if m.cmd_hex not in {"aabb", "aad1"}:
            continue
        path = os.path.join(
            job_dir,
            f"{n:03d}_{m.cmd_hex}_f{m.frame_start:06d}_len{len(m.payload):04d}.bin",
        )
        with open(path, "wb") as f:
            f.write(m.payload)
        n += 1


def write_messages_csv(messages: List[Message], path: str) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "frame_start",
                "frame_end",
                "time_start",
                "time_end",
                "channel",
                "cmd",
                "msg_len",
                "payload_len",
                "payload_prefix_hex",
            ]
        )
        for m in messages:
            w.writerow(
                [
                    m.frame_start,
                    m.frame_end,
                    f"{m.time_start:.6f}",
                    f"{m.time_end:.6f}",
                    m.channel_hex,
                    m.cmd_hex,
                    len(m.raw),
                    len(m.payload),
                    m.payload[:24].hex(),
                ]
            )


def process_dump(
    dump_zip: str, out_root: str, gap_seconds: float
) -> Tuple[str, Dict[str, object]]:
    base = os.path.splitext(os.path.basename(dump_zip))[0]
    out_dir = os.path.join(out_root, base)
    os.makedirs(out_dir, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="katasymbol_") as tmp:
        btsnoop_path = os.path.join(tmp, "btsnoop_hci.log")
        with zipfile.ZipFile(dump_zip) as zf:
            with zf.open("FS/data/log/bt/btsnoop_hci.log") as src, open(
                btsnoop_path, "wb"
            ) as dst:
                dst.write(src.read())

        chunks = run_tshark_extract(btsnoop_path)
        messages = reassemble_messages(chunks)
        jobs = split_jobs(messages, gap_seconds=gap_seconds)

    write_messages_csv(messages, os.path.join(out_dir, "messages.csv"))
    for i, job in enumerate(jobs, start=1):
        dump_job_payloads(job, out_dir, i)

    summary = {
        "dump": dump_zip,
        "chunks_spp": len(chunks),
        "messages_reassembled": len(messages),
        "jobs": [summarize_job(job) for job in jobs],
    }
    with open(os.path.join(out_dir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    return out_dir, summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Decode Katasymbol SPP stream from dumpstate btsnoop logs."
    )
    parser.add_argument("dumps", nargs="+", help="dumpstate zip files")
    parser.add_argument("--out", default="out/decode", help="output directory")
    parser.add_argument(
        "--gap-seconds",
        type=float,
        default=30.0,
        help="time gap to split jobs",
    )
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    all_results = []
    for dump in args.dumps:
        out_dir, summary = process_dump(dump, args.out, args.gap_seconds)
        all_results.append({"out_dir": out_dir, "summary": summary})

    print(json.dumps(all_results, indent=2))


if __name__ == "__main__":
    main()
