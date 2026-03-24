Diagnostic and research helpers live here so the productive print path stays small.

Use this directory for:
- test image generators
- btbuf and preview analyzers
- SVG/frontend comparison tools
- vendor-path and reverse-engineering sweeps

Keep only tools here that are reusable across sessions.
Delete one-off probes once their conclusion has been folded into a smaller canonical test.

Do not move user-facing or sender-core code here.

The productive core remains in:
- `scripts/katasymbol_print.py`
- `scripts/replay_sender.py`
- `scripts/raster_btbuf.py`
- `scripts/image_input.py`

Current canonical diagnostics:
- `compare_svg_bitmap_frontend.py`
- `sweep_svg_postprocess.py`
- `analyze_btbuf_columns.py`
- `render_btbuf_variants.py`
- `sweep_t15_canvas_params.py`
- `sweep_vendor_pipelines.py`
- `make_deductive_diagnostics.py`
- `make_parallel_line_grid_diagnostics.py`
