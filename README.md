# cleome

**`cleome`** is a Python CLI tool and library that takes any Japanese font (`.ttf` / `.otf`, both **Variable Fonts** and **Static Fonts**) and generates Web-optimized `unicode-range` `.woff2` slices and `@font-face` CSS.

Instead of relying on static fixed-charset templates or bloating CSS with thousands of fragmented ranges, `cleome` dynamically inspects the input font's `cmap` and builds a **4-tier progressive slice plan** powered by **CSS Cascade Shadowing** and **Inverted-Pyramid Kanji Frequency Granularity**.

---

## Key Architecture: 4-Tier Dynamic Slicing & CSS Cascade Shadowing

In CSS Fonts Module Level 4, when multiple `@font-face` rules within the same `font-family` have overlapping `unicode-range` descriptors, **later rules in the stylesheet take precedence over earlier rules**. `cleome` orders `@font-face` declarations from lowest priority (**Tier 4**, at the top of the CSS) to highest priority (**Tier 1**, at the bottom of the CSS):

| Tier (CSS Order) | Slice ID Pattern | Target Characters | `unicode-range` Strategy & Rationale |
| :--- | :--- | :--- | :--- |
| **1. Tier 4: Rare & Extended Kanji**<br>*(Declared FIRST / Lowest Priority)* | `<slug>-kanji-rare-01..NN.woff2`<br>*(~250 chars/slice, dynamic count)* | JIS Level 2-4, CJK Unified Ideographs Ext A-J, CJK Compatibility Ideographs not in Tier 3 | **1-Token Contiguous Hex Range (`U+XXXX-YYYY`)**: The `.woff2` binary excludes Tier 3 frequent Kanji, while the CSS `unicode-range` spans a single contiguous hex token (`0.2-0.5 KB` for Tier 4 `unicode-range` values vs. `55.8 KB` across Google Fonts' 60 rare-Kanji slices `.0`-`.59`, or `79.2 KB` / `81.2 KB` with property names across all 124 slices; total stylesheet is `~44-54 KB` raw / `~6.9-7.5 KB` Brotli vs. Google Fonts' `112.5 KB` raw / `17.6 KB` Brotli). Any frequent Kanji in that hex range is automatically shadowed by Tier 3-1 rules declared below. |
| **2. Tier 3: Frequent Kanji**<br>*(Declared Low → High Frequency)* | `<slug>-kanji-freq-40..01.woff2`<br>*(Up to 3,169 Kanji)* | 100% of all 2,136 Joyo Kanji (incl. 2010 JIS Level 3 `𠮟塡剝頰`) + all 2,965 JIS Level 1 Kanji + top Web-frequent JIS Level 2/3 & `𠮷` | **Inverted-Pyramid Granularity**:<br>• **Rank 1-400**: `200 chars × 2 slices` (hit with ~100% probability on articles; coarse grouping saves 6 HTTP requests)<br>• **Rank 401-1200**: `80 chars × 10 slices` (medium granularity)<br>• **Rank 1201-3169**: `~70 chars × 28 slices` (fine granularity minimizes collateral download when 1-2 mid-low frequency Kanji appear in long articles) |
| **3. Tier 2: UI & Extended Symbols**<br>*(Declared `symbols-ext` → `symbols-ui`)* | `<slug>-symbols-ext(-01..NN).woff2`<br>`<slug>-symbols-ui.woff2` | • `symbols-ui`: Arrows (`←↑→↓`), triangles (`▶◀▲▼`), circled digits (`⓪①-⑳`, `❶-❿`), checks (`✓`), stars/notes (`★☆♪♫♡⚠`)<br>• `symbols-ext`: Greek, Cyrillic, box drawing, math, CJK enclosed/units, halfwidth katakana | Separating lightweight `symbols-ui` (~15 KB) ensures common UI markers like `→`, `▶`, `①`, or `✓` never pull in heavy box-drawing or foreign script slices. |
| **4. Tier 1: Core Kana & Latin**<br>*(Declared LAST / Highest Priority)* | `<slug>-kana-core.woff2`<br>`<slug>-latin.woff2` | • `kana-core`: Complete Hiragana (`U+3040-309F`, incl. combining voiced marks `U+3099-309A`), Katakana (`U+30A0-30FF`, `U+31F0-31FF`), CJK Punctuation (`U+3000-303F`), Fullwidth ASCII/Symbols (`U+FF01-FF60`, `U+FFE0-FFE6`)<br>• `latin`: ASCII, Latin-1/Extended (`U+0000-024F`, `U+1E00-1EFF`), Combining Diacritical Marks (`U+0300-036F`, `U+1AB0-1AFF`, `U+1DC0-1DFF`, `U+FE20-FE2F`), Western punctuation & currency (`€`, `™`, `ℓ`) | Guarantees **zero omissions** for modern/historical kana (`む`, `モ`, `ヶ`, `ヵ`, `ゐ`, `ヱ`, `ゔ`) and CJK punctuation (`「」`, `【】`, `・`, `ー`), keeping Kana + Punctuation + Combining Marks (`U+3099-309A`) in `kana-core` and Latin base letters + Combining Diacritical Marks (`U+0300-036F`) in `latin`. |


