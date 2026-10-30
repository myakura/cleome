"""
Tests for dynamic 4-tier slice planning, CSS Cascade Shadowing invariants,
SIP/TIP plane separation, and CJK locale (`jp`, `kr`, `sc`, `tc`, `hk`) profiles.
"""

import copy
import io
import sys
import tempfile
import unittest
from pathlib import Path

import uharfbuzz as hb
from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.ttLib import TTFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from helpers import (
    assert_generic_cascade_invariants,
    assert_jp_plan_invariants,
    build_synthetic_charset,
    create_synthetic_ttfont,
)

from cleome import (
    FontMetadata,
    SliceConfig,
    build_slice_plan,
    detect_font_locale,
    parse_unicode_range,
    slice_font,
)
from cleome.data.kanji_freq import KANJI_FREQ_RANK
from cleome.data.kr_freq import KR_HANGUL_FREQ_CODEPOINTS
from cleome.data.sc_freq import SC_HANZI_FREQ_CODEPOINTS
from cleome.data.tc_freq import (
    HK_COLOCATED_CANTONESE_CHARS,
    HK_HANZI_FREQ_CODEPOINTS,
    TC_HANZI_FREQ_CODEPOINTS,
)
from cleome.locale import _SC_ONLY_FREQ
from cleome.slicer import _prepare_font_bundle


