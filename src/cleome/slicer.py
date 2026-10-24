"""
Core dynamic unicode-range slicing planner for Cleome.

Implements the 4-tier architecture:
- Tier 4 (kanji-rare-XX): Rare/Extended Kanji (JIS Level 2-4, Ext A-I, Compat) declared FIRST
  in CSS with 1-token contiguous hex `unicode-range` (CSS Cascade Shadowing).
- Tier 3 (kanji-freq-XX): High/Mid-frequency Kanji declared from lower-frequency to highest-frequency
  using Progressive (Inverted-Pyramid) Granularity.
- Tier 2 (symbols-ext / symbols-ui): Extended symbols & non-Kanji scripts followed by high-frequency
  Web UI symbols (`→`, `▶`, `①`, `✓`, `★`, `♪`).
- Tier 1 (kana-core / latin): Complete Hiragana, Katakana, CJK punctuation, Fullwidth ASCII (`kana-core`,
  zero omissions for `む`, `モ`, `ヶ`, `ヵ`), and core Western/Latin (`latin`) declared LAST (highest priority).
"""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from pathlib import Path

from cleome.data.kanji_freq import KANJI_FREQ_CODEPOINTS, KANJI_FREQ_RANK
from cleome.data.kr_freq import KR_HANGUL_FREQ_CODEPOINTS, KR_HANGUL_FREQ_RANK
from cleome.data.sc_freq import SC_HANZI_FREQ_CODEPOINTS, SC_HANZI_FREQ_RANK
from cleome.data.tc_freq import (
    HK_HANZI_FREQ_CODEPOINTS,
    HK_HANZI_FREQ_RANK,
    TC_HANZI_FREQ_CODEPOINTS,
    TC_HANZI_FREQ_RANK,
)
from cleome.locale import (
    CJK_IDEOGRAPH_REGIONS,
    SUPPORTED_LOCALES,
    detect_font_locale,
    is_cjk_ideograph,
    is_hangul_syllable,
    is_tier1_cjk_core,
    is_tier1_hangul_core,
    is_tier1_kana_core,
    is_tier1_latin,
    is_tier2_symbols_ui,
)
from cleome.urange import (
    CodepointSet,
    compact_unicode_range,
    format_unicode_range,
    parse_unicode_range,
)

_compact_range_with_shadowing = compact_unicode_range


