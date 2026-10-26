# Architecture and rationale

Cleome generates `unicode-range` `.woff2` slices and `@font-face` stylesheets for Japanese, Korean, and Chinese variable and static fonts. This document covers why Japanese web fonts need progressive slicing, what issues appear in Google Fonts' delivery of variable and static Japanese typefaces, how Cleome's 4-tier cascade shadowing and inverted-pyramid granularity address those issues, and how the OpenType subsetting pipeline handles layout features and variation sequences.


## Why Japanese web fonts need dynamic slicing

A Japanese font covers anywhere from 7,000-9,300 glyphs (JIS Level 1 and 2 / Adobe-Japan1-3 class, such as `Kosugi Maru` or `IBM Plex Sans JP`) to 16,700-18,000 glyphs (Adobe-Japan1-6 / JIS Level 1-4 class, such as `Noto Sans JP`, `Source Han Sans`, or `BIZ UDPGothic`). Even with WOFF2 Brotli compression, a single static weight is 1.0 to 3.0 MB, and a multi-axis variable font reaches 2.5 to 6.0 MB.

Self-hosting a CJK web font (from a CDN or through Next.js `next/font`, Astro, or Fontsource) usually means choosing among three approaches, each with a distinct trade-off:

* Serving an unsliced `.woff2` file (`next/font/local`) puts the entire font behind one `@font-face` rule. As soon as a single character on the page matches the font family, the browser downloads the multi-megabyte binary, which delays text rendering and causes layout shifts (FOUT / CLS).
* Running a per-page subsetter on every site build scans HTML and JS at deploy time. That adds CPU work to CI pipelines and only works when every string is known during the build. CMS updates, search results, or user-generated content with new characters fall back to system fonts.
* Copying Google Fonts' static 120-slice templates (`next/font/google` or pre-sliced packages) splits the font by `unicode-range` so the browser downloads only the slices used on the page. However, those setups share one fixed-charset template across all fonts of a language regardless of actual glyph coverage. Stylesheets grow to 107-150 KB per weight, and smaller-charset fonts emit ghost `unicode-range` rules for glyphs they do not contain.

Cleome slices a font once from its own `cmap` and writes deterministic `.woff2` files and a compact `@font-face` stylesheet. You can commit or host the output as static assets, and pages with arbitrary dynamic text work without per-deploy subsetting.


---


## Issues in Google Fonts' Japanese font delivery

To see how production CJK slicing behaves across variable and static fonts, we inspected the CSS2 API output and `.woff2` binaries (`cmap`, glyph counts, and OpenType layout tables) for 9 Japanese families on Google Fonts:

* Variable fonts: `Noto Sans JP` (v57) and `Noto Serif JP`
* Static fonts: `IBM Plex Sans JP` (v8), `BIZ UDPGothic` (v16), `M PLUS 1p` (v33), `Zen Kaku Gothic New` (v19), `Kosugi Maru`, `Sawarabi Gothic`, and `Klee One` (v13)


### How Google Fonts' shared 120-slice template works

