# WOFF2 subsetting, OpenType shaping, and slicing tiers

Editing rules for [`cleome/urange.py`](../../src/cleome/urange.py), [`cleome/slicer.py`](../../src/cleome/slicer.py), [`cleome/locale.py`](../../src/cleome/locale.py), and [`cleome/subsetter.py`](../../src/cleome/subsetter.py). See [`docs/architecture-and-rationale.md`](../../docs/architecture-and-rationale.md) for background rationale and [`skills/cleome/SKILL.md`](../../skills/cleome/SKILL.md) for the public API contract.

## CSS cascade shadowing and range compaction

* Order `@font-face` declarations from lowest priority (`Tier 4` rare ideographs and syllables) to highest priority (`Tier 1` core phonetic blocks and Latin).
* Delegate `<urange>` formatting, parsing, and gap compaction to `stixis` through `cleome/urange.py`, and bridge gaps in an earlier tier only across unmapped codepoints or codepoints shadowed by a higher-priority tier declared later.
* Never bridge `unicode-range` tokens across different Unicode planes (such as Plane 2 SIP and Plane 3 TIP).

## Intra-slice OpenType shaping

* Keep base characters and their combining marks or paired brackets in the same tier and slice so `fontTools.subset` retains multi-glyph `GSUB` and `GPOS` lookups (`ccmp`, `mark`, `mkmk`, `liga`, `kern`, `chws`, `vchw`, `ljmo`, `vjmo`, `tjmo`).
* Verify changes to character partitioning or subsetting options with HarfBuzz (`uharfbuzz`) shaping assertions before and after slicing.

## UVS and IVS (`cmap` Format 14) handling

* Filter `cmap` Format 14 mappings via `_prepare_uvs_for_slice` (`cleome/subsetter.py`) to retain default and non-default UVS/IVS records for base characters in the slice without leaving standalone variation selector codepoints in the Format 4 or Format 12 `cmap`.
* Keep Format 14 sequence retention separate from `patch_uvs_missing_base_cmap` (`cleome/metadata.py`).