### Automatic Font Metadata, UVS/IVS Preservation & OpenType Features

* **Variable & Static Auto-Detection**: Automatically extracts `font-family` (`name` ID 16/1), `font-weight` (continuous range from `fvar` `wght` axis or static `OS/2.usWeightClass`), `font-stretch` (`wdth` percentage range or static `OS/2.usWidthClass`), and `font-style` (`normal`, `italic`, or `oblique` range). When a Variable Font has an `ital` axis spanning both upright (`0`) and italic (`1`), `cleome` emits both `font-style: normal` and `font-style: italic` `@font-face` declarations referencing the same `.woff2` slice files (and also emits `oblique` declarations when combined with a `slnt` axis).
* **UVS / IVS Format 14 Sequence Preservation & Missing Base `cmap` Recovery**:
  * **Format 14 UVS Retention**: For every base character included in a slice, `cleome` retains all corresponding `cmap` Format 14 Variation Selector mappings (both default UVS and non-default UVS, such as IVS `U+E0100..U+E01EF` and `VS1..VS16`) and their variant glyphs inside that slice's `.woff2` file without polluting the base Format 4/12 `cmap`.
  * **Missing Base `cmap` Auto-Patching**: Independently detects characters mapped in `cmap` Format 14 that were accidentally omitted from Format 4/12 in upstream fonts (such as Joyo/JIS Level 1 Kanji `唾` `U+553E` in Adobe `SourceHanSansJP-VF.otf` v2.005) and restores them into the base `cmap` before slicing, prioritizing standard IVS (`U+E0100..U+E01EF`) ahead of `VS1..VS16`.
* **Intra-Slice OpenType Feature, License & Variable Table Preservation**: Retains all `GSUB`, `GPOS`, `GDEF` layout feature tags by default (`'*'` with forward `GSUB` glyph closure; configurable via `--layout-features`, which replaces the default feature list when specified), license metadata (`nameID` 0, 13 & 14), input font `head.modified` timestamps for deterministic builds, and `fvar`, `gvar`, `CFF2`, `HVAR`, `VVAR`, `MVAR`, `STAT`, `avar` tables across every `.woff2` slice.


### Notes & Trade-offs

* **OpenType Shaping Scope vs. Cross-Slice Limitations**: Retaining all feature tags (`layout_features=('*',)` + `retain_layout_closure=True`) is distinct from full pre-split shaping equivalence. Single-glyph substitutions (`vert`, `vrt2`, `jp78`, `jp90`, `fwid`, `pwid`, `palt`, `vpal`, `halt`, `vhal`) work in every slice, and multi-glyph lookups (`ccmp`, `mark`, `mkmk`, `liga`, `kern`) work when all participating input characters belong to the same slice (such as Latin base letters + `U+0300-036F` combining marks in `latin`, or Kana + `U+3099-309A` combining voiced marks and CJK punctuation in `kana-core`). Multi-glyph `GSUB`/`GPOS` lookups whose input glyphs are split across _different_ slices (for example, kerning or ligatures between characters in separate slices, or `U+0300-036F` marks applied to non-Latin base characters in `symbols-ext` or `kana-core`) are pruned from individual slices by `fontTools.subset` and cannot be shaped across separate `@font-face` files.
* **Tier 4 1-Token Hex Range & Unmapped Codepoints**: Because each Tier 4 slice advertises a single contiguous `U+XXXX-YYYY` range in CSS, if a page renders a character inside that hex span that is _not mapped in the font_ (e.g., an unimplemented CJK Extension ideograph between two mapped ideographs), the browser will download that single Tier 4 `.woff2` slice (~50-200 KB depending on the font) before discovering the glyph is absent and falling back to the next font in `font-family`.
* **Output Directory Cleanup**: Re-running `cleome` into an existing output directory overwrites `<slug>.css` and matching slice files, but does not delete old `.woff2` files left over from previous runs with different slugs or chunk counts. Clean the output directory when changing slicing parameters.

