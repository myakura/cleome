"""
Core dynamic unicode-range slicing planner for cleome.

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
from dataclasses import dataclass, field
import re
from pathlib import Path
from fontTools.ttLib import TTFont

from cleome.data.kanji_freq import KANJI_FREQ_CODEPOINTS, KANJI_FREQ_RANK

type CodepointSet = set[int] | frozenset[int]

CJK_IDEOGRAPH_REGIONS: tuple[tuple[str, int, int], ...] = (
    ("Ext-A", 0x3400, 0x4DBF),
    ("BMP", 0x4E00, 0x9FFF),
    ("Compat", 0xF900, 0xFAFF),
    ("SIP", 0x20000, 0x2FA1F),  # Ext B..F, Ext I (U+2EBF0..2EE5F), Compat Supplement
    ("TIP", 0x30000, 0x3347F),  # Ext G, Ext H, Ext J (U+323B0..3347F, Unicode 17.0)
)

# OpenType OS/2.usWidthClass (1..9) to CSS font-stretch percentage (5 = 100% normal is omitted)
OS2_WIDTH_CLASS_TO_CSS: frozendict[int, str] = frozendict(
    {
        1: "50%",
        2: "62.5%",
        3: "75%",
        4: "87.5%",
        6: "112.5%",
        7: "125%",
        8: "150%",
        9: "200%",
    }
)


@dataclass(frozen=True, slots=True)
class SliceConfig:
    """Configuration parameters for dynamic unicode-range slicing."""

    # Progressive (Inverted-Pyramid) tiers for Tier 3 (frequency-ranked Kanji):
    # List of (chunk_size, num_chunks) from highest frequency (rank 1) downward.
    # Any remaining frequency-ranked Kanji in the font's cmap after these tiers
    # are sliced using `freq_tail_chunk_size`.
    freq_tiers: tuple[tuple[int, int], ...] = (
        (200, 2),  # Rank 1..400: 200 chars x 2 slices
        (80, 10),  # Rank 401..1200: 80 chars x 10 slices
    )
    freq_tail_chunk_size: int = 70  # Rank 1201..3169: ~70 chars/slice (28 slices for 1,969 chars)

    # Target character count per slice for Tier 4 (rare Kanji) and Tier 2 (`symbols-ext`):
    rare_kanji_chunk_size: int = 250
    symbols_ext_chunk_size: int = 350

    # Subsetting & WOFF2 options:
    retain_layout_closure: bool = True
    drop_tables: tuple[str, ...] = ()
    patch_uvs_only_cmap: bool = True

    # CSS generation options:
    slices_subdir: str = "slices"
    url_prefix: str | None = None
    font_display: str = "swap"


@dataclass(frozen=True, slots=True)
class FontMetadata:
    """Auto-detected or user-overridden CSS @font-face metadata for a single font."""

    family_name: str
    slug: str
    is_variable: bool
    weight_css: str
    stretch_css: str | None
    style_css: str
    variant_suffix: str = ""
    codepoints: frozenset[int] = field(default_factory=frozenset)
    patched_uvs_codepoints: tuple[int, ...] = ()


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


def slugify_family(name: str) -> str:
    """Convert a font family name (e.g. 'GenSou Sans JP') into a URL/filename slug ('gensou-sans-jp')."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip()).strip("-").lower()
    return s or "font"


def _uvs_selector_sort_key(item: tuple[int, list[tuple[int, str | None]]]) -> tuple[int, int]:
    """Prioritize Ideographic Variation Selectors (U+E0100..U+E01EF) in ascending order before VS1..VS16."""
    sel = item[0]
    return (0 if 0xE0100 <= sel <= 0xE01EF else 1, sel)


