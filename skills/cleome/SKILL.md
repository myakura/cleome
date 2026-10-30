---
name: cleome
description: Slice Japanese, Korean, and Chinese (JP / KR / SC / TC / HK) variable and static fonts (.ttf / .otf) into 4-tier unicode-range .woff2 chunks and @font-face CSS using the Cleome (`cleome`) Python 3.15+ library and CLI. Activate when building CJK webfont subsets, inspecting font slice plans, or generating @font-face stylesheets.
---

# Cleome (`cleome`) consumer guide

Cleome (`cleome`) is a Python 3.15+ CLI tool and library that inspects a font's `cmap` and OpenType tables (`name`, `fvar`, `OS/2`, `post`, `GSUB`, `GPOS`) and generates 4-tier `unicode-range` `.woff2` slices and `@font-face` CSS. It preserves OpenType layout features, `cmap` Format 14 UVS/IVS variation sequences, license records (`nameID` `0`, `13`, `14`), and variable font axes without mutating caller-supplied `TTFont` instances in public inspection or slicing APIs (only `patch_uvs_missing_base_cmap` mutates in place).

## Core API signatures

```python
from cleome import (
    BuiltSliceFile,
    FontFaceDescriptors,
    FontMetadata,
    SUPPORTED_LOCALES,
    SliceBundleResult,
    SliceConfig,
    SliceSpec,
    build_slice_plan,
    compact_unicode_range,
    detect_font_locale,
    export_woff2_slices,
    extract_font_face_descriptors,
    extract_font_metadata,
    format_unicode_range,
    generate_css,
    parse_unicode_range,
    patch_uvs_missing_base_cmap,
    slice_font,
    slice_fonts,
    slugify_family,
)

if __name__ == "__main__":
    cfg = SliceConfig(locale="jp", rare_chunk_size=250)
    plan = build_slice_plan(
        {0x0041, 0x3042, 0x65E5, 0x4E02}, config=cfg, family_name="Demo JP"
    )
    assert [s.tier for s in plan] == [4, 3, 1, 1]
    ur_text = compact_unicode_range(
        [0x3041, 0x3043], forbidden_sorted=[0x3042], max_gap=0x80
    )
    assert parse_unicode_range(ur_text) == {0x3041, 0x3043}
    assert format_unicode_range([0x3041, 0x3043]) == "U+3041, U+3043"
    assert format_unicode_range((0x3041, 0x3043)) == "U+3041-3043"
```

