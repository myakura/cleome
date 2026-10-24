---
type: Reference
title: WOFF2 Subsetting, OpenType Shaping & Slicing Tiers
description: Domain rules for 4-tier CSS cascade shadowing, intra-slice OpenType shaping, and cmap Format 14 UVS/IVS subsetting.
status: stable
updated: 2026-10-25
stale_after: 2027-10-25T00:00:00+09:00
---

# WOFF2 Subsetting, OpenType Shaping & Slicing Tiers

Domain rules for `unicode-range` formatting and compaction ([`cleome/urange.py`](../../src/cleome/urange.py)), the 4-tier slicing planner ([`cleome/slicer.py`](../../src/cleome/slicer.py)), and the WOFF2 subsetting pipeline ([`cleome/subsetter.py`](../../src/cleome/subsetter.py)).


## CSS Cascade Shadowing & Range Compaction

* `@font-face` declarations are ordered from lowest priority (Tier 4 rare Kanji, using 1-token hex ranges) to highest priority (Tier 1 Kana and Latin).
* When compacting `unicode-range` intervals in an earlier tier via `compact_unicode_range` (`cleome/urange.py`), only bridge across unmapped gaps or codepoints that are guaranteed to be shadowed by a higher-priority tier declared later in the stylesheet.
* Never bridge `unicode-range` tokens across different Unicode planes (such as Plane 2 SIP and Plane 3 TIP).


## Intra-Slice OpenType Shaping

* `fontTools.subset` prunes multi-glyph `GSUB`/`GPOS` lookups (`ccmp`, `mark`, `mkmk`, `liga`, `kern`) whenever any participating glyph is absent from the slice.
* Keep base characters and their combining marks or paired brackets in the same tier and slice (Latin combining marks with `latin`, voiced Kana marks with `kana-core`, Conjoining and Compatibility Hangul Jamo with `hangul-core`, and Bopomofo letters + tone marks `U+02C7`, `U+02C9-02CB`, `U+02D9` with `cjk-core`) so composition and contextual positioning lookups survive subsetting.
* Verify changes to character partitioning or subsetting options with HarfBuzz (`uharfbuzz`) shaping assertions before and after slicing.


## UVS / IVS (`cmap` Format 14) Handling

* Retain `cmap` Format 14 variation selector mappings (both default and non-default UVS/IVS) and their variant glyphs for all base characters included in a slice (`_prepare_uvs_for_slice` in `cleome/subsetter.py`), without leaking standalone variation selector codepoints into the slice's base Format 4/12 `cmap`.
* Keep Format 14 sequence retention separate from `patch_uvs_missing_base_cmap` (`cleome/subsetter.py`), which optionally recovers characters that upstream fonts mapped in Format 14 but omitted from Format 4/12.
