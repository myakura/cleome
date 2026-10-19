# Chinese & Korean (`KR` / `SC` / `TC` / `HK`) Slicing Analysis

While [Architecture & Rationale](architecture-and-rationale.md) focuses on Japanese (`JP`) web fonts, Chinese (Simplified `SC`, Traditional `TC`, and Hong Kong `HK`) and Korean (`KR`) web fonts face analogous multi-megabyte delivery challenges with distinct script structures.

To evaluate how production CJK slicing behaves outside Japanese and how `cleome`'s 4-tier architecture applies to Chinese and Korean, we inspected 37 Google Fonts CSS2 stylesheets and 1,785 `.woff2` / `.ttf` slice binaries across 28 Chinese and Korean families:

* **Korean (`KR`, 12 families)**: Variable Fonts `Noto Sans KR` (v40), `Noto Serif KR`, and `Hahmlet`; Static Fonts `IBM Plex Sans KR`, `Nanum Gothic`, `Nanum Myeongjo`, `Gothic A1`, `Gowun Dodum`, `Gowun Batang`, `Do Hyeon`, `Black Han Sans`, and `Bagel Fat One` (plus `Orbit`, `Diphylleia`, `Gasoek One`, `Jua`, and `Song Myung`).
* **Simplified Chinese (`SC`, 9 families)**: Variable Fonts `Noto Sans SC` (v41) and `Noto Serif SC`; Static Fonts `ZCOOL XiaoWei`, `ZCOOL KuaiLe`, `ZCOOL QingKe HuangYou`, `Ma Shan Zheng`, `Long Cang`, `Liu Jian Mao Cao`, and `Zhi Mang Xing`.
* **Traditional Chinese (`TC`, 6 families)**: Variable Fonts `Noto Sans TC` and `Noto Serif TC`; Static Fonts `LXGW WenKai TC`, `Chocolate Classical Sans`, `Cactus Classical Serif`, and `Chiron GoRound TC`.
* **Hong Kong Chinese (`HK`, 4 families)**: Variable and Static Fonts `Noto Sans HK`, `Noto Serif HK`, `Chiron Hei HK`, and `Chiron Sung HK`.


---


## How Do Google Fonts' CJK Templates Compare Across Languages?

Like Japanese, every Korean, Simplified Chinese, Traditional Chinese, and Hong Kong Chinese family on Google Fonts is partitioned using a static per-language numbered template (`gfsubsets` / `nam-files`). Within each language group, every numbered slice `.N` emits the exact same `unicode-range` string across all families (0 mismatches across all inspected families). However, the allocation between codepoint-ordered rare slices and frequency-ranked slices differs sharply from Japanese:

