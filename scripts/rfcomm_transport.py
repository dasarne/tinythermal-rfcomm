#!/usr/bin/env python3
import socket
import time
from typing import Dict, List, Optional, Tuple


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
        for i, (cmd, fr, ts0) in enumerate(frames):
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
                dt = max(0.0, (frames[i + 1][2] - ts0) * timing_scale)
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