def patch_uvs_missing_base_cmap(font: TTFont) -> list[int]:
    """
    Detect characters that exist in cmap Format 14 (UVS) with a concrete variant glyph
    but were accidentally omitted from Format 4/12 (such as U+553E '唾' in Adobe SourceHanSansJP-VF.otf v2.005)
    and patch them into the base cmap tables in-memory.
    Prioritizes IVS selectors (U+E0100..U+E01EF) in ascending order so U+E0100 (standard base form) always wins.
    """
    if "cmap" not in font:
        return []
    best_cmap = font.getBestCmap() or {}
    uvs_additions: dict[int, str] = {}
    for table in font["cmap"].tables:
        if getattr(table, "format", None) == 14 and hasattr(table, "uvsDict"):
            for _uvs_selector, records in sorted(table.uvsDict.items(), key=_uvs_selector_sort_key):
                for cp, gname in records:
                    if cp not in best_cmap and cp not in uvs_additions and gname is not None:
                        uvs_additions[cp] = gname
    if not uvs_additions:
        return []

    patched: set[int] = set()
    for table in font["cmap"].tables:
        if getattr(table, "format", None) in (4, 12) and hasattr(table, "cmap"):
            for cp, gname in uvs_additions.items():
                if table.format == 4 and cp > 0xFFFF:
                    continue
                table.cmap[cp] = gname
                patched.add(cp)
    return sorted(patched)


def _fmt_num(val: float) -> str:
    if abs(val - round(val)) < 1e-4:
        return str(int(round(val)))
    return f"{val:.2f}".rstrip("0").rstrip(".")


def extract_font_metadata(
    font: TTFont,
    *,
    family_override: str | None = None,
    slug_override: str | None = None,
    weight_override: str | None = None,
    stretch_override: str | None = None,
    style_override: str | None = None,
    variant_suffix: str = "",
    patch_uvs: bool = True,
) -> FontMetadata:
    """Inspect TTFont tables (`name`, `fvar`, `OS/2`, `post`, `cmap`) to build FontMetadata."""
    patched_uvs = tuple(patch_uvs_missing_base_cmap(font)) if patch_uvs else ()

    # 1. Detect family name (prefer Typographic Family Name ID 16, then Family Name ID 1)
    detected_family = "Custom Font"
    if "name" in font:
        name_table = font["name"]
        for name_id in (16, 1, 4):
            rec = (
                name_table.getName(name_id, 3, 1, 0x0409)
                or name_table.getName(name_id, 1, 0, 0)
                or name_table.getDebugName(name_id)
            )
            if rec:
                val = rec.toUnicode() if hasattr(rec, "toUnicode") else str(rec)
                if val.strip():
                    detected_family = val.strip()
                    break

    family_name = family_override or detected_family
    slug = slug_override or slugify_family(family_name)

    # 2. Detect Variable axes (`fvar`) vs Static (`OS/2`)
    is_variable = "fvar" in font
    axes: dict[str, tuple[float, float, float]] = {}
    if is_variable:
        for ax in font["fvar"].axes:
            axes[ax.axisTag] = (float(ax.minValue), float(ax.defaultValue), float(ax.maxValue))

    # Weight
    if weight_override is not None:
        weight_css = weight_override
    elif "wght" in axes:
        w_min, _, w_max = axes["wght"]
        if abs(w_min - w_max) < 1e-4:
            weight_css = _fmt_num(w_min)
        else:
            weight_css = f"{_fmt_num(w_min)} {_fmt_num(w_max)}"
    elif "OS/2" in font:
        weight_css = str(int(font["OS/2"].usWeightClass))
    else:
        weight_css = "400"

    # Stretch (wdth axis for Variable Fonts, or OS/2.usWidthClass for Static Fonts)
    if stretch_override is not None:
        stretch_css = stretch_override
    elif "wdth" in axes:
        d_min, _, d_max = axes["wdth"]
        if abs(d_min - d_max) < 1e-4:
            stretch_css = f"{_fmt_num(d_min)}%" if abs(d_min - 100.0) > 1e-4 else None
        else:
            stretch_css = f"{_fmt_num(d_min)}% {_fmt_num(d_max)}%"
    elif not is_variable and "OS/2" in font:
        stretch_css = OS2_WIDTH_CLASS_TO_CSS.get(int(font["OS/2"].usWidthClass))
    else:
        stretch_css = None

    # Style (ital / slnt / OS/2 / post)
    if style_override is not None:
        style_css = style_override
    elif "slnt" in axes:
        s_min, _, s_max = axes["slnt"]
        style_css = f"oblique {_fmt_num(-s_max)}deg {_fmt_num(-s_min)}deg"
    elif "ital" in axes and axes["ital"][2] >= 0.5:
        style_css = "italic"
    elif "OS/2" in font and (font["OS/2"].fsSelection & 0x01):
        style_css = "italic"
    elif "post" in font and getattr(font["post"], "italicAngle", 0.0) != 0.0:
        style_css = "italic"
    else:
        style_css = "normal"

    cmap = font.getBestCmap() or {}
    codepoints = frozenset(cmap.keys())

    return FontMetadata(
        family_name=family_name,
        slug=slug,
        is_variable=is_variable,
        weight_css=weight_css,
        stretch_css=stretch_css,
        style_css=style_css,
        variant_suffix=variant_suffix,
        codepoints=codepoints,
        patched_uvs_codepoints=patched_uvs,
    )