@dataclass(frozen=True, slots=True)
class SliceConfig:
    """Configuration parameters for dynamic unicode-range slicing."""

    # Target CJK slicing profile: "auto" (detect from family name & cmap), "jp", "kr", "sc", "tc", or "hk".
    locale: str = "auto"

    # Progressive (Inverted-Pyramid) tiers for Tier 3 (frequency-ranked Kanji/Hanzi/Hangul):
    # List of (chunk_size, num_chunks) from highest frequency (rank 1) downward.
    # Any remaining frequency-ranked characters in the font's cmap after these tiers
    # are sliced using `freq_tail_chunk_size`.
    freq_tiers: tuple[tuple[int, int], ...] = (
        (200, 2),  # Rank 1..400: 200 chars x 2 slices
        (80, 10),  # Rank 401..1200: 80 chars x 10 slices
    )
    freq_tail_chunk_size: int = 70  # Rank 1201..N: ~70 chars/slice

    # Target character count per slice for Tier 4 (rare Kanji/Hanzi/Hangul) and Tier 2 (`symbols-ext`):
    rare_kanji_chunk_size: int = 250
    symbols_ext_chunk_size: int = 350

    # Subsetting & WOFF2 options:
    retain_layout_closure: bool = True
    layout_features: tuple[str, ...] = ("*",)
    drop_tables: tuple[str, ...] = ()
    patch_uvs_only_cmap: bool = True

    # CSS generation options:
    slices_subdir: str = "slices"
    url_prefix: str | None = None
    font_display: str = "swap"

    def __post_init__(self) -> None:
        norm_locale = self.locale.strip().lower()
        if norm_locale not in SUPPORTED_LOCALES:
            raise ValueError(
                f"Unsupported locale {self.locale!r}; expected one of {SUPPORTED_LOCALES}"
            )
        object.__setattr__(self, "locale", norm_locale)
        for chunk_size, num_chunks in self.freq_tiers:
            if chunk_size <= 0 or num_chunks <= 0:
                raise ValueError(
                    f"freq_tiers entries must have positive (chunk_size, num_chunks), got {(chunk_size, num_chunks)}"
                )
        if self.freq_tail_chunk_size <= 0:
            raise ValueError(f"freq_tail_chunk_size must be > 0, got {self.freq_tail_chunk_size}")
        if self.rare_kanji_chunk_size <= 0:
            raise ValueError(f"rare_kanji_chunk_size must be > 0, got {self.rare_kanji_chunk_size}")
        if self.symbols_ext_chunk_size <= 0:
            raise ValueError(f"symbols_ext_chunk_size must be > 0, got {self.symbols_ext_chunk_size}")
        subdir_str = self.slices_subdir.strip()
        if not subdir_str:
            raise ValueError("slices_subdir cannot be empty")
        subdir_norm = subdir_str.rstrip("/") or "."
        subdir_path = Path(subdir_norm)
        if (
            "\\" in subdir_str
            or "\x00" in subdir_str
            or subdir_str.startswith("/")
            or subdir_path.is_absolute()
            or ".." in subdir_path.parts
        ):
            raise ValueError(
                f"slices_subdir must be a relative path inside out_dir without '..', got {self.slices_subdir!r}"
            )
        display_str = self.font_display.strip()
        if not display_str:
            raise ValueError("font_display cannot be empty")
        if (
            any(ch in display_str for ch in (";", "{", "}", "\r", "\n", "\f", "'", '"', "\\"))
            or "/*" in display_str
            or "*/" in display_str
        ):
            raise ValueError(f"Invalid CSS font-display value: {self.font_display!r}")
        object.__setattr__(self, "font_display", display_str)


@dataclass(frozen=True, slots=True)
class SliceSpec:
    """Specification of a single unicode-range slice."""

    slice_id: str
    tier: int
    category: str
    description: str
    binary_codepoints: frozenset[int]
    css_unicode_range: str
    uses_cascade_shadowing: bool = False

    def filename(self, slug: str, variant_suffix: str = "") -> str:
        if variant_suffix:
            return f"{slug}-{variant_suffix}-{self.slice_id}.woff2"
        return f"{slug}-{self.slice_id}.woff2"


def _chunk_sorted_codepoints(
    sorted_cps: list[int], target_chunk_size: int
) -> list[list[int]]:
    """
    Split a sorted list of codepoints into balanced chunks of approximately `target_chunk_size`.
    Avoids leaving a tiny trailing remainder chunk by distributing evenly across `num_chunks`.
    """
    n = len(sorted_cps)
    if n == 0:
        return []
    if target_chunk_size <= 0 or n <= target_chunk_size:
        return [sorted_cps]
    num_chunks = max(1, round(n / target_chunk_size))
    chunks: list[list[int]] = []
    for i in range(num_chunks):
        start_idx = (i * n) // num_chunks
        end_idx = ((i + 1) * n) // num_chunks
        if end_idx > start_idx:
            chunks.append(sorted_cps[start_idx:end_idx])
    return chunks


def _build_progressive_freq_chunks(
    available_freq_cps: list[int],
    config: SliceConfig,
) -> list[tuple[int, int, frozenset[int]]]:
    """Slice frequency-ranked codepoints using `config.freq_tiers` and `config.freq_tail_chunk_size`."""
    freq_chunks: list[tuple[int, int, frozenset[int]]] = []
    cursor = 0
    for chunk_size, num_chunks in config.freq_tiers:
        for _ in range(num_chunks):
            if cursor >= len(available_freq_cps):
                break
            sub = available_freq_cps[cursor : cursor + chunk_size]
            if sub:
                freq_chunks.append((cursor + 1, cursor + len(sub), frozenset(sub)))
                cursor += len(sub)

    if cursor < len(available_freq_cps):
        tail_cps = available_freq_cps[cursor:]
        tail_lists = _chunk_sorted_codepoints(tail_cps, config.freq_tail_chunk_size)
        for sub in tail_lists:
            freq_chunks.append((cursor + 1, cursor + len(sub), frozenset(sub)))
            cursor += len(sub)
    return freq_chunks


