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
from cleome.data.kr_freq import KR_HANGUL_FREQ_CODEPOINTS, KR_HANGUL_FREQ_RANK
from cleome.data.sc_freq import SC_HANZI_FREQ_CODEPOINTS, SC_HANZI_FREQ_RANK
from cleome.data.tc_freq import (
    HK_EXTRA_CANTONESE_CHARS,
    HK_HANZI_FREQ_CODEPOINTS,
    HK_HANZI_FREQ_RANK,
    TC_HANZI_FREQ_CODEPOINTS,
    TC_HANZI_FREQ_RANK,
)

type CodepointSet = set[int] | frozenset[int]

SUPPORTED_LOCALES: tuple[str, ...] = ("auto", "jp", "kr", "sc", "tc", "hk")

_JP_ONLY_FREQ: frozenset[int] = frozenset(
    KANJI_FREQ_RANK.keys() - SC_HANZI_FREQ_RANK.keys() - TC_HANZI_FREQ_RANK.keys()
)
_SC_ONLY_FREQ: frozenset[int] = frozenset(
    SC_HANZI_FREQ_RANK.keys() - TC_HANZI_FREQ_RANK.keys() - KANJI_FREQ_RANK.keys()
)
_TC_ONLY_FREQ: frozenset[int] = frozenset(
    TC_HANZI_FREQ_RANK.keys() - SC_HANZI_FREQ_RANK.keys() - KANJI_FREQ_RANK.keys()
)
_HK_EXTRA_CPS: frozenset[int] = frozenset(ord(c) for c in HK_EXTRA_CANTONESE_CHARS)
_BOPOMOFO_TONE_MARKS: frozenset[int] = frozenset({0x02C7, 0x02C9, 0x02CA, 0x02CB, 0x02D9})

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
    styles_css: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.styles_css:
            object.__setattr__(self, "styles_css", (self.style_css,))



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
    static_italic = (
        ("OS/2" in font and bool(font["OS/2"].fsSelection & 0x01))
        or ("post" in font and getattr(font["post"], "italicAngle", 0.0) != 0.0)
    )
    if style_override is not None:
        styles_css: tuple[str, ...] = (style_override,)
    else:
        has_ital = "ital" in axes
        has_slnt = "slnt" in axes
        slnt_oblique: str | None = None
        slnt_has_zero = True
        if has_slnt:
            s_min, _, s_max = axes["slnt"]
            if abs(s_min - s_max) < 1e-4:
                if abs(s_min) >= 1e-4:
                    slnt_oblique = f"oblique {_fmt_num(-s_min)}deg"
                    slnt_has_zero = False
                else:
                    slnt_oblique = None
                    slnt_has_zero = True
            else:
                lo_deg = min(-s_min, -s_max)
                hi_deg = max(-s_min, -s_max)
                slnt_oblique = f"oblique {_fmt_num(lo_deg)}deg {_fmt_num(hi_deg)}deg"
                slnt_has_zero = lo_deg - 1e-4 <= 0.0 <= hi_deg + 1e-4

        if has_ital:
            i_min, _, i_max = axes["ital"]
            ital_has_normal = i_min < 0.5
            ital_has_italic = i_max >= 0.5
            styles_list: list[str] = []
            if slnt_oblique is None:
                if ital_has_normal:
                    styles_list.append("normal")
                if ital_has_italic:
                    styles_list.append("italic")
                if not styles_list:
                    styles_list.append("normal")
            else:
                if ital_has_italic:
                    if ital_has_normal and slnt_has_zero:
                        styles_list.append("normal")
                    styles_list.append("italic")
                    styles_list.append(slnt_oblique)
                else:
                    styles_list.append(slnt_oblique)
            styles_css = tuple(styles_list)
        elif slnt_oblique is not None:
            styles_css = (slnt_oblique,)
        elif static_italic:
            styles_css = ("italic",)
        else:
            styles_css = ("normal",)

    style_css = styles_css[0]

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
        styles_css=styles_css,
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
# Character Classification Predicates for the 4-Tier Multi-CJK Slicing Model
# ---------------------------------------------------------------------------