def format_unicode_range(codepoints: CodepointSet | list[int]) -> str:
    """Format a collection of integer codepoints into a minimal CSS `unicode-range` string."""
    sorted_cps = sorted(set(codepoints))
    if not sorted_cps:
        return ""
    ranges: list[tuple[int, int]] = []
    start = prev = sorted_cps[0]
    for cp in sorted_cps[1:]:
        if cp == prev + 1:
            prev = cp
        else:
            ranges.append((start, prev))
            start = prev = cp
    ranges.append((start, prev))
    return ", ".join(
        f"U+{s:X}" if s == e else f"U+{s:X}-{e:X}" for s, e in ranges
    )


def parse_unicode_range(ur_str: str) -> set[int]:
    """Parse a CSS `unicode-range` string back into a set of integer codepoints."""
    cps: set[int] = set()
    for part in ur_str.split(","):
        part = part.strip()
        if not part:
            continue
        if part.upper().startswith("U+"):
            part = part[2:]
        if "?" in part:
            lo = int(part.replace("?", "0"), 16)
            hi = int(part.replace("?", "F"), 16)
            cps.update(range(lo, hi + 1))
        elif "-" in part:
            s_str, e_str = part.split("-", 1)
            cps.update(range(int(s_str, 16), int(e_str, 16) + 1))
        else:
            cps.add(int(part, 16))
    return cps


# ---------------------------------------------------------------------------
# Character Classification Predicates for the 4-Tier Japanese Slicing Model
# ---------------------------------------------------------------------------

def is_cjk_ideograph(cp: int) -> bool:
    """Return True if `cp` is a CJK Unified or Compatibility Ideograph (Kanji)."""
    return (
        (0x3400 <= cp <= 0x4DBF)       # CJK Unified Ideographs Extension A
        or (0x4E00 <= cp <= 0x9FFF)    # CJK Unified Ideographs (BMP)
        or (0xF900 <= cp <= 0xFAFF)    # CJK Compatibility Ideographs
        or (0x20000 <= cp <= 0x2FA1F)  # CJK Unified Ideographs Ext B..F, Ext I & Compat Supplement (SIP)
        or (0x30000 <= cp <= 0x3347F)  # CJK Unified Ideographs Ext G, Ext H & Ext J (TIP, Unicode 17.0)
    )


def is_tier1_latin(cp: int) -> bool:
    """
    Tier 1 (`latin`): Basic ASCII, Latin-1 Supplement, Latin Extended-A/B,
    Latin Extended Additional (`U+1E00-1EFF`), General Punctuation (`U+2000-206F`),
    Currency Symbols (`U+20A0-20CF`), Trademark (`U+2122`), Litre (`U+2113`), BOM/Replacement.
    """
    return (
        (0x0000 <= cp <= 0x024F)
        or (0x1E00 <= cp <= 0x1EFF)
        or (0x2000 <= cp <= 0x206F)
        or (0x20A0 <= cp <= 0x20CF)
        or cp in (0x2113, 0x2122, 0xFEFF, 0xFFFD)
    )


