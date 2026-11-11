# WOFF2 subsetting, OpenType shaping, and slicing tiers

Editing rules for [`cleome/_plan/urange.py`](../../src/cleome/_plan/urange.py), [`cleome/_plan/config.py`](../../src/cleome/_plan/config.py), [`cleome/_plan/builder.py`](../../src/cleome/_plan/builder.py), [`cleome/_plan/bundle.py`](../../src/cleome/_plan/bundle.py), [`cleome/_plan/locale.py`](../../src/cleome/_plan/locale.py), [`cleome/_font/subset.py`](../../src/cleome/_font/subset.py), and [`cleome/_font/uvs.py`](../../src/cleome/_font/uvs.py). See [`docs/architecture-and-rationale.md`](../../docs/architecture-and-rationale.md) for background rationale and [`skills/cleome/SKILL.md`](../../skills/cleome/SKILL.md) for the public API contract.

## CSS cascade shadowing and range compaction

* Order `@font-face` declarations from lowest priority (`Tier 4` rare ideographs and syllables) to highest priority (`Tier 1` core phonetic blocks and Latin).
* Delegate `<urange>` formatting, parsing, and gap compaction to `stixis` through `cleome/_plan/urange.py`, and bridge gaps in an earlier tier only across unmapped codepoints or codepoints shadowed by a higher-priority tier declared later.
* Never bridge `unicode-range` tokens across different Unicode planes (such as Plane 2 SIP and Plane 3 TIP).
* In `SliceConfig.__post_init__` (`cleome/_plan/config.py`), store the lexically normalized relative `slices_subdir` (`Path(subdir_norm).as_posix()`) only after rejecting prohibited path forms, and reject `bool` or non-`int` chunk sizes (`freq_chunks`, `freq_tail_chunk_size`, `rare_chunk_size`, `symbols_ext_chunk_size`) and `workers` (`_validate_workers`) with `TypeError` and out-of-range integers with `ValueError`.

## Intra-slice OpenType shaping

* Keep base characters and their combining marks or paired brackets in the same tier and slice so `fontTools.subset` retains multi-glyph `GSUB` and `GPOS` lookups (`ccmp`, `mark`, `mkmk`, `liga`, `kern`, `chws`, `vchw`, `ljmo`, `vjmo`, `tjmo`).
* Verify changes to character partitioning or subsetting options with HarfBuzz (`uharfbuzz`) shaping assertions before and after slicing.

## UVS and IVS (`cmap` Format 14) handling

* Filter `cmap` Format 14 mappings via `_prepare_uvs_for_slice` (`cleome/_font/subset.py`) to retain default and non-default UVS/IVS records for base characters in the slice without leaving standalone variation selector codepoints in the Format 4 or Format 12 `cmap`.
* Keep Format 14 sequence retention separate from `patch_uvs_missing_base_cmap` (`cleome/_font/uvs.py`).
