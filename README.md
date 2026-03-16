# Katasymbol Linux Printing (RFCOMM)

This repo contains a working Linux print path for a Katasymbol thermal printer over Bluetooth RFCOMM.

## Requirements

- Manjaro/Arch packages:
  - `bluez`
  - `bluez-utils`
  - `python`
  - `python-pillow`
- Bluetooth service:
  - `sudo systemctl enable --now bluetooth.service`

## 3-Command Quickstart

1. Pair and trust device (once):

```bash
bluetoothctl
power on
agent on
default-agent
scan on
# wait for device MAC, then:
pair A4:93:40:5E:7B:74
trust A4:93:40:5E:7B:74
scan off
quit
```

2. Link test:

```bash
sudo l2ping -c 3 A4:93:40:5E:7B:74
sudo rfcomm -i hci0 connect 0 A4:93:40:5E:7B:74 1
```

3. Print image:

```bash
sudo python3 scripts/katasymbol_print.py test_pattern_64x32.png
```

By default, the wrapper auto-selects the newest decoded dump in `out/decode/` as template source.
If no `--mac` is given, it tries config MAC first, then Bluetooth auto-discovery.

## Main CLI

Use this for normal printing:

```bash
sudo python3 scripts/katasymbol_print.py <image.png>
```

Useful options:

- `--config <path>`: use another config file.
- `--init-config`: create default config and exit.
- `--print-config`: print effective config and exit.
- `--mac <MAC>`: explicit printer MAC (overrides config/discovery).
- `--printer-name-pattern <str>`: extra matching token for auto-discovery (repeatable).
- `--no-auto-discover`: require explicit/configured MAC.
- `--dry-run`: build payloads/logs only, do not send.
- `--channels 1,2,3`: fallback channel list.
- `--keep-template-aabb`: diagnostic mode (sends captured template payload).
- `--lzma-encoder xz`: use `xz` backend instead of Python `lzma` for generated payload.
- Image preparation (enabled by default):
  - `--rotate auto|0|90|180|270` (`auto`: lange Bildseite bleibt Drucklaenge)
  - `--fit-mode shrink|fit|stretch`
  - `--dither auto|threshold|floyd|ordered`
  - `--align center|top|bottom`
  - `--offset-x N`, `--offset-y N`
  - `--head-height N` (default from template geometry)
  - `--no-prepare` to bypass preprocessing.

Artifacts are written to `out/replay_sender/<timestamp>/`:

- `meta.json`
- `send_log.json` (when `--send`)
- `btbuf.bin`, `lzma.bin`, `aabb_*.bin`, `frames.bin`

## Defaults File

On first run, `scripts/katasymbol_print.py` creates `.katasymbol_print.json` in the current directory if missing.
This file contains defaults for:

- printer selection (`mac`, `auto_discover`, `name_patterns`)
- connection timing (`channel`, `channels`, timeouts, delays)
- image preprocessing (`rotate`, `fit_mode`, `dither`, alignment/offsets)
- transfer toggles (`scale_to_canvas_width`, `use_template_nozero`)

## Tools

- `scripts/katasymbol_print.py`: user-facing wrapper.
- `scripts/replay_sender.py`: protocol sender/engine.
- `scripts/decode_spp.py`: decode SPP stream from dumpstate `btsnoop_hci.log`.
- `scripts/decode_lzma_btbuf.py`: decode `aabb` payloads to btBuf and render.

## CUPS Backend Sketch (optional)

If you later want CUPS integration, create a backend script that receives the print file from CUPS and forwards it to `katasymbol_print.py`.

Minimal sketch:

```sh
#!/bin/sh
# /usr/lib/cups/backend/katasymbol
# argv: job-id user title copies options [file]
FILE="$6"
[ -z "$FILE" ] && FILE="-"
/usr/bin/python3 /path/to/Katasymbol/scripts/katasymbol_print.py "$FILE" --mac A4:93:40:5E:7B:74
```

Then map queue data to PNG/PBM before calling the script (e.g. with ImageMagick/Ghostscript filter chain).