def is_tier1_kana_core(cp: int) -> bool:
    """
    Tier 1 (`kana-core`):
    - `U+3000-303F`: CJK Symbols and Punctuation (`　`, `、`, `。`, `「」`, `『』`, `【】`, `〜`, `々`, `〆`)
    - `U+3040-309F`: Hiragana (`ぁ-ん`, including `む`, `ゐ`, `ゔ`, `゛`, `゜`, `ゝ`, `ゞ`)
    - `U+30A0-30FF`: Katakana (`ァ-ン`, including `モ`, `ヶ`, `ヵ`, `ヱ`, `・`, `ー`, `ヽ`, `ヾ`)
    - `U+31F0-31FF`: Katakana Phonetic Extensions (`ㇰ-ㇿ`)
    - `U+FF01-FF60`: Fullwidth ASCII variants (`！-～`, `０-９`, `Ａ-Ｚ`, `ａ-ｚ`, `（）`)
    - `U+FFE0-FFE6`: Fullwidth Currency/Symbol variants (`¢`, `£`, `¬`, `¯`, `¦`, `¥`, `₩`)
    """
    return (
        (0x3000 <= cp <= 0x30FF)
        or (0x31F0 <= cp <= 0x31FF)
        or (0xFF01 <= cp <= 0xFF60)
        or (0xFFE0 <= cp <= 0xFFE6)
    )


def is_tier2_symbols_ui(cp: int) -> bool:
    """
    Tier 2 (`symbols-ui`): High-frequency UI & article symbols:
    - `U+2190-21FF`: Arrows (`←`, `↑`, `→`, `↓`, `↔`, `⇒`, etc.)
    - `U+2460-2473`, `U+24EA`: Circled Numbers `①-⑳`, `⓪`
    - `U+25A0-266F`, `U+26A0`: Geometric Shapes (`■□▲△▶▷▼▽◀◁◆◇○◎●`), Stars (`★☆`),
      Weather (`☀☁☂☃`), Hearts/Suits (`♠♡♢♣♤♥♦♧`), Musical Notes (`♩♪♫♬`), Warning (`⚠`)
    - `U+2713-2716`, `U+2776-2793`: Check/Cross marks (`✓✔✕✖`), Negative Circled Numbers (`❶-❿`)
    """
    return (
        (0x2190 <= cp <= 0x21FF)
        or (0x2460 <= cp <= 0x2473)
        or cp == 0x24EA
        or (0x25A0 <= cp <= 0x266F)
        or cp == 0x26A0
        or (0x2713 <= cp <= 0x2716)
        or (0x2776 <= cp <= 0x2793)
    )


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


