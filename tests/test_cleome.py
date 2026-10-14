"""
Comprehensive test suite for cleome:
- Dynamic 4-tier slice planning & CSS Cascade Shadowing invariant across variable character sets
- Zero-omission guarantee for Kana (`む`, `モ`, `ヶ`, `ヵ`, `ゐ`, `ヱ`, `ゔ`) and CJK punctuation
- 1-token hex range verification for Tier 4 rare Kanji
- UVS Format 14 missing base cmap auto-patching (e.g. U+553E `唾`)
- Variable Font (`wght` x `wdth`) and Multi-file Static Font WOFF2 slicing and CSS generation
"""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from fontTools.ttLib import TTFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from cleome import (
    SliceConfig,
    build_slice_plan,
    extract_font_metadata,
    parse_unicode_range,
    slice_font,
    slice_fonts,
)


def resolve_winning_slice(plan, cp: int):
    """
    Simulate CSS `@font-face` cascade resolution:
    Later rules in CSS override earlier rules when `unicode-range` overlaps.
    """
    winner = None
    for spec in plan:
        if cp in parse_unicode_range(spec.css_unicode_range):
            winner = spec
    return winner


class TestCleomeSlicer(unittest.TestCase):
    def test_cascade_shadowing_invariant_small_and_full_charsets(self) -> None:
        """
        Verify that for both an 8,340-char subset and a 16,743-char full CJK font,
        every mapped codepoint resolves via CSS cascade to the exact slice whose
        binary contains that codepoint, and Tier 4 slices use 1-token hex ranges.
        """
        shs_path = Path("/Users/myakura/projects/gensou-font/sources/jp/SourceHanSansJP-VF.otf")
        if not shs_path.exists():
            self.skipTest("SourceHanSansJP-VF.otf not found")

        with TTFont(shs_path) as tt:
            meta = extract_font_metadata(tt)

        # 1. Test full 16,743-char cmap (including patched U+553E '唾')
        self.assertIn(0x553E, meta.codepoints, "U+553E '唾' should be auto-patched from UVS Format 14")
        plan_full = build_slice_plan(meta.codepoints)
        self._assert_plan_invariants(plan_full, meta.codepoints)

        # 2. Test smaller subset (~8,340 chars)
        gensou_vf = Path("/Users/myakura/projects/gensou-font/dist/jp/variable/GenSouSansJP-VF.ttf")
        if gensou_vf.exists():
            with TTFont(gensou_vf) as g_tt:
                g_meta = extract_font_metadata(g_tt)
            plan_sub = build_slice_plan(g_meta.codepoints)
            self._assert_plan_invariants(plan_sub, g_meta.codepoints)
            # Smaller font must dynamically produce fewer Tier 4 rare slices than full font
            rare_full = [s for s in plan_full if s.tier == 4]
            rare_sub = [s for s in plan_sub if s.tier == 4]
            self.assertLess(len(rare_sub), len(rare_full))

    def _assert_plan_invariants(self, plan, codepoints: frozenset[int]) -> None:
        # Check partition completeness & mutual exclusivity of binary_codepoints
        union_bin = set()
        for spec in plan:
            self.assertTrue(spec.binary_codepoints, f"Empty slice {spec.slice_id}")
            overlap = union_bin & spec.binary_codepoints
            self.assertFalse(overlap, f"Duplicate binary codepoints in {spec.slice_id}")
            union_bin |= spec.binary_codepoints
        self.assertEqual(union_bin, set(codepoints))

        # Check Tier 4 uses 1-token unicode-range (no commas!)
        for spec in plan:
            if spec.tier == 4:
                self.assertNotIn(
                    ",",
                    spec.css_unicode_range,
                    f"Tier 4 slice {spec.slice_id} must be a 1-token range, got {spec.css_unicode_range}",
                )

        # Check Kana Core zero-omission guarantee (`む`, `モ`, `ヶ`, `ヵ`, `ゐ`, `ヱ`, `ゔ`, `「`, `」`, `０`)
        kana_slice = next(s for s in plan if s.slice_id == "kana-core")
        for ch in "あむモヶヵゐヱゔ、。「」【】０９ＡＺㇰㇿ":
            cp = ord(ch)
            if cp in codepoints:
                self.assertIn(cp, kana_slice.binary_codepoints, f"'{ch}' missing from kana-core")

        # Check Latin & UI Symbols placement
        latin_slice = next(s for s in plan if s.slice_id == "latin")
        for ch in "A0€₩™ℓĀāĪīŪūĒēŌō":
            cp = ord(ch)
            if cp in codepoints:
                self.assertIn(cp, latin_slice.binary_codepoints, f"'{ch}' missing from latin")

        sym_ui_slice = next(s for s in plan if s.slice_id == "symbols-ui")
        for ch in "→▶◀✓✔⚠♡♥★♪❶❿①⑳":
            cp = ord(ch)
            if cp in codepoints:
                self.assertIn(cp, sym_ui_slice.binary_codepoints, f"'{ch}' missing from symbols-ui")

        # Check that all 2010 Joyo Kanji (including JIS Level 3 𠮟塡剝頰 & 唾) + 𠮷 reside in Tier 3 (kanji-freq)
        tier3_cps = frozenset().union(*(s.binary_codepoints for s in plan if s.tier == 3))
        for ch in "唾𠮟塡剝頰𠮷鷗噓繫﨑髙楷諧毀憬錮傲恣嘲喩慄":
            cp = ord(ch)
            if cp in codepoints:
                self.assertIn(cp, tier3_cps, f"'{ch}' (U+{cp:04X}) should be in Tier 3 kanji-freq")

        # Pre-parse unicode-range for fast cascade simulation across all codepoints
        parsed_ranges = [(spec, parse_unicode_range(spec.css_unicode_range)) for spec in plan]
        for cp in codepoints:
            winner = None
            for spec, ur_set in parsed_ranges:
                if cp in ur_set:
                    winner = spec
            self.assertIsNotNone(winner, f"Codepoint U+{cp:04X} not matched by any CSS unicode-range")
            self.assertIn(
                cp,
                winner.binary_codepoints,
                f"CSS Cascade Shadowing mismatch for U+{cp:04X} ({chr(cp)}): "
                f"CSS matched {winner.slice_id}, but binary does not contain it!",
            )

    def test_end_to_end_variable_and_static_export(self) -> None:
        """Test actual WOFF2 slice generation and CSS output for Variable and Static fonts."""
        gensou_vf = Path("/Users/myakura/projects/gensou-font/dist/jp/variable/GenSouSansJP-VF.ttf")
        if not gensou_vf.exists():
            self.skipTest("GenSouSansJP-VF.ttf not found")

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "vf_web"
            res = slice_font(gensou_vf, out_dir=out_dir)
            self.assertTrue(res.css_path.exists())
            self.assertEqual(res.css_path.name, "gensou-sans-jp.css")
            self.assertIn("font-weight: 250 900;", res.css_text)
            self.assertIn("font-stretch: 70% 100%;", res.css_text)
            self.assertTrue(all(bf.path.exists() and bf.size_bytes > 0 for bf in res.built_files))

            # Also test multi-file static fonts if available
            static_ttf_dir = Path("/Users/myakura/projects/gensou-font/dist/jp/static/ttf")
            if static_ttf_dir.exists():
                reg = static_ttf_dir / "GenSouSansJP-Cond-Regular.ttf"
                bold = static_ttf_dir / "GenSouSansJP-Cond-Bold.ttf"
                if reg.exists() and bold.exists():
                    static_out = Path(tmp) / "static_web"
                    # Limit to a small synthetic cmap or test directly with workers
                    s_res = slice_fonts([reg, bold], out_dir=static_out)
                    self.assertTrue(s_res.css_path.exists())
                    self.assertIn("font-weight: 400;", s_res.css_text)
                    self.assertIn("font-weight: 700;", s_res.css_text)
                    self.assertEqual(len(s_res.built_files), len(s_res.slice_specs) * 2)


if __name__ == "__main__":
    unittest.main()
