# Cleome

Cleome is a Python CLI tool and library that slices Japanese, Korean, and Chinese (`JP` / `KR` / `SC` / `TC` / `HK`) variable and static fonts (`.ttf` / `.otf`) into `unicode-range` `.woff2` files and `@font-face` CSS.

Cleome inspects a font's `cmap` once and writes deterministic static assets instead of scanning page HTML on every site deploy. You can host the generated `.woff2` chunks and `@font-face` stylesheet directly from Next.js, Astro, Fontsource, or any static CDN. Compared to Google Fonts' static slicing templates, Cleome produces smaller stylesheets and transfers fewer `.woff2` bytes while keeping OpenType layout features, UVS/IVS variation sequences, and variable font axes intact.

---


## Installation

Requires Python `>= 3.15` and [`uv`](https://docs.astral.sh/uv/).

```bash
# Install as a standalone CLI tool from GitHub
uv tool install git+https://github.com/myakura/cleome

# Or run directly without installing
uvx --from git+https://github.com/myakura/cleome cleome plan SourceHanSansJP-VF.otf

# Or add to a Python project
uv add git+https://github.com/myakura/cleome
```

---


## CLI usage

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


## Python API usage

Call `slice_font` or `slice_fonts` from a build script with either file paths or in-memory `fontTools.ttLib.TTFont` objects. Cleome does not mutate caller-supplied `TTFont` instances.

When `workers > 1` (the default), Cleome runs subsetting in a `ProcessPoolExecutor`. On platforms that use `spawn` or `forkserver` (`macOS`, `Windows`, and `Linux` on Python 3.14+), guard top-level scripts with `if __name__ == "__main__":` or pass `workers=1`.

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


## Development and testing

```bash
uv sync
uv run python -m unittest discover -s tests -v -f
```

---


## Documentation

* [Architecture and rationale](docs/architecture-and-rationale.md) explains the 4-tier cascade shadowing design, inverted-pyramid Kanji granularity simulations, OpenType subsetting details, and the issues found in Google Fonts' Japanese delivery.
* [Chinese and Korean (`KR` / `SC` / `TC` / `HK`) slicing analysis](docs/cjk-slicing-analysis.md) covers Google Fonts' Korean and Chinese (`SC`, `TC`, `HK`) slice templates, ghost `unicode-range` rules, punctuation/Jamo/Bopomofo splits, and 4-tier simulation results.

---


## Acknowledgments

* [Google Fonts](https://fonts.google.com/) published the production stylesheets, slice definitions (`gfsubsets` / `nam-files`), and corpus frequency data analyzed during this project.
* [Noto Sans Japanese (`Noto Sans JP`)](https://fonts.google.com/noto/specimen/Noto+Sans+JP) and [Source Han Sans (`源ノ角ゴシック`)](https://github.com/adobe-fonts/source-han-sans) were the primary Japanese typefaces used to test and verify Cleome.
* [fontTools](https://github.com/fonttools/fonttools) handles OpenType table inspection, `cmap` / `GSUB` / `GPOS` subsetting (`fontTools.subset`), and WOFF2 packing.
* [Brotli](https://github.com/google/brotli) compresses and decompresses the WOFF2 streams.
* [HarfBuzz](https://github.com/harfbuzz/harfbuzz) and [uharfbuzz](https://github.com/harfbuzz/uharfbuzz) verify OpenType glyph shaping, `cmap` Format 14 variation sequences, and combining mark composition (`ccmp`) in the test suite.
* [Stixis](https://github.com/myakura/stixis) validates and serializes the generated `@font-face` CSS rules and `unicode-range` descriptors.

---


## License

[MIT License](LICENSE)