def build_slice_plan(
    codepoints: CodepointSet,
    config: SliceConfig | None = None,
) -> list[SliceSpec]:
    """
    Analyze an input font's `codepoints` (from `cmap`) and dynamically construct
    an ordered list of `SliceSpec` objects from lowest CSS priority (Tier 4, declared first)
    to highest CSS priority (Tier 1, declared last).

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

    # Separate Kanji vs Non-Kanji
    kanji_cps = {cp for cp in all_cps if is_cjk_ideograph(cp)}
    non_kanji_cps = all_cps - kanji_cps

    # -----------------------------------------------------------------------
    # 1. Partition Non-Kanji into Tier 1 (latin, kana-core) and Tier 2 (symbols-ui, symbols-ext)
    # -----------------------------------------------------------------------
    latin_cps: set[int] = set()
    kana_cps: set[int] = set()
    sym_ui_cps: set[int] = set()
    sym_ext_cps: set[int] = set()
    for cp in non_kanji_cps:
        if is_tier1_latin(cp):
            latin_cps.add(cp)
        elif is_tier1_kana_core(cp):
            kana_cps.add(cp)
        elif is_tier2_symbols_ui(cp):
            sym_ui_cps.add(cp)
        else:
            sym_ext_cps.add(cp)

    # -----------------------------------------------------------------------
    # 2. Partition Kanji into Tier 3 (Frequency-Ranked) and Tier 4 (Rare/Extended)
    # -----------------------------------------------------------------------
    available_freq_kanji = sorted(
        kanji_cps & KANJI_FREQ_RANK.keys(), key=KANJI_FREQ_RANK.__getitem__
    )

    freq_chunks: list[tuple[int, int, frozenset[int]]] = []
    cursor = 0
    for chunk_size, num_chunks in config.freq_tiers:
        for _ in range(num_chunks):
            if cursor >= len(available_freq_kanji):
                break
            sub = available_freq_kanji[cursor : cursor + chunk_size]
            if sub:
                freq_chunks.append((cursor + 1, cursor + len(sub), frozenset(sub)))
                cursor += len(sub)

    if cursor < len(available_freq_kanji):
        tail_kanji = available_freq_kanji[cursor:]
        tail_lists = _chunk_sorted_codepoints(tail_kanji, config.freq_tail_chunk_size)
        for sub in tail_lists:
            freq_chunks.append((cursor + 1, cursor + len(sub), frozenset(sub)))
            cursor += len(sub)

    # Python 3.15 (PEP 798): unpacking in set comprehension
    tier3_kanji_set = frozenset({*c[2] for c in freq_chunks})
    rare_kanji_sorted = sorted(kanji_cps - tier3_kanji_set)

    # -----------------------------------------------------------------------
    # 3. Build SliceSpecs in CSS declaration order (Tier 4 -> Tier 3 -> Tier 2 -> Tier 1)
    # -----------------------------------------------------------------------
    slices: list[SliceSpec] = []

    # --- Tier 4: Rare / Extended Kanji (declared first in CSS, 1-token hex range) ---
    rare_chunk_tuples: list[tuple[str, list[int], int, int]] = []
    for reg_name, reg_lo, reg_hi in CJK_IDEOGRAPH_REGIONS:
        lo_idx = bisect_left(rare_kanji_sorted, reg_lo)
        hi_idx = bisect_right(rare_kanji_sorted, reg_hi)
        reg_cps = rare_kanji_sorted[lo_idx:hi_idx]
        if not reg_cps:
            continue
        reg_chunks = _chunk_sorted_codepoints(reg_cps, config.rare_kanji_chunk_size)
        for ch_cps in reg_chunks:
            rare_chunk_tuples.append((reg_name, ch_cps, ch_cps[0], ch_cps[-1]))

    total_rare = len(rare_chunk_tuples)
    for i, (reg_name, ch_cps, s_hex, e_hex) in enumerate(rare_chunk_tuples, start=1):
        ur_token = f"U+{s_hex:X}" if s_hex == e_hex else f"U+{s_hex:X}-{e_hex:X}"
        slices.append(
            SliceSpec(
                slice_id=f"kanji-rare-{i:02d}",
                tier=4,
                category="kanji-rare",
                description=(
                    f"Tier 4: Rare/Extended Kanji {i:02d}/{total_rare} "
                    f"({reg_name} {ur_token}, {len(ch_cps)} chars, cascade-shadowed)"
                ),
                binary_codepoints=frozenset(ch_cps),
                css_unicode_range=ur_token,
                uses_cascade_shadowing=True,
            )
        )

    # --- Tier 3: High & Mid-Frequency Kanji (Progressive Granularity) ---
    # Declared from lower frequency (chunk N) to highest frequency (chunk 01) so that
    # `kanji-freq-01` (Rank 1..200) is closest to the bottom of Tier 3.
    total_freq = len(freq_chunks)
    for idx in range(total_freq - 1, -1, -1):
        rank_start, rank_end, chunk_cps = freq_chunks[idx]
        chunk_num = idx + 1
        slices.append(
            SliceSpec(
                slice_id=f"kanji-freq-{chunk_num:02d}",
                tier=3,
                category="kanji-freq",
                description=(
                    f"Tier 3: Frequent Kanji {chunk_num:02d}/{total_freq} "
                    f"(Rank {rank_start}..{rank_end}, {len(chunk_cps)} chars)"
                ),
                binary_codepoints=chunk_cps,
                css_unicode_range=format_unicode_range(chunk_cps),
                uses_cascade_shadowing=False,
            )
        )

    # --- Tier 2: Extended Non-Kanji (`symbols-ext`) & High-Frequency UI Symbols (`symbols-ui`) ---
    kanji_sorted = sorted(kanji_cps)
    if sym_ext_cps:
        sym_ext_sorted = sorted(sym_ext_cps)
        sym_ext_chunks = _chunk_sorted_codepoints(
            sym_ext_sorted, config.symbols_ext_chunk_size
        )
        # Since `sym_ext_chunks` partitions `sym_ext_sorted` into contiguous ascending intervals,
        # no codepoint from another `symbols-ext` chunk can lie between `ch_cps[0]` and `ch_cps[-1]`.
        # Thus only `kanji_sorted` (Tier 3/4, declared earlier) must be avoided when bridging gaps.
        for idx, ch_cps in enumerate(sym_ext_chunks, start=1):
            slice_id = (
                "symbols-ext"
                if len(sym_ext_chunks) == 1
                else f"symbols-ext-{idx:02d}"
            )
            ur_str = _compact_range_with_shadowing(ch_cps, kanji_sorted)
            slices.append(
                SliceSpec(
                    slice_id=slice_id,
                    tier=2,
                    category="symbols-ext",
                    description=(
                        f"Tier 2: Extended Symbols & Non-Kanji "
                        f"({idx}/{len(sym_ext_chunks)}, {len(ch_cps)} chars)"
                    ),
                    binary_codepoints=frozenset(ch_cps),
                    css_unicode_range=ur_str,
                    uses_cascade_shadowing=True,
                )
            )

    if sym_ui_cps:
        forbidden_ui_sorted = sorted(kanji_cps | sym_ext_cps)
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

    # --- Tier 1: Core Kana/Punctuation (`kana-core`) & Core Western (`latin`) ---
    if kana_cps:
        forbidden_kana_sorted = sorted(kanji_cps | sym_ext_cps | sym_ui_cps)
        ur_kana = _compact_range_with_shadowing(sorted(kana_cps), forbidden_kana_sorted)
        slices.append(
            SliceSpec(
                slice_id="kana-core",
                tier=1,
                category="kana-core",
                description=(
                    f"Tier 1: Core Hiragana, Katakana, CJK Punctuation & Fullwidth ASCII "
                    f"({len(kana_cps)} chars)"
                ),
                binary_codepoints=frozenset(kana_cps),
                css_unicode_range=ur_kana,
                uses_cascade_shadowing=True,
            )
        )

    if latin_cps:
        forbidden_latin_sorted = sorted(kanji_cps | sym_ext_cps | sym_ui_cps | kana_cps)
        ur_latin = _compact_range_with_shadowing(sorted(latin_cps), forbidden_latin_sorted)
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

    return slices


def _compact_range_with_shadowing(
    sorted_target_cps: list[int],
    forbidden_sorted: list[int],
    max_gap: int = 0x80,
) -> str:
    """
    Merge adjacent target codepoints into contiguous hex ranges `[start, end]` whenever:
    1. The gap between consecutive target codepoints is `<= max_gap`, AND
    2. The open interval `(prev, cp)` contains ZERO codepoints from `forbidden_sorted`
       (verified in O(log F) via binary search).
    """
    if not sorted_target_cps:
        return ""
    ranges: list[tuple[int, int]] = []
    start = prev = sorted_target_cps[0]
    for cp in sorted_target_cps[1:]:
        if cp == prev + 1 or (
            (cp - prev) <= max_gap
            and bisect_right(forbidden_sorted, prev) == bisect_left(forbidden_sorted, cp)
        ):
            prev = cp
        else:
            ranges.append((start, prev))
            start = prev = cp
    ranges.append((start, prev))

    return ", ".join(
        f"U+{s:X}" if s == e else f"U+{s:X}-{e:X}" for s, e in ranges
    )
