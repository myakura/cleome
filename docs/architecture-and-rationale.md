# Architecture & Rationale

`cleome` generates Web-optimized `unicode-range` `.woff2` slices and `@font-face` stylesheets for Japanese Variable and Static fonts. This document explains why Japanese web fonts require progressive slicing, what structural issues were found when inspecting how Google Fonts serves both Variable and Static Japanese typefaces, how `cleome`'s 4-tier cascade shadowing and inverted-pyramid granularity solve those issues, and how the OpenType subsetting pipeline evolved.


## Why Do Japanese Web Fonts Need Dynamic Slicing?

A Japanese font covers anywhere from ~7,000-9,300 glyphs (JIS Level 1 and 2 / Adobe-Japan1-3 class, such as `Kosugi Maru` or `IBM Plex Sans JP`) to 16,700-18,000 glyphs (Adobe-Japan1-6 / JIS Level 1-4 class, such as `Noto Sans JP`, `Source Han Sans`, or `BIZ UDPGothic`). Even with WOFF2 Brotli compression, a single static weight is 1.0 to 3.0 MB, and a multi-axis Variable Font reaches 2.5 to 6.0 MB.

Serving a Japanese font as a single `@font-face` rule forces the browser to download the entire multi-megabyte binary synchronously as soon as a single character on the page matches the font family, delaying text rendering and causing layout shifts (FOUT / CLS). Conversely, static per-page subsetting works only when every possible text string is known at build time, which fails for blogs, CMS-driven sites, user-generated content, and web applications.

CSS `unicode-range` partitioning splits a font into smaller `.woff2` chunks so the browser downloads only the slices required by the characters on the page. However, existing public setups rely on a single static fixed-charset template shared across all Japanese fonts regardless of their actual glyph coverage, leaving no general-purpose open-source tool that dynamically adapts to any Japanese `.ttf` or `.otf` font's character set.


---


## What Issues Were Found in Google Fonts' Japanese Font Delivery?

To understand how production CJK font slicing behaves across both Variable and Static fonts, we inspected the CSS2 API output and `.woff2` binaries (`cmap`, glyph counts, and OpenType layout tables) across 9 Japanese families on Google Fonts:

* **Variable Fonts**: `Noto Sans JP` (v57) and `Noto Serif JP`
* **Non-Variable (Static) Fonts**: `IBM Plex Sans JP` (v8), `BIZ UDPGothic` (v16), `M PLUS 1p` (v33), `Zen Kaku Gothic New` (v19), `Kosugi Maru`, `Sawarabi Gothic`, and `Klee One` (v13)


### How Does Google Fonts' Shared 120-Slice Template Work?

