# `@font-face` CSS generation, variable axes, and variant bundling

Domain rules for extracting font metadata ([`cleome/metadata.py`](../../src/cleome/metadata.py)), generating `@font-face` stylesheets ([`cleome/css.py`](../../src/cleome/css.py)), and orchestrating cached multi-variant builds ([`cleome/pipeline.py`](../../src/cleome/pipeline.py)) across variable and static fonts.

## Variable and static axis mapping

* `extract_font_face_descriptors` and `extract_font_metadata` (`cleome/metadata.py`) extract `font-weight`, `font-stretch`, and `font-style` descriptors from `fvar` axes (`wght`, `wdth`, `ital`, `slnt`) for variable fonts, and from `OS/2` (`usWeightClass`, `usWidthClass`, `fsSelection`) and `post` (`italicAngle`) tables for static fonts.
* When a variable font includes `ital`, `slnt`, or both axes, `generate_css` (`cleome/css.py`) emits `@font-face` rules for each supported style (`normal`, `italic`, `oblique ...`) pointing to the same `.woff2` slice files. Emit `font-style: normal` only when the font's axis ranges include an upright form (`ital < 0.5` and `0deg` within `slnt`).

## Multi-font variant bundling and content-hashed filenames

* `validate_and_compute_variant_suffixes` (`cleome/metadata.py`) validates that bundling multiple font files into one stylesheet shares a common `font-family` name (unless overridden) and a unique `(font-weight, font-stretch, font-style)` descriptor tuple for every emitted rule.
* Raise a `ValueError` on family mismatches or descriptor collisions rather than auto-numbering colliding variants or filenames (`-2`, `-3`).
* When bundled variants have different `cmap` coverage, `slice_fonts` (`cleome/pipeline.py`) builds and exports each variant's slices from its own mapped codepoints rather than the cross-font union.
* When `SliceConfig.filename_hash` is `True` (default), `_subset_single_slice_worker` (`cleome/subsetter.py`) appends an 8-character SHA-256 hex digest of the built `.woff2` bytes (`<slug>(-<variant>)-<slice_id>-<hash8>.woff2`), `_export_woff2_slices_batch` prunes superseded hashes for the built stems, and `_try_load_cached_bundle` (`cleome/pipeline.py`) verifies the on-disk filename hash against the `.woff2` content.
* `slice_font` and `slice_fonts` (`cleome/pipeline.py`, `--css-out` in `cleome/cli.py`) accept an explicit `css_out` file path so consumers can write the `@font-face` stylesheet outside `out_dir` (such as `src/styles/fonts.css` while `out_dir` is `public/fonts`); `css_filename` and `css_out` are mutually exclusive.
