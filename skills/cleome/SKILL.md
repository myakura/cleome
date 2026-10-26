---
name: cleome
description: Slice Japanese, Korean, and Chinese (JP / KR / SC / TC / HK) variable and static fonts (.ttf / .otf) into 4-tier unicode-range .woff2 chunks and @font-face CSS using the Cleome (`cleome`) Python 3.15+ library and CLI. Activate when building CJK webfont subsets, inspecting font slice plans, or generating @font-face stylesheets.
---

# Cleome (`cleome`) consumer guide

Cleome (`cleome`) is a Python 3.15+ CLI tool and library that inspects a font's `cmap` and OpenType tables (`name`, `fvar`, `OS/2`, `post`, `GSUB`, `GPOS`) and generates 4-tier `unicode-range` `.woff2` slices and `@font-face` CSS. It preserves OpenType layout features, `cmap` Format 14 UVS/IVS variation sequences, license records (`nameID` `0`, `13`, `14`), and variable font axes without mutating caller-supplied `TTFont` instances.

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
    cfg = SliceConfig(locale="jp", rare_kanji_chunk_size=250)
    plan = build_slice_plan({0x0041, 0x3042, 0x65E5, 0x4E02}, config=cfg, family_name="Demo JP")
    assert [s.tier for s in plan] == [4, 3, 1, 1]
    ur_text = compact_unicode_range([0x3041, 0x3043], forbidden_sorted=[0x3042], max_gap=0x80)
    assert parse_unicode_range(ur_text) == {0x3041, 0x3043}