def _build_rare_ideograph_slices(
    rare_ideographs_sorted: list[int],
    config: SliceConfig,
    *,
    category: str,
    label: str,
) -> list[SliceSpec]:
    """Partition rare CJK ideographs strictly within `CJK_IDEOGRAPH_REGIONS` using 1-token hex ranges."""
    rare_chunk_tuples: list[tuple[str, list[int], int, int]] = []
    for reg_name, reg_lo, reg_hi in CJK_IDEOGRAPH_REGIONS:
        lo_idx = bisect_left(rare_ideographs_sorted, reg_lo)
        hi_idx = bisect_right(rare_ideographs_sorted, reg_hi)
        reg_cps = rare_ideographs_sorted[lo_idx:hi_idx]
        if not reg_cps:
            continue
        reg_chunks = _chunk_sorted_codepoints(reg_cps, config.rare_kanji_chunk_size)
        for ch_cps in reg_chunks:
            rare_chunk_tuples.append((reg_name, ch_cps, ch_cps[0], ch_cps[-1]))

    slices: list[SliceSpec] = []
    total_rare = len(rare_chunk_tuples)
    for i, (reg_name, ch_cps, s_hex, e_hex) in enumerate(rare_chunk_tuples, start=1):
        ur_token = f"U+{s_hex:X}" if s_hex == e_hex else f"U+{s_hex:X}-{e_hex:X}"
        slices.append(
            SliceSpec(
                slice_id=f"{category}-{i:02d}",
                tier=4,
                category=category,
                description=(
                    f"Tier 4: {label} {i:02d}/{total_rare} "
                    f"({reg_name} {ur_token}, {len(ch_cps)} chars, cascade-shadowed)"
                ),
                binary_codepoints=frozenset(ch_cps),
                css_unicode_range=ur_token,
                uses_cascade_shadowing=True,
            )
        )
    return slices


def _build_freq_slices(
    freq_chunks: list[tuple[int, int, frozenset[int]]],
    *,
    category: str,
    label: str,
) -> list[SliceSpec]:
    """Build Tier 3 frequency-ranked slices in reverse rank order (lowest frequency first in CSS)."""
    slices: list[SliceSpec] = []
    total_freq = len(freq_chunks)
    for idx in range(total_freq - 1, -1, -1):
        rank_start, rank_end, chunk_cps = freq_chunks[idx]
        chunk_num = idx + 1
        slices.append(
            SliceSpec(
                slice_id=f"{category}-{chunk_num:02d}",
                tier=3,
                category=category,
                description=(
                    f"Tier 3: {label} {chunk_num:02d}/{total_freq} "
                    f"(Rank {rank_start}..{rank_end}, {len(chunk_cps)} chars)"
                ),
                binary_codepoints=chunk_cps,
                css_unicode_range=format_unicode_range(chunk_cps),
                uses_cascade_shadowing=False,
            )
        )
    return slices


def build_slice_plan(
    codepoints: CodepointSet,
    config: SliceConfig | None = None,
    *,
    family_name: str = "",
) -> list[SliceSpec]:
    """
    Analyze an input font's `codepoints` (from `cmap`) and dynamically construct
    an ordered list of `SliceSpec` objects from lowest CSS priority (Tier 4, declared first)
    to highest CSS priority (Tier 1, declared last).

    Supports multi-CJK profiles via `config.locale` (`"auto"`, `"jp"`, `"kr"`, `"sc"`, `"tc"`, `"hk"`).

    Guarantees:
    1. Every codepoint in `codepoints` belongs to the `binary_codepoints` of at least one slice.
    2. Across the generated CSS `@font-face` rules, the LAST rule whose `css_unicode_range`
       matches any codepoint `cp in codepoints` is strictly the slice whose `binary_codepoints`
       contains `cp` (100% Cascade Shadowing correctness).
    """
    if config is None:
        config = SliceConfig()

    all_cps = set(codepoints)
    if not all_cps:
        return []

    resolved_locale = (
        detect_font_locale(all_cps, family_name=family_name)
        if config.locale == "auto"
        else config.locale
    )

    if resolved_locale == "kr":
        return _build_kr_slice_plan(all_cps, config)
    return _build_ideograph_slice_plan(all_cps, config, resolved_locale)