def is_cjk_ideograph(cp: int) -> bool:
    """Return True if `cp` is a CJK Unified or Compatibility Ideograph (Kanji / Hanzi / Hanja)."""
    return (
        (0x3400 <= cp <= 0x4DBF)       # CJK Unified Ideographs Extension A
        or (0x4E00 <= cp <= 0x9FFF)    # CJK Unified Ideographs (BMP)
        or (0xF900 <= cp <= 0xFAFF)    # CJK Compatibility Ideographs
        or (0x20000 <= cp <= 0x2FA1F)  # CJK Unified Ideographs Ext B..F, Ext I & Compat Supplement (SIP)
        or (0x30000 <= cp <= 0x3347F)  # CJK Unified Ideographs Ext G, Ext H & Ext J (TIP, Unicode 17.0)
    )


def is_hangul_syllable(cp: int) -> bool:
    """Return True if `cp` is a precomposed modern Hangul syllable (`U+AC00-D7A3`, 11,172 chars)."""
    return 0xAC00 <= cp <= 0xD7A3


def is_tier1_latin(cp: int) -> bool:
    """
    Tier 1 (`latin`): Basic ASCII, Latin-1 Supplement, Latin Extended-A/B (`U+0000-024F`),
    Combining Diacritical Marks (`U+0300-036F`, `U+1AB0-1AFF`, `U+1DC0-1DFF`, `U+FE20-FE2F`),
    Latin Extended Additional (`U+1E00-1EFF`), General Punctuation (`U+2000-206F`),
    Currency Symbols (`U+20A0-20CF`), Pe̍h-ōe-jī superscripts (`U+1D3A` `ᴺ`, `U+207F` `ⁿ`),
    Trademark (`U+2122`), Litre (`U+2113`), BOM/Replacement.
    """
    return (
        (0x0000 <= cp <= 0x024F)
        or (0x0300 <= cp <= 0x036F)
        or (0x1AB0 <= cp <= 0x1AFF)
        or (0x1DC0 <= cp <= 0x1DFF)
        or (0x1E00 <= cp <= 0x1EFF)
        or (0x2000 <= cp <= 0x206F)
        or (0x20A0 <= cp <= 0x20CF)
        or (0xFE20 <= cp <= 0xFE2F)
        or cp in (0x1D3A, 0x207F, 0x2113, 0x2122, 0xFEFF, 0xFFFD)
    )


