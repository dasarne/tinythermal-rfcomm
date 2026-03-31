#!/usr/bin/env python3
import lzma
import subprocess
from pathlib import Path
from typing import List, Tuple


def chunk_lzma_for_aabb(lz: bytes) -> List[bytes]:
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
    return out


def btbuf_to_aabb_payloads_python(btbuf: bytes) -> Tuple[bytes, List[bytes]]:
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
    if len(lz) >= 13:
        lz = lz[:5] + len(btbuf).to_bytes(8, "little") + lz[13:]
    return lz, chunk_lzma_for_aabb(lz)


def java_lzma_encoder_paths(repo_root: Path) -> Tuple[Path, Path, Path]:
    helper_java = repo_root / "tools" / "java" / "ApkLzmaEncode.java"
    sdk_root = repo_root / "third_party" / "lzma-sdk-java"
    build_dir = repo_root / "tools" / "java" / "build"
    return helper_java, sdk_root, build_dir


def ensure_java_lzma_encoder_compiled(repo_root: Path) -> Path:
    helper_java, sdk_root, build_dir = java_lzma_encoder_paths(repo_root)
    sevenzip_root = sdk_root / "SevenZip"
    java_sources = [helper_java, *sorted(sevenzip_root.rglob("*.java"))]
    if len(java_sources) <= 1:
        raise RuntimeError(f"missing Java LZMA SDK sources under {sevenzip_root}")
    main_class = build_dir / "ApkLzmaEncode.class"
    latest_src_mtime = max(p.stat().st_mtime for p in java_sources)
    if main_class.exists() and main_class.stat().st_mtime >= latest_src_mtime:
        return build_dir
    build_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["javac", "-d", str(build_dir), *[str(p) for p in java_sources]]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"javac failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return build_dir


def btbuf_to_aabb_payloads_java(btbuf: bytes, repo_root: Path) -> Tuple[bytes, List[bytes]]:
    build_dir = ensure_java_lzma_encoder_compiled(repo_root)
    cmd = ["java", "-cp", str(build_dir), "ApkLzmaEncode"]
    proc = subprocess.run(cmd, input=btbuf, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"java compression failed: {proc.stderr.decode(errors='ignore').strip()}")
    lz = proc.stdout
    return lz, chunk_lzma_for_aabb(lz)


def btbuf_to_aabb_payloads_xz(btbuf: bytes) -> Tuple[bytes, List[bytes]]:
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
    if len(lz) >= 13:
        lz = lz[:5] + len(btbuf).to_bytes(8, "little") + lz[13:]
    return lz, chunk_lzma_for_aabb(lz)


def encode_btbuf(btbuf: bytes, encoder: str, repo_root: Path) -> Tuple[bytes, List[bytes]]:
    if encoder == "java":
        return btbuf_to_aabb_payloads_java(btbuf, repo_root)
    if encoder == "xz":
        return btbuf_to_aabb_payloads_xz(btbuf)
    if encoder == "python":
        return btbuf_to_aabb_payloads_python(btbuf)
    raise ValueError(f"unsupported encoder backend: {encoder}")


def encode_btbuf_pages(btbuf_pages: List[bytes], encoder: str, repo_root: Path) -> Tuple[List[bytes], List[List[bytes]]]:
    lz_streams: List[bytes] = []
    aabb_groups: List[List[bytes]] = []
    for page in btbuf_pages:
        lz, aabb = encode_btbuf(page, encoder, repo_root)
        lz_streams.append(lz)
        aabb_groups.append(aabb)
    return lz_streams, aabb_groups