The 120-slice numbered partitioning (`.0` to `.119`) is not specific to Variable Fonts; it is a universal static template ([`googlefonts/nam-files`](https://github.com/googlefonts/nam-files) / `gfsubsets`) introduced when Google Fonts [launched Japanese support in September 2018](https://developers.googleblog.com/2018/09/google-fonts-launches-japanese-support.html) (`Noto Sans JP` was still served as static fonts) and applied identically across every Japanese family on Google Fonts. Whether a font is Variable or Static, or contains 16,600 characters or 7,000 characters, every emitted numbered slice `.N` uses the exact same `unicode-range` string (100% identical across all inspected families), ordered in CSS from lowest priority at the top to highest priority at the bottom:

* **Codepoint-ordered rare slices (`.0` to `.59`, up to 60 slices)**: The 13,381 codepoints not in the frequency tiers, sorted by Unicode codepoint and split mechanically into chunks of 223 characters (`.59` has 224 characters). In `Noto Sans JP`, these 60 slices total 4,031.3 KB for the Variable Font (averaging 67.2 KB/slice) and 1,675.2 KB for Static Regular 400 (averaging 27.9 KB/slice).
* **Frequency-ranked Kanji and symbols (`.60` to `.117`, 58 slices)**: Strictly 50 codepoints per slice (2,900 characters total), ordered by web corpus frequency from `.117` (highest) down to `.60` (mid-low). Covers 82.5% of JIS Level 1 Kanji (2,445 of 2,965), 168 frequent JIS Level 2 Kanji, fullwidth alphanumerics, circled numbers, and halfwidth katakana. Averages 20.1 KB/slice in `Noto Sans JP` VF and 5.0 to 8.7 KB/slice across Static 400 fonts (`M PLUS 1p` 5.0 KB, `IBM Plex Sans JP` 5.6 KB, `Noto Sans JP` Static 5.6 KB, `BIZ UDPGothic` 5.9 KB, `Zen Kaku Gothic New` 6.0 KB, `Kosugi Maru` 8.6 KB).
* **Core Kana and ASCII/date slices (`.118` and `.119`, 2 slices)**: `.119` (176 `unicode-range` codepoints) holds core Hiragana, Katakana, and basic punctuation; `.118` (134 codepoints) holds ASCII characters plus three Kanji (`日`, `月`, `点`). While `.119` maps ~176 characters in `cmap`, its `.woff2` binary size varies by 2.5x across Static 400 fonts depending on how many unmapped vertical/alternate glyphs are pulled in via `GSUB` layout closure (`M PLUS 1p` 12.6 KB / 220 glyphs, `IBM Plex Sans JP` 16.0 KB / 203 glyphs, `Zen Kaku Gothic New` 17.8 KB / 203 glyphs, `BIZ UDPGothic` 18.2 KB / 377 glyphs, `Kosugi Maru` 20.0 KB / 201 glyphs, `Noto Sans JP` Static 30.8 KB / 400 glyphs, `Noto Sans JP` VF 77.0 KB / 400 glyphs).
* **Script subsets (`cyrillic`, `latin-ext`, `latin`, plus family-dependent `vietnamese`, `greek`, etc., 3 to 8 slices)**: Standard script templates declared at the very bottom of the stylesheet.


### 1. Why Do Stylesheets Bloat to 107-113 KB per Weight (220+ KB for Two Static Weights)?

For a single weight or Variable Font request, the Google Fonts stylesheet is **107.1 to 112.5 KB** uncompressed (17.6 KB Brotli). When requesting two weights (`wght@400;700`), the CSS doubles to **220.6 to 226.6 KB** (242 to 252 `@font-face` blocks). In a single-style 112.5 KB stylesheet, the `unicode-range` descriptor values alone consume 79,122 characters (77.3 KB, or 81.2 KB including property names):

* **Script subsets (`latin`, etc., 3 to 8 slices)**: 569 characters (0.6 KB, 0.7% of `unicode-range` text)
* **Core Kana and ASCII (`.119` and `.118`, 2 slices)**: 506 characters (0.5 KB, 0.6%)
* **Frequency-ranked slices (`.117` to `.60`, 58 slices)**: 22,926 characters (22.4 KB, 29.0%), listing 2,900 non-contiguous codepoints
* **Codepoint-ordered rare slices (`.59` to `.0`, up to 60 slices)**: 57,121 characters (55.8 KB, 70.4%)

Over 70% of all `unicode-range` text is spent in the 60 rare-Kanji slices (`.59` to `.0`), which are almost never downloaded on ordinary pages. Because 2,900 frequent characters were plucked out into `.117` to `.60`, the remaining codepoints in `U+4E00-9FFF` are full of gaps. Google Fonts writes mutually exclusive `unicode-range` lists that step around every single hole, turning 60 rare chunks into 55.8 KB of fragmented hex tokens per weight (over 111 KB when two weights are requested).


### 2. What Are "Ghost `unicode-range`s" in Non-Variable and Smaller-Charset Fonts?

Inspecting the 7 non-variable Japanese fonts revealed a major flaw in applying a single 120-slice template across all typefaces. Google Fonts' subsetting pipeline intersects each template slice (`.0` to `.119`) with the target font's `cmap`:

* If the intersection is completely empty (0 characters), that `@font-face` block is omitted (for example, `.0` and `.1` are omitted in `Zen Kaku Gothic New` and `Kosugi Maru` because they lack Plane 2 SIP ideographs, and `.4` is omitted in `M PLUS 1p` and `Sawarabi Gothic`).
* However, **if even 1 character from a 223-character template slice exists in the font, Google Fonts emits the entire unmodified 223-character `unicode-range` string into the CSS**.

Inspecting the actual `.woff2` `cmap` tables of these static fonts exposed numerous sparse slices ("ghost `unicode-range`s") where 95% to 99% of the codepoints advertised in CSS do not exist in the `.woff2` binary:

* **`IBM Plex Sans JP` `.0.woff2` (2.8 KB)**: CSS declares 223 codepoints, but the `.woff2` `cmap` contains only 10 characters (`𨳝𨻶兔冤屠巽冕杓櫛爨`; 95.5% absent).
* **`IBM Plex Sans JP` `.1.woff2` (1.6 KB)**: CSS declares 223 codepoints (including `𠮷` `U+20BB7`), but the `.woff2` `cmap` contains only 4 characters (`𠮟𡨚𤏐𥡴`; 98.2% absent). When a page using `IBM Plex Sans JP` renders the common name character `𠮷`, the browser sees `U+20BB7` in `.1`'s `unicode-range`, downloads `.1.woff2`, discovers `𠮷` is missing from the font binary, and only then falls back to the next font in `font-family`.
* **`IBM Plex Sans JP` `.50.woff2` (5.9 KB)**: CSS declares 223 codepoints, but `cmap` contains only 57 JIS Level 2 characters (74.4% absent).
* **`M PLUS 1p` `.0.woff2` (0.8 KB) and `Zen Kaku Gothic New` `.2.woff2` (0.9 KB)**: Each declares 223 codepoints in CSS while containing only 2 characters in `cmap` (`𦍌𧾷` in `M PLUS 1p`, `￢￤` in `Zen Kaku Gothic New`; 99.1% absent).
* **`Kosugi Maru` `.59.woff2` (1.8 KB)**: CSS declares 224 codepoints, but `cmap` contains only 10 characters (95.5% absent).

As a result, JIS Level 1+2 fonts (~7,000 to 9,300 characters) not only trigger wasted network requests before falling back on unmapped characters, but also bloat their stylesheets to 107-226 KB by listing 7,000 to 9,000 rare Kanji they do not even contain.


### 3. Why Do Common Kana (`む`, `モ`), Punctuation, and UI Symbols Trigger Extra Kanji Downloads Across All Fonts?

Because all Japanese fonts on Google Fonts share the same `.0` to `.119` template, every single family inherits the same codepoint anomalies in `.119` and `.118`:

* **Modern Hiragana `む` (`U+3080`) and Katakana `モ` (`U+30E2`) are missing from `.119` in every font**: `.119` includes 85 Hiragana and 85 Katakana characters, but omits `む` and `モ`. Instead, `む` is placed in `.115` (alongside Kanji `以先公初別力...`) and `モ` is placed in `.116` (alongside `下今他付体作...`). Furthermore, `ヶ` (`U+30F6`) is in `.106`, `ヵ` (`U+30F5`) is in `.79`, standalone voiced marks `゛` / `゜` (`U+309B` / `U+309C`) are in `.65` / `.77`, historical `ゐ` / `ヱ` are in `.61`, and `ゔ` (`U+3094`) is in `.54`. Rendering a pure-kana heading or paragraph containing `む` and `モ` forces the browser to download `.119` plus two Kanji chunks (`.115` and `.116`) - and for a static font using Regular 400 and Bold 700, those extra Kanji chunks are downloaded twice.
* **`.118` mixes ASCII with `日`, `月`, `点` while omitting `N` (`U+004E`)**: `.118` contains `A` through `M` and `O` through `Z`, but uppercase `N` is placed in `.117` (the top Kanji chunk), and 14 ASCII symbols (`# $ % & + < = > @ ^ _ ` | ~`) are scattered across `.115` to `.86`. Because the `[latin]` block (`U+0000-00FF`) declared at the end of the CSS overrides 123 of the 134 codepoints in `.118` (as well as `N` in `.117`), `.118` is never used for ASCII; it is downloaded only when `日`, `月`, `点`, or `‼ ⁈ ⁉` appear.
* **CJK punctuation and UI symbols are scattered across Kanji and 223-char chunks**: Japanese quotation marks `「」` (`U+300C-300D`), brackets `【】`, and fullwidth digits `０-９` are mixed into `.117` to `.108`. Common UI symbols such as `→` (`U+2192`) reside in `.58` (a 223-character codepoint-ordered slice), pulling in dozens of unrelated mathematical and box-drawing glyphs whenever a single arrow appears.


### 4. Why Do Long Articles Trigger 40 to 50 Requests (or Double Downloads Across Static Weights)?

In a 4,500-character technical article (553 unique characters), the 50-character fixed chunks trigger 46 `.woff2` requests (1,026.3 KB for a Variable Font). At the top of the frequency curve, the most frequent 400 Kanji appear in almost every article, so splitting them into 50-character slices simply forces 8 separate HTTP requests per weight on every page. Furthermore, whereas a Variable Font shares a single set of 124 `.woff2` URLs across `font-weight: 400` and `700`, non-variable fonts (`IBM Plex Sans JP`, `BIZ UDPGothic`, `M PLUS 1p`, `Zen Kaku Gothic New`) emit completely separate `.woff2` URLs per weight (242 to 252 files total), doubling the request count for every core slice used in both headings and body text. Conversely, if even a single character falls outside the 2,900 characters in `.117` to `.60` (including the 517 JIS Level 1 Kanji relegated to `.59` to `.0`, or 2010 Joyo Kanji additions like `𠮟`, `塡`, `剝`, `頰`), it triggers a 223-character Tier 4 slice.


---


## How Does `cleome` Solve These Issues?

`cleome` replaces static templates with a dynamic 4-tier slicing planner ([`cleome/slicer.py`](../src/cleome/slicer.py)) that inspects each input font's actual `cmap` and combines three mechanisms.


### 1. CSS Cascade Shadowing for 1-Token Rare Kanji Ranges (Tier 4)

Under [CSS Fonts Module Level 4](https://www.w3.org/TR/css-fonts-4/#unicode-range-desc), when multiple `@font-face` rules within the same `font-family` and style descriptors have overlapping `unicode-range` values, the rule declared later in the stylesheet takes precedence.

`cleome` places **Tier 4 (`kanji-rare-01..NN`)** at the very top of the stylesheet (lowest priority) and **Tier 3 to Tier 1** below it (higher priority):

* Each Tier 4 `.woff2` binary is subsetted to exclude all Tier 3 frequent Kanji (containing ~250 rare Kanji per slice by default).
* Instead of listing hundreds of fragmented ranges around the excluded Tier 3 characters, each Tier 4 `@font-face` rule declares a single contiguous hex span from its first to its last rare Kanji actually present in the font: `unicode-range: U+XXXX-YYYY;`.
* Whenever a frequent Kanji inside `U+XXXX-YYYY` appears on a page, it is matched by the higher-priority Tier 3 rule declared later in the CSS, so the Tier 4 slice is never requested unless a genuinely rare Kanji in that span is rendered.
* **Result**: Tier 4 `unicode-range` text shrinks from 55.8 KB across 60 slices in Google Fonts to **0.2 to 0.5 KB** (466 characters across 42 slices for a 16,743-character font, or ~160 characters across 15 slices for an ~8,300-character JIS Level 1+2 font - a >99% reduction in Tier 4 `unicode-range` text), reducing the single-variant stylesheet from 107-112.5 KB raw (17.6 KB Brotli) to **30 to 54 KB raw (~6.0 to 7.5 KB Brotli)**.
* **Plane boundary invariant**: Tier 4 chunks are partitioned strictly within separate CJK ideograph regions (`Ext-A` `U+3400-4DBF`, `BMP` `U+4E00-9FFF`, `Compat` `U+F900-FAFF`, `SIP` `U+20000-2FA1F`, and `TIP` `U+30000-3347F`) so a 1-token range never bridges across non-Kanji blocks or across Plane 2 (SIP) and Plane 3 (TIP).
* **Trade-off on unmapped codepoints inside a Tier 4 span**: Because a Tier 4 rule advertises a contiguous `U+XXXX-YYYY` span between its first and last mapped rare Kanji in that region, if a page renders a codepoint _strictly inside that span_ that is not mapped in the font (such as an unimplemented CJK ideograph between two mapped rare ideographs), the browser will download that single Tier 4 `.woff2` slice before discovering the glyph is absent and falling back to the next font. However, unlike Google Fonts' static template, `cleome` never emits ranges for unmapped blocks or planes (so a JIS Level 1+2 font with no Plane 2 SIP ideographs never emits a `U+20000+` rule and never triggers a false download on `𠮷` `U+20BB7`).


### 2. Semantic Non-Kanji Partitioning & `cmap`-Driven Range Compaction (Tier 1 & Tier 2)

`cleome` separates all non-Kanji characters from Kanji slices and partitions them into four semantic categories declared at the bottom of the stylesheet:

* **Tier 1 `latin` (Declared last / Highest priority)**: Basic ASCII, Latin-1 Supplement, Latin Extended-A/B and Additional (`U+0000-024F`, `U+1E00-1EFF`, covering Hepburn romanization macrons `Ā ā Ī ī Ū ū Ē ē Ō ō`), Combining Diacritical Marks (`U+0300-036F`, `U+1AB0-1AFF`, `U+1DC0-1DFF`, `U+FE20-FE2F`), General Punctuation (`U+2000-206F`), Currency Symbols (`U+20A0-20CF`, `€`, `₩`), `ℓ` (`U+2113`), and `™` (`U+2122`).
* **Tier 1 `kana-core`**: Complete CJK Symbols and Punctuation (`U+3000-303F`: `　`, `、`, `。`, `「」`, `『』`, `【】`, `々`, `〆`), Hiragana (`U+3040-309F`, including `む`, `ゐ`, `ゔ`, standalone `゛`/`゜`, and combining voiced marks `U+3099-309A`), Katakana (`U+30A0-30FF`, including `モ`, `ヶ`, `ヵ`, `ヱ`, `・`, `ー`), Katakana Phonetic Extensions (`U+31F0-31FF`: `ㇰ-ㇿ`), and Fullwidth ASCII/Symbols (`U+FF01-FF60`, `U+FFE0-FFE6`). Guarantees zero omissions for kana and punctuation without ever pulling in a Kanji slice.
* **Tier 2 `symbols-ui`**: High-frequency UI and article symbols (~15 KB VF / ~6-8 KB Static), including arrows (`U+2190-21FF`: `←↑→↓↔⇒`), circled numbers (`①-⑳`, `⓪`, `❶-❿`), geometric shapes (`■□▲△▶▷▼▽◀◁◆◇○◎●`), stars/notes/marks (`★☆♪♫♡♥⚠✓✔✕✖`). Isolating `symbols-ui` ensures that an accordion arrow `▶` or step number `①` never downloads heavy box-drawing or foreign script chunks.
* **Tier 2 `symbols-ext` (`symbols-ext-01..NN`)**: Remaining non-Kanji characters present in the font's `cmap` (Greek, Cyrillic, box drawing, math symbols, enclosed CJK letters/ideographs, halfwidth katakana), dynamically split into balanced chunks of ~350 characters.

Because every slice is constructed strictly from the intersection with the input font's `cmap`, smaller-charset fonts (such as ~7,000- to 9,300-character JIS Level 1+2 static fonts) automatically scale down in slice count (from 91 slices to ~55-65 slices) and never emit ghost `unicode-range` declarations for characters outside their repertoire. In Tier 1, Tier 2, and Tier 3, `cleome` lists or compacts only the codepoints actually present in the font (bridging small unmapped gaps `<= 0x80` in Tier 1 and Tier 2 via binary search only when the gap contains zero codepoints belonging to any lower-priority slice).


### 3. Inverted-Pyramid Kanji Granularity (Tier 3)

To determine how Kanji should be grouped, we simulated four realistic Japanese text scenarios against a 2-axis Variable Font across four slicing models:

* **Model A (Pure Unicode Block Slicing, 24 slices, no frequency ranking)**: Splits `U+4E00-9FFF` into 16 contiguous hex blocks. While CSS shrinks to 5.4 KB, Unicode CJK Unified Ideographs are ordered by Kangxi radical and stroke count rather than frequency. Basic everyday Kanji (`一`, `人`, `日`, `月`, `語`, `駅`) are scattered across almost every block, causing a 25-character UI label to hit 7 Kanji blocks (**1,940.6 KB**) and a 4,500-character article to download **4,196.9 KB**.
* **Model B (Coarse Fixed Frequency Granularity, 30 slices, 250 chars x 10)**: Performs well on short UI labels (3 files, 232.4 KB), but degrades on a 4,500-character article (**17 files, 2,059.4 KB** vs. Google Fonts' 1,026.3 KB). In a long article (~400 unique Kanji), mid-to-low frequency Kanji (ranks 1,000 to 3,000) appear sparsely - often just 1 or 2 characters per chunk. When mid-to-low frequency chunks contain 100 to 250 characters each, a single hit pulls in 99 to 249 unused Kanji as collateral download (false positives).
* **Model C (Inverted-Pyramid Progressive Granularity, adopted by `cleome`)**: Matches chunk sizes to Zipf's law across **3,169 Tier 3 Kanji** ([`cleome/data/kanji_freq.py`](../src/cleome/data/kanji_freq.py)):
  * **Rank 1 to 400 (`kanji-freq-01..02`, 200 chars x 2 slices)**: Hit with ~100% probability on any article. Grouping them into 2 slices instead of 8 fifty-character slices saves 6 HTTP requests with zero wasted bandwidth on articles.
  * **Rank 401 to 1,200 (`kanji-freq-03..12`, 80 chars x 10 slices)**: Medium granularity for moderately frequent Kanji.
  * **Rank 1,201 to 3,169 (`kanji-freq-13..40`, ~70 chars x 28 slices)**: Fine granularity covering 100% of all 2,136 Joyo Kanji (including the 2010 JIS Level 3 additions `𠮟`, `塡`, `剝`, `頰`), 100% of all 2,965 JIS Level 1 Kanji, frequent JIS Level 2/3 & Hyogai print-standard Kanji (`鷗`, `噓`, `繫`, `禱`, `屛`, `焰`, `摑`, `俱`, `吞`, `啞`, `麴`, `麵`), and `𠮷` (`U+20BB7`). Keeps collateral download minimal when 1 or 2 mid-low frequency Kanji appear in a long article, and prevents Joyo or JIS Level 1 Kanji from spilling into Tier 4.


### Simulation Comparison (Variable Font Baseline)

#### Google Fonts Current (124 slices, 50-char fixed)

* Total slices (`@font-face` rules): 124
* Raw CSS size (`unicode-range` text): 112.5 KB (77.3 KB)
* Scenario 1 - Kana & punctuation (`こんにちは、むかし「モモタロウ」が…`): 4 files / 130.1 KB (`.119` + `.117` + `.116` + `.115`)
* Scenario 2 - Short UI label (`ログイン・新規会員登録 (2026年10月14日)`): 7 files / 184.0 KB
* Scenario 3 - Blog intro (~200 chars / 121 unique): 18 files / 388.5 KB
* Scenario 4 - Full technical article (~4,500 chars / 553 unique): 46 files / 1,026.3 KB


#### Model A: Pure Unicode Blocks (24 slices, no frequency ranking)

* Total slices (`@font-face` rules): 24
* Raw CSS size (`unicode-range` text): 5.4 KB (0.5 KB)
* Scenario 1 - Kana & punctuation: 1 file / 104.4 KB (`kana-core` only)
* Scenario 2 - Short UI label: 9 files / 1,940.6 KB (basic Kanji scattered across blocks)
* Scenario 3 - Blog intro (~200 chars / 121 unique): 14 files / 3,092.3 KB
* Scenario 4 - Full technical article (~4,500 chars / 553 unique): 19 files / 4,196.9 KB


#### Model B: Fixed Coarse Frequency Granularity (30 slices, 250 chars x 10 + cascade shadowing)

* Total slices (`@font-face` rules): 30
* Raw CSS size (`unicode-range` text): 25.9 KB (19.7 KB)
* Scenario 1 - Kana & punctuation: 1 file / 104.4 KB
* Scenario 2 - Short UI label: 3 files / 232.4 KB
* Scenario 3 - Blog intro (~200 chars / 121 unique): 8 files / 622.4 KB
* Scenario 4 - Full technical article (~4,500 chars / 553 unique): 17 files / 2,059.4 KB (inflated by collateral downloads in mid-low frequency chunks)


#### Model C (`cleome` default: 91 slices, Inverted-Pyramid + cascade shadowing)

* Total slices (`@font-face` rules): 91 (automatically scales down to 61 for ~8,300-char JIS Level 1+2 fonts)
* Raw CSS size (`unicode-range` text): ~34-54 KB (25.4 KB; ~6.0-7.5 KB Brotli)
* Scenario 1 - Kana & punctuation: 1 file / 104.4 KB (`kana-core` only, zero Kanji chunks pulled in)
* Scenario 2 - Short UI label: 3 files / 203.9 KB
* Scenario 3 - Blog intro (~200 chars / 121 unique): 10 files / 453.5 KB
* Scenario 4 - Full technical article (~4,500 chars / 553 unique): 28 files / 973.4 KB (fewer requests and smaller transfer than Google Fonts' 46 files / 1,026.3 KB)


---


## How Were OpenType Shaping and Binary Subsetting Challenges Resolved?

Building a general-purpose slicer for arbitrary Japanese `.ttf` and `.otf` fonts surfaced several OpenType and CSS edge cases that shaped `cleome`'s implementation ([`cleome/subsetter.py`](../src/cleome/subsetter.py) and [`cleome/css.py`](../src/cleome/css.py)).


### 1. Intra-Slice OpenType Shaping vs. Cross-Slice Lookup Pruning

When a font is split into multiple `@font-face` files via `unicode-range`, browsers treat each slice as an independent font face and never apply `GSUB` (glyph substitution) or `GPOS` (glyph positioning) lookups across slice boundaries. Furthermore, `fontTools.subset` prunes any multi-glyph lookup (`ccmp`, `mark`, `mkmk`, `liga`, `kern`) if any participating input glyph is absent from the slice.

* **Preserving all layout features (`layout_features=('*',)` + `layout_closure=True`)**: By default, `cleome` keeps all OpenType layout feature tags and expands forward `GSUB` glyph closure within each slice. Single-glyph substitutions (such as vertical writing forms `vert`, `vrt2`, proportional/halfwidth alternate metrics `palt`, `vpal`, `halt`, `vhal`, `fwid`, `pwid`, and glyph set variants `jp78`, `jp90`, `nlck`) work in every slice.
* **Co-locating combining marks with their base characters**: Initially, Combining Diacritical Marks (`U+0300-036F`) were classified into Tier 2 (`symbols-ext`) while Latin base letters were in Tier 1 (`latin`). Because base letters and combining marks were in separate slices, `fontTools.subset` stripped `ccmp`/`mark` composition lookups from both slices, breaking decomposed sequences like `o + U+0304` (`ō`). Moving `U+0300-036F`, `U+1AB0-1AFF`, `U+1DC0-1DFF`, and `U+FE20-FE2F` into Tier 1 `latin`, alongside keeping combining voiced marks `U+3099-309A` in `kana-core`, ensures that intra-slice composition and kerning survive subsetting, which `cleome`'s test suite verifies end-to-end using HarfBuzz (`uharfbuzz`). Multi-glyph lookups whose input glyphs lie in _different_ slices (such as `U+0300-036F` marks applied to non-Latin base characters in `symbols-ext` or `kana-core`) are still pruned by `fontTools.subset` and cannot be shaped across separate `@font-face` files.


### 2. `cmap` Format 14 (UVS / IVS) Retention Without Base `cmap` Pollution

Japanese fonts frequently include a `cmap` Format 14 subtable mapping Ideographic Variation Sequences (IVS `U+E0100..U+E01EF`) and Standardized Variants (`VS1..VS16`, `U+FE00..U+FE0F`) to variant glyphs.

* **Retaining UVS sequences per slice**: When `fontTools.subset` runs, `cleome`'s `_prepare_uvs_for_slice` filters `table.uvsDict` to base characters present in the slice and passes both the base codepoints and active Variation Selector codepoints to `subsetter.populate(unicodes=...)`. This preserves both default UVS (`glyphName=None`) and non-default UVS entries (and pulls their target variant glyphs into the slice) while explicitly removing standalone Variation Selector codepoints from the Format 4/12 base `cmap` so one slice cannot claim `U+E0100` globally across the font family.


### 3. Auto-Patching Upstream Missing Base `cmap` Entries (The `U+553E` `唾` Bug)

While evaluating character coverage against upstream Adobe `SourceHanSansJP-VF.otf` (v2.005), we found that the Joyo and JIS Level 1 Kanji `唾` (`U+553E`, JIS row 34 cell 35) was missing from the normal Format 4 and Format 12 `cmap` subtables - even though its glyph (`glyph03714`) was present in the binary and mapped in `cmap` Format 14 (`U+553E + U+E0100 -> glyph03714`).

* **Deterministic Format 14 recovery**: Before planning or subsetting, `patch_uvs_missing_base_cmap` inspects `cmap` Format 14 for any base codepoint that maps to a concrete glyph (`gname is not None`) but is absent from `getBestCmap()`, and restores it into the Format 4/12 subtables in memory. When multiple variation selectors exist for the same omitted base character, `cleome` sorts selectors with Ideographic Variation Selectors (`U+E0100..U+E01EF`) in ascending order ahead of `VS1..VS16`, ensuring the standard base form (`U+E0100`) is chosen deterministically.


### 4. Variable Font Axes (`wght`, `wdth`, `ital`, `slnt`) & Multi-Font Static Bundling

* **Automatic axis and style descriptor extraction**: `extract_font_metadata` inspects `fvar` in Variable Fonts to emit continuous CSS ranges (`font-weight: 250 900`, `font-stretch: 70% 100%`), and inspects `OS/2` (`usWeightClass`, `usWidthClass`, `fsSelection`) and `post` (`italicAngle`) in Static Fonts. When a Variable Font includes an `ital` axis spanning upright (`< 0.5`) and italic (`>= 0.5`) or a `slnt` axis, `cleome` emits `@font-face` rules for each supported style (`normal`, `italic`, `oblique <min>deg <max>deg`) pointing to the same `.woff2` slice files, omitting `normal` if the font's axis ranges do not include an upright form.
* **Per-variant `cmap` plans and fail-fast bundling**: When bundling multiple static font files into a single stylesheet, `cleome` validates that all inputs share the same `font-family` (unless overridden) and have unique `(font-weight, font-stretch, font-style)` descriptors, and computes each variant's slices from its own mapped codepoints (`variant_slice_specs`) so a subsetted italic or condensed weight never emits ghost `unicode-range` rules for glyphs only present in the regular weight.
* **Non-destructive, deterministic builds & output directory behavior**: Public Python APIs (`slice_font`, `slice_fonts`, `export_woff2_slices`) serialize in-memory `TTFont` inputs without mutating the caller's object, preserve `head.modified` (`recalcTimestamp=False`) so repeated builds produce byte-identical `.woff2` files, and retain license records (`nameID` 0, 13, 14) in every slice. Re-running `cleome` into an existing output directory overwrites `<slug>.css` and matching slice files without deleting unrelated `.woff2` files from previous runs with different slugs or chunk counts.


---


## References


### Google Fonts CJK Subsetting & Slicing

* Google Fonts Team, "[Google Fonts launches Japanese support](https://developers.googleblog.com/2018/09/google-fonts-launches-japanese-support.html)", _Google Developers Blog_, September 28, 2018. Explains how Google Fonts analyzed character frequency across millions of Japanese web pages, observed the high-frequency head vs. long tail of rare characters, and adopted the two-stage `unicode-range` + WOFF2 + HTTP/2 slicing strategy (segmenting the top ~3,000 characters by frequency into fine-grained slices and sorting the remaining characters by Unicode codepoint into equally sized slices), while noting that OpenType layout features break across slice boundaries.
* Google Fonts Team, "[Google Fonts launches Korean support](https://developers.googleblog.com/2018/04/google-fonts-launches-korean-support.html)", _Google Developers Blog_, April 4, 2018. Introduces Google Fonts' CJK `unicode-range` subsetting pipeline, corpus-driven character clustering, and cross-site slice caching.
* Quan Wang and Yiran Mao (Google Inc.), "[Learning Better Font Slicing Strategies from Data](https://www.tdcommons.org/dpubs_series/906/)", _Technical Disclosure Commons_, Defensive Publications Series 906, December 2017 ([project page](https://google.github.io/speaker-id/publications/MLFont/)). Details Google's data-driven CJK font slicing research using topic models and genetic algorithms, applied to `Noto Sans CJK` (`Noto Sans SC Sliced` on Google Fonts Early Access).
* Rod Sheeter, "[Smaller Fonts with WOFF 2.0 and unicode-range](https://developers.googleblog.com/2015/02/smaller-fonts-with-woff-20-and-unicode.html)", _Google Developers Blog_, February 18, 2015. Covers Google Fonts' deployment of `unicode-range` subsetting with `fontTools` and WOFF 2.0.
* [`googlefonts/nam-files`](https://github.com/googlefonts/nam-files) (`gfsubsets`): Repository containing the static codepoint subset definitions (`.nam` files) used by Google Fonts to slice fonts for its CSS API.


### Specifications & Font Tooling

* W3C, "[CSS Fonts Module Level 4 - Character range: the `unicode-range` descriptor](https://www.w3.org/TR/css-fonts-4/#unicode-range-desc)" and "[Font Matching Algorithm](https://www.w3.org/TR/css-fonts-4/#font-matching-algorithm)". Defines `@font-face` rule ordering and composite font cascade precedence when multiple faces share style descriptors and overlap in `unicode-range`.
* W3C, "[WOFF File Format 2.0](https://www.w3.org/TR/WOFF2/)".
* Microsoft / ISO/IEC 14496-22, "[OpenType Specification - `cmap` Format 14: Unicode Variation Sequences](https://learn.microsoft.com/en-us/typography/opentype/spec/cmap#format-14-unicode-variation-sequences)", "[`GSUB` - Glyph Substitution Table](https://learn.microsoft.com/en-us/typography/opentype/spec/gsub)", and "[`fvar` - Font Variations Table](https://learn.microsoft.com/en-us/typography/opentype/spec/fvar)".
* [`fonttools/fonttools`](https://github.com/fonttools/fonttools) (`fontTools.subset`) and [`harfbuzz/uharfbuzz`](https://github.com/harfbuzz/uharfbuzz).
* [`notofonts/noto-cjk`](https://github.com/notofonts/noto-cjk) / [`adobe-fonts/source-han-sans`](https://github.com/adobe-fonts/source-han-sans): Upstream `Noto Sans CJK` / `Source Han Sans` pan-CJK and region-specific OpenType/CFF2 fonts.
