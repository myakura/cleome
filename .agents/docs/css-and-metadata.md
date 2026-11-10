# `@font-face` CSS generation, variable axes, and variant bundling

Editing rules for [`cleome/_font/metadata.py`](../../src/cleome/_font/metadata.py), [`cleome/_names.py`](../../src/cleome/_names.py), [`cleome/_output/paths.py`](../../src/cleome/_output/paths.py), [`cleome/_output/css.py`](../../src/cleome/_output/css.py), and [`cleome/_pipeline.py`](../../src/cleome/_pipeline.py). See [`docs/architecture-and-rationale.md`](../../docs/architecture-and-rationale.md) for background rationale, [`docs/hosting-and-usage.md`](../../docs/hosting-and-usage.md) for output layout and caching behavior, and [`skills/cleome/SKILL.md`](../../skills/cleome/SKILL.md) for the public API contract.

## Variable and static axis mapping

* Extract family names (including Windows BMP `(3, 1, 0x0409)` and UCS-4 `(3, 10, 0x0409)` `name` records) and `font-weight`, `font-stretch`, and `font-style` from `fvar` (`wght`, `wdth`, `ital`, `slnt`) for variable fonts and from `OS/2` (including `fsSelection` bit 9 `OBLIQUE` and bit 0 `ITALIC`) and `post` for static fonts (`extract_font_face_descriptors` in `cleome/_font/metadata.py`).
* In `extract_font_face_descriptors` (`cleome/_font/metadata.py`), include `"normal"` in `styles_css` (which `generate_css` in `cleome/_output/css.py` expands into `@font-face` rules) only when the font's axis ranges include an upright form (`ital < 0.5` when `ital` is present and `0deg` within `slnt` when `slnt` is present), alongside `"italic"` (`ital >= 0.5`) and `"oblique <min>deg <max>deg"` (`slnt != 0`).

## Multi-font variant bundling and output integrity

* Raise a `ValueError` in `validate_and_compute_variant_suffixes` (`cleome/_font/metadata.py`) on `font-family` mismatches (unless overridden) or `(font-weight, font-stretch, font-style)` collisions rather than auto-numbering colliding variants (`-2`, `-3`).
* Build and export each bundled variant's slices from its own mapped codepoints (`variant_slice_specs`) rather than the cross-font union.
* In `slice_fonts` (`cleome/_pipeline.py`), prune superseded content-hashed `.woff2` files via `_prune_superseded_hashed_slices` only after the new CSS file has been written atomically, and verify on-disk filename hashes in `_try_load_cached_bundle`.
* Keep `css_filename` and `css_path` mutually exclusive across `slice_font`, `slice_fonts`, and `cleome/cli.py`.
