"""
Unicode character classification predicates and CJK locale auto-detection
(`"jp"`, `"kr"`, `"sc"`, `"tc"`, `"hk"`) for Cleome.
"""

import re

from cleome.data.kanji_freq import KANJI_FREQ_RANK
from cleome.data.sc_freq import SC_HANZI_FREQ_RANK
from cleome.data.tc_freq import HK_EXTRA_CANTONESE_CHARS, TC_HANZI_FREQ_RANK

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
_BOPOMOFO_TONE_MARKS: frozenset[int] = frozenset(
    {0x02C7, 0x02C9, 0x02CA, 0x02CB, 0x02D9}
)

CJK_IDEOGRAPH_REGIONS: tuple[tuple[str, int, int], ...] = (
    ("Ext-A", 0x3400, 0x4DBF),
    ("BMP", 0x4E00, 0x9FFF),
    ("Compat", 0xF900, 0xFAFF),
    ("SIP", 0x20000, 0x2FA1F),  # Ext B..F, Ext I (U+2EBF0..2EE5F), Compat Supplement
    ("TIP", 0x30000, 0x3347F),  # Ext G, Ext H, Ext J (U+323B0..3347F, Unicode 17.0)
)


def is_cjk_ideograph(cp: int) -> bool:
    """Return True if `cp` is a CJK Unified or Compatibility Ideograph (Kanji / Hanzi / Hanja)."""
    return any(lo <= cp <= hi for _, lo, hi in CJK_IDEOGRAPH_REGIONS)


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
        if (tokens & {"hk", "hkg", "hongkong", "cantonese", "yue"}) or (
            "hong kong" in lower_name
        ):
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
