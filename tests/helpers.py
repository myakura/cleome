"""
Shared synthetic font builders and CSS Cascade Shadowing invariant assertions for the Cleome test suite.
"""

import sys
import unittest
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
from fontTools.ttLib.tables.TupleVariation import TupleVariation

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from cleome import SliceSpec, parse_unicode_range
from cleome.data.kanji_freq import KANJI_FREQ_CODEPOINTS


def build_synthetic_charset(*, full: bool) -> frozenset[int]:
    """Build a realistic Japanese font codepoint set (either ~8,300 subset or ~16,700 full)."""
    cps: set[int] = set()
    # Core Latin (`U+0020..007E`, `U+00A0..024F`, `U+0300..036F`, `U+2000..206F`, `U+20A0..20CF`, `ℓ`, `™`)
    cps.update(range(0x0020, 0x007F))
    cps.update(range(0x00A0, 0x0250))
    cps.update(range(0x0300, 0x0370))
    cps.update(range(0x2000, 0x2070))
    cps.update(range(0x20A0, 0x20D0))
    cps.update((0x2113, 0x2122))
    # Core Kana (`U+3000..30FF`, `U+31F0..31FF`, `U+FF01..FF60`, `U+FFE0..FFE6`)
    cps.update(range(0x3000, 0x3100))
    cps.update(range(0x31F0, 0x3200))
    cps.update(range(0xFF01, 0xFF61))
    cps.update(range(0xFFE0, 0xFFE7))
    # UI Symbols (`U+2190..21FF`, `U+2460..2473`, `U+24EA`, `U+25A0..266F`, `U+26A0`, `U+2713..2714`, `U+2776..277F`)
    cps.update(range(0x2190, 0x2200))
    cps.update(range(0x2460, 0x2474))
    cps.add(0x24EA)
    cps.update(range(0x25A0, 0x2670))
    cps.add(0x26A0)
    cps.update((0x2713, 0x2714))
    cps.update(range(0x2776, 0x2780))
    # All Tier 3 frequent Kanji (3,169 chars including 2,136 Joyo, JIS Level 1, 𠮷, etc.)
    cps |= set(KANJI_FREQ_CODEPOINTS)

    if full:
        # Extended symbols (~2,100 chars across Greek, Cyrillic, Box Drawing, Enclosed CJK, etc.)
        cps.update(range(0x0391, 0x0450))
        cps.update(range(0x2500, 0x2580))
        cps.update(range(0x3200, 0x33FF))
        cps.update(range(0x1F100, 0x1F260))
        # ~10,500 Rare & Extended Kanji (JIS Level 2-4, CJK Ext A/B)
        cps.update(range(0x3400, 0x3800))
        cps.update(range(0x4E00, 0x7800))
        cps.update(range(0x20000, 0x20200))
    else:
        # Smaller subset (~8,300 chars total): ~900 extended symbols + ~3,500 JIS Level 2 rare Kanji
        cps.update(range(0x0391, 0x03D0))
        cps.update(range(0x2500, 0x2550))
        cps.update(range(0x3220, 0x3360))
        cps.update(range(0x4E00, 0x6200))

    return frozenset(cps)


def create_synthetic_ttfont(
    codepoints: list[int],
    *,
    family_name: str = "Test Sans JP",
    weight: int = 400,
    width_class: int = 5,
    is_variable: bool = False,
    uvs_only_cp: tuple[int, int] | None = None,
) -> TTFont:
    """
    Create an in-memory TrueType font with optional `wght` + `wdth` variable axes
    and optional UVS Format 14 sequence for testing missing base cmap auto-patching.
    """
    glyph_order = [".notdef"] + [f"uni{cp:04X}" for cp in codepoints]
    cmap = {cp: f"uni{cp:04X}" for cp in codepoints}

    if uvs_only_cp is not None:
        uvs_cp, _ = uvs_only_cp
        uvs_gname = f"uni{uvs_cp:04X}_uvs"
        glyph_order.append(uvs_gname)

    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(cmap)

    pen = TTGlyphPen(None)
    pen.moveTo((100, 0))
    pen.lineTo((100, 700))
    pen.lineTo((800, 700))
    pen.lineTo((800, 0))
    pen.closePath()
    base_glyph = pen.glyph()

    empty_pen = TTGlyphPen(None)
    glyf_map = {g: (empty_pen.glyph() if g == ".notdef" else base_glyph) for g in glyph_order}
    fb.setupGlyf(glyf_map)

    metrics = {g: (1000, 100) for g in glyph_order}
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=880, descent=-120)
    fb.setupNameTable(
        {
            "familyName": family_name,
            "styleName": "Regular" if weight == 400 else "Bold",
            "licenseDescription": "SIL Open Font License 1.1",
            "licenseInfoURL": "https://openfontlicense.org",
        }
    )
    fb.setupOS2(sTypoAscender=880, sTypoDescender=-120, usWeightClass=weight, usWidthClass=width_class)
    fb.setupPost()

    if is_variable:
        axes = [
            ("wght", 250, 400, 900, "Weight"),
            ("wdth", 70, 100, 100, "Width"),
        ]
        fb.setupFvar(axes, [])
        variations = {
            g: [
                TupleVariation(
                    {"wght": (0.0, 1.0, 1.0)},
                    [(0, 0), (0, 10), (10, 10), (10, 0), None, None, None, None],
                )
            ]
            if g != ".notdef"
            else []
            for g in glyph_order
        }
        fb.setupGvar(variations)

    tt = fb.font
    tt["head"].modified = 3700000000  # Fixed deterministic timestamp

    if uvs_only_cp is not None:
        uvs_cp, uvs_sel = uvs_only_cp
        uvs_gname = f"uni{uvs_cp:04X}_uvs"
        sub14 = CmapSubtable.newSubtable(14)
        sub14.platformID = 0
        sub14.platEncID = 5
        sub14.language = 0
        sub14.cmap = {}
        sub14.uvsDict = {uvs_sel: [(uvs_cp, uvs_gname)]}
        tt["cmap"].tables.append(sub14)

    return tt