def _build_ideograph_slice_plan(
    all_cps: set[int],
    config: SliceConfig,
    locale: str,
) -> list[SliceSpec]:
    """
    4-tier slice planner for Japanese (`jp`) and Chinese (`sc`, `tc`, `hk`) profiles:
    - Tier 4: `kanji-rare-XX` / `hanzi-rare-XX` (1-token hex ranges per Unicode ideograph region).
    - Tier 3: `kanji-freq-XX` / `hanzi-freq-XX` (Inverted-Pyramid frequency granularity).
    - Tier 2: `symbols-ext` & `symbols-ui`.
    - Tier 1: `kana-core` (`jp`) or `cjk-core` (`sc`/`tc`/`hk`), followed by `latin`.
    """
    is_jp = locale == "jp"
    ideograph_cps = {cp for cp in all_cps if is_cjk_ideograph(cp)}
    non_ideograph_cps = all_cps - ideograph_cps

    latin_cps: set[int] = set()
    core_cps: set[int] = set()
    sym_ui_cps: set[int] = set()
    sym_ext_cps: set[int] = set()
    for cp in non_ideograph_cps:
        # Check `is_tier1_cjk_core` before `is_tier1_latin` for Chinese profiles so Bopomofo tone marks
        # (`U+02C7`, `U+02C9..02CB`, `U+02D9`) co-locate with Bopomofo letters in `cjk-core`.
        if not is_jp and is_tier1_cjk_core(cp):
            core_cps.add(cp)
        elif is_tier1_latin(cp):
            latin_cps.add(cp)
        elif is_jp and is_tier1_kana_core(cp):
            core_cps.add(cp)
        elif is_tier2_symbols_ui(cp):
            sym_ui_cps.add(cp)
        else:
            sym_ext_cps.add(cp)

    if is_jp:
        freq_rank_map = KANJI_FREQ_RANK
        rare_cat, rare_label = "kanji-rare", "Rare/Extended Kanji"
        freq_cat, freq_label = "kanji-freq", "Frequent Kanji"
        sym_ext_label = "Extended Symbols & Non-Kanji"
        core_id = "kana-core"
        core_desc = f"Tier 1: Core Hiragana, Katakana, CJK Punctuation & Fullwidth ASCII ({len(core_cps)} chars)"
    else:
        freq_rank_map = (
            SC_HANZI_FREQ_RANK
            if locale == "sc"
            else (HK_HANZI_FREQ_RANK if locale == "hk" else TC_HANZI_FREQ_RANK)
        )
        rare_cat, rare_label = "hanzi-rare", "Rare/Extended Hanzi"
        freq_cat, freq_label = "hanzi-freq", "Frequent Hanzi"
        sym_ext_label = "Extended Symbols & Non-Hanzi"
        core_id = "cjk-core"
        core_desc = f"Tier 1: Core CJK Punctuation, Bopomofo & Fullwidth ASCII ({len(core_cps)} chars)"

    available_freq = sorted(
        ideograph_cps & freq_rank_map.keys(), key=freq_rank_map.__getitem__
    )
    freq_chunks = _build_progressive_freq_chunks(available_freq, config)
    tier3_set = frozenset({*c[2] for c in freq_chunks})
    rare_sorted = sorted(ideograph_cps - tier3_set)

    slices: list[SliceSpec] = _build_rare_ideograph_slices(
        rare_sorted,
        config,
        category=rare_cat,
        label=rare_label,
    )
    slices.extend(
        _build_freq_slices(
            freq_chunks,
            category=freq_cat,
            label=freq_label,
        )
    )
    _append_tier2_and_tier1_slices(
        slices,
        config=config,
        earlier_cps=ideograph_cps,
        sym_ext_cps=sym_ext_cps,
        sym_ext_label=sym_ext_label,
        sym_ui_cps=sym_ui_cps,
        core_cps=core_cps,
        core_id=core_id,
        core_desc=core_desc,
        latin_cps=latin_cps,
    )
    return slices


