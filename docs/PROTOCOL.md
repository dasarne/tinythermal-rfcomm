# Protocol Notes (Reverse Engineered)

This document captures the current understanding of the Bluetooth print protocol used by this project.
It is derived from real traffic captures and implementation behavior in `scripts/replay_sender.py`.

## Transport

- Bluetooth RFCOMM socket (`AF_BLUETOOTH`, `SOCK_STREAM`, `BTPROTO_RFCOMM`)
- Typical channel: `1` (fallback scan may try multiple channels)

## Frame Envelope

Every command frame starts with:

- `0x7e 0x5a` (sync)
- `len_le16` (payload length after sync/len)
- `0x10 0x01` or `0x10 0x02` (message type)

### Type `0x1001`

`build_1001(cmd_hex, payload)`:

- sync `7e5a`
- `len = 4 + payload_len` (little endian)
- type `1001`
- command `aa??` in big-endian (`aa11`, `aa30`, ...)
- payload

### Type `0x1002` (`aabb` only)

`build_1002_aabb(payload_504)`:

- sync `7e5a`
- fixed length `0x01fc` (little endian)
- type `1002`
- command `aabb`
- exactly 504 bytes payload

## High-Level Print Sequence

The sender reuses a captured job sequence (`messages.csv`) and swaps data-bearing `aabb` payload(s):

1. Setup/status commands from template (`aa11`, `aa30`, `aa18`, ...)
2. Transfer setup (`aa5c`) with a generated payload:
   - `checksum_le16 + 00 01 + frame_size_le16 + frame_count_le16`
3. One or more `aabb` frames (`1002` envelope, 504-byte payload each)
4. Transfer trigger (`aa10`)
5. Optional extra template frames after `aa10` (`post_frames_after_aa10`)

Default behavior stops shortly after `aa10` to reduce risk.

## Image to `btbuf`

Raster format generated into fixed `btbuf` (4000 bytes):

- `btbuf[2:4] = 0x100e` (little endian)
- `btbuf[4:6] = width_le16` (effective width after optional trim)
- `btbuf[6] = bytes_per_col` (usually `12` => `96` dots high)
- `btbuf[8:10] = 1`, `btbuf[10:12] = 1`
- `btbuf[12] = no_zero_index` (leading-column trim marker)
- bitmap data starts at `btbuf[14]`

Bitmap packing:

- one column = `bytes_per_col` bytes
- each byte packs 8 vertical pixels
- bit order is LSB-first
- black pixel maps to bit `1`

Header checksum:

- `btbuf[0:2]` = sum-based checksum used by app/protocol.
- Computed over header and periodic marker bytes as implemented in `image_to_btbuf_with_canvas`.

## LZMA and `aabb` Chunking

`btbuf` is compressed with LZMA "alone" format:

- filter: `LZMA1`
- dict: `8 KiB`
- params: `lc=3, lp=0, pb=2, mode=normal, nice_len=128, mf=bt4`

Compressed bytes are split into chunks of up to `500` bytes.
Each chunk becomes a 504-byte `aabb` payload:

- bytes `[2]` chunk index
- byte `[3]` total chunks
- bytes `[4..]` chunk data
- bytes `[0:2]` chunk checksum (`sum(payload[2:504]) & 0xffff`, little endian)

## Template-Dependent Behavior

Two fields from captured template geometry matter:

- `width`
- `no_zero_index`

When `--use-template-nozero` is enabled:

- canvas width uses `template_width + template_nozero`
- `btbuf[12]` forced from template
- data is shifted accordingly to match expected firmware behavior

## Commands Observed in Print Jobs

Frequently seen:

- `aa11`, `aa30`, `aa18`: status/poll/control style exchanges
- `aad0`, `aad1`: transfer-related setup blocks
- `aab0`, `aac9`, `aa13`, `aaba`: mode/state transitions
- `aa5c`: start-transfer metadata
- `aabb`: compressed raster chunks
- `aa10`: execute/print trigger

Exact semantics are partially inferred. Use `send_log.json` and captures to validate on new hardware variants.

## Capture/Decode Toolchain

- `scripts/decode_spp.py`: decode outgoing print jobs from `btsnoop_hci.log`
- `scripts/decode_lzma_btbuf.py`: reconstruct `btbuf` and rendered images from captured `aabb`

These tools are the reference for extending support and validating protocol changes.