def assert_generic_cascade_invariants(
    tc: unittest.TestCase,
    plan: list[SliceSpec],
    codepoints: frozenset[int],
) -> None:
    """Verify partition completeness, 1-token Tier 4 hex ranges, and 100% CSS Cascade Shadowing resolution."""
    union_bin: set[int] = set()
    for spec in plan:
        tc.assertTrue(spec.binary_codepoints, f"Empty slice {spec.slice_id}")
        overlap = union_bin & spec.binary_codepoints
        tc.assertFalse(overlap, f"Duplicate binary codepoints in {spec.slice_id}")
        union_bin |= spec.binary_codepoints
        if spec.tier == 4:
            tc.assertNotIn(
                ",",
                spec.css_unicode_range,
                f"Tier 4 slice {spec.slice_id} must be a 1-token range, got {spec.css_unicode_range}",
            )
    tc.assertEqual(union_bin, set(codepoints))

    resolved: dict[int, SliceSpec] = {}
    for spec in plan:
        ur_set = parse_unicode_range(spec.css_unicode_range)
        for cp in ur_set & codepoints:
            resolved[cp] = spec

    for cp in codepoints:
        winner = resolved.get(cp)
        tc.assertIsNotNone(winner, f"Codepoint U+{cp:04X} not matched by any CSS unicode-range")
        assert winner is not None
        tc.assertIn(
            cp,
            winner.binary_codepoints,
            f"CSS Cascade Shadowing mismatch for U+{cp:04X} ({chr(cp)}): "
            f"CSS matched {winner.slice_id}, but binary does not contain it!",
        )


def assert_jp_plan_invariants(
    tc: unittest.TestCase,
    plan: list[SliceSpec],
    codepoints: frozenset[int],
) -> None:
    """Verify Japanese 4-tier slice invariants including Kana Core, Latin, UI symbols, and Joyo Kanji."""
    assert_generic_cascade_invariants(tc, plan, codepoints)

    # Check Kana Core zero-omission guarantee (`む`, `モ`, `ヶ`, `ヵ`, `ゐ`, `ヱ`, `ゔ`, `「`, `」`, `０`, `\u3099`, `\u309A`)
    kana_slice = next((s for s in plan if s.slice_id == "kana-core"), None)
    for ch in "あむモヶヵゐヱゔ\u3099\u309A、。「」【】０９ＡＺㇰㇿ":
        cp = ord(ch)
        if cp in codepoints:
            tc.assertIsNotNone(kana_slice)
            assert kana_slice is not None
            tc.assertIn(cp, kana_slice.binary_codepoints, f"'{ch}' missing from kana-core")

    # Check Latin & UI Symbols placement (including Combining Diacritical Marks U+0300..036F)
    latin_slice = next((s for s in plan if s.slice_id == "latin"), None)
    for ch in "A0€₩™ℓĀāĪīŪūĒēŌō\u0300\u0301\u0302\u0303\u0304\u0308":
        cp = ord(ch)
        if cp in codepoints:
            tc.assertIsNotNone(latin_slice)
            assert latin_slice is not None
            tc.assertIn(cp, latin_slice.binary_codepoints, f"'{ch}' missing from latin")

    sym_ui_slice = next((s for s in plan if s.slice_id == "symbols-ui"), None)
    for ch in "→▶◀✓✔⚠♡♥★♪❶❿①⑳":
        cp = ord(ch)
        if cp in codepoints:
            tc.assertIsNotNone(sym_ui_slice)
            assert sym_ui_slice is not None
            tc.assertIn(cp, sym_ui_slice.binary_codepoints, f"'{ch}' missing from symbols-ui")

    # Check that all 2010 Joyo Kanji (including JIS Level 3 𠮟塡剝頰 & 唾) + 𠮷 reside in Tier 3 (kanji-freq)
    tier3_cps = {*s.binary_codepoints for s in plan if s.tier == 3}
    for ch in "唾𠮟塡剝頰𠮷鷗噓繫﨑髙楷諧毀憬錮傲恣嘲喩慄":
        cp = ord(ch)
        if cp in codepoints:
            tc.assertIn(cp, tier3_cps, f"'{ch}' (U+{cp:04X}) should be in Tier 3 kanji-freq")