---


## CLI Usage

```bash
# Slice a Variable Font (outputs <out-dir>/<slug>.css and <out-dir>/slices/<slug>-*.woff2)
cleome GenSouSansJP-VF.ttf -o dist/web

# Preview the dynamic 4-tier slice plan without writing files
cleome GenSouSansJP-VF.ttf --dry-run

# Slice multiple Static Font weights into a single unified @font-face CSS and slices/ directory
cleome GenSouSansJP-Regular.ttf GenSouSansJP-Bold.ttf -o dist/web-static

# Customize CSS font-family, URL prefix, chunk sizes, or retained OpenType layout features
# Note: --layout-features replaces the entire feature list (default is '*' for all features),
# so include vertical writing (vert,vrt2,vpal,vhal,vkrn) and composition (ccmp,locl) tags if needed.
cleome MyFont-VF.otf -o dist/web \
  --family "My Font JP" \
  --slug "my-font-jp" \
  --url-prefix "/assets/fonts/slices" \
  --rare-chunk-size 250 \
  --layout-features "ccmp,locl,kern,mark,mkmk,liga,palt,halt,vpal,vhal,vkrn,vert,vrt2,ruby"
```

---


## Python API Usage

`cleome` can be called directly from font build pipelines (accepting either file paths or in-memory `fontTools.ttLib.TTFont` objects without mutating the caller's `TTFont`).

> **Note**: When `workers > 1` (the default), `cleome` uses `ProcessPoolExecutor`. On platforms using `spawn` or `forkserver` (`macOS`, `Windows`, and `Linux` on Python 3.14+), top-level scripts must guard calls with `if __name__ == "__main__":` (or pass `workers=1`).

```python
from pathlib import Path
from cleome import SliceConfig, slice_font, slice_fonts

if __name__ == "__main__":
    # 1. Slice a Variable Font (path or in-memory TTFont)
    result = slice_font(
        "dist/jp/variable/GenSouSansJP-VF.ttf",
        out_dir=Path("dist/jp/web"),
        config=SliceConfig(
            slices_subdir="slices",
            rare_kanji_chunk_size=250,
        ),
    )
    print(f"Generated {result.css_path} and {len(result.built_files)} WOFF2 slices")

    # 2. Slice multiple Static Font weights into a unified stylesheet
    static_result = slice_fonts(
        [
            "dist/jp/static/ttf/GenSouSansJP-Cond-Regular.ttf",
            "dist/jp/static/ttf/GenSouSansJP-Cond-Bold.ttf",
        ],
        out_dir=Path("dist/jp/web-static"),
        family_name="GenSou Sans JP Cond",
    )
```

---


## Development & Testing

The test suite requires `uharfbuzz` (declared in `[project.optional-dependencies].test` and `[dependency-groups].test` in `pyproject.toml`) to verify HarfBuzz OpenType shaping (`cmap` Format 14 IVS/UVS sequences and Latin combining mark `ccmp` composition) before and after slicing.

```bash
# 1. Create a virtual environment and install cleome with test dependencies (using uv)
uv venv
uv pip install -e ".[test]"

# Or using standard venv + pip:
# python3 -m venv .venv
# .venv/bin/pip install -e ".[test]"

# 2. Run the test suite
.venv/bin/python -m unittest discover -s tests -v
```