def _build_kr_slice_plan(all_cps: set[int], config: SliceConfig) -> list[SliceSpec]:
    """
    4-tier slice planner for Korean (`kr` profile):
    - Tier 4: `hanja-rare-XX` (CJK ideographs) & `hangul-rare-XX` (non-KS X 1001 Hangul syllables), 1-token hex ranges.
    - Tier 3: `hangul-freq-XX` (KS X 1001 & web-frequent Hangul syllables, Inverted-Pyramid granularity).
    - Tier 2: `symbols-ext` & `symbols-ui`.
    - Tier 1: `hangul-core` (CJK punctuation, Compatibility Jamo, Conjoining Jamo, Fullwidth ASCII) & `latin`.
    """
    hangul_syl_cps = {cp for cp in all_cps if is_hangul_syllable(cp)}
    hanja_cps = {cp for cp in all_cps if is_cjk_ideograph(cp)}
    non_ideograph_cps = all_cps - hangul_syl_cps - hanja_cps

    latin_cps: set[int] = set()
    hangul_core_cps: set[int] = set()
    sym_ui_cps: set[int] = set()
    sym_ext_cps: set[int] = set()
    for cp in non_ideograph_cps:
        if is_tier1_latin(cp):
            latin_cps.add(cp)
        elif is_tier1_hangul_core(cp):
            hangul_core_cps.add(cp)
        elif is_tier2_symbols_ui(cp):
            sym_ui_cps.add(cp)
        else:
            sym_ext_cps.add(cp)

    available_freq_hangul = sorted(
        hangul_syl_cps & KR_HANGUL_FREQ_RANK.keys(), key=KR_HANGUL_FREQ_RANK.__getitem__
    )
    freq_chunks = _build_progressive_freq_chunks(available_freq_hangul, config)
    tier3_hangul_set = frozenset({*c[2] for c in freq_chunks})
    rare_hangul_sorted = sorted(hangul_syl_cps - tier3_hangul_set)
    rare_hanja_sorted = sorted(hanja_cps)

    # Tier 4a: Rare Hanja (1-token hex range per Unicode ideograph region)
    slices: list[SliceSpec] = _build_rare_ideograph_slices(
        rare_hanja_sorted,
        config,
        category="hanja-rare",
        label="Rare/Extended Hanja",
    )

    # Tier 4b: Rare Hangul Syllables (U+AC00..D7A3, 1-token hex range, shadowed by Tier 3 hangul-freq)
    rare_hangul_chunks = _chunk_sorted_codepoints(
        rare_hangul_sorted, config.rare_kanji_chunk_size
    )
    total_rare_hangul = len(rare_hangul_chunks)
    for i, ch_cps in enumerate(rare_hangul_chunks, start=1):
        s_hex, e_hex = ch_cps[0], ch_cps[-1]
        ur_token = f"U+{s_hex:X}" if s_hex == e_hex else f"U+{s_hex:X}-{e_hex:X}"
        slices.append(
            SliceSpec(
                slice_id=f"hangul-rare-{i:02d}",
                tier=4,
                category="hangul-rare",
                description=(
                    f"Tier 4: Rare Hangul Syllables {i:02d}/{total_rare_hangul} "
                    f"({ur_token}, {len(ch_cps)} chars, cascade-shadowed)"
                ),
                binary_codepoints=frozenset(ch_cps),
                css_unicode_range=ur_token,
                uses_cascade_shadowing=True,
            )
        )

    slices.extend(
        _build_freq_slices(
            freq_chunks,
            category="hangul-freq",
            label="Frequent Hangul Syllables",
        )
    )
    _append_tier2_and_tier1_slices(
        slices,
        config=config,
        earlier_cps=hanja_cps | hangul_syl_cps,
        sym_ext_cps=sym_ext_cps,
        sym_ext_label="Extended Symbols & Non-Korean",
        sym_ui_cps=sym_ui_cps,
        core_cps=hangul_core_cps,
        core_id="hangul-core",
        core_desc=(
            f"Tier 1: Core Hangul Jamo, CJK Punctuation & Fullwidth ASCII "
            f"({len(hangul_core_cps)} chars)"
        ),
        latin_cps=latin_cps,
    )
    return slices


