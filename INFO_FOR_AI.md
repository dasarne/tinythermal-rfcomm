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

## Trust Levels (Important)

Use these labels when changing behavior:

- `verified`: observed in captures and confirmed by successful print tests
- `inferred`: likely based on behavior, but not vendor-confirmed
- `unknown`: plausible but unverified; avoid hard assumptions

Current examples:

- `verified`: envelope sync (`7e5a`), `1001/1002`, `aabb` chunk layout, `aa10` trigger usage
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
5. Only then modify `scripts/replay_sender.py` or transport timings.