| Template | Numbered Slices | Codepoint-Ordered Rare Slices (Tier 4 Equivalent) | Frequency-Ranked & Core Slices (Tier 3 / Tier 1 Equivalent) | Single-Weight Raw CSS (`unicode-range` Text) | Two-Weight Static Raw CSS |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`JP` (Japanese)** | 120 (`.0`..`.119`) | **60 slices** (`.0`..`.59`)<br>223-224 chars/slice (13,381 chars)<br>67.2 KB/slice VF, 27.9 KB Static | **58 slices** (`.60`..`.117`): **50 chars/slice** (2,900 chars; 20.1 KB VF)<br>**2 slices** (`.118`..`.119`): ASCII + Kana (310 chars) | **112.5 KB**<br>(79.2 KB / 17.6 KB Brotli) | **220.6-226.6 KB** |
| **`KR` (Korean)** | 120 (`.0`..`.119`) | **100 slices** (`.0`..`.99`)<br>154-155 chars/slice (15,493 chars)<br>29.8 KB/slice VF, 15.8 KB Static | **20 slices only** (`.100`..`.119`): **100 chars/slice** (2,000 chars; 19.5 KB VF)<br>_(No dedicated Jamo or punctuation core slice)_ | **93.4 KB** (with Hanja, 60.4 KB)<br>**54.2-58.5 KB** (Hangul-only) | **117.4 KB**<br>(`IBM Plex Sans KR`) |
| **`SC` (Simplified Chinese)** | 97 (`.4`..`.119`)<br>_(slots `.0`..`.3`, `.7`..`.20`, `.92`..`.96` unused)_ | **77 slices** (`.4`..`.99`)<br>138-139 chars/slice (10,669 chars)<br>41.2 KB/slice VF, 22.1 KB Static | **19 slices** (`.100`..`.118`): **188 chars/slice** (3,572 chars; **57.6 KB/slice VF**)<br>**1 top slice** (`.119`): **276 chars** (**74.4 KB VF**) | **110.7 KB**<br>(82.3 KB / 17.3 KB Brotli) | **220.6 KB**<br>(`Noto Sans SC`) |
| **`TC` (Traditional Chinese)** | 104-108 (`.0`..`.123`) | **84-88 slices** (`.0`..`.103`)<br>134-135 chars/slice (11,278+ chars)<br>33.1 KB/slice VF, 17.9 KB Static | **19 slices** (`.104`..`.122`): **213 chars/slice** (4,047 chars; **68.8 KB/slice VF**)<br>**1 top slice** (`.123`): **272 chars** (35.5 KB VF) | **122.4-128.6 KB**<br>(91.9-93.8 KB / 19.5 KB Brotli) | **253.4 KB**<br>(`LXGW WenKai TC`) |
| **`HK` (Hong Kong Chinese)** | 105 (`.0`..`.119`) | **85 slices** (`.0`..`.99`)<br>168-169 chars/slice (14,262 chars, incl. SIP)<br>45.7 KB/slice VF, 24.5 KB Static | **19 slices** (`.100`..`.118`): **213 chars/slice** + **1 top slice** (`.119`): **272 chars**<br>_(**100% identical to `TC`'s `.104`..`.123`**)_ | **138.6-150.4 KB**<br>(108.2-116.1 KB / 22.4 KB Brotli) | **280+ KB** |


---


## What Issues Were Found in Google Fonts' Korean (`KR`) Delivery?


### 1. Why Does `.119` Contain Only 13 Hangul Syllables While Scattering `ㅋㅋㅋ` and `《》` Across 14 Slices?

Unlike Japanese `.119` (which holds 170 Hiragana and Katakana characters), Korean `.119` (100 codepoints, 16.2 KB VF) contains **76 ASCII characters** (which are shadowed and overridden by the `[latin]` block at the bottom of the stylesheet), 11 Latin-1/general punctuation symbols, and **only 13 Hangul syllables** (`가고기다로리사스시이인지하`). Nineteen remaining ASCII characters (`9 I M N H | & + > @ ~ # % _ < = ^ $ \``) are scattered across `.118` down to `.109`.

Furthermore, characters used constantly in modern Korean web UI, comments, and prose are fragmented across the stylesheet:

* **Modern Hangul Compatibility Jamo (`U+3131-3163`, 51 characters such as `ㅋㅋㅋ`, `ㅎㅎ`, `ㅜㅜ`, `ㅠㅠ`) are scattered across 14 slices**: `ㅋ` is in `.114`, `ㅎ` is in `.113`, `ㅠ` is in `.112`, `ㅇ` is in `.111`, `ㄷㅅㅜ` are in `.110`, `ㄱㄴㄹㅁㅂㅈㅡㅣ` are in `.109`, `ㅊㅌ` are in `.108`, `ㅍ` is in `.107`, `ㅏ` is in `.105`, `ㅓ` is in `.102`, `ㅑㅗ` are in `.101`, `ㄲㅆㅉ` are in `.100`, `ㄳㄵ` are in `.95`, and the remaining 24 compatibility jamo (`ㅐㅒㅔㅕㅖㅘㅙㅚㅛㅝㅞㅟㅢ` etc.) are in `.94`. A short conversational string like `ㅋㅋㅋ ㅎㅎ ㅜㅜ` triggers three separate 100-character Hangul/Hanja slices (`.114`, `.113`, and `.110`).
* **Paired Korean book-title brackets `《》` (`U+300A-300B`) are split across two slices**: The opening double angle bracket `《` (`U+300A`) is placed in `.105` (24.7 KB VF), while the closing bracket `》` (`U+300B`) is placed in `.106` (23.8 KB VF). Rendering a single book or statute title `《…》` forces the browser to download two 100-character slices totaling 48.5 KB.
* **Conjoining Hangul Jamo (`U+1100-11FF`, `U+A960-A97F`, `U+D7B0-D7FF`) are 100% omitted (0 codepoints)**: Upstream `Noto Sans KR` and `Noto Serif KR` include all 256 conjoining jamo (`U+1100-11FF`) and Extended-A/B blocks required for Unicode NFD-normalized Korean text (such as macOS filenames) and Old Hangul (`ccmp` / `ljmo` / `vjmo` / `tjmo` shaping). However, Google Fonts' `KR` template omits every single conjoining jamo codepoint (0 codepoints emitted across all 124 slices), even though the `TC` template accidentally retains 28 of them in `.96` and `.97`.


### 2. Why Do Only 60.9% of KS X 1001 Common Hangul Syllables Fit in the Frequency Tiers?

Because Google Fonts allocates only 20 slices of 100 characters (`.100` to `.119`, 2,000 codepoints total) to its Korean frequency tier - and spends 561 of those 2,000 slots on 215 Hanja, 93 Japanese Kana, 95 ASCII characters, and symbols - only **1,439 Hangul syllables** are included in `.100` to `.119`.

The Korean industrial standard **KS X 1001** defines **2,350 common precomposed Hangul syllables** (out of 11,172 total Unicode Hangul syllables in `U+AC00-D7A3`). Google Fonts' frequency tier covers only **60.9% (1,431 of 2,350)** of KS X 1001 Hangul syllables, relegating the remaining **919 KS X 1001 syllables** to the 64 codepoint-ordered rare slices (`.2` to `.65`, 155 characters/slice).


### 3. Why Do Hangul-Only and KS X 1001 Display Fonts Emit 85% to 98% "Ghost `unicode-range`s"?

The 919 KS X 1001 Hangul syllables excluded from `.100` to `.119` are distributed across every single slice from `.2` to `.65` (typically 6 to 27 KS X 1001 syllables per 155-character slice). In addition, `.100` to `.112` mix 215 Hanja and 93 Japanese Kana directly into the Hangul frequency slices, and `.65` mixes 19 Hangul syllables with 136 Hanja.

When Google Fonts intersects this template with Hangul-only fonts (`IBM Plex Sans KR`, `Nanum Gothic`, `Hahmlet`, `Gowun Dodum`) or 2,350-syllable KS X 1001 display fonts (`Do Hyeon`, `Black Han Sans`, `Bagel Fat One`), severe ghost `unicode-range` declarations occur across the stylesheet:

* **`IBM Plex Sans KR` `.95.woff2` (1.1 KB) and `Nanum Gothic` `.95.woff2` (1.9 KB)**: CSS declares 155 codepoints (including 67 Japanese Kana), but the `.woff2` `cmap` contains only 11 and 15 punctuation/jamo characters (**92.9% and 90.3% absent**).
* **`IBM Plex Sans KR` `.65.woff2` (2.2 KB) and `Nanum Gothic` `.65.woff2` (3.4 KB)**: CSS declares 155 codepoints (136 Hanja + 19 Hangul), but `cmap` contains only the 19 Hangul syllables (`갂갃갅갆갉갊갋갌...`; **87.7% absent**).
* **`IBM Plex Sans KR` `.100.woff2` (4.7 KB) and `.101.woff2` (4.5 KB)**: CSS declares 100 codepoints each, but because these fonts lack Hanja and Japanese Kana, 53% and 56% of the advertised codepoints are absent from the binary.
* **`Do Hyeon`, `Black Han Sans`, and `Bagel Fat One` across all 63 rare Hangul slices (`.2` to `.64`)**: Because these KS X 1001 fonts contain 6 to 27 syllables in every one of `.2` to `.64`, **none of the 63 rare Hangul slices are omitted**. Each slice advertises all 154 or 155 Hangul syllables in CSS while containing only a handful of glyphs in `cmap` - for example, `Do Hyeon` `.5.woff2` (1.5 KB, 8 of 154 present, **94.8% absent**), `Do Hyeon` `.65.woff2` (0.9 KB, 4 of 155 present, **97.4% absent**), `Black Han Sans` `.95.woff2` (0.8 KB, 2 of 155 present, **98.7% absent**), and `Bagel Fat One` `.98.woff2` (1.6 KB, 5 of 155 present, **96.8% absent**).


---


## What Issues Were Found in Google Fonts' Chinese (`SC` / `TC` / `HK`) Delivery?


### 1. Why Do Short Chinese Headings and Intros Download 250 KB to 760 KB of WOFF2 Slices?

Whereas Japanese text uses Hiragana and Katakana for 60% to 70% of character tokens, Chinese text is written almost entirely in Hanzi (logographs). Despite this, Google Fonts' `SC`, `TC`, and `HK` templates allocate **only 20 frequency slices**, packing **188 codepoints/slice in `SC`** (`.100` to `.118`, plus 276 codepoints in `.119`) and **213 codepoints/slice in `TC` and `HK`** (`.104` to `.122` in `TC` / `.100` to `.118` in `HK`, plus 272 codepoints in `.123` / `.119`).

This is the exact opposite extreme of Google Fonts' 50-character Japanese slices and exhibits the collateral download pathology of coarse fixed granularity (Model B in [Architecture & Rationale](architecture-and-rationale.md#3-inverted-pyramid-kanji-granularity-tier-3)):

* In `Noto Sans SC` VF, each 188-character frequency slice averages **57.6 KB** (`.119` is **74.4 KB**; Static 400 averages 30.7 KB).
* In `Noto Sans TC` and `Noto Sans HK` VF, each 213-character frequency slice averages **68.8 KB** (`.118` in `TC` / `.114` in `HK` reaches **82.9 KB**; Static 400 averages 36.6 KB).
* Rendering a 21-character Simplified Chinese greeting (`你好，世界！「欢迎来到这里」--请稍候……`) hits 8 slices and downloads **416.9 KB** in `Noto Sans SC` VF (223.0 KB in Static 400).
* Rendering a 170-character blog intro (~120 unique characters) hits 14 slices (**747.5 KB**) in `Noto Sans SC` VF and 12 slices (**761.9 KB**) in `Noto Sans TC` VF - nearly double Japanese's 388.5 KB and approaching the bandwidth of a full 4,500-character Japanese article.


### 2. How Are Chinese Punctuation, Paired Brackets (`〈〉`, `『』`), and Bopomofo (`ㄅㄆㄇㄈ`) Fragmented?

Neither `SC` nor `TC`/`HK` defines a dedicated CJK punctuation or phonetic core slice:

* **Chinese punctuation is scattered across 10 Hanzi slices, splitting paired brackets (`〈〉` and `『』`) across different files**:
  * In **`SC`**, `、。，．：·` and `U+2014` are in `.119`, `！（）` are in `.118`, `？【】` are in `.117`, `　《》` are in `.116`, `；` is in `.115`, `…` (`U+2026`) is in `.114`, `「」` are in `.110`, `『』` are in `.106`, and **the single angle brackets `〈` (`U+3008`) and `〉` (`U+3009`) are split between `.100` (62.8 KB) and `.101` (56.6 KB)** - so a single `〈…〉` citation downloads two 188-character slices totaling **119.4 KB**. Furthermore, Mainland China's primary quotation marks (`U+201C-201D` and `U+2018-2019`) are overridden by the `[latin]` block at the bottom of the stylesheet, separating them from adjacent CJK commas and periods in `.119` and `.118` and breaking cross-slice OpenType contextual halfwidth spacing (`GPOS` `chws`, `vchw`, `halt`).
  * In **`TC` and `HK`**, `、。，：` are in `.123` (`.119` in `HK`), `？！「」（）` are in `.122` (`.118`), `　《》【】` are in `.121` (`.117`), `；…` are in `.119` (`.115`), `·` is in `.118` (`.114`), and **the standard Traditional Chinese secondary quotation marks `『` (`U+300E`) and `』` (`U+300F`) are split between `.117` (`.113` in `HK`, 69.5 KB) and `.116` (`.112` in `HK`, 70.1 KB)** - forcing a **139.6 KB** download of 426 unrelated Hanzi whenever a nested quote `『…』` appears, while pruning `chws`/`vchw` contextual spacing between `「` and `『`.
* **Standard Bopomofo (`ㄅㄆㄇㄈ`, `U+3105-3129`) and tone marks (`ˊ ˇ ˋ ˙`) are split across 8 slices, breaking `GPOS` `mark` positioning**:
  * In **`TC` and `HK`**, the 37 standard Zhuyin (Bopomofo) letters are divided across 4 slices: 19 finals/medials (`ㄓㄔㄘㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦㄧㄨㄩ`) in `.123` (`HK` `.119`), `ㄉ` alone in `.107` (`HK` `.103`), `ㄇㄌ` in `.104` (`HK` `.100`), and **15 basic initials (`ㄅㄆㄈㄊㄋㄍㄎㄏㄐㄑㄒㄕㄖㄗㄙ`) in the rare slice `.89` (`HK` `.88`)** (note that in upstream `googlefonts/nam-files`, `slices/traditional-chinese_default.txt` `.119` replaced those 19 Bopomofo finals with 18 high-frequency Hanzi `來們天好平年成所方明更治相美能自要首`, whereas Google Fonts' CSS2 API for `Noto Sans TC` serves `hongkong-chinese_default.txt`'s `.119` as `.123`).
  * Simultaneously, the 2nd, 3rd, and 4th tone marks (`ˊ` `U+02CA`, `ˇ` `U+02C7`, `ˋ` `U+02CB`) are captured by the `[latin-ext]` subset at the bottom of the CSS, while the neutral tone mark (`˙` `U+02D9`) stays in `.105` (`HK` `.101`). Because Bopomofo base letters and their tone marks never reside in the same `.woff2` binary, `fontTools.subset` prunes all `GPOS` `mark`/`mkmk` tone-mark attachment lookups.
  * In **`SC`**, 1 Bopomofo letter (`ㄧ`) is in `.119`, 35 are in `.86`, and 1 (`ㄩ`) is in `.85`, while all tone marks are in `.97` / `[latin-ext]`.
* **Foreign scripts, Emoji, Japanese Kana, Simplified-in-Traditional cross-script noise, and Private Use Area (PUA) codepoints pollute the top 20 Chinese frequency slices**:
  * **`SC` (`.100` to `.119`)** embeds 136 Japanese Kana (135 in `.119`), 21 PUA codepoints (`U+E606`..`U+F012B`), 7 Arabic letters (`ابةعلي` in `.102`), 6 Devanagari characters (`दनहिी्` in `.101`), 3 Thai letters (`ทยไ` in `.101`), and 3 Hangul syllables (`국어한` in `.105`).
  * **`TC` (`.104` to `.123`) and `HK` (`.100` to `.119`)** embed 214 Simplified-only Hanzi (including `GSUB` `dlig`-pulled `点` `U+70B9` in `.123`/`.119` while Traditional `點` `U+9EDE` is relegated to `.122`/`.118`, plus `会`, `简`, `欢`, `国`, `时`, `来`), 146 Japanese Kana (142 in `.118`/`.114`), 15 PUA codepoints, 11 color Emoji (`😊😘😭🎉👉💕` in `.104`/`.105`), 10 Arabic letters, 6 Devanagari characters, 6 Thai letters, 5 Hebrew letters (`ביערת` in `.106`), and 5 Hangul syllables.


### 3. Why Do GB2312 Static Fonts Emit 95%+ Ghost Slices and Why Does `Chiron Hei HK` Override All 113 Slices?

* **Ghost `unicode-range`s in GB2312 (~6,763-character) Simplified Chinese fonts**:
  * Google Fonts' `SC` frequency tier (`.100` to `.119`) contains 3,443 Hanzi, covering 84.0% (3,153 of 3,755) of GB2312 Level 1 Hanzi and only 6.8% (204 of 3,008) of GB2312 Level 2 Hanzi. The remaining **3,373 GB2312 Hanzi** are scattered across all 65 rare Hanzi slices (`.21` to `.85`).
  * Consequently, GB2312 display and handwriting fonts (`ZCOOL XiaoWei`, `ZCOOL KuaiLe`, `ZCOOL QingKe HuangYou`, `Ma Shan Zheng`, `Long Cang`, `Liu Jian Mao Cao`, `Zhi Mang Xing`) still emit **91 to 92 numbered slices** and **104.1 to 107.6 KB of CSS** (nearly identical to 20,000-character `Noto Sans SC`'s 110.7 KB).
  * Inspecting the `.woff2` `cmap` tables of `ZCOOL XiaoWei`, `Ma Shan Zheng`, and `Long Cang` shows extreme ghost `unicode-range` rates across rare and symbol slices: `.90.woff2` (0.7 to 1.4 KB) declares 139 codepoints in CSS while containing only 2 characters (`€` and `−`; **98.6% absent**); `.30.woff2` (3.2 to 4.8 KB) declares 138 Hanzi while containing only 5 (`錾鍪鎏鏊鏖`; **96.4% absent**); `.6.woff2` (0.8 to 1.5 KB) declares 138 codepoints while containing 7 (**94.9% absent**); `.21.woff2` declares 138 codepoints while containing 11 (**92.0% absent**); and even `.119.woff2` (the top core slice!) declares 276 codepoints while missing 178 of them (**64.5% absent**, because all 135 Japanese Kana and Latin-Ext characters declared in `.119` are absent from GB2312 fonts).
  * In `TC`, `LXGW WenKai TC` `.16.woff2` (1.1 KB) declares 134 codepoints while containing only 1 PUA glyph (`U+E003`; **99.3% absent**), and `Chocolate Classical Sans` `.5.woff2` (1.2 KB) and `.6.woff2` (1.1 KB) declare 134 codepoints each while containing only a single emoji glyph (`🤖` and `🔒`; **99.3% absent**).
* **Monolithic `[chinese-traditional]` cascade override in `Chiron Hei HK` (`昭源黑體`)**:
  * In `Chiron Hei HK` (both `wght@400` and `wght@250..900`), Google Fonts emits 113 numbered slice rules (`.0` to `.119`, 150.4 to 155.8 KB of CSS) and then emits a 114th rule labeled `/* chinese-traditional */` with `unicode-range: U+3000-303F, U+3105-312F, U+4E00-9FFF;` (21,099 codepoints).
  * Because `/* chinese-traditional */` is declared **after** `.0` to `.119`, CSS cascade precedence causes it to **shadow and override every BMP Hanzi (`U+3000-303F`, `U+4E00-9FFF`) and Bopomofo character (`U+3105-312F`) across all 113 numbered slices**, forcing the browser to download the monolithic 6,391-glyph font binary (4.56 MB when `.ttf` is served on `wght@250..900`) as soon as a single Chinese character is rendered.


---


## How Does `cleome`'s 4-Tier Architecture Perform on Korean and Chinese?

Applying `cleome`'s three core mechanisms - **(1) Tier 4 CSS Cascade Shadowing with 1-token hex ranges**, **(2) Tier 1 & Tier 2 semantic non-ideograph partitioning and `cmap`-driven compaction**, and **(3) Tier 3 Inverted-Pyramid progressive granularity** - resolves these issues across Korean, Simplified Chinese, and Traditional/Hong Kong Chinese.


### 1. Korean (`KR`) 4-Tier Architecture & Simulation Comparison

* **Tier 1 (`latin` & `hangul-core`, declared last / highest priority)**: `hangul-core` co-locates all CJK Symbols and Punctuation (`U+3000-303F`, keeping `《》`, `「」`, `『』`, `【】` together), all Hangul Compatibility Jamo (`U+3130-318F`, keeping `ㅋㅋㅋ`, `ㅎㅎ`, `ㅜㅜ`, `ㅠㅠ` in a single slice), all Conjoining Hangul Jamo (`U+1100-11FF`, `U+A960-A97F`, `U+D7B0-D7FF`, preserving NFD/Old Hangul `ccmp`/`ljmo`/`vjmo`/`tjmo` composition), and Fullwidth ASCII/Symbols (`U+FF01-FF60`, `U+FFA0-FFDC`, `U+FFE0-FFE6`).
* **Tier 2 (`symbols-ui` & `symbols-ext`)**: Separates common UI and article symbols (`℃`, `Ⅰ-Ⅻ`, `←↑→↓`, `▶`, `∞≒≠≦≧`, `①-⑳`, `★`, `✓`) from extended symbols and non-Korean scripts.
* **Tier 3 (`hangul-freq-01..29`, Inverted-Pyramid Hangul Syllables)**: Covers **100% of all 2,350 KS X 1001 common Hangul syllables** (2,358 syllables total) using `SliceConfig`'s unified progressive chunk sizes:
  * **Rank 1 to 400 (`hangul-freq-01..02`, 200 chars x 2 slices, ~28 KB/slice VF)**: Covers ~90% of everyday Korean text in 2 slices.
  * **Rank 401 to 1,200 (`hangul-freq-03..12`, 80 chars x 10 slices, ~13 KB/slice VF)**: Medium granularity for moderately frequent syllables.
  * **Rank 1,201 to 2,358 (`hangul-freq-13..29`, ~68 chars x 17 slices, ~12 KB/slice VF)**: Fine granularity for lower-frequency KS X 1001 syllables so sparse hits in long articles never pull in 155-character rare slices.
* **Tier 4 (`hangul-rare-01..35` & `hanja-rare-01..20`, CSS Cascade Shadowing, declared first / lowest priority)**: Partitions the remaining 8,814 non-KS X 1001 modern Hangul syllables (`U+AC00-D7A3`) and 4,888 Hanja (`U+3400-4DBF`, `U+4E00-9FFF`, `U+F900-FAFF`) into ~250-character slices, each advertising a single contiguous hex token (`U+XXXX-YYYY`).
  * **CSS reduction**: Tier 4 `unicode-range` text drops from **43.4 KB across 94 slices** in Google Fonts `KR` to **0.7 KB across 55 slices (a 98.4% reduction)**.
  * **Automatic scaling for Hangul-only and KS X 1001 fonts**: Because slices are built strictly from the input font's `cmap`, a Hangul-only font (`IBM Plex Sans KR`) omits all 20 Hanja slices (scaling to 71 slices / ~35 KB CSS), and a 2,350-syllable KS X 1001 display font (`Do Hyeon`, `Black Han Sans`, `Bagel Fat One`) omits both Hanja and `hangul-rare` (scaling to **36 slices / ~27 KB CSS** with **zero ghost `unicode-range`s**).


| Metric / Scenario | Google Fonts `KR` Current<br>_(124 slices, 100-char fixed)_ | `cleome` 4-Tier Model for `KR`<br>_(91 slices, Inverted-Pyramid + cascade shadowing)_ |
| :--- | :--- | :--- |
| **Total slices (`@font-face` rules)** | 124 _(92-99 for Hangul-only; 88-93 for KS X 1001)_ | **91** _(scales down to 71 for Hangul-only and **36** for KS X 1001 fonts)_ |
| **Raw CSS size (`unicode-range` text)** | 93.4 KB (60.4 KB) | **~39.5 KB (20.4 KB; -57.7% raw CSS reduction)** |
| **Scenario 1: Jamo & punctuation**<br>(`ㅋㅋㅋ ㅎㅎ! 「안녕하세요?」 《한글》 ㅜㅜ`) | 10 files / 184.7 KB | **4 files / 95.6 KB (-48.2% transfer)**<br>_(or 2 files / 38.6 KB for pure Jamo & punctuation)_ |
| **Scenario 2: Short UI label**<br>(`로그인 · 회원가입 (2026년 10월 19일)`) | 4 files / 69.6 KB | **2 files / 55.2 KB**<br>(`latin` + `hangul-freq-01`) |
| **Scenario 3: Blog intro**<br>(~220 chars / 100 unique) | 11 files / 184.3 KB | **7 files / 161.6 KB** |
| **Scenario 4: Full technical article**<br>(~4,600 chars / 421 unique) | 18 files / 329.2 KB | **12 files / 232.4 KB**<br>_(6 fewer requests & -29.4% smaller transfer)_ |


---


### 2. Simplified Chinese (`SC`) & Traditional / Hong Kong Chinese (`TC` / `HK`) 4-Tier Architecture & Simulation Comparison

* **Tier 1 (`latin` & `cjk-core`, declared last / highest priority)**: `cjk-core` co-locates all CJK Symbols and Punctuation (`U+3000-303F`, keeping `〈〉`, `『』`, `「」`, `《》`, `【】` together so `GPOS` `chws`/`vchw` contextual spacing is preserved), Fullwidth ASCII/Symbols (`U+FF01-FF60`, `U+FFE0-FFE6`), and **all Bopomofo letters (`U+3100-312F`, `U+31A0-31BF`) together with their tone marks (`U+02C7`, `U+02C9-02CB`, `U+02D9`)** so `GPOS` `mark`/`mkmk` Zhuyin tone positioning survives subsetting in a single slice. `latin` includes Pe̍h-ōe-jī superscripts (`U+1D3A` `ᴺ`, `U+207F` `ⁿ`) alongside Latin Extended-A/B/Additional and Combining Diacritical Marks.
* **Tier 2 (`symbols-ui` & `symbols-ext`)**: Separates high-frequency UI and article symbols (`℃`, `Ⅰ-Ⅻ`, `←↑→↓`, `∞≒≠≦≧`, `①-⑳`, `■□▲△◆◇◈★☆✓`) from extended non-Hanzi glyphs (preventing foreign scripts or PUA glyphs from polluting Hanzi frequency slices).
* **Tier 3 (`hanzi-freq-01..NN`, Inverted-Pyramid Hanzi)**: Replaces Google Fonts' coarse 188-character (`SC`) and 213-character (`TC`/`HK`) chunks with progressive granularity across 4,045 (`SC`), 3,739 (`TC`), and 3,751 (`HK`) frequent Hanzi (covering 100% of GB2312 Level 1's 3,755 Hanzi in `SC`). In `TC` and `HK`, Traditional `點` (`U+9EDE`) is promoted to `hanzi-freq-01` (rank 41) in place of `GSUB` `dlig`-pulled Simplified `点` (`U+70B9`), and all 214 Simplified-only cross-script characters (`点`, `会`, `简`, `欢`, `国`, `时`, `来`, etc.) are demoted to the tail of Tier 3 (`hanzi-freq-45..48`) so they never displace Traditional Hanzi in top slices while still avoiding 250-char Tier 4 downloads on mixed-script pages. In `HK`, the 37 tail Cantonese colloquial & HKSCS characters (`嘅唔喺咗啲冇佢咁睇嘢嚟仲哋嗰畀揾諗攞揀傾呀喎咩咋嘛喇𨋢...`) are co-located in `hanzi-freq-13` (ranks 1,201..1,237) while keeping the top 1,200 written Traditional Chinese slices (`hanzi-freq-01..12`) 100% aligned with `TC`:
  * **Rank 1 to 400 (`hanzi-freq-01..02`, 200 chars x 2 slices, ~60 to 64 KB/slice VF)**: Ultra-high-frequency Hanzi present in virtually every Chinese paragraph.
  * **Rank 401 to 1,200 (`hanzi-freq-03..12`, 80 chars x 10 slices, ~26 to 28 KB/slice VF)**: Medium granularity for common vocabulary, cutting per-slice collateral download in half compared to Google Fonts' 188/213-character slices.
  * **Rank 1,201 to 3,739/3,751/4,045 (`hanzi-freq-13..NN`, ~70 chars x 36 to 41 slices, ~24 to 25 KB/slice VF)**: Fine granularity for mid-to-low frequency common Hanzi (with `hanzi-freq-13` in `HK` co-locating core Cantonese colloquial characters), reducing collateral download by ~60% per hit when sparse characters appear in headings or articles.
* **Tier 4 (`hanzi-rare-01..NN`, CSS Cascade Shadowing, declared first / lowest priority)**: Partitions the remaining ~8,500 to 12,400 rare Hanzi strictly within each Unicode ideograph region (`Ext-A` `U+3400-4DBF`, `BMP` `U+4E00-9FFF`, `Compat` `U+F900-FAFF`, `SIP` `U+20000-2FA1F` including Hong Kong HKSCS Cantonese characters, and `TIP` `U+30000-3347F`) into ~250-character slices with 1-token hex ranges (`U+XXXX-YYYY`).
  * **CSS reduction**: Tier 4 `unicode-range` text shrinks from **54.7 KB (`SC`), 61.5 KB (`TC`), and 78.3 KB (`HK`) to 0.4-0.5 KB (a >99.2% reduction)**, cutting overall single-weight CSS size in half (from 110.7-150.4 KB to **54.6-57.1 KB raw / ~7 KB Brotli**).
  * **Automatic scaling for GB2312 static fonts**: A 6,763-character GB2312 font (`ZCOOL XiaoWei`, `Ma Shan Zheng`, `Long Cang`) scales down to **66 slices (~48 KB CSS)** with **zero ghost `unicode-range`s**.

| Metric / Scenario (`SC` - `Noto Sans SC` VF) | Google Fonts `SC` Current<br>_(101 slices, 188-char fixed)_ | `cleome` 4-Tier Model for `SC`<br>_(96 slices, Inverted-Pyramid + cascade shadowing)_ |
| :--- | :--- | :--- |
| **Total slices (`@font-face` rules)** | 101 _(92-93 for ~6,763-char GB2312 static fonts)_ | **96** _(scales down to **66** for GB2312 static fonts)_ |
| **Raw CSS size (`unicode-range` text)** | 110.7 KB (82.3 KB; 54.7 KB in Tier 4 alone) | **~56.3 KB (36.2 KB; 0.4 KB in Tier 4, -49.1% reduction)** |
| **Scenario 1: Greeting & punctuation**<br>(`你好，世界！「欢迎来到这里」--请稍候……`) | 8 files / 416.9 KB | **7 files / 242.7 KB (-41.8% / -174.2 KB)** |
| **Scenario 2: Short UI label**<br>(`登录 · 注册新账号 (2026年10月19日)`) | 5 files / 249.5 KB | **4 files / 172.4 KB (-30.9% transfer)** |
| **Scenario 3: Blog intro**<br>(~170 chars / 118 unique) | 14 files / 747.5 KB | **20 files / 575.7 KB (-23.0% / -171.8 KB)** |
| **Scenario 4: Full technical article**<br>(~4,500 chars / 563 unique) | 22 files / 1,228.5 KB | **41 files / 1,123.7 KB (-104.8 KB transfer)** |

| Metric / Scenario (`TC` / `HK` - `Noto Sans TC` VF) | Google Fonts `TC` / `HK` Current<br>_(108 / 109 slices, 213-char fixed)_ | `cleome` 4-Tier Model for `TC` / `HK`<br>_(96 / 108 slices, Inverted-Pyramid + cascade shadowing)_ |
| :--- | :--- | :--- |
| **Total slices (`@font-face` rules)** | 108 (`TC`) / 109 (`HK`) | **96 (`TC`) / 108 (`HK`)** |
| **Raw CSS size (`unicode-range` text)** | 122.4 KB (`TC`, 91.9 KB) / 138.6 KB (`HK`, 108.2 KB) | **~54.6 KB (`TC`, -55.4%) / ~57.1 KB (`HK`, -58.8%)** |
| **Scenario 1: Greeting & Bopomofo**<br>(`你好，世界！「注音符號：ㄅㄆㄇㄈ、ㄓㄔㄕㄖ」`) | 7 files / 394.4 KB | **6 files / 234.5 KB (-40.5% / -159.9 KB)**<br>_(pure Bopomofo & punctuation requires **1 file / 18.2 KB** vs. 7 files / 382 KB)_ |
| **Scenario 2: Short UI label**<br>(`登入 · 註冊新帳號 (2026年10月19日)`) | 5 files / 251.9 KB | **5 files / 210.3 KB (-16.5% transfer)** |
| **Scenario 3: Blog intro**<br>(~170 chars / 124 unique) | 12 files / 761.9 KB | **17 files / 530.7 KB (-30.3% / -231.2 KB)** |
| **Scenario 4: Full technical article**<br>(~4,500 chars / 554 unique) | 27 files / 1,539.5 KB | **44 files / 1,407.4 KB (-132.1 KB transfer)** |