def _append_tier2_and_tier1_slices(
    slices: list[SliceSpec],
    *,
    config: SliceConfig,
    earlier_cps: set[int],
    sym_ext_cps: set[int],
    sym_ext_label: str,
    sym_ui_cps: set[int],
    core_cps: set[int],
    core_id: str,
    core_desc: str,
    latin_cps: set[int],
) -> None:
    """Append Tier 2 (`symbols-ext`, `symbols-ui`) and Tier 1 (`<core_id>`, `latin`) with cascade-safe compaction."""
    earlier_sorted = sorted(earlier_cps)
    if sym_ext_cps:
        sym_ext_sorted = sorted(sym_ext_cps)
        sym_ext_chunks = _chunk_sorted_codepoints(
            sym_ext_sorted, config.symbols_ext_chunk_size
        )
        for idx, ch_cps in enumerate(sym_ext_chunks, start=1):
            slice_id = (
                "symbols-ext"
                if len(sym_ext_chunks) == 1
                else f"symbols-ext-{idx:02d}"
            )
            ur_str = _compact_range_with_shadowing(ch_cps, earlier_sorted)
            slices.append(
                SliceSpec(
                    slice_id=slice_id,
                    tier=2,
                    category="symbols-ext",
                    description=(
                        f"Tier 2: {sym_ext_label} "
                        f"({idx}/{len(sym_ext_chunks)}, {len(ch_cps)} chars)"
                    ),
                    binary_codepoints=frozenset(ch_cps),
                    css_unicode_range=ur_str,
                    uses_cascade_shadowing=True,
                )
            )

    if sym_ui_cps:
        forbidden_ui_sorted = sorted(earlier_cps | sym_ext_cps)
        ur_ui = _compact_range_with_shadowing(sorted(sym_ui_cps), forbidden_ui_sorted)
        slices.append(
            SliceSpec(
                slice_id="symbols-ui",
                tier=2,
                category="symbols-ui",
                description=f"Tier 2: High-Frequency UI & Article Symbols ({len(sym_ui_cps)} chars)",
                binary_codepoints=frozenset(sym_ui_cps),
                css_unicode_range=ur_ui,
                uses_cascade_shadowing=True,
            )
        )

    if core_cps:
        forbidden_core_sorted = sorted(earlier_cps | sym_ext_cps | sym_ui_cps)
        ur_core = _compact_range_with_shadowing(sorted(core_cps), forbidden_core_sorted)
        slices.append(
            SliceSpec(
                slice_id=core_id,
                tier=1,
                category=core_id,
                description=core_desc,
                binary_codepoints=frozenset(core_cps),
                css_unicode_range=ur_core,
                uses_cascade_shadowing=True,
            )
        )

    if latin_cps:
        forbidden_latin_sorted = sorted(
            earlier_cps | sym_ext_cps | sym_ui_cps | core_cps
        )
        ur_latin = _compact_range_with_shadowing(
            sorted(latin_cps), forbidden_latin_sorted
        )
        slices.append(
            SliceSpec(
                slice_id="latin",
                tier=1,
                category="latin",
                description=f"Tier 1: Basic Latin, Western Punctuation & Currency ({len(latin_cps)} chars)",
                binary_codepoints=frozenset(latin_cps),
                css_unicode_range=ur_latin,
                uses_cascade_shadowing=True,
            )
        )