def is_tier1_kana_core(cp: int) -> bool:
    """
    Tier 1 (`kana-core`, `jp` profile):
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


def is_tier1_hangul_core(cp: int) -> bool:
    """
    Tier 1 (`hangul-core`, `kr` profile):
    - `U+1100-11FF`, `U+A960-A97F`, `U+D7B0-D7FF`: Conjoining Hangul Jamo (Choseong, Jungseong, Jongseong
      for NFD decomposed Hangul and Old Hangul `ccmp`/`ljmo`/`vjmo`/`tjmo` composition)
    - `U+3000-303F`: CJK Symbols and Punctuation (`　`, `、`, `。`, `「」`, `『』`, `《》`, `〈〉`, `【】`)
    - `U+3130-318F`: Hangul Compatibility Jamo (`ㄱ-ㅎ`, `ㅏ-ㅣ`, e.g. `ㅋㅋㅋ`, `ㅎㅎ`, `ㅜㅜ`, `ㅠㅠ`)
    - `U+FF01-FF60`: Fullwidth ASCII variants (`！-～`, `０-９`, `Ａ-Ｚ`, `ａ-ｚ`, `（）`)
    - `U+FFA0-FFDC`: Halfwidth Hangul Jamo variants
    - `U+FFE0-FFE6`: Fullwidth Currency/Symbol variants (`¢`, `£`, `¬`, `¯`, `¦`, `¥`, `₩`)
    """
    return (
        (0x1100 <= cp <= 0x11FF)
        or (0x3000 <= cp <= 0x303F)
        or (0x3130 <= cp <= 0x318F)
        or (0xA960 <= cp <= 0xA97F)
        or (0xD7B0 <= cp <= 0xD7FF)
        or (0xFF01 <= cp <= 0xFF60)
        or (0xFFA0 <= cp <= 0xFFDC)
        or (0xFFE0 <= cp <= 0xFFE6)
    )


def is_tier1_cjk_core(cp: int) -> bool:
    """
    Tier 1 (`cjk-core`, `sc` / `tc` / `hk` profiles):
    - `U+02C7`, `U+02C9-02CB`, `U+02D9`: Mandarin / Bopomofo tone marks (`ˇ`, `ˉ`, `ˊ`, `ˋ`, `˙`)
      co-located with Bopomofo letters so `GPOS` `mark`/`mkmk` positioning survives subsetting
    - `U+3000-303F`: CJK Symbols and Punctuation (`　`, `、`, `。`, `「」`, `『』`, `《》`, `〈〉`, `【】`)
    - `U+3100-312F`, `U+31A0-31BF`: Bopomofo and Bopomofo Extended (`ㄅ-ㄩ`, `ㆠ-ㆿ`)
    - `U+FF01-FF60`: Fullwidth ASCII variants (`！-～`, `０-９`, `Ａ-Ｚ`, `ａ-ｚ`, `（）`)
    - `U+FFE0-FFE6`: Fullwidth Currency/Symbol variants (`¢`, `£`, `¬`, `¯`, `¦`, `¥`, `₩`)
    """
    return (
        cp in _BOPOMOFO_TONE_MARKS
        or (0x3000 <= cp <= 0x303F)
        or (0x3100 <= cp <= 0x312F)
        or (0x31A0 <= cp <= 0x31BF)
        or (0xFF01 <= cp <= 0xFF60)
        or (0xFFE0 <= cp <= 0xFFE6)
    )


def is_tier2_symbols_ui(cp: int) -> bool:
    """
    Tier 2 (`symbols-ui`): High-frequency UI & article symbols:
    - `U+2103`: Degree Celsius (`℃`)
    - `U+2160-216B`: Uppercase Roman Numerals (`Ⅰ-Ⅻ`)
    - `U+2190-21FF`: Arrows (`←`, `↑`, `→`, `↓`, `↔`, `⇒`, etc.)
    - `U+221E`, `U+2252`, `U+2260`, `U+2266-2267`: Common CJK Math/Comparison (`∞`, `≒`, `≠`, `≦`, `≧`)
    - `U+2460-2473`, `U+24EA`: Circled Numbers `①-⑳`, `⓪`
    - `U+25A0-266F`, `U+26A0`: Geometric Shapes (`■□▲△▶▷▼▽◀◁◆◇◈○◎●`), Stars (`★☆`),
      Weather (`☀☁☂☃`), Hearts/Suits (`♠♡♢♣♤♥♦♧`), Musical Notes (`♩♪♫♬`), Warning (`⚠`)
    - `U+2713-2716`, `U+2776-2793`: Check/Cross marks (`✓✔✕✖`), Negative Circled Numbers (`❶-❿`)
    """
    return (
        cp == 0x2103
        or (0x2160 <= cp <= 0x216B)
        or (0x2190 <= cp <= 0x21FF)
        or cp in (0x221E, 0x2252, 0x2260, 0x2266, 0x2267)
        or (0x2460 <= cp <= 0x2473)
        or cp == 0x24EA
        or (0x25A0 <= cp <= 0x266F)
        or cp == 0x26A0
        or (0x2713 <= cp <= 0x2716)
        or (0x2776 <= cp <= 0x2793)
    )


def detect_font_locale(codepoints: CodepointSet, family_name: str = "") -> str:
    """
    Detect the optimal CJK slicing profile (`"jp"`, `"kr"`, `"sc"`, `"tc"`, or `"hk"`)
    from an optional `family_name` and the font's `codepoints` (`cmap`).
    Defaults to `"jp"` when ambiguous or for Japanese/Western charsets.
    """
    if family_name:
        lower_name = family_name.lower()
        tokens = set(re.findall(r"[a-z0-9]+", lower_name))
        if tokens & {"jp", "jpn", "japanese", "kana", "mincho", "kaku", "maru"}:
            return "jp"
        if tokens & {
            "kr",
            "kor",
            "korean",
            "hangul",
            "batang",
            "dotum",
            "dodum",
            "gulim",
            "malgun",
            "nanum",
            "myeongjo",
        }:
            return "kr"
        if tokens & {"sc", "chs", "cn", "zhs", "hans", "simplified"}:
            return "sc"
        if (tokens & {"hk", "hkg", "hongkong", "cantonese", "yue"}) or ("hong kong" in lower_name):
            return "hk"
        if tokens & {"tc", "cht", "tw", "zht", "hant", "traditional"}:
            return "tc"

    cps = codepoints if isinstance(codepoints, (set, frozenset)) else set(codepoints)
    if not cps:
        return "jp"

    hangul_syl_count = 0
    hangul_jamo_count = 0
    kana_count = 0
    bopomofo_count = 0
    cjk_count = 0
    sip_count = 0
    for cp in cps:
        if 0xAC00 <= cp <= 0xD7A3:
            hangul_syl_count += 1
        elif (0x1100 <= cp <= 0x11FF) or (0x3131 <= cp <= 0x318E):
            hangul_jamo_count += 1
        elif (0x3041 <= cp <= 0x3096) or (0x30A1 <= cp <= 0x30FA):
            kana_count += 1
        elif (0x3105 <= cp <= 0x312F) or (0x31A0 <= cp <= 0x31BF):
            bopomofo_count += 1
        elif is_cjk_ideograph(cp):
            cjk_count += 1
            if 0x20000 <= cp <= 0x2FA1F:
                sip_count += 1

    if hangul_syl_count >= 1000 or (
        (hangul_syl_count + hangul_jamo_count) > 0
        and hangul_syl_count >= cjk_count
        and hangul_syl_count > kana_count
    ):
        return "kr"

    sc_only_hits = len(cps & _SC_ONLY_FREQ)
    tc_only_hits = len(cps & _TC_ONLY_FREQ)
    jp_only_hits = len(cps & _JP_ONLY_FREQ)
    hk_extra_hits = len(cps & _HK_EXTRA_CPS)

    sc_ratio = sc_only_hits / len(_SC_ONLY_FREQ)
    tc_ratio = tc_only_hits / len(_TC_ONLY_FREQ)
    jp_ratio = jp_only_hits / len(_JP_ONLY_FREQ)

    if sc_only_hits >= 30 or tc_only_hits >= 30 or jp_only_hits >= 30:
        if jp_ratio >= sc_ratio and jp_ratio >= tc_ratio and jp_only_hits >= 30:
            return "jp"
        if sc_ratio > tc_ratio * 1.2 and sc_ratio > jp_ratio * 1.2:
            return "sc"
        if tc_ratio >= sc_ratio and tc_ratio > jp_ratio * 1.2:
            return "hk" if (hk_extra_hits >= 6 or sip_count >= 800) else "tc"
        if sc_ratio > tc_ratio and sc_ratio > jp_ratio:
            return "sc"

    if hk_extra_hits > 0 and hk_extra_hits >= jp_only_hits and kana_count == 0:
        return "hk"
    if bopomofo_count > 0 and kana_count == 0 and tc_only_hits >= sc_only_hits:
        return "tc"
    if sc_only_hits > tc_only_hits and sc_only_hits > jp_only_hits and kana_count == 0:
        return "sc"
    if tc_only_hits > sc_only_hits and tc_only_hits > jp_only_hits and kana_count == 0:
        return "tc"

    return "jp"


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