* `SliceConfig(locale="auto", freq_chunks=((200, 2), (80, 10)), freq_tail_chunk_size=70, rare_chunk_size=250, symbols_ext_chunk_size=350, layout_closure=True, layout_features=("*",), drop_tables=(), uvs_base_repair=True, slices_subdir="slices", url_prefix=None, font_display="swap", filename_hash=True)`. Frozen configuration dataclass validated in `__post_init__`. `locale` accepts any value in `SUPPORTED_LOCALES` (`"auto"`, `"jp"`, `"kr"`, `"sc"`, `"tc"`, `"hk"`). When `filename_hash=True` (default), each built `.woff2` filename includes an 8-character SHA-256 content digest (`<slug>(-<variant>)-<slice_id>-<hash8>.woff2`).
* `slice_font(font, out_dir, *, family_name=None, slug=None, css_filename=None, css_path=None, weight=None, stretch=None, style=None, config=None, workers=None, force=False) -> SliceBundleResult`. Slice one `.ttf` or `.otf` path or in-memory `TTFont` into `.woff2` files and `@font-face` CSS. Pass `css_path` to write the stylesheet to an explicit path outside `out_dir` (such as `src/styles/fonts.css` while `out_dir="public/fonts"`; when `config.url_prefix` is `None`, Cleome computes a relative URL prefix from `css_path`'s parent directory to `slices_dir`). Skips subsetting when `force=False` and existing outputs match the input SHA-256 digest and configuration (`result.cached == True`).
* `slice_fonts(fonts, out_dir, *, family_name=None, slug=None, css_filename=None, css_path=None, weight=None, stretch=None, style=None, config=None, workers=None, force=False) -> SliceBundleResult`. Bundle multiple static weights, widths, or styles into one `@font-face` stylesheet and `slices/` directory. Validates that all variants share a common family name (unless `family_name` is passed) and distinct `(font-weight, font-stretch, font-style)` descriptors.
* `build_slice_plan(codepoints, config=None, *, family_name="") -> list[SliceSpec]`. Build the ordered 4-tier slice plan from lowest CSS priority (`tier=4`, declared first) to highest CSS priority (`tier=1`, declared last).
* `detect_font_locale(codepoints, family_name="") -> str`. Auto-detect `"jp"`, `"kr"`, `"sc"`, `"tc"`, or `"hk"` from `family_name` tokens and `cmap` coverage ratios.
* `extract_font_face_descriptors(font, *, family_override=None, weight_override=None, stretch_override=None, style_override=None) -> FontFaceDescriptors`. Extract CSS `@font-face` descriptors from `name`, `fvar` (`wght`, `wdth`, `ital`, `slnt`), `OS/2`, and `post`.
* `extract_font_metadata(font, *, family_override=None, slug_override=None, weight_override=None, stretch_override=None, style_override=None, variant_suffix="", uvs_base_repair=True) -> FontMetadata`. Extract descriptors, `codepoints`, and `patched_uvs_codepoints` from a `TTFont` without mutating `font`.
* `export_woff2_slices(font_or_bytes, meta, slice_specs, slices_dir, config=None, workers=None) -> list[BuiltSliceFile]`. Export `.woff2` slices in parallel without mutating `font_or_bytes`.
* `generate_css(fonts_and_files, config=None, *, cache_key=None, out_digest=None, css_path=None, slices_dir=None) -> str`. Render the `@font-face` stylesheet using Stixis (`SerializationOptions.cleome()`).
* `patch_uvs_missing_base_cmap(font) -> list[int]`. In-place helper that restores characters mapped in `cmap` Format 14 (UVS/IVS) that upstream fonts omitted from Format 4 or Format 12, prioritizing IVS (`U+E0100..U+E01EF`) over `VS1..VS16`.
* `slugify_family(name) -> str`. Convert a font family name into a kebab-case filename slug.
* `format_unicode_range(codepoints: set[int] | frozenset[int] | list[int] | tuple[int, int]) -> str`, `parse_unicode_range(ur_str: str) -> set[int]`, and `compact_unicode_range(sorted_target_cps: list[int], forbidden_sorted: list[int], max_gap: int = 0x80) -> str`. Format, parse, and compact CSS `unicode-range` strings without shadowing lower-priority slices. In `format_unicode_range`, a `set`, `frozenset`, or `list` (including a 2-element `list` like `[0x3041, 0x3043]`) is formatted as individual codepoints (`"U+3041, U+3043"`), whereas a 2-element `tuple[int, int]` like `(0x3041, 0x3043)` is formatted as an inclusive `(start, end)` interval (`"U+3041-3043"`).

## Slicing tiers and runtime rules

See [`docs/architecture-and-rationale.md`](../../docs/architecture-and-rationale.md) for the full 4-tier cascade shadowing design and OpenType shaping rationale.

* **Four-tier CSS cascade shadowing (`Tier 4` -> `Tier 3` -> `Tier 2` -> `Tier 1`).** `Tier 4` rare ideograph/syllable slices sit at the top of the stylesheet with 1-token hex ranges (`U+XXXX-YYYY`) per Unicode block (never bridging across planes), shadowed by `Tier 3` inverted-pyramid frequency chunks (`200x2`, `80x10`, `~70` tail), `Tier 2` symbols (`symbols-ext`, `symbols-ui`), and `Tier 1` core phonetic and Latin slices (`kana-core`, `hangul-core`, `cjk-core`, `latin`) declared below.
* **Multiprocessing guard.** When `workers > 1` (or `workers=None` on multi-core systems), `slice_font`, `slice_fonts`, and `export_woff2_slices` spawn a `ProcessPoolExecutor`. Guard top-level scripts with `if __name__ == "__main__":` or pass `workers=1`.
* **Layout feature overrides.** `SliceConfig(layout_features=("*",))` keeps all OpenType layout features by default. Passing specific tags via `layout_features` or `--layout-features` replaces the entire list, so include vertical and composition tags (`ccmp,locl,kern,mark,mkmk,liga,palt,halt,vpal,vhal,vkrn,vert,vrt2,ruby`) when needed.

## CLI (`cleome`)

* Preview a 4-tier slice plan without writing files (read-only):
  * `uv run cleome plan <font.ttf> [--locale auto|jp|kr|sc|tc|hk] [--json]`
* Build `.woff2` slices and `@font-face` CSS (skips subsetting when cached outputs match; pass `--dry-run` to validate without writing files, `--force` to rebuild, `--no-filename-hash` to disable per-slice content hashes, or `--css-out` to place the stylesheet in a separate directory):
  * `uv run cleome build <font.ttf> -o dist/web [--locale auto|jp|kr|sc|tc|hk] [--css-out <path.css>] [--no-filename-hash] [--dry-run] [--force] [--json]`
  * `uv run cleome build MyFont-Regular.ttf MyFont-Bold.ttf -o dist/web-static --family "My Font"`
* Exit codes: `0` (success / cache hit / `--dry-run`), `1` (runtime build or worker error), `2` (invalid arguments, CSS descriptors, or path configuration), `3` (permission denied), `4` (missing, directory, or unreadable/malformed input font).

## Host and consume generated CSS and slices

See [`docs/hosting-and-usage.md`](../../docs/hosting-and-usage.md) for complete deployment examples, HTML `<head>` ordering, preloading rules, CORS headers, and `Cache-Control` setup.

* **URL resolution (`--slices-dir`, `--url-prefix`, `--css-out`, and `--filename-hash`).** Keep defaults when serving `<slug>.css` and `slices/*.woff2` together from a static folder. Pass `--css-out` and `--url-prefix` when importing CSS into a bundler while serving `.woff2` slices from `public/` or a CDN, or pass `--no-filename-hash` when a bundler adds its own asset hash.
* **Preloading and HTTP headers.** Preload at most the Tier 1 core slices (`latin` and `kana-core`, `hangul-core`, or `cjk-core`) with `as="font" type="font/woff2" crossorigin`. Serve `.woff2` files with `Content-Type: font/woff2` (and `Access-Control-Allow-Origin` on cross-origin CDNs) without HTTP `Content-Encoding` re-compression, and use `Cache-Control: public, max-age=31536000, immutable` when content hashes are enabled.

## File Issues from consumer projects

When filing a GitHub Issue on `myakura/cleome` from a consumer project:

1. **Put one concern in each Issue.** Group related edge cases in the same module (such as `slicer.py`, `subsetter.py`, or `metadata.py`) into one Issue, and split unrelated changes into separate Issues.
2. **Use the three-section layout (`What Happens?`, `How to Reproduce?`, `What Should Happen?`).**
   * Name the Cleome function, class, or CLI command and its observed behavior.
   * Write a self-contained Python 3.15+ snippet using `cleome` and `fontTools` imports, or provide `cleome plan --json` output.
3. **Run every reproduction snippet before filing.**
   * Run the snippet with `uv run python -c "..."` or `uv run cleome plan ...` and include the actual output or traceback. Do not mix unexecuted guesses from reading source code with verified assertions.
4. **Add labels when creating the Issue.**
   * Pass `--label bug`, `--label enhancement`, or `--label documentation` to `gh issue create`, and use `--body-file <file>` when the Markdown body contains backticks.
