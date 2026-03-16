# tinythermal-rfcomm

Unofficial Linux Bluetooth printing toolkit for small thermal label/receipt printers that expose an RFCOMM Serial Port profile.

This project is not affiliated with or endorsed by any printer vendor.

## What It Does

- Converts an input image to the printer bitstream (`btbuf`).
- Compresses and packets data into protocol chunks (`aabb` frames).
- Replays a known-good command sequence over Bluetooth RFCOMM.
- Provides a high-level CLI that can auto-select templates and optionally auto-discover a printer.

## Current Status

- Works on Linux (tested on Manjaro/KDE) with BlueZ.
- Protocol path is reverse-engineered from real captures.
- Known risk: some printers can enter a bad firmware state (freeze/no reset path) after failed sessions.

Read this first: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

## Install

```bash
sudo pacman -S --needed bluez bluez-utils python python-pillow
sudo systemctl enable --now bluetooth.service
```

## Quickstart (First Print)

1. Pair and trust printer once:

```bash
bluetoothctl
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

Useful options:

- `--mac <MAC>`: explicit printer MAC.
- `--dry-run`: build payload/artifacts only, do not send.
- `--fit-mode shrink|fit|stretch`
- `--rotate auto|0|90|180|270`
- `--dither auto|threshold|floyd|ordered`
- `--config <path>`: use alternate config file.
- `--print-config`: print merged defaults.

Artifacts:

- `out/replay_sender/<timestamp>/meta.json`
- `out/replay_sender/<timestamp>/send_log.json` (when sending)
- `out/replay_sender/<timestamp>/btbuf.bin`

## Developer Docs

- Protocol details: [docs/PROTOCOL.md](docs/PROTOCOL.md)
- Failure handling and operational notes: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

## Repository Layout

- `scripts/katasymbol_print.py`: user-facing print wrapper
- `scripts/replay_sender.py`: low-level protocol sender
- `scripts/decode_spp.py`: decode outgoing SPP/BTSnoop captures
- `scripts/decode_lzma_btbuf.py`: decode captured `aabb` payloads
- `scripts/analyze_payloads.py`: compare and inspect payload behavior

## Safety Notes

- Do not send repeated stress runs without pauses.
- If the printer becomes unresponsive, stop sending immediately.
- Prefer one print job per power cycle while debugging.
