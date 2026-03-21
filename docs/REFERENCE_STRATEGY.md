# Reference Strategy

This project now uses an explicit two-layer reference model:

1. captured vendor traffic as the ultimate protocol ground truth
2. the embedded Java LZMA path as the current executable encoder ground truth

That split matters because not every input image has a captured on-wire reference, but every generated image can be compared against the Java path.

## Why This Exists

The reverse-engineering work established:

- generated `btbuf` data can be made printer-compatible
- Python `lzma` / `xz` streams were formally valid but did not print reliably
- the Java LZMA path derived from the vendor APK does print reliably

So the project now treats the Java encoder as the default compatibility anchor, while captured `aabb` frames remain the final truth when a real vendor reference exists.

## Ground Truth Levels

### Level 1: Captured Vendor Traffic

Use this when the repository contains a real capture for the same print case.

Current canonical example:

- decoded template `btbuf`: `out/btbuf_decode/out__decode__dumpstate-2026-03-16-16-45-48_ref_pattern__job_001/btbuf.bin`
- captured `aabb` chunk: `out/decode/dumpstate-2026-03-16-16-45-48_ref_pattern/job_001/002_aabb_f001145_len0504.bin`

This is the strongest available reference because it comes from the real vendor app talking to the real printer.

### Level 2: Java Encoder Ground Truth

Use this when no captured `aabb` exists for a given input image.

Current implementation:

- helper: `tools/java/ApkLzmaEncode.java`
- vendored SDK: `third_party/lzma-sdk-java/`
- backend module: `scripts/encoder_backends.py`

This path reproduces the vendor-style LZMA encoder closely enough to produce stable prints.

### Level 3: Candidate Encoders

These are useful for comparison, regression analysis, and future simplification:

- `python`
- `xz`

They are currently treated as experimental candidates, not as default production paths.

## Recommended Workflow

### For protocol work

1. compare against captured traffic if available
2. if no capture exists, compare against the Java encoder path
3. only then evaluate whether a Python-native encoder can be brought closer

### For image/raster work

1. hold encoder constant at `java`
2. vary raster generation / placement / scaling
3. validate with dry runs first, hardware second

### For encoder work

1. keep raster constant
2. compare candidate encoders directly to `java`
3. use capture comparisons only for cases with a known vendor reference

## Regression Harness

Use:

```bash
python3 scripts/analyze_lzma_encoders.py
```

Outputs:

- timestamped run directory under `out/encoder_matrix/`
- generated dry-run artifacts for each image/encoder pair
- `summary.json` with:
  - comparison to captured `aabb` LZMA body
  - comparison to the selected reference encoder (`java` by default)

## Decision Rules

When changing code:

- if `java` regresses against capture for the template case, stop and investigate
- if a raster change improves print quality while `java` remains stable, keep it isolated to raster code
- do not treat a candidate encoder as production-ready just because it matches public LZMA parameters

## Current Default

The repository currently defaults to:

- `--lzma-encoder java`

That default is operational, not ideological. If a Python-native encoder later reproduces the same behavior well enough, the default can change again.
