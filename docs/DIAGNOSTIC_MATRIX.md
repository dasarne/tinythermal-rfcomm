# Diagnostic Matrix

This document tracks the currently observed print defects as separate classes.
The goal is to avoid forcing all visible errors into a single explanation.

Use these labels in future analysis and commits:

- `H`: dense horizontal-line interference
- `E`: shortening at horizontal line ends
- `T`: local top-zone offset / about-one-pixel upper mismatch
- `W`: vertical wrap / lower content reappears at the top
- `C`: corner / junction / rectangle-connection artifacts

## Working Definitions

### `H` Dense Horizontal-Line Interference

Observed:

- tightly spaced parallel 1px horizontal lines are not rendered faithfully
- larger vertical spacing is noticeably more stable
- line count matters, but spacing is the stronger lever

Implemented interpretation:

- treat this as its own class, not just as a variant of the old "top line" problem

Risk:

- can easily be confused with `E` or `T` when using complex motifs

### `E` Shortened Horizontal Ends

Observed:

- continuous horizontal lines lose pixels at their ends
- this is especially visible in dense line-bundle diagnostics

Implemented interpretation:

- keep `E` separate until proven to be only a consequence of `H`

Risk:

- some future tests may show `E` as a subcase of `H`

### `T` Top-Zone Offset

Observed:

- in some real motifs the upper area looks displaced by about one pixel
- most visible in the original long-label reference motifs, not in every minimal test

Implemented interpretation:

- top-zone-only class until isolated better

Risk:

- can be masked by `H` in tests that place too much structure near the top edge

### `W` Vertical Wrap

Observed:

- lower content can be cut off and reappear at the top
- does not dominate every test

Implemented interpretation:

- separate class from `H/E/T`
- for the relevant `T15`/diagnostic path, the major mismatch was traced to the `btbuf` layout model:
  - vendor `T15Print` writes raster data starting at offset `14`
  - the repo had modeled that payload as if raster data started at offset `16`
  - after switching the `T15`-like path to `data_offset = 14`, the dedicated wrap probe converged physically

Risk:

- can be mistaken for global vertical shift if the test image is too dense

### `C` Corner / Junction Artifacts

Observed:

- small local issues at box edges, crossings, and rectangle joins

Implemented interpretation:

- secondary class; not currently considered the main blocker

Risk:

- can look more important than it is when using box-heavy diagnostics

## Current Evidence Matrix

| Test / Motif | H | E | T | W | C |
|---|---:|---:|---:|---:|---:|
| `Inkscape-Test.png` | medium | low-medium | clear | sometimes | clear |
| Blackline diagnostic | clear | clear | weak | sometimes | low |
| `horizontals_only` | clear | clear | no | not dominant | no |
| `boxes_only` | no | no | low | sometimes | clear but small |
| `horizontals_plus_boxes` | clear | clear | low | sometimes | medium |
| `stroke_weights` | clear for thin lines | medium | no | sometimes | no |
| micro `lines_only` | clear | clear | no | unclear | no |
| micro `lines+vertical` | clear | clear | no | unclear | low |
| micro `lines+block` | clear | clear | no | unclear | low |
| parallel-grid | very clear, spacing-driven | medium | no | not dominant | no |

## Current Conclusions

1. There is almost certainly more than one failure mechanism.
2. `W` now has a concrete low-level cause and validated fix for the relevant `T15`-style path.
3. `T` was later fixed on the validated long-label reference case by switching that path to the vendor-nearer `vendor-like-t15` renderer class.
4. `H` and `E` are also strongly improved on the same validated long-label path, though they remain useful diagnostic labels for future regressions.
5. `C` is real but currently secondary.

## Next Diagnostic Strategy

1. Keep using dedicated probes when a regression appears; avoid mixed motifs for first isolation.
2. Check `btbuf`/vendor-class choice before tuning micro-parameters.
3. For SVG-vs-bitmap issues, compare frontend raster output before touching transport.
4. Treat this matrix as a regression taxonomy, not only as a list of currently open blockers.
