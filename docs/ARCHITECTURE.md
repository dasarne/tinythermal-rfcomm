# Architecture Notes

This document separates two questions:

1. what shape the codebase has now
2. what shape would be cleaner on a green field

## Current State

The current implementation is effective, but it is not yet architecturally clean.

The code reflects successful reverse engineering under uncertainty:

- behavior was discovered experimentally
- firmware failures forced defensive switches and fallback paths
- many options were added to isolate unknowns quickly

That was rational during discovery. It is also why the current source is harder to read than a purpose-designed driver.

## Where The Source Has Softened

### 1. Too much responsibility per script

Current large entry points:

- `scripts/katasymbol_print.py`
- `scripts/replay_sender.py`

Both files mix several concerns:

- CLI parsing
- config handling
- image preparation
- template inference
- transport decisions
- protocol building
- experiment switches
- regression-oriented compatibility logic

That makes local changes easy during exploration, but raises the maintenance cost afterward.

### 2. Experimental compatibility knobs live in production paths

Examples:

- `--compat-raster-preset`
- `--scale-width-bias`
- `--use-template-nozero`
- `--force-no_zero_index`

These switches were useful to converge on a working implementation. The tradeoff is that the production path now shares code with historical experiments.

### 3. Ground truth and candidate logic are not strongly separated

The repo now has three classes of behavior:

- captured-reference behavior
- Java-ground-truth behavior
- experimental candidate behavior

They are operationally understood, but the module boundaries do not enforce that separation yet.

### 4. Reverse-engineering artifacts and user-facing code sit very close together

The current repo intentionally keeps rich artifacts and exploratory tools nearby. That is valuable, but it blurs the boundary between:

- "tool a normal user should run"
- "tool a maintainer should run"
- "tool for protocol research only"

## Is The Source Harder For Beginners Than Necessary?

Yes.

Not because it is careless, but because the code still preserves the history of discovery.

Specific beginner costs:

- many options exist before the main conceptual model is obvious
- some naming is historical rather than domain-ideal
- key invariants are spread across code, docs, and artifacts
- success depends on a few known-good combinations that are not yet encoded as stronger abstractions

## Is It Poorly Maintainable?

Not broadly, but it is trending there if left as-is.

Current strengths:

- rich artifacts
- reproducible dry runs
- documented protocol context
- now a stable encoder reference path
- named user-facing presets for known-good paths
- dedicated frontend comparison scripts for SVG-vs-bitmap analysis

Current risks:

- large scripts become a merge-conflict magnet
- behavior changes are hard to localize
- more compatibility flags will make the default path harder to reason about
- frontend-specific presets (`SVG` vs. bitmap) can blur whether a problem belongs to rasterization or transport, even though the validated long-label reference case now converges

## Green-Field Architecture

If implementing this cleanly today, the project would likely use a small package with explicit layers:

### 1. `image_pipeline`

Responsibility:

- load image
- rotate / fit / dither
- place into canonical print canvas

Output:

- a normalized monochrome image object

### 2. `raster`

Responsibility:

- convert normalized image into `btbuf`
- own width / height / `no_zero_index`
- expose structured geometry metadata

Output:

- `Btbuf` domain object, not raw bytes alone

### 3. `encoder`

Responsibility:

- convert `Btbuf` to LZMA stream
- provide multiple backends:
  - `java_ground_truth`
  - `python_candidate`

Output:

- `CompressedPayload` domain object

### 4. `protocol`

Responsibility:

- build `aa5c`, `aabb`, `aa10`, and envelope frames
- chunking
- checksums
- sequence materialization from template rules

Output:

- ordered frame list

### 5. `transport`

Responsibility:

- RFCOMM connect/send/receive
- timeouts
- channel fallback
- send logs

Output:

- transport result and raw observations

### 6. `reference`

Responsibility:

- load captured references
- compare generated artifacts to:
  - capture
  - Java ground truth
- produce regression summaries

### 7. thin CLIs

Examples:

- `print`
- `prepare-image`
- `analyze-encoders`
- `replay-template`

Each CLI should orchestrate the layers, not implement core behavior.

## Practical Refactor Direction

A full rewrite is not required.

The practical path is:

1. stabilize current working defaults
2. extract pure functions into modules without changing behavior
3. isolate experimental presets from default production code
4. make Java ground truth and candidate encoders explicit backend modules
5. keep reverse-engineering tools, but separate them from user-facing flows

## Recommendation

Do not rewrite everything now.

The project has just crossed from "fragile exploration" to "reliable printing." That is the right moment to start modular extraction, not a clean-slate rewrite.

The best next architectural step is incremental:

- keep behavior
- reduce script size
- strengthen boundaries
- preserve regression visibility

## Practical Note For AI-Friendly Maintenance

For this repository, "good architecture" is not only smaller modules. It also means preserving explicit comparison paths so future human or AI maintainers can answer:

- is this a frontend rasterization problem?
- is this a btbuf/layout problem?
- is this a transport/protocol problem?

The current SVG work established a useful pattern:

- compare SVG raster to bitmap reference before binarization
- compare prepared 1-bit images before sender placement
- compare generated `btbuf` artifacts before live printing

Those checkpoints should be preserved even if code is refactored into cleaner modules later.
