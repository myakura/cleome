# `@font-face` CSS generation, variable axes, and variant bundling

Editing rules for [`cleome/metadata.py`](../../src/cleome/metadata.py), [`cleome/css.py`](../../src/cleome/css.py), and [`cleome/pipeline.py`](../../src/cleome/pipeline.py). See [`docs/architecture-and-rationale.md`](../../docs/architecture-and-rationale.md) for background rationale, [`docs/hosting-and-usage.md`](../../docs/hosting-and-usage.md) for output layout and caching behavior, and [`skills/cleome/SKILL.md`](../../skills/cleome/SKILL.md) for the public API contract.

## Variable and static axis mapping

* Extract `font-weight`, `font-stretch`, and `font-style` from `fvar` (`wght`, `wdth`, `ital`, `slnt`) for variable fonts and from `OS/2` and `post` for static fonts (`cleome/metadata.py`).
* In `generate_css` (`cleome/css.py`), emit `font-style: normal` only when the font's axis ranges include an upright form (`ital < 0.5` and `0deg` within `slnt`).

## Multi-font variant bundling and output integrity

* Raise a `ValueError` in `validate_and_compute_variant_suffixes` (`cleome/metadata.py`) on `font-family` mismatches (unless overridden) or `(font-weight, font-stretch, font-style)` collisions rather than auto-numbering colliding variants (`-2`, `-3`).
* Build and export each bundled variant's slices from its own mapped codepoints (`variant_slice_specs`) rather than the cross-font union.
* In `slice_fonts` (`cleome/pipeline.py`), prune superseded content-hashed `.woff2` files via `_prune_superseded_hashed_slices` only after the new CSS file has been written atomically, and verify on-disk filename hashes in `_try_load_cached_bundle`.
* Keep `css_filename` and `css_path` mutually exclusive across `slice_font`, `slice_fonts`, and `cleome/cli.py`.
