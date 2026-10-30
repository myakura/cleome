# Cleome

Cleome is a Python CLI tool and library that slices Japanese, Korean, and Chinese (`JP` / `KR` / `SC` / `TC` / `HK`) variable and static fonts (`.ttf` / `.otf`) into `unicode-range` `.woff2` files and `@font-face` CSS.

Cleome inspects a font's `cmap` once and writes deterministic static assets instead of scanning page HTML on every site deploy. You can host the generated `.woff2` chunks and `@font-face` stylesheet directly from Next.js, Astro, Fontsource, or any static CDN. Compared to Google Fonts' static slicing templates, Cleome produces smaller stylesheets and transfers fewer `.woff2` bytes while keeping OpenType layout features, UVS/IVS variation sequences, and variable font axes intact.

---


## Installation

Requires Python `>= 3.15` and [`uv`](https://docs.astral.sh/uv/).

```bash
# Install as a standalone CLI tool from PyPI
uv tool install cleome

# Or run directly without installing
uvx cleome plan SourceHanSansJP-VF.otf

# Or add to a Python project
uv add cleome

# Install the unreleased development version from GitHub
uv tool install git+https://github.com/myakura/cleome
```

---


## CLI usage

See the [Hosting and usage guide](https://github.com/myakura/cleome/blob/main/docs/hosting-and-usage.md) for deployment layouts (`--slices-dir`, `--url-prefix`, `--css-out`, `--no-filename-hash`) and [`skills/cleome/SKILL.md`](https://github.com/myakura/cleome/blob/main/skills/cleome/SKILL.md) for the full CLI and Python API contract.

```bash
# Preview the dynamic 4-tier slice plan without writing files (read-only)
cleome plan SourceHanSansJP-VF.otf
cleome plan SourceHanSansJP-VF.otf --json

# Slice a Variable Font (outputs <out-dir>/<slug>.css and <out-dir>/slices/<slug>-*-<hash8>.woff2)
cleome build SourceHanSansJP-VF.otf -o dist/web
cleome build SourceHanSansJP-VF.otf -o dist/web --dry-run
cleome build SourceHanSansJP-VF.otf -o dist/web --force
cleome build SourceHanSansJP-VF.otf -o public/fonts --url-prefix "/fonts/slices" --css-out src/styles/fonts.css
cleome build SourceHanSansJP-VF.otf -o src/assets/fonts --no-filename-hash

# Explicitly select a CJK slicing profile (auto, jp, kr, sc, tc, hk)
cleome build SourceHanSansJP-VF.otf --locale jp -o dist/web-jp
cleome build NotoSansKR-VF.ttf --locale kr -o dist/web-kr

# Slice multiple Static Font weights into a single unified @font-face CSS and slices/ directory
cleome build MyFontJP-Regular.ttf MyFontJP-Bold.ttf -o dist/web-static

# Customize CSS font-family, URL prefix, chunk sizes, or retained OpenType layout features
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

See [`skills/cleome/SKILL.md`](https://github.com/myakura/cleome/blob/main/skills/cleome/SKILL.md) for all exported functions, dataclasses, non-mutation guarantees, and `ProcessPoolExecutor` entrypoint rules.

```python
from pathlib import Path
from cleome import (
    SliceConfig,
    format_unicode_range,
    slice_font,
    slice_fonts,
)

if __name__ == "__main__":
    # 1. Slice a Variable Font (path or in-memory TTFont; locale="auto" by default)
    result = slice_font(
        "SourceHanSansJP-VF.otf",
        out_dir=Path("dist/web"),
        config=SliceConfig(
            locale="auto",  # "auto", "jp", "kr", "sc", "tc", or "hk"
            slices_subdir="slices",
            rare_chunk_size=250,
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

    # 3. Format CSS unicode-range tokens: a set/frozenset/list formats discrete codepoints,
    #    whereas a 2-element tuple (start, end) formats an inclusive interval
    assert format_unicode_range([0x3041, 0x3043]) == "U+3041, U+3043"
    assert format_unicode_range((0x3041, 0x3043)) == "U+3041-3043"
```

---


## Development and testing

```bash
uv sync
uv run python -m unittest discover -s tests -v -f
```

---


## Documentation

* [Hosting and usage guide](https://github.com/myakura/cleome/blob/main/docs/hosting-and-usage.md) explains how to deploy generated `<slug>.css` and `.woff2` slices on static hosts, bundlers, and CDNs, configure `--url-prefix` and `--slices-dir`, and set preload, CORS, and `Cache-Control` headers.
* [Architecture and rationale](https://github.com/myakura/cleome/blob/main/docs/architecture-and-rationale.md) explains the 4-tier cascade shadowing design, inverted-pyramid Kanji granularity simulations, OpenType subsetting details, and the issues found in Google Fonts' Japanese delivery.
* [Chinese and Korean (`KR` / `SC` / `TC` / `HK`) slicing analysis](https://github.com/myakura/cleome/blob/main/docs/cjk-slicing-analysis.md) covers Google Fonts' Korean and Chinese (`SC`, `TC`, `HK`) slice templates, ghost `unicode-range` rules, punctuation, Jamo, and Bopomofo splits, and 4-tier simulation results.

---


## Acknowledgments

* [Google Fonts](https://fonts.google.com/) published the production stylesheets, slice definitions (`gfsubsets` / `nam-files`), and corpus frequency data analyzed during this project.
* [Noto Sans Japanese (`Noto Sans JP`)](https://fonts.google.com/noto/specimen/Noto+Sans+JP) and [Source Han Sans (`源ノ角ゴシック`)](https://github.com/adobe-fonts/source-han-sans) were the primary Japanese typefaces used to test and verify Cleome.
* [fontTools](https://github.com/fonttools/fonttools) handles OpenType table inspection, `cmap`, `GSUB`, and `GPOS` subsetting (`fontTools.subset`), and WOFF2 packing.
* [Brotli](https://github.com/google/brotli) compresses and decompresses the WOFF2 streams.
* [HarfBuzz](https://github.com/harfbuzz/harfbuzz) and [uharfbuzz](https://github.com/harfbuzz/uharfbuzz) verify OpenType glyph shaping, `cmap` Format 14 variation sequences, and combining mark composition (`ccmp`) in the test suite.
* [Stixis](https://github.com/myakura/stixis) validates and serializes the generated `@font-face` CSS rules and `unicode-range` descriptors.

---


## License

[MIT License](https://github.com/myakura/cleome/blob/main/LICENSE)