In [`googlefonts/nam-files`](https://github.com/googlefonts/nam-files) (`gfsubsets`), Google Fonts maintains two sets of files per CJK language: `lang/*_default.nam` (static character repertoire definitions; `japanese_default.nam` defines 6,672 codepoints covering all 6,356 Kanji in JIS X 0208 Level 1 and Level 2) and `slices/*_default.txt` (the 120-slice `unicode-range` partitioning `.0` to `.119` used by the CSS2 API).

Google Fonts introduced the 120-slice numbered template when it [launched Japanese support in September 2018](https://developers.googleblog.com/2018/09/google-fonts-launches-japanese-support.html), while `Noto Sans JP` was still served as static weights. Every Japanese family on Google Fonts uses this same template. Whether a font is variable or static, or contains 16,600 characters or 7,000 characters, each emitted slice `.N` has the exact same `unicode-range` string across all inspected families, ordered in CSS from lowest priority at the top to highest priority at the bottom:

* Slices `.0` to `.59` (up to 60 codepoint-ordered rare slices) hold the 13,381 codepoints outside the frequency tiers, sorted by Unicode codepoint and split into chunks of 223 characters (`.59` has 224 characters). In `Noto Sans JP`, these 60 slices total 4,031.3 KB for the variable font (averaging 67.2 KB/slice) and 1,675.2 KB for Static Regular 400 (averaging 27.9 KB/slice).
* Slices `.60` to `.117` (58 frequency-ranked slices) contain 50 codepoints per slice (2,900 characters total), ordered by web corpus frequency from `.117` (highest) down to `.60` (mid-low), with characters inside each slice sorted by codepoint (`U+4E00..U+9FA5`). This tier covers 82.5% of JIS Level 1 Kanji (2,445 of 2,965), 168 frequent JIS Level 2 Kanji, fullwidth alphanumerics, circled numbers, and halfwidth katakana. Slice sizes average 20.1 KB in `Noto Sans JP` VF and 5.0 to 8.7 KB across Static 400 fonts (`M PLUS 1p` 5.0 KB, `IBM Plex Sans JP` 5.6 KB, `Noto Sans JP` Static 5.6 KB, `BIZ UDPGothic` 5.9 KB, `Zen Kaku Gothic New` 6.0 KB, `Kosugi Maru` 8.6 KB).
* Slices `.118` and `.119` (2 core slices) sit above the frequency tiers. `.119` (176 `unicode-range` codepoints) holds Hiragana, Katakana, and basic punctuation; `.118` (134 codepoints) holds ASCII characters plus three Kanji (`日`, `月`, `点`). Although `.119` maps ~176 characters in `cmap`, its `.woff2` size varies by 2.5x across Static 400 fonts depending on how many unmapped vertical or alternate glyphs `GSUB` layout closure pulls in (`M PLUS 1p` 12.6 KB / 220 glyphs, `IBM Plex Sans JP` 16.0 KB / 203 glyphs, `Zen Kaku Gothic New` 17.8 KB / 203 glyphs, `BIZ UDPGothic` 18.2 KB / 377 glyphs, `Kosugi Maru` 20.0 KB / 201 glyphs, `Noto Sans JP` Static 30.8 KB / 400 glyphs, `Noto Sans JP` VF 77.0 KB / 400 glyphs).
* Named script subsets (`cyrillic`, `latin-ext`, `latin`, plus family-dependent `vietnamese`, `greek`, and others; 3 to 8 slices) sit at the bottom of the stylesheet.


### 1. Stylesheets reach 107-113 KB per weight (220+ KB for two static weights)

For a single weight or variable font request, the Google Fonts stylesheet is 107.1 to 112.5 KB uncompressed (17.6 KB Brotli). Requesting two weights (`wght@400;700`) doubles the CSS to 220.6 to 226.6 KB (242 to 252 `@font-face` blocks). In a single-style 112.5 KB stylesheet, the `unicode-range` descriptor values alone take 79,122 characters (77.3 KB, or 81.2 KB including property names):

* Script subsets (`latin` and others, 3 to 8 slices): 569 characters (0.6 KB, 0.7% of `unicode-range` text)
* Core kana and ASCII (`.119` and `.118`, 2 slices): 506 characters (0.5 KB, 0.6%)
* Frequency-ranked slices (`.117` to `.60`, 58 slices): 22,926 characters (22.4 KB, 29.0%), listing 2,900 non-contiguous codepoints
* Codepoint-ordered rare slices (`.59` to `.0`, up to 60 slices): 57,121 characters (55.8 KB, 70.4%)

Over 70% of all `unicode-range` text sits in the 60 rare-Kanji slices (`.59` to `.0`), which ordinary pages rarely download. Because `.117` to `.60` remove 2,900 frequent characters from `U+4E00-9FFF`, the remaining codepoints in that block are full of gaps. Google Fonts writes mutually exclusive `unicode-range` lists that step around every hole, which turns 60 rare chunks into 55.8 KB of hex tokens per weight (over 111 KB when two weights are requested).


### 2. Ghost `unicode-range` rules in smaller-charset fonts

Inspecting the 7 static Japanese fonts showed what happens when one 120-slice template is applied to smaller character sets. Google Fonts' subsetting pipeline intersects each template slice (`.0` to `.119`) with the target font's `cmap`:

* When the intersection is empty (0 characters), Google Fonts omits that `@font-face` block. For example, `Zen Kaku Gothic New` and `Kosugi Maru` omit `.0` and `.1` because they lack Plane 2 SIP ideographs, and `M PLUS 1p` and `Sawarabi Gothic` omit `.4`.
* When even 1 character from a 223-character template slice exists in the font, Google Fonts emits the entire unmodified 223-character `unicode-range` string into the CSS.

In the `.woff2` `cmap` tables of these static fonts, 95% to 99% of the codepoints advertised in CSS for several slices are absent from the binary:

* `IBM Plex Sans JP` `.0.woff2` (2.8 KB) declares 223 codepoints in CSS, but its `cmap` contains only 10 characters (`𨳝𨻶兔冤屠巽冕杓櫛爨`; 95.5% absent).
* `IBM Plex Sans JP` `.1.woff2` (1.6 KB) declares 223 codepoints (including `𠮷` `U+20BB7`), but its `cmap` contains only 4 characters (`𠮟𡨚𤏐𥡴`; 98.2% absent). When a page using `IBM Plex Sans JP` renders `𠮷`, the browser matches `U+20BB7` in `.1`'s `unicode-range`, downloads `.1.woff2`, finds `𠮷` missing from the binary, and then falls back to the next font in `font-family`.
* `IBM Plex Sans JP` `.50.woff2` (5.9 KB) declares 223 codepoints, while its `cmap` contains 57 JIS Level 2 characters (74.4% absent).
* `M PLUS 1p` `.0.woff2` (0.8 KB) and `Zen Kaku Gothic New` `.2.woff2` (0.9 KB) each declare 223 codepoints in CSS while containing 2 characters in `cmap` (`𦍌𧾷` in `M PLUS 1p`, `￢￤` in `Zen Kaku Gothic New`; 99.1% absent).
* `Kosugi Maru` `.59.woff2` (1.8 KB) declares 224 codepoints, while its `cmap` contains 10 characters (95.5% absent).

JIS Level 1+2 fonts (~7,000 to 9,300 characters) both trigger wasted network requests before falling back on unmapped characters and carry 7,000 to 9,000 unmapped rare Kanji in their CSS.


### 3. Common kana (`む`, `モ`), punctuation, and UI symbols pull in Kanji slices

Because all Japanese fonts on Google Fonts share the `.0` to `.119` template, every family inherits the same codepoint placement in `.119` and `.118`:

* `.119` includes 85 Hiragana and 85 Katakana characters, but omits modern Hiragana `む` (`U+3080`) and Katakana `モ` (`U+30E2`). Instead, `む` is in `.115` (with Kanji `以先公初別力...`) and `モ` is in `.116` (with `下今他付体作...`). In addition, `ヶ` (`U+30F6`) is in `.106`, `ヵ` (`U+30F5`) is in `.79`, standalone voiced marks `゛` / `゜` (`U+309B` / `U+309C`) are in `.65` / `.77`, historical `ゐ` / `ヱ` are in `.61`, and `ゔ` (`U+3094`) is in `.54`. Rendering a kana-only heading or paragraph that contains `む` and `モ` downloads `.119` plus two Kanji chunks (`.115` and `.116`). On a static font using Regular 400 and Bold 700, the browser downloads those extra Kanji chunks for each weight.
* `.118` mixes ASCII with `日`, `月`, and `点`, while omitting uppercase `N` (`U+004E`). `N` sits in `.117` (the top Kanji chunk), and 14 ASCII symbols (`# $ % & + < = > @ ^ _ ` | ~`) are spread across `.115` to `.86`. Because the `[latin]` block (`U+0000-00FF`) at the end of the CSS overrides 123 of the 134 codepoints in `.118` (and `N` in `.117`), the browser never uses `.118` for ASCII and downloads it only when `日`, `月`, `点`, or `‼ ⁈ ⁉` appear.
* Japanese quotation marks `「」` (`U+300C-300D`), brackets `【】`, and fullwidth digits `０-９` sit in `.117` to `.108`. Common UI symbols such as `→` (`U+2192`) sit in `.58` (a 223-character codepoint-ordered slice), so a single arrow downloads dozens of mathematical and box-drawing glyphs.


### 4. Long articles trigger 40-50 requests per weight

In a 4,500-character technical article (553 unique characters), Google Fonts' 50-character slices trigger 46 `.woff2` requests (1,026.3 KB for a variable font). At the top of the frequency curve, the 400 most common Kanji appear in almost every article, so splitting them into 50-character slices costs 8 separate HTTP requests per weight on every page.

A variable font shares one set of 124 `.woff2` URLs across `font-weight: 400` and `700`, whereas static fonts (`IBM Plex Sans JP`, `BIZ UDPGothic`, `M PLUS 1p`, `Zen Kaku Gothic New`) emit separate `.woff2` URLs per weight (242 to 252 files total). That doubles the request count for every slice shared between headings and body text. At the other end of the curve, any character outside the 2,900 characters in `.117` to `.60` (including 517 JIS Level 1 Kanji placed in `.59` to `.0`, or 2010 Joyo Kanji additions like `𠮟`, `塡`, `剝`, `頰`) triggers a 223-character Tier 4 slice.


---


## How Cleome slices a font

Cleome replaces static templates with a 4-tier slicing planner ([`cleome/slicer.py`](../src/cleome/slicer.py)) and `unicode-range` compaction helpers ([`cleome/urange.py`](../src/cleome/urange.py)) that read each input font's `cmap`. It orders `@font-face` rules from lowest priority (Tier 4, at the top of the stylesheet) to highest priority (Tier 1, at the bottom of the stylesheet):

| Tier (CSS order) | Slice ID pattern | Target characters | `unicode-range` strategy |
| :--- | :--- | :--- | :--- |
| **1. Tier 4: Rare and extended Kanji**<br>_(Declared first / lowest priority)_ | `<slug>-kanji-rare-01..NN.woff2`<br>_(~250 chars/slice, dynamic count)_ | JIS Level 2-4, CJK Unified Ideographs Ext A-J, CJK Compatibility Ideographs not in Tier 3 | **1-token contiguous hex range (`U+XXXX-YYYY`)**: The `.woff2` binary excludes Tier 3 frequent Kanji, while the CSS `unicode-range` spans a single contiguous hex token (`0.2 to 0.5 KB` for Tier 4 `unicode-range` values vs. `55.8 KB` across Google Fonts' 60 rare-Kanji slices `.0` to `.59`). Tier 3-1 rules declared below shadow any frequent Kanji inside that hex span. |
| **2. Tier 3: Frequent Kanji**<br>_(Declared low -> high frequency)_ | `<slug>-kanji-freq-40..01.woff2`<br>_(Up to 3,169 Kanji)_ | All 2,136 Joyo Kanji (incl. 2010 JIS Level 3 `𠮟塡剝頰`) + all 2,965 JIS Level 1 Kanji + top web-frequent JIS Level 2/3 & `𠮷` | **Inverted-pyramid granularity**:<br>- **Rank 1-400**: `200 chars x 2 slices` (hit on almost every article; coarse grouping saves 6 HTTP requests)<br>- **Rank 401-1,200**: `80 chars x 10 slices` (medium granularity)<br>- **Rank 1,201-3,169**: `~70 chars x 28 slices` (fine granularity limits collateral downloads when 1-2 mid-low frequency Kanji appear in long articles) |
| **3. Tier 2: UI and extended symbols**<br>_(Declared `symbols-ext` -> `symbols-ui`)_ | `<slug>-symbols-ext(-01..NN).woff2`<br>`<slug>-symbols-ui.woff2` | `symbols-ui`: Arrows (`←↑→↓`), triangles (`▶◀▲▼`), circled digits (`⓪①-⑳`, `❶-❿`), Roman numerals (`Ⅰ-Ⅻ`), `℃`, CJK math (`∞≒≠≦≧`), checks (`✓`), stars/notes (`★☆♪♫♡⚠`)<br>`symbols-ext`: Greek, Cyrillic, box drawing, extended math, CJK enclosed/units, halfwidth katakana | Keeping `symbols-ui` (~16 KB VF / ~7-9 KB Static) separate from `symbols-ext` prevents common UI markers like `→`, `▶`, `①`, `✓`, `℃`, or `≒` from pulling in box-drawing or foreign script slices. |
| **4. Tier 1: Core kana and Latin**<br>_(Declared last / highest priority)_ | `<slug>-kana-core.woff2`<br>`<slug>-latin.woff2` | `kana-core`: Hiragana (`U+3040-309F`, incl. combining voiced marks `U+3099-309A`), Katakana (`U+30A0-30FF`, `U+31F0-31FF`), CJK Punctuation (`U+3000-303F`), Fullwidth ASCII/Symbols (`U+FF01-FF60`, `U+FFE0-FFE6`)<br>`latin`: ASCII, Latin-1/Extended (`U+0000-024F`, `U+1E00-1EFF`, POJ `U+1D3A` `ᴺ` / `U+207F` `ⁿ`), Combining Diacritical Marks (`U+0300-036F`, `U+1AB0-1AFF`, `U+1DC0-1DFF`, `U+FE20-FE2F`), Western punctuation & currency (`€`, `™`, `ℓ`) | Keeps all modern and historical kana (`む`, `モ`, `ヶ`, `ヵ`, `ゐ`, `ヱ`, `ゔ`), CJK punctuation (`「」`, `【】`, `・`, `ー`), and combining voiced marks (`U+3099-309A`) in `kana-core`, and keeps Latin base letters with their combining diacritical marks (`U+0300-036F`) in `latin`. |


### 1. CSS cascade shadowing for 1-token rare Kanji ranges (Tier 4)

Under [CSS Fonts Module Level 4](https://www.w3.org/TR/css-fonts-4/#unicode-range-desc), when multiple `@font-face` rules with the same `font-family` and style descriptors have overlapping `unicode-range` values, the rule declared later in the stylesheet takes precedence.

Cleome places Tier 4 (`kanji-rare-01..NN`) at the top of the stylesheet (lowest priority) and Tiers 3 through 1 below it (higher priority):

* Each Tier 4 `.woff2` binary excludes all Tier 3 frequent Kanji and holds ~250 rare Kanji by default.
* Instead of stepping around excluded Tier 3 codepoints, each Tier 4 `@font-face` rule declares one contiguous hex span from its first to its last rare Kanji present in the font: `unicode-range: U+XXXX-YYYY;`.
* When a frequent Kanji inside `U+XXXX-YYYY` appears on a page, the higher-priority Tier 3 rule declared later in the CSS matches it. The browser requests the Tier 4 slice only when a rare Kanji in that span is rendered.
* Tier 4 `unicode-range` text shrinks from 55.8 KB across 60 slices in Google Fonts to 0.2 to 0.5 KB (466 characters across 42 slices for a 16,743-character font, or ~160 characters across 15 slices for an ~8,300-character JIS Level 1+2 font). A single-variant stylesheet drops from 107-112.5 KB raw (17.6 KB Brotli) to 30 to 54 KB raw (~6.0 to 7.5 KB Brotli).
* Tier 4 chunks are partitioned within separate CJK ideograph regions (`Ext-A` `U+3400-4DBF`, `BMP` `U+4E00-9FFF`, `Compat` `U+F900-FAFF`, `SIP` `U+20000-2FA1F`, and `TIP` `U+30000-3347F`), so a 1-token range never bridges across non-Kanji blocks or across Plane 2 (SIP) and Plane 3 (TIP).
* Because a Tier 4 rule advertises a contiguous `U+XXXX-YYYY` span between its first and last mapped rare Kanji in that region, rendering an unmapped codepoint strictly inside that span causes the browser to download that one Tier 4 `.woff2` slice before falling back to the next font. Unlike Google Fonts' static template, Cleome never emits ranges for unmapped blocks or planes, so a JIS Level 1+2 font without Plane 2 SIP ideographs never emits a `U+20000+` rule or triggers a false download on `𠮷` (`U+20BB7`).


### 2. Non-Kanji partitioning and `cmap` range compaction (Tier 1 and Tier 2)

Cleome separates non-Kanji characters from Kanji slices and groups them into four categories at the bottom of the stylesheet:

* Tier 1 `latin` (declared last, highest priority) covers Basic ASCII, Latin-1 Supplement, Latin Extended-A, Extended-B, and Extended Additional (`U+0000-024F`, `U+1E00-1EFF`, including Hepburn romanization macrons `Ā ā Ī ī Ū ū Ē ē Ō ō` and Pe̍h-ōe-jī superscripts `U+1D3A` `ᴺ` and `U+207F` `ⁿ`), Combining Diacritical Marks (`U+0300-036F`, `U+1AB0-1AFF`, `U+1DC0-1DFF`, `U+FE20-FE2F`), General Punctuation (`U+2000-206F`), Currency Symbols (`U+20A0-20CF`, `€`, `₩`), `ℓ` (`U+2113`), and `™` (`U+2122`).
* Tier 1 `kana-core` covers CJK Symbols and Punctuation (`U+3000-303F`: `　`, `、`, `。`, `「」`, `『』`, `【】`, `々`, `〆`), Hiragana (`U+3040-309F`, including `む`, `ゐ`, `ゔ`, standalone `゛` and `゜`, and combining voiced marks `U+3099-309A`), Katakana (`U+30A0-30FF`, including `モ`, `ヶ`, `ヵ`, `ヱ`, `・`, `ー`), Katakana Phonetic Extensions (`U+31F0-31FF`: `ㇰ-ㇿ`), and Fullwidth ASCII and Symbols (`U+FF01-FF60`, `U+FFE0-FFE6`). Kana and punctuation never pull in a Kanji slice.
* Tier 2 `symbols-ui` holds common UI and article symbols (~16 KB VF, ~7-9 KB Static), including `℃` (`U+2103`), Roman numerals (`U+2160-216B`: `Ⅰ-Ⅻ`), arrows (`U+2190-21FF`: `←↑→↓↔⇒`), CJK math and comparison symbols (`∞`, `≒`, `≠`, `≦`, `≧`), circled numbers (`①-⑳`, `⓪`, `❶-❿`), geometric shapes (`■□▲△▶▷▼▽◀◁◆◇◈○◎●`), and stars, notes, and marks (`★☆♪♫♡♥⚠✓✔✕✖`).
* Tier 2 `symbols-ext` (`symbols-ext-01..NN`) holds the remaining non-Kanji characters in the font's `cmap` (Greek, Cyrillic, box drawing, math symbols, enclosed CJK letters and ideographs, halfwidth katakana), split into chunks of ~350 characters.

Because Cleome builds every slice from the input font's `cmap`, smaller-charset fonts (~7,000- to 9,300-character JIS Level 1+2 static fonts) scale down from 91 slices to 55-65 slices and never emit ghost `unicode-range` declarations for characters outside their repertoire. In Tiers 1, 2, and 3, Cleome compacts only the codepoints present in the font, bridging small unmapped gaps (`<= 0x80`) in Tiers 1 and 2 via binary search only when the gap contains zero codepoints from any lower-priority slice.


### 3. Inverted-pyramid Kanji granularity (Tier 3)

To decide how to group Kanji, we simulated four Japanese text scenarios against a 2-axis variable font across four slicing models:

* Model A (pure Unicode block slicing, 24 slices, no frequency ranking) splits `U+4E00-9FFF` into 16 contiguous hex blocks. CSS shrinks to 5.4 KB, but Unicode CJK Unified Ideographs are ordered by Kangxi radical and stroke count rather than frequency. Everyday Kanji (`一`, `人`, `日`, `月`, `語`, `駅`) are spread across almost every block, so a 25-character UI label hits 7 Kanji blocks (1,940.6 KB) and a 4,500-character article downloads 4,196.9 KB.
* Model B (coarse fixed frequency granularity, 30 slices, 250 chars x 10) handles short UI labels in 3 files (232.4 KB), but downloads 17 files (2,059.4 KB vs. Google Fonts' 1,026.3 KB) on a 4,500-character article. In a long article (~400 unique Kanji), mid-to-low frequency Kanji (ranks 1,000 to 3,000) appear only 1 or 2 times per chunk. When those chunks hold 100 to 250 characters each, a single hit pulls in 99 to 249 unused Kanji.
* Model C (inverted-pyramid progressive granularity, used by Cleome) matches chunk sizes to Zipf's law across 3,169 Tier 3 Kanji ([`cleome/data/kanji_freq.py`](../src/cleome/data/kanji_freq.py)):
  * Rank 1 to 400 (`kanji-freq-01..02`, 200 chars x 2 slices): Hit on almost every article. Grouping them into 2 slices instead of 8 fifty-character slices saves 6 HTTP requests without adding unused bandwidth on articles.
  * Rank 401 to 1,200 (`kanji-freq-03..12`, 80 chars x 10 slices): Medium granularity for moderately frequent Kanji.
  * Rank 1,201 to 3,169 (`kanji-freq-13..40`, ~70 chars x 28 slices): Fine granularity covering all 2,136 Joyo Kanji (including the 2010 JIS Level 3 additions `𠮟`, `塡`, `剝`, `頰`), all 2,965 JIS Level 1 Kanji, frequent JIS Level 2/3 and Hyogai print-standard Kanji (`鷗`, `噓`, `繫`, `禱`, `屛`, `焰`, `摑`, `俱`, `吞`, `啞`, `麴`, `麵`), and `𠮷` (`U+20BB7`). This limits collateral downloads when 1 or 2 mid-low frequency Kanji appear in a long article and keeps Joyo and JIS Level 1 Kanji out of Tier 4.


### Simulation comparison (variable font baseline)

Combining 1-token Tier 4 cascade shadowing with Tier 3 inverted-pyramid Kanji granularity (`Model C`) cuts raw CSS size by more than half compared to Google Fonts and reduces both HTTP requests and `.woff2` transfer bytes across short UI strings and long articles.

| Metric / Scenario | Google Fonts Current<br>_(124 slices, 50-char fixed)_ | Model A: Pure Unicode Blocks<br>_(24 slices, no frequency)_ | Model B: Coarse Frequency<br>_(30 slices, 250 chars x 10)_ | Model C: Cleome Default<br>_(91 slices, Inverted-Pyramid)_ |
| :--- | :--- | :--- | :--- | :--- |
| **Total slices (`@font-face` rules)** | 124 | 24 | 30 | **91** _(scales down to 61 for ~8,300-char JIS Level 1+2 fonts)_ |
| **Raw CSS size (`unicode-range` text)** | 112.5 KB (77.3 KB) | 5.4 KB (0.5 KB) | 25.9 KB (19.7 KB) | **~34-54 KB (25.4 KB; ~6.0-7.5 KB Brotli)** |
| **Scenario 1: Kana & punctuation**<br>(`こんにちは、むかし「モモタロウ」が…`) | 4 files / 130.1 KB<br>(`.119` + `.117` + `.116` + `.115`) | 1 file / 104.4 KB<br>(`kana-core` only) | 1 file / 104.4 KB | **1 file / 104.4 KB**<br>(`kana-core` only, zero Kanji chunks pulled in) |
| **Scenario 2: Short UI label**<br>(`ログイン・新規会員登録 (2026年10月14日)`) | 7 files / 184.0 KB | 9 files / 1,940.6 KB<br>_(basic Kanji scattered across blocks)_ | 3 files / 232.4 KB | **3 files / 203.9 KB** |
| **Scenario 3: Blog intro**<br>(~200 chars / 121 unique) | 18 files / 388.5 KB | 14 files / 3,092.3 KB | 8 files / 622.4 KB | **10 files / 453.5 KB** |
| **Scenario 4: Full technical article**<br>(~4,500 chars / 553 unique) | 46 files / 1,026.3 KB | 19 files / 4,196.9 KB | 17 files / 2,059.4 KB<br>_(inflated by collateral downloads)_ | **28 files / 973.4 KB**<br>_(fewer requests & smaller transfer than Google Fonts)_ |


---


## OpenType shaping and subsetting details

Slicing arbitrary Japanese `.ttf` and `.otf` fonts surfaced several OpenType and CSS edge cases handled in [`cleome/urange.py`](../src/cleome/urange.py), [`cleome/metadata.py`](../src/cleome/metadata.py), [`cleome/subsetter.py`](../src/cleome/subsetter.py), [`cleome/css.py`](../src/cleome/css.py), and [`cleome/pipeline.py`](../src/cleome/pipeline.py).


### 1. Intra-slice OpenType shaping and cross-slice lookup pruning

When a font is split into multiple `@font-face` files via `unicode-range`, browsers treat each slice as an independent font face and never apply `GSUB` (glyph substitution) or `GPOS` (glyph positioning) lookups across slice boundaries. In addition, `fontTools.subset` prunes any multi-glyph lookup (`ccmp`, `mark`, `mkmk`, `liga`, `kern`) if any participating input glyph is absent from the slice.

By default, Cleome keeps all OpenType layout feature tags (`layout_features=('*',)` and `layout_closure=True`) and expands forward `GSUB` glyph closure within each slice. Single-glyph substitutions (vertical writing forms `vert` and `vrt2`, proportional and halfwidth alternate metrics `palt`, `vpal`, `halt`, `vhal`, `fwid`, `pwid`, and glyph set variants `jp78`, `jp90`, `nlck`) work in every slice.

Initially, Cleome placed Combining Diacritical Marks (`U+0300-036F`) in Tier 2 (`symbols-ext`) while Latin base letters were in Tier 1 (`latin`). Because base letters and combining marks were in different slices, `fontTools.subset` stripped `ccmp` and `mark` composition lookups from both slices, which broke decomposed sequences like `o + U+0304` (`ō`). Moving `U+0300-036F`, `U+1AB0-1AFF`, `U+1DC0-1DFF`, and `U+FE20-FE2F` into Tier 1 `latin`, and keeping combining voiced marks `U+3099-309A` in `kana-core`, preserves intra-slice composition and kerning (verified in the test suite with `uharfbuzz`). `fontTools.subset` still prunes multi-glyph lookups whose input glyphs lie in different slices (such as `U+0300-036F` marks applied to non-Latin base characters in `symbols-ext` or `kana-core`), and browsers cannot shape them across separate `@font-face` files.


### 2. Retaining `cmap` Format 14 (UVS and IVS) without polluting the base `cmap`

Japanese fonts often include a `cmap` Format 14 subtable that maps Ideographic Variation Sequences (IVS `U+E0100..U+E01EF`) and Standardized Variants (`VS1..VS16`, `U+FE00..U+FE0F`) to variant glyphs.

When `fontTools.subset` runs, `_prepare_uvs_for_slice` filters `table.uvsDict` to base characters present in the slice and passes both the base codepoints and active Variation Selector codepoints to `subsetter.populate(unicodes=...)`. That retains both default UVS (`glyphName=None`) and non-default UVS entries and pulls their target variant glyphs into the slice. Afterwards, Cleome removes standalone Variation Selector codepoints from the Format 4 and Format 12 base `cmap` subtables so one slice cannot claim `U+E0100` across the whole font family.


### 3. Restoring base `cmap` entries omitted by upstream fonts (`U+553E` `唾`)

While checking character coverage against upstream Adobe `SourceHanSansJP-VF.otf` (v2.005), we found that the Joyo and JIS Level 1 Kanji `唾` (`U+553E`, JIS row 34 cell 35) was missing from the Format 4 and Format 12 `cmap` subtables, even though its glyph (`glyph03714`) was present in the binary and mapped in `cmap` Format 14 (`U+553E + U+E0100 -> glyph03714`).

Before planning or subsetting, `patch_uvs_missing_base_cmap` checks `cmap` Format 14 for any base codepoint that maps to a concrete glyph (`gname is not None`) but is absent from `getBestCmap()`, and restores it into the Format 4 and Format 12 subtables in memory. When multiple variation selectors exist for the same omitted base character, Cleome sorts Ideographic Variation Selectors (`U+E0100..U+E01EF`) in ascending order ahead of `VS1..VS16` so it selects the standard base form (`U+E0100`) deterministically.


### 4. Variable font axes (`wght`, `wdth`, `ital`, `slnt`) and multi-font static bundling

* `extract_font_metadata` reads `fvar` in variable fonts to emit continuous CSS ranges (`font-weight: 250 900`, `font-stretch: 70% 100%`), and reads `OS/2` (`usWeightClass`, `usWidthClass`, `fsSelection`) and `post` (`italicAngle`) in static fonts. When a variable font has an `ital` axis spanning upright (`< 0.5`) and italic (`>= 0.5`) or a `slnt` axis, Cleome emits `@font-face` rules for each supported style (`normal`, `italic`, `oblique <min>deg <max>deg`) pointing to the same `.woff2` slice files. It omits `normal` when the font's axis ranges do not include an upright form.
* When bundling multiple static font files into one stylesheet, Cleome checks that all inputs share the same `font-family` (unless overridden) and have unique `(font-weight, font-stretch, font-style)` descriptors. It computes each variant's slices from its own mapped codepoints (`variant_slice_specs`) so a subsetted italic or condensed weight never emits ghost `unicode-range` rules for glyphs that exist only in the regular weight.
* Public Python APIs (`slice_font`, `slice_fonts`, `export_woff2_slices`) serialize in-memory `TTFont` inputs without mutating the caller's object, keep `head.modified` (`recalcTimestamp=False`) so repeated builds produce byte-identical `.woff2` files, and retain license records (`nameID` 0, 13, 14) in every slice. By default (`filename_hash=True`), Cleome includes an 8-character SHA-256 content digest in each `.woff2` filename and prunes superseded hashes for the built stems without deleting unrelated `.woff2` files from other font families.


---


## References


### Google Fonts CJK subsetting and slicing

* Google Fonts Team, "[Google Fonts launches Japanese support](https://developers.googleblog.com/2018/09/google-fonts-launches-japanese-support.html)", _Google Developers Blog_, September 28, 2018. Explains how Google Fonts analyzed character frequency across Japanese web pages and adopted the two-stage `unicode-range` + WOFF2 + HTTP/2 slicing strategy (splitting the top ~3,000 characters by frequency into fine-grained slices and sorting the remaining characters by codepoint into equal-sized slices), while noting that OpenType layout features do not apply across slice boundaries.
* Google Fonts Team, "[Google Fonts launches Korean support](https://developers.googleblog.com/2018/04/google-fonts-launches-korean-support.html)", _Google Developers Blog_, April 4, 2018. Introduces Google Fonts' CJK `unicode-range` subsetting pipeline, corpus-driven character clustering, and cross-site slice caching.
* Quan Wang and Yiran Mao (Google Inc.), "[Learning Better Font Slicing Strategies from Data](https://www.tdcommons.org/dpubs_series/906/)", _Technical Disclosure Commons_, Defensive Publications Series 906, December 2017 ([project page](https://google.github.io/speaker-id/publications/MLFont/)). Details Google's data-driven CJK font slicing research using topic models and genetic algorithms, applied to `Noto Sans CJK` (`Noto Sans SC Sliced` on Google Fonts Early Access).
* Rod Sheeter, "[Smaller Fonts with WOFF 2.0 and unicode-range](https://developers.googleblog.com/2015/02/smaller-fonts-with-woff-20-and-unicode.html)", _Google Developers Blog_, February 18, 2015. Covers Google Fonts' deployment of `unicode-range` subsetting with `fontTools` and WOFF 2.0.
* [`googlefonts/nam-files`](https://github.com/googlefonts/nam-files) (`gfsubsets`): Static codepoint subset definitions (`.nam` and slice files) used by Google Fonts to slice fonts for its CSS API.


### Specifications and font tooling

* W3C, "[CSS Fonts Module Level 4 - Character range: the `unicode-range` descriptor](https://www.w3.org/TR/css-fonts-4/#unicode-range-desc)" and "[Font Matching Algorithm](https://www.w3.org/TR/css-fonts-4/#font-matching-algorithm)". Defines `@font-face` rule ordering and composite font cascade precedence when multiple faces share style descriptors and overlap in `unicode-range`.
* W3C, "[WOFF File Format 2.0](https://www.w3.org/TR/WOFF2/)".
* Microsoft / ISO/IEC 14496-22, "[OpenType Specification - `cmap` Format 14: Unicode Variation Sequences](https://learn.microsoft.com/en-us/typography/opentype/spec/cmap#format-14-unicode-variation-sequences)", "[`GSUB` - Glyph Substitution Table](https://learn.microsoft.com/en-us/typography/opentype/spec/gsub)", and "[`fvar` - Font Variations Table](https://learn.microsoft.com/en-us/typography/opentype/spec/fvar)".
* [`fonttools/fonttools`](https://github.com/fonttools/fonttools) (`fontTools.subset`) and [`harfbuzz/uharfbuzz`](https://github.com/harfbuzz/uharfbuzz).
* [`notofonts/noto-cjk`](https://github.com/notofonts/noto-cjk) / [`adobe-fonts/source-han-sans`](https://github.com/adobe-fonts/source-han-sans): Upstream `Noto Sans CJK` / `Source Han Sans` pan-CJK and region-specific OpenType/CFF2 fonts.
