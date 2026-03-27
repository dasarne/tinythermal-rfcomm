# Troubleshooting

## Important Warning

Some printers can enter a firmware deadlock state (blink, no print, no clean power-off).
If that happens:

- stop all further send attempts
- physically power-cycle/reset if possible
- avoid repeated stress retries

## Quick Diagnostics

Run in this order:

```bash
bluetoothctl show
bluetoothctl devices
bluetoothctl info AA:BB:CC:DD:EE:FF
sudo l2ping -i hci0 -c 3 AA:BB:CC:DD:EE:FF
```

If `l2ping` fails, RFCOMM print will fail too.

## Error: `No route to host`

Meaning: no usable ACL link to printer.

Actions:

1. Disable phone Bluetooth temporarily (avoid competing connection).
2. Restart adapter/service:
   ```bash
   sudo systemctl restart bluetooth
   ```
3. Re-scan and verify RSSI appears in `bluetoothctl info`.
4. Re-test `l2ping`.
5. Retry print.

## Error: `TimeoutError` on `sock.connect`

Meaning: RFCOMM channel was not reachable in time.

Actions:

- increase connect timeout:
  ```bash
  --connect-timeout 8
  ```
- try fallback channels:
  ```bash
  --channels 1,2,3,4
  ```
- ensure no stale connection from another host/app.

## Printer blinks but prints nothing

If `send_log.json` shows valid responses through `aa10`, but no print:

- likely printer state issue (battery/head temp/mechanical/firmware)
- wait and power-cycle printer
- test one known-good high-contrast image first (about 201x96 px)
- do not chain multiple jobs quickly

## Pairing problems (`AuthenticationFailed`)

`AuthenticationFailed` can still appear even when trust/connect occasionally works.

Try:

```bash
bluetoothctl
power on
scan on
pair AA:BB:CC:DD:EE:FF
trust AA:BB:CC:DD:EE:FF
scan off
quit
```

If unstable, remove and re-pair:

```bash
bluetoothctl remove AA:BB:CC:DD:EE:FF
```

Then pair again.

## `send_log.json` is empty (`[]`)

Meaning: connection failed before first frame TX.

Check:

- MAC is correct
- channel list is sensible
- `l2ping` success before print

## Safe Operating Pattern

Until firmware behavior is fully characterized:

1. One print job per power cycle.
2. Use conservative pacing (`--delay-ms 30` or higher).
3. Keep post-trigger frames low (default already limited).
4. Prefer known-good template and image during debugging.
5. If the fast default path misbehaves, retry with slower original pacing:
   ```bash
   sudo python3 scripts/katasymbol_print.py <image> --slow
   ```
6. If the printer is asleep or Bluetooth is flaky, retry with explicit wakeup/preflight:
   ```bash
   sudo python3 scripts/katasymbol_print.py <image> --bt-preflight
   ```
7. Prefer the Java LZMA backend unless you are intentionally comparing encoders:
   ```bash
   sudo python3 scripts/katasymbol_print.py <image> --lzma-encoder java
   ```
8. For long physical SVG labels, the normal command now auto-selects the validated long SVG path for suitable inputs:
   ```bash
   sudo python3 scripts/katasymbol_print.py <image>.svg
   ```
   If you need to force that path explicitly:
   ```bash
   sudo python3 scripts/katasymbol_print.py <image>.svg --long-label-svg
   ```
   Current validated long-label path details:
   - uses the vendor-nearer `vendor-like-t15` raster class
   - disables the generic prepare stage
   - uses centered placement with `contain`
   - uses threshold binarization with `threshold = 230`
   - SVG additionally uses `svg_pixels_per_mm = 12.0`
9. For long bitmap labels where the bitmap itself is the reference, the normal command now auto-selects the long bitmap path for suitable inputs:
   ```bash
   sudo python3 scripts/katasymbol_print.py <image>.png
   ```
   If you need to force that path explicitly:
   ```bash
   sudo python3 scripts/katasymbol_print.py <image>.png --long-label-bitmap
   ```
10. `--t-experimental` currently aliases the same validated long-label path and is not needed for normal use.
11. The wrapper default raster preset is already the current known-good path; do not override it unless you are debugging protocol/raster behavior.
12. The wrapper now crops white margins during preprocessing by default. Use `--no-crop-content` only if that crop is undesirable for a specific image.
13. `--despeckle` is intentionally optional. It can remove isolated dots, but it may also alter thin artwork more than desired.

## Useful Files for Bug Reports

Attach:

- `out/replay_sender/<timestamp>/meta.json`
- `out/replay_sender/<timestamp>/send_log.json`
- command used
- OS + BlueZ version
- printer model/name and firmware (if known)
