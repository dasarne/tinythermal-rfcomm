# INFO_FOR_AI.md

This file is a fast-context handover for future AI/LLM contributors.
If you are a human maintainer, this can still be useful as a high-level project map.

## Project Intent

`tinythermal-rfcomm` is a reverse-engineered Linux print path for a small Bluetooth thermal printer family.
Goal: make printing work reliably from Linux with minimal friction, while preserving enough low-level detail for protocol extension/porting.

## Proven Scope

- Reverse engineering and successful print validation were performed on:
  - Katasymbol E10
  - build year 2025
- Do not assume cross-model compatibility without capture-based verification.

## Current Practical Baselines

- Short/default path:
  - `sudo python3 scripts/katasymbol_print.py <image>`
  - verified working
- Long bitmap path:
  - `sudo python3 scripts/katasymbol_print.py <image>.png`
  - suitable wide bitmap inputs now auto-select the long bitmap path
  - `--long-label-bitmap` remains available as an explicit override
  - currently the best known long-label path
  - visually very close to the vendor app
- Long SVG path:
  - `sudo python3 scripts/katasymbol_print.py <image>.svg`
  - suitable wide SVG inputs now auto-select the validated long SVG path
  - `--long-label-svg` remains available as an explicit override
  - validated on `Inkscape-Test.svg` against both dry-run artifacts and physical print comparison
  - for the validated reference case, SVG and bitmap converge to identical `btbuf` output

Validated long-label SVG frontend settings for the current reference case:

- renderer: `rsvg-convert`
- `svg_pixels_per_mm = 12.0`
- `dither = threshold`
- `threshold = 230`
- `bbox_inset_y = 1`

Known remaining visual deviation on the validated long-label reference path:

- no dominant remaining issue in the validated `Inkscape-Test.png` / `Inkscape-Test.svg` case
- if future edge cases differ, compare at the bitmap/raster stage first, not at transport
- for the separate `W` diagnostic class, the relevant `T15`-style `btbuf` path uses `data_offset = 14`

## Trust Levels (Important)

Use these labels when changing behavior:

- `verified`: observed in captures and confirmed by successful print tests
- `inferred`: likely based on behavior, but not vendor-confirmed
- `unknown`: plausible but unverified; avoid hard assumptions

Current examples:

- `verified`: envelope sync (`7e5a`), `1001/1002`, `aabb` chunk layout, `aa10` trigger usage
- `verified`: relevant `T15`-style `btbuf` raster payload starts at offset `14`
- `inferred`: detailed semantic names for many `aa..` commands

## Architecture Map

- `scripts/katasymbol_print.py`
  - user-facing wrapper
  - image preprocessing, config, Bluetooth preflight, template auto-select
- `scripts/replay_sender.py`
  - low-level protocol builder/sender
  - converts image -> `btbuf` -> LZMA -> `aabb`
  - replays captured command sequence with replaced payload
- `scripts/decode_spp.py`
  - extracts outgoing print jobs from dump/capture logs
- `scripts/decode_lzma_btbuf.py`
  - decodes `aabb` back to `btbuf`/renderings for analysis
- `scripts/analyze_payloads.py`
  - comparison and reporting utilities
- `scripts/diagnostics/compare_svg_bitmap_frontend.py`
  - compare SVG rasterization against a bitmap reference before binarization
- `scripts/diagnostics/sweep_svg_postprocess.py`
  - sweep SVG frontend postprocessing against a bitmap reference
- `scripts/diagnostics/`
  - grouped location for transient diagnostics, test-image generators, sweeps, and vendor-path experiments
- `docs/DIAGNOSTIC_MATRIX.md`
  - current working split of remaining print defects into `H/E/T/W/C`

## Operational Realities

- Printer firmware can become unstable/frozen.
- Bluetooth link stability dominates reliability (`l2ping` success is a strong prerequisite).
- "Technically valid send_log" does not always guarantee physical print.

See:

- `docs/TROUBLESHOOTING.md`
- `docs/PROTOCOL.md`

## Change Strategy for Future Contributors

1. Keep a known-good baseline run for bytewise comparison.
2. Change one protocol/timing variable at a time.
3. Always store artifacts (`meta.json`, `send_log.json`, payload binaries).
4. Annotate commits with whether a change is:
   - behavior-preserving refactor
   - protocol-affecting change
   - operational workaround

## Suggested Documentation Conventions

When updating docs/code comments, keep this format:

- "Observed": raw captured behavior
- "Implemented": what this repo currently does
- "Rationale": why this implementation choice was made
- "Risk": what might break on other devices

## Existing Ecosystem "Standards" for AI Context Files

No single universal standard exists yet. In practice, these files are common:

- `AGENTS.md` (agent/tooling instructions)
- `CLAUDE.md` / `CURSOR.md` / `COPILOT.md` (tool-specific guidance)
- `CONTRIBUTING.md` (human + AI contribution expectations)
- dedicated handover files like this one (`INFO_FOR_AI.md`)

Recommendation for this repo:

- keep `INFO_FOR_AI.md` short and factual
- keep protocol truth in `docs/PROTOCOL.md`
- keep operational truth in `docs/TROUBLESHOOTING.md`

## Quick Start for a Future LLM Session

1. Read `README.md` for user goals and scope.
2. Read `docs/PROTOCOL.md` for on-wire behavior.
3. Read `docs/TROUBLESHOOTING.md` for known failure modes.
4. Inspect latest `out/replay_sender/<timestamp>/meta.json` and `send_log.json` examples (if available).
5. If the question is SVG-vs-bitmap quality, inspect `out/svg_frontend_compare/` and `out/svg_postprocess_sweep/` before touching transport.
6. Only then modify `scripts/replay_sender.py` or transport timings.
