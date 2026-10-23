# Cleome

**Cleome** is a Python CLI tool and library that takes Japanese, Korean, or Chinese (`JP` / `KR` / `SC` / `TC` / `HK`) fonts (`.ttf` / `.otf`, both **Variable Fonts** and **Static Fonts**) and generates Web-optimized `unicode-range` `.woff2` slices and `@font-face` CSS.

Cleome slices a font **once** from its character set into deterministic static assets rather than scanning page HTML on every site deploy. The generated `.woff2` chunks and `@font-face` CSS drop directly into any self-hosting setup (Next.js, Astro, Fontsource, or static CDNs) and handle arbitrary dynamic text while cutting stylesheet size and `.woff2` transfer compared to Google Fonts' static slicing templates, preserving OpenType layout features, UVS/IVS variation sequences, and Variable Font axes.

---


## CLI Usage

```bash
# Preview the dynamic 4-tier slice plan without writing files (read-only)
cleome plan SourceHanSansJP-VF.otf
cleome plan SourceHanSansJP-VF.otf --json

# Slice a Variable Font (auto-detects CJK profile; outputs <out-dir>/<slug>.css and <out-dir>/slices/<slug>-*.woff2)
# Subsequent builds with unchanged inputs/config skip WOFF2 subsetting automatically; pass --force to rebuild
cleome build SourceHanSansJP-VF.otf -o dist/web
cleome build SourceHanSansJP-VF.otf -o dist/web --force

# Explicitly select a CJK slicing profile (auto, jp, kr, sc, tc, hk)
cleome build SourceHanSansJP-VF.otf --locale jp -o dist/web-jp
cleome build NotoSansKR-VF.ttf --locale kr -o dist/web-kr

# Slice multiple Static Font weights into a single unified @font-face CSS and slices/ directory
cleome build MyFontJP-Regular.ttf MyFontJP-Bold.ttf -o dist/web-static

# Customize CSS font-family, URL prefix, chunk sizes, or retained OpenType layout features
# Note: --layout-features replaces the entire feature list (default is '*' for all features),
# so include vertical writing (vert,vrt2,vpal,vhal,vkrn) and composition (ccmp,locl) tags if needed.
cleome build MyFont-VF.otf -o dist/web \
  --locale jp \
  --family "My Font JP" \
  --slug "my-font-jp" \
  --url-prefix "/assets/fonts/slices" \
  --rare-chunk-size 250 \
  --layout-features "ccmp,locl,kern,mark,mkmk,liga,palt,halt,vpal,vhal,vkrn,vert,vrt2,ruby"
```

---


## Python API Usage

Cleome can be called directly from font build pipelines (accepting either file paths or in-memory `fontTools.ttLib.TTFont` objects without mutating the caller's `TTFont`).

> **Note**: When `workers > 1` (the default), Cleome uses `ProcessPoolExecutor`. On platforms using `spawn` or `forkserver` (`macOS`, `Windows`, and `Linux` on Python 3.14+), top-level scripts must guard calls with `if __name__ == "__main__":` (or pass `workers=1`).

```python
from pathlib import Path
from cleome import SliceConfig, slice_font, slice_fonts

if __name__ == "__main__":
    # 1. Slice a Variable Font (path or in-memory TTFont; locale="auto" by default)
    result = slice_font(
        "SourceHanSansJP-VF.otf",
        out_dir=Path("dist/web"),
        config=SliceConfig(
            locale="auto",  # "auto", "jp", "kr", "sc", "tc", or "hk"
            slices_subdir="slices",
            rare_kanji_chunk_size=250,
        ),
    )
    print(f"Generated {result.css_path} and {len(result.built_files)} WOFF2 slices")

    # 2. Slice multiple Static Font weights into a unified stylesheet
    static_result = slice_fonts(
        [
            "MyFontJP-Regular.ttf",
            "MyFontJP-Bold.ttf",
        ],
        out_dir=Path("dist/web-static"),
        family_name="My Font JP",
    )
```

---


## Development & Testing

```bash
uv venv
uv pip install -e ".[test]"
.venv/bin/python -m unittest discover -s tests -v -f
```

---


## Documentation

* **[Architecture & Rationale](docs/architecture-and-rationale.md)**: Background, Google Fonts (`Noto Sans JP` and static fonts) `unicode-range` analysis, 4-tier cascade shadowing, inverted-pyramid Kanji granularity simulations, and OpenType subsetting details.
* **[Chinese & Korean (`KR` / `SC` / `TC` / `HK`) Slicing Analysis](docs/cjk-slicing-analysis.md)**: Analysis of Google Fonts' Korean and Chinese (`SC`, `TC`, `HK`) slicing templates, ghost `unicode-range`s, punctuation/Jamo/Bopomofo fragmentation, and 4-tier simulation comparisons.

---


## Acknowledgments

* **[Google Fonts](https://fonts.google.com/)**: For pioneering frequency-ranked `unicode-range` web font delivery and providing the production stylesheets and slices that informed Cleome's analysis and Kanji frequency rankings.
* **[Noto Sans Japanese (`Noto Sans JP`)](https://fonts.google.com/noto/specimen/Noto+Sans+JP) & [Source Han Sans (`源ノ角ゴシック`)](https://github.com/adobe-fonts/source-han-sans)**: For serving as the foundational open-source Japanese typefaces inspected during the design and verification of Cleome.
* **[fontTools](https://github.com/fonttools/fonttools)**: For OpenType table inspection, `cmap` / `GSUB` / `GPOS` subsetting (`fontTools.subset`), and WOFF2 table packing.
* **[Brotli](https://github.com/google/brotli)**: For WOFF2 stream compression and decompression.
* **[HarfBuzz](https://github.com/harfbuzz/harfbuzz) & [uharfbuzz](https://github.com/harfbuzz/uharfbuzz)**: For verifying OpenType glyph shaping, `cmap` Format 14 IVS/UVS variation sequences, and combining mark composition (`ccmp`) across slices.

---


## License

[MIT License](LICENSE)
