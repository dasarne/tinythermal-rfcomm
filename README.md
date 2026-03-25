# tinythermal-rfcomm

Unofficial Linux Bluetooth printing toolkit for small thermal label/receipt printers that expose an RFCOMM Serial Port profile.

This project is not affiliated with or endorsed by any printer vendor.

## Project Principle

This repository is intentionally context-rich so future maintainers (human or AI) can continue quickly:

- understand what is known
- understand what is inferred
- reproduce and extend with minimal repeated discovery work

## Compatibility Scope

- Protocol was derived on a Katasymbol E10 (manufacturing year 2025).
- Runtime tests were performed only on this exact device family/sample.
- Other models/firmware revisions may require protocol or timing adjustments.

## What It Does

- Converts an input image to the printer bitstream (`btbuf`).
- Compresses and packets data into protocol chunks (`aabb` frames).
- Replays a known-good command sequence over Bluetooth RFCOMM.
- Provides a high-level CLI that can auto-select templates and optionally auto-discover a printer.

## Current Status

- Works on Linux (tested on Manjaro/KDE) with BlueZ.
- Protocol path is reverse-engineered from real captures.
- Default short-label path is stable for everyday printing.
- Long bitmap labels can now be reproduced essentially at vendor quality.
- Long SVG labels can now be reproduced on the same validated path for suitable physical label sizes.
- Suitable long bitmap and SVG inputs are auto-detected; no extra CLI flag is normally required.
- Known risk: some printers can enter a bad firmware state (freeze/no reset path) after failed sessions.

Read this first: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

## Install

Arch / Manjaro:

```bash
sudo pacman -S --needed bluez bluez-utils python python-pillow
sudo systemctl enable --now bluetooth.service
```

Debian / Ubuntu:

```bash
sudo apt update
sudo apt install -y bluez python3 python3-pil
sudo systemctl enable --now bluetooth
```

Fedora:

```bash
sudo dnf install -y bluez bluez-tools python3 python3-pillow
sudo systemctl enable --now bluetooth
```

openSUSE (Tumbleweed/Leap):

```bash
sudo zypper install -y bluez python3 python3-Pillow
sudo systemctl enable --now bluetooth
```

## Quickstart (First Print)

1. Pair and trust printer once:

```bash
bluetoothctl
```

Then, in the interactive `bluetoothctl` prompt, enter:

```text
power on
agent on
default-agent
scan on
# find printer MAC
pair AA:BB:CC:DD:EE:FF
trust AA:BB:CC:DD:EE:FF
scan off
quit
```

2. Optional link check:

```bash
sudo l2ping -c 3 AA:BB:CC:DD:EE:FF
```

3. Print:

```bash
sudo python3 scripts/katasymbol_print.py test_pattern_64x32.png --mac AA:BB:CC:DD:EE:FF
```

## User CLI

Main command:

```bash
sudo python3 scripts/katasymbol_print.py <image>
```

Current wrapper defaults already use the known-good production path:

- image preprocessing enabled
- content-aware white-margin crop enabled
- Java LZMA encoder
- `decoded-template-bbox` raster compatibility preset
- Bluetooth auto-discovery/template auto-selection when available

Supported input formats:

- raster: `PNG`, `JPG`
- vector: `SVG` (rasterized automatically via `rsvg-convert`, fallback `magick`)

Useful options:

- `--mac <MAC>`: explicit printer MAC.
- `--dry-run`: build payload/artifacts only, do not send.
- `--prepare-only`: only run image preprocessing, then exit (no protocol build/send).
- `--bt-preflight`: enable Bluetooth wakeup/scan/l2ping preflight before sending.
- `--slow`: fallback timing mode using original template pacing.
- `--aggressive`: riskier transport mode with extra post-trigger frames and shorter inter-frame delay.
- `--long-label-svg`: explicit override for the validated long SVG preset based on `InkscapeTest2/job_002`.
- `--long-label-bitmap`: explicit override for the current best long bitmap preset based on `InkscapeTest2/job_002`.
- `--lzma-encoder java|python|xz`: transfer encoder backend. `java` is the current default and known-good path.
- `--compat-raster-preset ...`: reverse-engineering/testing override. Normal users should not need this.
- `--fit-mode shrink|fit|stretch`
- `--rotate auto|0|90|180|270`
- `--dither auto|threshold|floyd|ordered`
- `--no-crop-content`: keep original white margins instead of cropping to visible content
- `--despeckle`: optional removal of isolated black speckles; useful for noisy line art, not enabled by default
- `--config <path>`: use alternate config file.
- `--print-config`: print merged defaults.

Artifacts:

- `out/replay_sender/<timestamp>/meta.json`
- `out/replay_sender/<timestamp>/send_log.json` (when sending)
- `out/replay_sender/<timestamp>/btbuf.bin`

Image-prep only (recommended to verify rotation/sizing before sending):

```bash
python3 scripts/katasymbol_print.py <image> --prepare-only
```

Normal send mode:

```bash
sudo python3 scripts/katasymbol_print.py <image>
```

Send with explicit Bluetooth wakeup/preflight:

```bash
sudo python3 scripts/katasymbol_print.py <image> --bt-preflight
```

Long physical SVG label:

```bash
sudo python3 scripts/katasymbol_print.py Inkscape-Test.svg
```

Note:

- suitable long SVG inputs now auto-select the validated long-label SVG path
- `--long-label-svg` remains available as an explicit override
- the validated SVG preset currently uses:
  - `rsvg-convert`
  - `svg_pixels_per_mm = 12.0`
  - `dither = threshold`
  - `threshold = 230`
  - `bbox_inset_y = 1`

Long physical bitmap label:

```bash
sudo python3 scripts/katasymbol_print.py Inkscape-Test.png
```

Current assessment:

- bitmap and SVG reference cases now converge to identical dry-run `btbuf` artifacts
- auto-selection chooses the validated long-label preset for suitable wide bitmap and SVG inputs
- the bitmap path remains the simplest physical reference when debugging frontend questions
- `--long-label-bitmap` remains available as an explicit override

Slower fallback mode for diagnostics:

```bash
sudo python3 scripts/katasymbol_print.py <image> --slow
```

Aggressive mode for transport experiments:

```bash
sudo python3 scripts/katasymbol_print.py <image> --aggressive
```

Encoder dry-run matrix:

```bash
python3 scripts/analyze_lzma_encoders.py
```

## Developer Docs

- Protocol details: [docs/PROTOCOL.md](docs/PROTOCOL.md)
- Reference model and regression workflow: [docs/REFERENCE_STRATEGY.md](docs/REFERENCE_STRATEGY.md)
- Architecture assessment and refactor direction: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Failure handling and operational notes: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
- Reverse engineering workflow: [docs/REVERSE_ENGINEERING_HOWTO.md](docs/REVERSE_ENGINEERING_HOWTO.md)
- AI/LLM handover context: [INFO_FOR_AI.md](INFO_FOR_AI.md)

Frontend comparison helpers for maintainers / AI sessions:

- `scripts/diagnostics/compare_svg_bitmap_frontend.py`: compare SVG rasterization against a bitmap reference before binarization
- `scripts/diagnostics/sweep_svg_postprocess.py`: sweep SVG grayscale postprocessing against a bitmap reference

Recent low-level finding for maintainers:

- the relevant `T15`-style `btbuf` layout uses raster data starting at offset `14`, not `16`
- this was the key fix for the previously isolated vertical-wrap (`W`) defect in the diagnostic path

Current print-defect classes and their evidence matrix:

- `docs/DIAGNOSTIC_MATRIX.md`

## Transparency

- This repository was largely developed with GPT-5.3/Codex assistance ("vibe-coded").
- Treat the source as a practical reverse-engineering artifact, not as a formally verified implementation.
- The maintainer may not be able to provide deeper internals beyond what is documented here.
- The project intentionally keeps rich logs/context to help future maintainers and LLM-based contributors continue the work.

## Repository Layout

- Core print path:
  - `scripts/katasymbol_print.py`: user-facing print wrapper
  - `scripts/replay_sender.py`: low-level protocol sender
  - `scripts/raster_btbuf.py`: isolated btbuf/template raster handling
  - `scripts/image_input.py`: image/SVG loading and preprocessing
  - `scripts/encoder_backends.py`: isolated LZMA/aabb encoder backends
  - `scripts/protocol_frames.py`: isolated protocol frame/materialization logic
  - `scripts/rfcomm_transport.py`: isolated RFCOMM transport/send logging
- Reverse-engineering helpers:
  - `scripts/decode_spp.py`: decode outgoing SPP/BTSnoop captures
  - `scripts/decode_lzma_btbuf.py`: decode captured `aabb` payloads
  - `scripts/analyze_payloads.py`: compare and inspect payload behavior
- Diagnostics and one-off analysis live under `scripts/diagnostics/`
  - test image generators
  - raster/btbuf analyzers
  - SVG/frontend comparison sweeps
  - vendor-path experiments

## Safety Notes

- Do not send repeated stress runs without pauses.
- If the printer becomes unresponsive, stop sending immediately.
- Prefer one print job per power cycle while debugging.