```

* `SliceConfig(locale="auto", freq_tiers=((200, 2), (80, 10)), freq_tail_chunk_size=70, rare_kanji_chunk_size=250, symbols_ext_chunk_size=350, retain_layout_closure=True, layout_features=("*",), drop_tables=(), patch_uvs_only_cmap=True, slices_subdir="slices", url_prefix=None, font_display="swap")`: Frozen configuration dataclass validated in `__post_init__`. `locale` accepts any value in `SUPPORTED_LOCALES` (`"auto"`, `"jp"`, `"kr"`, `"sc"`, `"tc"`, `"hk"`).
* `slice_font(font, out_dir, *, family_name=None, slug=None, css_filename=None, weight=None, stretch=None, style=None, config=None, workers=None, force=False) -> SliceBundleResult`: Slice a single `.ttf`/`.otf` path or in-memory `TTFont` into `.woff2` files and `@font-face` CSS. Skips subsetting when `force=False` and existing outputs match the input SHA-256 digest and configuration (`result.cached == True`).
* `slice_fonts(fonts, out_dir, *, family_name=None, slug=None, css_filename=None, weight=None, stretch=None, style=None, config=None, workers=None, force=False) -> SliceBundleResult`: Bundle multiple static weights, widths, or styles into a single `@font-face` stylesheet and `slices/` directory. Validates that all variants share a common family name (unless `family_name` is passed) and distinct `(font-weight, font-stretch, font-style)` descriptors.
* `build_slice_plan(codepoints, config=None, *, family_name="") -> list[SliceSpec]`: Build the ordered 4-tier slice plan from lowest CSS priority (`tier=4`, declared first) to highest CSS priority (`tier=1`, declared last).
* `detect_font_locale(codepoints, family_name="") -> str`: Auto-detect `"jp"`, `"kr"`, `"sc"`, `"tc"`, or `"hk"` from `family_name` tokens and `cmap` coverage ratios.
* `extract_font_face_descriptors(font, *, family_override=None, weight_override=None, stretch_override=None, style_override=None) -> FontFaceDescriptors`: Extract CSS `@font-face` descriptors from `name`, `fvar` (`wght`, `wdth`, `ital`, `slnt`), `OS/2`, and `post`.
* `extract_font_metadata(font, *, family_override=None, slug_override=None, weight_override=None, stretch_override=None, style_override=None, variant_suffix="", patch_uvs=True) -> FontMetadata`: Extract descriptors, `codepoints`, and `patched_uvs_codepoints` from a `TTFont`.
* `export_woff2_slices(font_or_bytes, meta, slice_specs, slices_dir, config=None, workers=None) -> list[BuiltSliceFile]`: Export `.woff2` slices in parallel without mutating `font_or_bytes`.
* `generate_css(fonts_and_files, config=None, *, cache_key=None, out_digest=None) -> str`: Render the `@font-face` stylesheet using Stixis (`SerializationOptions.cleome()`).
* `patch_uvs_missing_base_cmap(font) -> list[int]`: Restore characters mapped in `cmap` Format 14 (UVS/IVS) that upstream fonts omitted from Format 4/12, prioritizing IVS (`U+E0100..U+E01EF`) over `VS1..VS16`.
* `slugify_family(name) -> str`: Convert a font family name into a kebab-case filename slug.
* `format_unicode_range(codepoints) -> str`, `parse_unicode_range(ur_str) -> set[int]`, and `compact_unicode_range(sorted_target_cps, forbidden_sorted, max_gap=0x80) -> str`: Format, parse, and compact CSS `unicode-range` strings without shadowing lower-priority slices.

## Slicing tiers and runtime rules

* **Four-tier CSS cascade shadowing (`Tier 4` -> `Tier 3` -> `Tier 2` -> `Tier 1`).**
  * `Tier 4` (`kanji-rare-XX`, `hanzi-rare-XX`, `hangul-rare-XX`, `hanja-rare-XX`) is emitted first with a 1-token contiguous hex range (`U+XXXX-YYYY`) per Unicode block (`Ext-A`, `BMP`, `Compat`, `SIP`, `TIP`, never bridging across planes). Its `.woff2` binary excludes `Tier 3` frequent characters, which are shadowed by `Tier 3`..`Tier 1` rules declared later in the stylesheet.
  * `Tier 3` (`kanji-freq-XX`, `hanzi-freq-XX`, `hangul-freq-XX`) uses inverted-pyramid frequency chunks (`200x2` top 400, `80x10` ranks 401..1,200, and `~70` tail).
  * `Tier 2` separates `symbols-ext` from `symbols-ui` (`→`, `▶`, `①`, `✓`, `℃`, `Ⅰ-Ⅻ`, `≒`).
  * `Tier 1` co-locates base characters with their combining marks and paired punctuation (`kana-core` in `jp`, `hangul-core` in `kr`, `cjk-core` with Bopomofo and tone marks in `sc`/`tc`/`hk`, and `latin` with `U+0300-036F` combining marks) so intra-slice `GSUB`/`GPOS` lookups (`ccmp`, `mark`, `mkmk`, `chws`, `vchw`, `ljmo`, `vjmo`, `tjmo`) survive `fontTools.subset`.
* **Multiprocessing guard.** When `workers > 1` (or `workers=None` on multi-core systems), `slice_font`, `slice_fonts`, and `export_woff2_slices` spawn a `ProcessPoolExecutor`. Guard top-level scripts with `if __name__ == "__main__":` or pass `workers=1`.
* **Layout feature overrides.** `SliceConfig(layout_features=("*",))` keeps all OpenType layout features by default. Passing specific tags via `layout_features` or `--layout-features` replaces the entire list, so include vertical and composition tags (`ccmp,locl,kern,mark,mkmk,liga,palt,halt,vpal,vhal,vkrn,vert,vrt2,ruby`) when needed.

## CLI (`cleome`)

* Preview a 4-tier slice plan without writing files (read-only):
  * `uv run cleome plan <font.ttf> [--locale auto|jp|kr|sc|tc|hk] [--json]`
* Build `.woff2` slices and `@font-face` CSS (skips subsetting when cached outputs match; pass `--force` to rebuild):
  * `uv run cleome build <font.ttf> -o dist/web [--locale auto|jp|kr|sc|tc|hk] [--force] [--json]`
  * `uv run cleome build MyFont-Regular.ttf MyFont-Bold.ttf -o dist/web-static --family "My Font"`

## Hosting and consuming generated CSS and slices

* **URL resolution (`--slices-dir` and `--url-prefix`).**
  * By default, `<out-dir>/<slug>.css` references `url('./slices/<slug>-<slice_id>.woff2') format('woff2')`, which resolves relative to the `.css` file URL. When serving both `<slug>.css` and `slices/*.woff2` from a static directory (such as `public/fonts/<slug>/`), keep the defaults and link `<link rel="stylesheet" href="/fonts/<slug>/<slug>.css">`.
  * When importing `<slug>.css` into a bundler (Vite, Astro, Next.js) or serving slices from a separate CDN origin, pass `--url-prefix "/fonts/<slug>/slices"` or `--url-prefix "https://cdn.example.com/fonts/<slug>/v1/slices"` so URLs do not depend on the bundled stylesheet's path.
  * Pass `--slices-dir .` (or `SliceConfig(slices_subdir=".")`) to write `.woff2` files into the same directory as `<slug>.css`.
* **HTML `<head>` placement and preloading.**
  * Place `<link>` tags in `<head>` **after** `<meta charset="utf-8">`, viewport/context `<meta>` tags, `<title>`, and any synchronous inline `<script>` or `<script type="importmap">`, and **before** external `<script>` tags (`defer` or `type="module"`):
    1. `<link rel="preconnect" href="https://cdn.example.com" crossorigin>` (only when serving slices from a separate origin)
    2. `<link rel="preload" href=".../<slug>-kana-core.woff2" as="font" type="font/woff2" crossorigin>` (optional; at most Tier 1 core slices)
    3. `<link rel="stylesheet" href=".../<slug>.css">`
  * **Do not** preload all `.woff2` slices or Tier 2-4 Kanji/Hanzi/Hangul chunks; browsers use `unicode-range` to download only the slices needed by rendered text. If preloading above-the-fold text, preload at most the Tier 1 core slice (`<slug>-latin.woff2` and/or `<slug>-kana-core.woff2`, `<slug>-hangul-core.woff2`, `<slug>-cjk-core.woff2`) and always include `as="font" type="font/woff2" crossorigin` (even on same-origin hosts) to match `@font-face`'s anonymous CORS mode.
  * **Do not** load `<slug>.css` via CSS `@import url(...)` (which hides `<slug>.css` from browser preload scanners), **do not** place blocking `<script src="...">` before `<link rel="preconnect">` or `<link rel="stylesheet">` (or synchronous inline `<script>` after `<link rel="stylesheet">`), and **do not** place `<link rel="stylesheet">` inside `<body>`.
* **HTTP headers and cache invalidation.**
  * Cross-origin CDN responses for `.woff2` files must include `Access-Control-Allow-Origin` and `Content-Type: font/woff2` (without HTTP `Content-Encoding` re-compression, since WOFF2 streams are already Brotli-compressed). Compress `<slug>.css` with HTTP Brotli or gzip.
  * Because slice filenames (`<slug>-<slice_id>.woff2`) do not include content hashes, host under a versioned directory path (such as `/fonts/<slug>/v1/`) when setting `Cache-Control: public, max-age=31536000, immutable` so font or Cleome updates switch `<slug>.css` and `.woff2` slices atomically.

## Filing Issues from consumer projects

When filing a GitHub Issue on `myakura/cleome` from a consumer project:

1. **Put one concern in each Issue.** Group related edge cases in the same module (such as `slicer.py`, `subsetter.py`, or `metadata.py`) into one Issue, and split unrelated changes into separate Issues.
2. **Use the three-section layout (`What Happens?`, `How to Reproduce?`, `What Should Happen?`).**
   * Name the Cleome function, class, or CLI command and its observed behavior.
   * Write a self-contained Python 3.15+ snippet using `cleome` and `fontTools` imports, or provide `cleome plan --json` output.
3. **Run every reproduction snippet before filing.**
   * Execute the snippet with `uv run python -c "..."` or `uv run cleome plan ...` and include the actual output or traceback. Do not mix unexecuted guesses from reading source code with verified assertions.
4. **Add labels when creating the Issue.**
   * Pass `--label bug`, `--label enhancement`, or `--label documentation` to `gh issue create`, and use `--body-file <file>` when the Markdown body contains backticks.
