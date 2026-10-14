# cleome

**`cleome`** is a Python CLI tool and library that takes any Japanese font (`.ttf` / `.otf`, both **Variable Fonts** and **Static Fonts**) and generates Web-optimized `unicode-range` `.woff2` slices and `@font-face` CSS.

Instead of relying on static fixed-charset templates or bloating CSS with thousands of fragmented ranges, `cleome` dynamically inspects the input font's `cmap` and builds a **4-tier progressive slice plan** powered by **CSS Cascade Shadowing** and **Inverted-Pyramid Kanji Frequency Granularity**.

---

## Key Architecture: 4-Tier Dynamic Slicing & CSS Cascade Shadowing

In CSS Fonts Module Level 4, when multiple `@font-face` rules within the same `font-family` have overlapping `unicode-range` descriptors, **later rules in the stylesheet take precedence over earlier rules**. `cleome` orders `@font-face` declarations from lowest priority (**Tier 4**, at the top of the CSS) to highest priority (**Tier 1**, at the bottom of the CSS):

| Tier (CSS Order) | Slice ID Pattern | Target Characters | `unicode-range` Strategy & Rationale |
| :--- | :--- | :--- | :--- |
| **1. Tier 4: Rare & Extended Kanji**<br>*(Declared FIRST / Lowest Priority)* | `<slug>-kanji-rare-01..NN.woff2`<br>*(~250 chars/slice, dynamic count)* | JIS Level 2–4, CJK Unified Ideographs Ext A–I, CJK Compatibility Ideographs not in Tier 3 | **1-Token Contiguous Hex Range (`U+XXXX-YYYY`)**: The `.woff2` binary excludes Tier 3 frequent Kanji, while the CSS `unicode-range` spans a single contiguous hex token (`0.2–0.5 KB` total CSS instead of Google Fonts' `55.8 KB` Swiss-cheese ranges). Any frequent Kanji in that hex range is automatically shadowed by Tier 3–1 rules declared below. |
| **2. Tier 3: Frequent Kanji**<br>*(Declared Low → High Frequency)* | `<slug>-kanji-freq-40..01.woff2`<br>*(Up to 3,136 Kanji)* | All 2,965 JIS Level 1 Kanji + top Web-frequent JIS Level 2 Kanji (ranked deterministically offline) | **Inverted-Pyramid Granularity**:<br>• **Rank 1–400**: `200 chars × 2 slices` (hit with ~100% probability on articles; coarse grouping saves 6 HTTP requests)<br>• **Rank 401–1200**: `80 chars × 10 slices` (medium granularity)<br>• **Rank 1201–3136**: `~69 chars × 28 slices` (fine granularity minimizes collateral download when 1–2 mid-low frequency Kanji appear in long articles) |
| **3. Tier 2: UI & Extended Symbols**<br>*(Declared `symbols-ext` → `symbols-ui`)* | `<slug>-symbols-ext(-01..NN).woff2`<br>`<slug>-symbols-ui.woff2` | • `symbols-ui`: Arrows (`←↑→↓`), triangles (`▶◀▲▼`), circled digits (`⓪①–⑳`, `❶–❿`), checks (`✓`), stars/notes (`★☆♪♫♡⚠`)<br>• `symbols-ext`: Greek, Cyrillic, box drawing, math, CJK enclosed/units, halfwidth katakana | Separating lightweight `symbols-ui` (~15 KB) ensures common UI markers like `→`, `▶`, `①`, or `✓` never pull in heavy box-drawing or foreign script slices. |
| **4. Tier 1: Core Kana & Latin**<br>*(Declared LAST / Highest Priority)* | `<slug>-kana-core.woff2`<br>`<slug>-latin.woff2` | • `kana-core`: Complete Hiragana (`U+3040-309F`), Katakana (`U+30A0-30FF`, `U+31F0-31FF`), CJK Punctuation (`U+3000-303F`), Fullwidth ASCII/Symbols (`U+FF01-FF60`, `U+FFE0-FFE6`)<br>• `latin`: ASCII, Latin-1/Extended, Western punctuation & currency (`€`, `™`, `ℓ`) | Guarantees **zero omissions** for modern/historical kana (`む`, `モ`, `ヶ`, `ヵ`, `ゐ`, `ヱ`, `ゔ`) and CJK punctuation (`「」`, `【】`, `・`, `ー`), keeping all Kana + Punctuation kerning/metrics within a single slice. |

### Automatic Font Metadata & Upstream `cmap` Bug Recovery

- **Variable & Static Auto-Detection**: Automatically extracts `font-family` (`name` ID 16/1), `font-weight` (continuous range from `fvar` `wght` axis or static `OS/2.usWeightClass`), `font-stretch` (`wdth` percentage range if present), and `font-style` (`normal`, `italic`, or `oblique` range).
- **UVS Format 14 Base `cmap` Auto-Patching**: Detects characters mapped in `cmap` Format 14 (Variation Selector sequences) that were accidentally omitted from Format 4/12 in upstream fonts (such as Joyo/JIS Level 1 Kanji `唾` `U+553E` in Adobe `SourceHanSansJP-VF.otf` v2.005) and restores them before slicing.
- **OpenType Feature & Variable Table Preservation**: Retains `GSUB`, `GPOS`, `GDEF` layout features and `fvar`, `gvar`, `CFF2`, `HVAR`, `VVAR`, `MVAR`, `STAT`, `avar` tables across every `.woff2` slice.

---

## CLI Usage

```bash
# Slice a Variable Font (outputs <out-dir>/<slug>.css and <out-dir>/slices/<slug>-*.woff2)
cleome GenSouSansJP-VF.ttf -o dist/web

# Preview the dynamic 4-tier slice plan without writing files
cleome GenSouSansJP-VF.ttf --dry-run

# Slice multiple Static Font weights into a single unified @font-face CSS and slices/ directory
cleome GenSouSansJP-Regular.ttf GenSouSansJP-Bold.ttf -o dist/web-static

# Customize CSS font-family, URL prefix, or chunk sizes
cleome MyFont-VF.otf -o dist/web \
  --family "My Font JP" \
  --slug "my-font-jp" \
  --url-prefix "/assets/fonts/slices" \
  --rare-chunk-size 250
```

---

## Python API Usage

`cleome` can be called directly from font build pipelines (accepting either file paths or in-memory `fontTools.ttLib.TTFont` objects):

```python
from pathlib import Path
from cleome import slice_font, slice_fonts, SliceConfig

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