class TestSlicerAndLocales(unittest.TestCase):
    def test_cascade_shadowing_invariant_small_and_full_charsets(self) -> None:
        """
        Verify that for both an ~8,300-char subset and a ~16,700-char full CJK charset,
        every mapped codepoint resolves via CSS cascade to the exact slice whose
        binary contains that codepoint, and Tier 4 slices use 1-token hex ranges.
        """
        self.assertIsInstance(KANJI_FREQ_RANK, frozendict)
        full_cps = build_synthetic_charset(full=True)
        sub_cps = build_synthetic_charset(full=False)

        plan_full = build_slice_plan(full_cps)
        assert_jp_plan_invariants(self, plan_full, full_cps)

        plan_sub = build_slice_plan(sub_cps)
        assert_jp_plan_invariants(self, plan_sub, sub_cps)

        # Smaller font must dynamically produce fewer Tier 4 rare slices than full font
        rare_full = [s for s in plan_full if s.tier == 4]
        rare_sub = [s for s in plan_sub if s.tier == 4]
        self.assertLess(len(rare_sub), len(rare_full))

    def test_sip_and_tip_planes_never_bridge_in_tier4(self) -> None:
        """
        Verify that Rare Kanji in Plane 2 (SIP, U+20000..2FA1F) and Plane 3 (TIP, U+30000..3347F)
        are placed in separate Tier 4 slices so no 1-token hex range spans across planes.
        """
        cps = {ord("A"), ord("あ"), 0x20001, 0x2FA1D, 0x30000, 0x323B0}
        plan = build_slice_plan(cps)
        assert_jp_plan_invariants(self, plan, frozenset(cps))
        rare_slices = [s for s in plan if s.tier == 4]
        self.assertEqual(len(rare_slices), 2)
        for s in rare_slices:
            ur_cps = parse_unicode_range(s.css_unicode_range)
            has_sip = any(0x20000 <= c <= 0x2FA1F for c in ur_cps)
            has_tip = any(0x30000 <= c <= 0x3347F for c in ur_cps)
            self.assertFalse(
                has_sip and has_tip,
                f"Slice {s.slice_id} bridged SIP and TIP: {s.css_unicode_range}",
            )

    def test_cjk_locale_auto_detection_and_validation(self) -> None:
        """
        Verify `SliceConfig(locale=...)` normalization and validation, and `detect_font_locale`
        across family names and character sets for `jp`, `kr`, `sc`, `tc`, and `hk`.
        """
        self.assertEqual(SliceConfig(locale="KR").locale, "kr")
        self.assertEqual(SliceConfig(locale=" Auto ").locale, "auto")
        with self.assertRaisesRegex(ValueError, "Unsupported locale"):
            SliceConfig(locale="fr")
        with self.assertRaisesRegex(ValueError, "slices_subdir cannot be empty"):
            SliceConfig(slices_subdir="   ")
        with self.assertRaisesRegex(ValueError, "font_display cannot be empty"):
            SliceConfig(font_display="")
        with self.assertRaisesRegex(ValueError, "Invalid CSS font-display value"):
            SliceConfig(font_display="not-a-display-keyword")

        # 1. Family name detection
        self.assertEqual(detect_font_locale({ord("A")}, "Noto Sans KR"), "kr")
        self.assertEqual(detect_font_locale({ord("A")}, "IBM Plex Sans KR"), "kr")
        self.assertEqual(detect_font_locale({ord("A")}, "Nanum Gothic"), "kr")
        self.assertEqual(detect_font_locale({ord("A")}, "Noto Sans SC"), "sc")
        self.assertEqual(detect_font_locale({ord("A")}, "Noto Sans TC"), "tc")
        self.assertEqual(detect_font_locale({ord("A")}, "Chiron Hei HK"), "hk")
        self.assertEqual(detect_font_locale({ord("A")}, "GenSou Sans JP"), "jp")

        # 2. Charset-based detection (when family_name is empty or generic, including GB2312/Big5/KSX1001 Kana)
        kana_block = set(range(0x3041, 0x3094)) | set(range(0x30A1, 0x30F7))
        kr_cps = set(KR_HANGUL_FREQ_CODEPOINTS[:1500]) | set(range(0x0020, 0x007F))
        self.assertEqual(detect_font_locale(kr_cps, "Custom Display"), "kr")
        self.assertEqual(
            detect_font_locale(
                set(KR_HANGUL_FREQ_CODEPOINTS[:500]) | kana_block, "Custom Display"
            ),
            "kr",
        )

        sc_cps = set(SC_HANZI_FREQ_CODEPOINTS) | set(range(0x0020, 0x007F))
        self.assertEqual(detect_font_locale(sc_cps, "Custom Sans"), "sc")
        self.assertEqual(
            detect_font_locale(
                set(SC_HANZI_FREQ_CODEPOINTS[:2000]) | kana_block, "Custom Sans"
            ),
            "sc",
        )
        self.assertEqual(
            detect_font_locale(
                set(SC_HANZI_FREQ_CODEPOINTS[:-20])
                | set(TC_HANZI_FREQ_CODEPOINTS[:400])
                | kana_block,
                "Custom Sans",
            ),
            "sc",
        )

        tc_cps = set(TC_HANZI_FREQ_CODEPOINTS) | set(range(0x3105, 0x312A))
        self.assertEqual(detect_font_locale(tc_cps, "Custom Serif"), "tc")
        self.assertEqual(
            detect_font_locale(
                set(TC_HANZI_FREQ_CODEPOINTS[:2000]) | kana_block, "Custom Serif"
            ),
            "tc",
        )

        hk_cps = (
            set(HK_HANZI_FREQ_CODEPOINTS)
            | set(range(0x3105, 0x312A))
            | set(range(0x20000, 0x20400))
        )
        self.assertEqual(detect_font_locale(hk_cps, "Custom Hei"), "hk")
        self.assertEqual(detect_font_locale(hk_cps | kana_block, "Custom Hei"), "hk")

        jp_cps = build_synthetic_charset(full=False)
        self.assertEqual(detect_font_locale(jp_cps, "Custom Gothic"), "jp")
        self.assertEqual(
            detect_font_locale(build_synthetic_charset(full=True), "Custom Gothic"),
            "jp",
        )

    def test_prepare_font_bundle_detects_locale_from_union_codepoints(self) -> None:
        """
        Issue 32 regression test: when bundling variants with differing codepoints under
        `locale="auto"`, `_prepare_font_bundle` detects the locale from the union of all
        variants' codepoints so `resolved_locale`, per-variant slice plans, and `union_plan`
        are independent of variant order.
        """
        sc_cps = frozenset(sorted(_SC_ONLY_FREQ)[:50] + [0x0041, 0x3001, 0x3002])
        ascii_cps = frozenset(range(0x0020, 0x007F)) | frozenset({0x3001, 0x3002})

        m_sc = FontMetadata(
            family_name="Custom Sans",
            slug="custom-sans",
            is_variable=False,
            weight_css="400",
            stretch_css="100%",
            style_css="normal",
            styles_css=("normal",),
            codepoints=sc_cps,
        )
        m_latin = FontMetadata(
            family_name="Custom Sans",
            slug="custom-sans",
            is_variable=False,
            weight_css="400",
            stretch_css="100%",
            style_css="italic",
            styles_css=("italic",),
            codepoints=ascii_cps,
        )

        b1 = _prepare_font_bundle([m_sc, m_latin], SliceConfig(locale="auto"))
        b2 = _prepare_font_bundle([m_latin, m_sc], SliceConfig(locale="auto"))

        self.assertEqual(b1.resolved_locale, "sc")
        self.assertEqual(b2.resolved_locale, "sc")
        self.assertEqual(
            [s.slice_id for s in b1.variant_plans[0]],
            ["hanzi-freq-01", "cjk-core", "latin"],
        )
        self.assertEqual(
            [s.slice_id for s in b1.variant_plans[1]], ["cjk-core", "latin"]
        )
        self.assertEqual(b2.variant_plans, [b1.variant_plans[1], b1.variant_plans[0]])
        self.assertEqual(b1.union_plan, b2.union_plan)
        self.assertEqual(
            [s.slice_id for s in b2.union_plan], ["hanzi-freq-01", "cjk-core", "latin"]
        )

    def test_korean_profile_cascade_shadowing_and_jamo_colocation(self) -> None:
        """
        Verify the Korean (`kr`) 4-tier profile:
        1. Co-locates CJK punctuation (`《》`, `「」`), Compatibility Jamo (`ㅋㅋㅋ`, `ㅎㅎ`, `ㅜㅜ`),
           and Conjoining Jamo (`U+1100-11FF`, `U+A960-A97F`, `U+D7B0-D7FF`) in `hangul-core`.
        2. Places all 2,350 KS X 1001 common Hangul syllables in Tier 3 (`hangul-freq-*`).
        3. Uses 1-token hex ranges for Tier 4 `hangul-rare-*` and `hanja-rare-*` with 100% Cascade Shadowing.
        4. Preserves OpenType `GSUB` `ccmp` Conjoining Jamo composition in `hangul-core` `.woff2`.
        """
        ksx_only_cps: set[int] = set(range(0x0020, 0x007F))
        ksx_only_cps.update(range(0x3000, 0x3040))  # 《》, 「」, 『』, 【】
        ksx_only_cps.update(range(0x3131, 0x3164))  # Compatibility Jamo ㄱ..ㅎ, ㅏ..ㅣ
        ksx_only_cps.update(
            (0x1100, 0x1161, 0xA960, 0xD7B0)
        )  # Conjoining Jamo + Ext-A/B
        ksx_only_cps.update(range(0x2190, 0x21A0))  # UI arrows
        ksx_only_cps.update(KR_HANGUL_FREQ_CODEPOINTS)

        # KS X 1001 display font (no rare Hangul, no Hanja) -> zero Tier 4 slices
        plan_ksx = build_slice_plan(frozenset(ksx_only_cps), SliceConfig(locale="kr"))
        assert_generic_cascade_invariants(self, plan_ksx, frozenset(ksx_only_cps))
        self.assertEqual([s for s in plan_ksx if s.tier == 4], [])

        hangul_core = next(s for s in plan_ksx if s.slice_id == "hangul-core")
        for ch in "《》「」『』【】ㅋㅎㅜㅠ\u1100\u1161\ua960\ud7b0":
            self.assertIn(
                ord(ch),
                hangul_core.binary_codepoints,
                f"{ch!r} missing from hangul-core",
            )

        # Full Korean font with rare Hangul syllables and Hanja
        full_kr_cps = set(ksx_only_cps)
        full_kr_cps.update(
            range(0xAC00, 0xB800)
        )  # Includes rare non-KS X 1001 syllables
        full_kr_cps.update(range(0x4E00, 0x5100))  # Rare Hanja
        plan_full_kr = build_slice_plan(
            frozenset(full_kr_cps),
            SliceConfig(locale="auto"),
            family_name="Noto Sans KR",
        )
        assert_generic_cascade_invariants(self, plan_full_kr, frozenset(full_kr_cps))
        self.assertTrue(any(s.category == "hanja-rare" for s in plan_full_kr))
        self.assertTrue(any(s.category == "hangul-rare" for s in plan_full_kr))
        self.assertTrue(any(s.category == "hangul-freq" for s in plan_full_kr))

        # Verify Conjoining Jamo GSUB ccmp shaping survives WOFF2 subsetting in hangul-core
        jamo_cps = [
            ord("A"),
            0x1100,
            0x1161,
            ord("ㅋ"),
            ord("《"),
            ord("》"),
            ord("가"),
        ]
        tt_kr = create_synthetic_ttfont(jamo_cps, family_name="Test Sans KR")
        tt_kr.setGlyphOrder(tt_kr.getGlyphOrder() + ["jamo_ga_comp"])
        tt_kr["glyf"]["jamo_ga_comp"] = tt_kr["glyf"]["uni0041"]
        tt_kr["hmtx"]["jamo_ga_comp"] = (920, 100)
        addOpenTypeFeaturesFromString(
            tt_kr,
            """
            languagesystem DFLT dflt;
            languagesystem hang dflt;
            feature ccmp {
                sub uni1100 uni1161 by jamo_ga_comp;
            } ccmp;
            """,
        )

        with tempfile.TemporaryDirectory() as tmp:
            res_kr = slice_font(tt_kr, out_dir=Path(tmp) / "kr_out", workers=1)
            hc_bf = next(
                bf for bf in res_kr.built_files if bf.spec.slice_id == "hangul-core"
            )
            with TTFont(hc_bf.path) as hc_tt:
                cmap_hc = hc_tt.getBestCmap()
                gsub_hc = hc_tt["GSUB"].table
                self.assertEqual(len(gsub_hc.LookupList.Lookup), 1)
                lig_hc = (
                    gsub_hc.LookupList.Lookup[0]
                    .SubTable[0]
                    .ligatures[cmap_hc[0x1100]][0]
                )
                self.assertEqual(lig_hc.Component, [cmap_hc[0x1161]])

                buf_io = io.BytesIO()
                c = copy.deepcopy(hc_tt)
                c.flavor = None
                c.save(buf_io, reorderTables=False)
                order = c.getGlyphOrder()
                face = hb.Face(hb.Blob(buf_io.getvalue()))
                hb_font = hb.Font(face)
                hb_buf = hb.Buffer()
                hb_buf.add_str("\u1100\u1161")
                hb_buf.guess_segment_properties()
                hb.shape(hb_font, hb_buf)
                shaped = [
                    (order[info.codepoint], pos.x_advance)
                    for info, pos in zip(hb_buf.glyph_infos, hb_buf.glyph_positions)
                ]
                self.assertEqual(shaped, [(lig_hc.LigGlyph, 920)])

    def test_chinese_sc_tc_hk_profiles_and_bopomofo_tone_mark_shaping(self) -> None:
        """
        Verify Simplified Chinese (`sc`), Traditional Chinese (`tc`), and Hong Kong (`hk`) profiles:
        1. Co-locates paired CJK brackets (`〈〉`, `『』`, `「」`, `《》`) and Bopomofo (`ㄅ-ㄩ`) +
           Mandarin tone marks (`U+02C7`, `U+02C9-02CB`, `U+02D9`) in `cjk-core`, without `latin`
           shadowing the tone marks.
        2. Places 100% of GB2312 Level 1 Hanzi in `sc` Tier 3 (`hanzi-freq-*`) and Cantonese HKSCS
           characters (`嘅`, `啲`, `𨋢` `U+282E2`) in `hk` Tier 3 (`hanzi-freq-*`).
        3. Preserves Bopomofo + tone mark `GSUB`/`GPOS` shaping in `cjk-core` `.woff2`.
        """
        base_cjk_cps: set[int] = set(
            range(0x0020, 0x0250)
        )  # ASCII + Latin-Ext surrounding tone marks
        base_cjk_cps.update(range(0x0300, 0x0330))  # Combining diacritics in latin
        base_cjk_cps.update((0x1D3A, 0x207F))  # Pe̍h-ōe-jī superscripts ᴺ, ⁿ
        base_cjk_cps.update(
            (0x02C7, 0x02C9, 0x02CA, 0x02CB, 0x02D9)
        )  # Bopomofo tone marks ˇ ˉ ˊ ˋ ˙
        base_cjk_cps.update(
            (0x2103, 0x2160, 0x216B, 0x221E, 0x2252, 0x2260, 0x2266, 0x2267)
        )  # ℃, Ⅰ, Ⅻ, ∞, ≒, ≠, ≦, ≧
        base_cjk_cps.update(range(0x3000, 0x3040))  # 〈〉, 『』, 「」, 《》
        base_cjk_cps.update(range(0x3105, 0x312A))  # Bopomofo ㄅ..ㄩ
        base_cjk_cps.update(range(0x4E00, 0x5300))  # BMP Hanzi (mix of freq & rare)
        base_cjk_cps.update(
            (0x3400, 0x3410, 0x20001, 0x282E2, 0x30001)
        )  # Ext-A, SIP (𨋢), TIP

        for loc, freq_sample in (
            ("sc", SC_HANZI_FREQ_CODEPOINTS[:600]),
            ("tc", TC_HANZI_FREQ_CODEPOINTS),
            ("hk", HK_HANZI_FREQ_CODEPOINTS),
        ):
            cps = frozenset(base_cjk_cps | set(freq_sample))
            plan = build_slice_plan(cps, SliceConfig(locale=loc))
            assert_generic_cascade_invariants(self, plan, cps)

            cjk_core = next(s for s in plan if s.slice_id == "cjk-core")
            latin_slice = next(s for s in plan if s.slice_id == "latin")
            sym_ui_slice = next(s for s in plan if s.slice_id == "symbols-ui")
            for ch in "ᴺⁿ":
                self.assertIn(
                    ord(ch),
                    latin_slice.binary_codepoints,
                    f"POJ {ch!r} missing from latin",
                )
            for ch in "℃ⅠⅫ∞≒≠≦≧":
                self.assertIn(
                    ord(ch),
                    sym_ui_slice.binary_codepoints,
                    f"UI symbol {ch!r} missing from symbols-ui",
                )
            for ch in "〈〉『』「」《》ㄅㄆㄇㄈˇˉˊˋ˙":
                self.assertIn(
                    ord(ch),
                    cjk_core.binary_codepoints,
                    f"{ch!r} missing from cjk-core ({loc})",
                )
                self.assertNotIn(ord(ch), latin_slice.binary_codepoints)
                self.assertNotIn(
                    ord(ch),
                    parse_unicode_range(latin_slice.css_unicode_range),
                    f"latin css_unicode_range must not shadow {ch!r} in cjk-core ({loc})",
                )

            if loc in ("tc", "hk"):
                s1 = next(s for s in plan if s.slice_id == "hanzi-freq-01")
                self.assertIn(ord("點"), s1.binary_codepoints)
                self.assertNotIn(ord("点"), s1.binary_codepoints)
                tail_tier3 = {
                    *next(
                        s for s in plan if s.slice_id == f"hanzi-freq-{n:02d}"
                    ).binary_codepoints
                    for n in (45, 46, 47, 48)
                }
                for simp_ch in "点会简欢国时来":
                    self.assertIn(
                        ord(simp_ch),
                        tail_tier3,
                        f"Simplified {simp_ch!r} should be in TC/HK Tier 3 tail",
                    )

            if loc == "hk":
                s13 = next(s for s in plan if s.slice_id == "hanzi-freq-13")
                for ch in HK_COLOCATED_CANTONESE_CHARS:
                    self.assertIn(
                        ord(ch),
                        s13.binary_codepoints,
                        f"Cantonese {ch!r} must be co-located in HK hanzi-freq-13",
                    )

        # Verify Bopomofo + tone mark (ㄅ + ˇ U+02C7) shaping in cjk-core WOFF2 slice
        bopo_cps = [
            ord("A"),
            0x024F,
            0x0300,
            ord("ㄅ"),
            0x02C7,
            ord("『"),
            ord("』"),
            ord("語"),
        ]
        tt_tc = create_synthetic_ttfont(bopo_cps, family_name="Test Sans TC")
        tt_tc.setGlyphOrder(tt_tc.getGlyphOrder() + ["bopo_ba_caron"])
        tt_tc["glyf"]["bopo_ba_caron"] = tt_tc["glyf"]["uni0041"]
        tt_tc["hmtx"]["bopo_ba_caron"] = (875, 100)
        addOpenTypeFeaturesFromString(
            tt_tc,
            """
            languagesystem DFLT dflt;
            languagesystem bopo dflt;
            feature ccmp {
                sub uni3105 uni02C7 by bopo_ba_caron;
            } ccmp;
            """,
        )

        with tempfile.TemporaryDirectory() as tmp:
            res_tc = slice_font(tt_tc, out_dir=Path(tmp) / "tc_out", workers=1)
            cc_bf = next(
                bf for bf in res_tc.built_files if bf.spec.slice_id == "cjk-core"
            )
            with TTFont(cc_bf.path) as cc_tt:
                cmap_cc = cc_tt.getBestCmap()
                gsub_cc = cc_tt["GSUB"].table
                self.assertEqual(len(gsub_cc.LookupList.Lookup), 1)
                lig_cc = (
                    gsub_cc.LookupList.Lookup[0]
                    .SubTable[0]
                    .ligatures[cmap_cc[ord("ㄅ")]][0]
                )
                self.assertEqual(lig_cc.Component, [cmap_cc[0x02C7]])

                buf_io = io.BytesIO()
                c = copy.deepcopy(cc_tt)
                c.flavor = None
                c.save(buf_io, reorderTables=False)
                order = c.getGlyphOrder()
                face = hb.Face(hb.Blob(buf_io.getvalue()))
                hb_font = hb.Font(face)
                hb_buf = hb.Buffer()
                hb_buf.add_str("ㄅ\u02c7")
                hb_buf.guess_segment_properties()
                hb.shape(hb_font, hb_buf)
                shaped = [
                    (order[info.codepoint], pos.x_advance)
                    for info, pos in zip(hb_buf.glyph_infos, hb_buf.glyph_positions)
                ]
                self.assertEqual(shaped, [(lig_cc.LigGlyph, 875)])


if __name__ == "__main__":
    unittest.main()
