# WOFF2 subsetting, OpenType shaping, and slicing tiers

Domain rules for `unicode-range` formatting and compaction ([`cleome/urange.py`](../../src/cleome/urange.py)), the 4-tier slicing planner ([`cleome/slicer.py`](../../src/cleome/slicer.py)), and the WOFF2 subsetting pipeline ([`cleome/subsetter.py`](../../src/cleome/subsetter.py)).

## CSS cascade shadowing and range compaction

* Cleome orders `@font-face` declarations from lowest priority (Tier 4 rare ideographs and syllables, using 1-token hex ranges) to highest priority (Tier 1 core phonetic blocks and Latin).
* When compacting `unicode-range` intervals in an earlier tier with `compact_unicode_range` (`cleome/urange.py`), bridge only across unmapped gaps or codepoints shadowed by a higher-priority tier declared later in the stylesheet.
* Never bridge `unicode-range` tokens across different Unicode planes (such as Plane 2 SIP and Plane 3 TIP).

## Intra-slice OpenType shaping

* `fontTools.subset` prunes multi-glyph `GSUB` and `GPOS` lookups (`ccmp`, `mark`, `mkmk`, `liga`, `kern`, `chws`, `vchw`, `ljmo`, `vjmo`, `tjmo`) whenever any participating glyph is absent from the slice.
* Keep base characters and their combining marks or paired brackets in the same tier and slice (Latin combining marks with `latin`, voiced Kana marks with `kana-core`, Conjoining and Compatibility Hangul Jamo with `hangul-core`, and Bopomofo letters plus tone marks `U+02C7`, `U+02C9-02CB`, `U+02D9` with `cjk-core`) so composition and contextual positioning lookups survive subsetting.
* Verify changes to character partitioning or subsetting options with HarfBuzz (`uharfbuzz`) shaping assertions before and after slicing.

## UVS and IVS (`cmap` Format 14) handling

* Retain `cmap` Format 14 variation selector mappings (both default and non-default UVS and IVS) and their variant glyphs for all base characters included in a slice (`_prepare_uvs_for_slice` in `cleome/subsetter.py`), without leaking standalone variation selector codepoints into the slice's base Format 4 or Format 12 `cmap`.
* Keep Format 14 sequence retention separate from `patch_uvs_missing_base_cmap` (`cleome/metadata.py`, re-exported by `cleome/subsetter.py`), which recovers characters that upstream fonts mapped in Format 14 but omitted from Format 4 and Format 12.
