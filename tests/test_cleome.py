"""
Comprehensive self-contained test suite for cleome:
- Dynamic 4-tier slice planning & CSS Cascade Shadowing invariant across variable character sets
- Zero-omission guarantee for Kana (`む`, `モ`, `ヶ`, `ヵ`, `ゐ`, `ヱ`, `ゔ`) and CJK punctuation
- 1-token hex range verification for Tier 4 rare Kanji
- UVS Format 14 missing base cmap auto-patching (e.g. U+553E `唾`)
- Variable Font (`wght` x `wdth`) and Multi-file Static Font WOFF2 slicing and CSS generation
"""

from io import StringIO
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
from fontTools.ttLib.tables.TupleVariation import TupleVariation

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from cleome import (
    SliceConfig,
    build_slice_plan,
    extract_font_metadata,
    parse_unicode_range,
    slice_font,
    slice_fonts,
)
from cleome.cli import main as cli_main
from cleome.data.kanji_freq import KANJI_FREQ_CODEPOINTS, KANJI_FREQ_RANK


def _build_synthetic_charset(*, full: bool) -> frozenset[int]:
    """Build a realistic Japanese font codepoint set (either ~8,300 subset or ~16,700 full)."""
    cps: set[int] = set()
    # Core Latin (`U+0020..007E`, `U+00A0..024F`, `U+2000..206F`, `U+20A0..20CF`, `ℓ`, `™`)
    cps.update(range(0x0020, 0x007F))
    cps.update(range(0x00A0, 0x0250))
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


def _create_synthetic_ttfont(
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
        uvs_cp, uvs_sel = uvs_only_cp
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
        # Add minimal gvar so fontTools.subset treats it as a valid TrueType variable font
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


class TestCleomeSlicer(unittest.TestCase):
    def test_cascade_shadowing_invariant_small_and_full_charsets(self) -> None:
        """
        Verify that for both an ~8,300-char subset and a ~16,700-char full CJK charset,
        every mapped codepoint resolves via CSS cascade to the exact slice whose
        binary contains that codepoint, and Tier 4 slices use 1-token hex ranges.
        """
        self.assertIsInstance(KANJI_FREQ_RANK, frozendict)
        full_cps = _build_synthetic_charset(full=True)
        sub_cps = _build_synthetic_charset(full=False)

        plan_full = build_slice_plan(full_cps)
        self._assert_plan_invariants(plan_full, full_cps)

        plan_sub = build_slice_plan(sub_cps)
        self._assert_plan_invariants(plan_sub, sub_cps)

        # Smaller font must dynamically produce fewer Tier 4 rare slices than full font
        rare_full = [s for s in plan_full if s.tier == 4]
        rare_sub = [s for s in plan_sub if s.tier == 4]
        self.assertLess(len(rare_sub), len(rare_full))

    def test_uvs_format14_missing_base_cmap_autopatch_and_e2e_woff2(self) -> None:
        """
        Verify that a character present in cmap Format 14 (UVS) but missing from
        base Format 4/12 (like U+553E '唾' in SourceHanSansJP-VF.otf v2.005) is
        automatically patched into the generated WOFF2 slice both when slicing a
        TTFont in memory (without mutating the caller's TTFont) and when slicing a Path.
        """
        tt = _create_synthetic_ttfont(
            [ord("A"), ord("あ"), ord("日")],
            uvs_only_cp=(0x553E, 0xE0100),
        )
        self.assertNotIn(0x553E, tt.getBestCmap())

        with tempfile.TemporaryDirectory() as tmp:
            # 1. Pass in-memory TTFont: caller's TTFont must NOT be mutated, yet WOFF2 has U+553E
            mem_out = Path(tmp) / "mem_out"
            mem_res = slice_font(tt, out_dir=mem_out, workers=2)
            self.assertNotIn(0x553E, tt.getBestCmap(), "Caller's in-memory TTFont must not be mutated")
            kanji_woff2 = next(bf.path for bf in mem_res.built_files if "kanji-freq" in bf.spec.slice_id)
            with TTFont(kanji_woff2) as slice_tt:
                self.assertIn(0x553E, slice_tt.getBestCmap(), "WOFF2 slice from in-memory TTFont must contain U+553E")

            # 2. Pass Path on disk: worker process patches Format 14 -> base cmap in WOFF2
            disk_font = Path(tmp) / "uvs_font.ttf"
            tt.save(disk_font, reorderTables=False)
            disk_out = Path(tmp) / "disk_out"
            disk_res = slice_font(disk_font, out_dir=disk_out, workers=2)
            disk_kanji_woff2 = next(bf.path for bf in disk_res.built_files if "kanji-freq" in bf.spec.slice_id)
            with TTFont(disk_kanji_woff2) as slice_tt:
                self.assertIn(0x553E, slice_tt.getBestCmap(), "WOFF2 slice from disk Path must contain U+553E")

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
        kana_slice = next((s for s in plan if s.slice_id == "kana-core"), None)
        for ch in "あむモヶヵゐヱゔ、。「」【】０９ＡＺㇰㇿ":
            cp = ord(ch)
            if cp in codepoints:
                self.assertIsNotNone(kana_slice)
                self.assertIn(cp, kana_slice.binary_codepoints, f"'{ch}' missing from kana-core")

        # Check Latin & UI Symbols placement
        latin_slice = next((s for s in plan if s.slice_id == "latin"), None)
        for ch in "A0€₩™ℓĀāĪīŪūĒēŌō":
            cp = ord(ch)
            if cp in codepoints:
                self.assertIsNotNone(latin_slice)
                self.assertIn(cp, latin_slice.binary_codepoints, f"'{ch}' missing from latin")

        sym_ui_slice = next((s for s in plan if s.slice_id == "symbols-ui"), None)
        for ch in "→▶◀✓✔⚠♡♥★♪❶❿①⑳":
            cp = ord(ch)
            if cp in codepoints:
                self.assertIsNotNone(sym_ui_slice)
                self.assertIn(cp, sym_ui_slice.binary_codepoints, f"'{ch}' missing from symbols-ui")

        # Check that all 2010 Joyo Kanji (including JIS Level 3 𠮟塡剝頰 & 唾) + 𠮷 reside in Tier 3 (kanji-freq)
        tier3_cps = {*s.binary_codepoints for s in plan if s.tier == 3}
        for ch in "唾𠮟塡剝頰𠮷鷗噓繫﨑髙楷諧毀憬錮傲恣嘲喩慄":
            cp = ord(ch)
            if cp in codepoints:
                self.assertIn(cp, tier3_cps, f"'{ch}' (U+{cp:04X}) should be in Tier 3 kanji-freq")

        # Simulate CSS cascade across all @font-face rules in declaration order
        resolved: dict[int, object] = {}
        for spec in plan:
            ur_set = parse_unicode_range(spec.css_unicode_range)
            for cp in ur_set & codepoints:
                resolved[cp] = spec

        for cp in codepoints:
            winner = resolved.get(cp)
            self.assertIsNotNone(winner, f"Codepoint U+{cp:04X} not matched by any CSS unicode-range")
            self.assertIn(
                cp,
                winner.binary_codepoints,
                f"CSS Cascade Shadowing mismatch for U+{cp:04X} ({chr(cp)}): "
                f"CSS matched {winner.slice_id}, but binary does not contain it!",
            )

    def test_sip_and_tip_planes_never_bridge_in_tier4(self) -> None:
        """
        Verify that Rare Kanji in Plane 2 (SIP, U+20000..2FA1F) and Plane 3 (TIP, U+30000..3347F)
        are placed in separate Tier 4 slices so no 1-token hex range spans across planes.
        """
        cps = {ord("A"), ord("あ"), 0x20001, 0x2FA1D, 0x30000, 0x323B0}
        plan = build_slice_plan(cps)
        self._assert_plan_invariants(plan, frozenset(cps))
        rare_slices = [s for s in plan if s.tier == 4]
        self.assertEqual(len(rare_slices), 2)
        for s in rare_slices:
            ur_cps = parse_unicode_range(s.css_unicode_range)
            has_sip = any(0x20000 <= c <= 0x2FA1F for c in ur_cps)
            has_tip = any(0x30000 <= c <= 0x3347F for c in ur_cps)
            self.assertFalse(has_sip and has_tip, f"Slice {s.slice_id} bridged SIP and TIP: {s.css_unicode_range}")

    def test_uvs_selector_deterministic_priority_ivs_over_vs(self) -> None:
        """
        Verify that when multiple UVS selectors exist in Format 14 for an unmapped base character,
        IVS selectors (U+E0100..U+E01EF) take precedence over VS1..VS16 (U+FE00..U+FE0F), and
        U+E0100 takes precedence over U+E0102 regardless of dict insertion order.
        """
        tt = _create_synthetic_ttfont([ord("A")])
        glyph_order = tt.getGlyphOrder() + ["glyph_vs1", "glyph_iv2", "glyph_iv0"]
        tt.setGlyphOrder(glyph_order)
        for g in ("glyph_vs1", "glyph_iv2", "glyph_iv0"):
            tt["glyf"][g] = tt["glyf"]["uni0041"]
            tt["hmtx"][g] = (1000, 100)

        sub14 = CmapSubtable.newSubtable(14)
        sub14.platformID = 0
        sub14.platEncID = 5
        sub14.language = 0
        sub14.cmap = {}
        sub14.uvsDict = {
            0xFE00: [(0x553E, "glyph_vs1")],
            0xE0102: [(0x553E, "glyph_iv2")],
            0xE0100: [(0x553E, "glyph_iv0")],
        }
        tt["cmap"].tables.append(sub14)

        meta = extract_font_metadata(tt, patch_uvs=True)
        self.assertIn(0x553E, meta.codepoints)
        self.assertEqual(tt.getBestCmap()[0x553E], "glyph_iv0")

    def test_end_to_end_variable_and_static_export_with_woff2_verification(self) -> None:
        """
        Test WOFF2 slice generation, exact cmap verification of every built slice,
        license nameID (13, 14) preservation, head.modified determinism, and static font-stretch.
        """
        sample_chars = "A0€あむモヶ「」→▶①Ωα日人一𠮟𠮷鷗" + "".join(chr(cp) for cp in range(0x4E10, 0x4E30))
        sample_cps = sorted({ord(c) for c in sample_chars})

        vf_tt = _create_synthetic_ttfont(
            sample_cps,
            family_name="Test Sans JP",
            is_variable=True,
        )

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "vf_web"
            res = slice_font(vf_tt, out_dir=out_dir, workers=2)
            self.assertTrue(res.css_path.exists())
            self.assertEqual(res.css_path.name, "test-sans-jp.css")
            self.assertIn("font-weight: 250 900;", res.css_text)
            self.assertIn("font-stretch: 70% 100%;", res.css_text)

            for bf in res.built_files:
                self.assertTrue(bf.path.exists() and bf.size_bytes > 0)
                with TTFont(bf.path) as woff2_tt:
                    self.assertEqual(set(woff2_tt.getBestCmap().keys()), set(bf.spec.binary_codepoints))
                    self.assertEqual(woff2_tt["head"].modified, 3700000000)
                    name_ids = {r.nameID for r in woff2_tt["name"].names}
                    self.assertIn(13, name_ids, "License description (nameID 13) must be retained")
                    self.assertIn(14, name_ids, "License info URL (nameID 14) must be retained")

            # Test multi-file static fonts (Condensed Regular 400 + Condensed Bold 700, width_class=3 -> 75%)
            reg_path = Path(tmp) / "TestSansJP-Cond-Regular.ttf"
            bold_path = Path(tmp) / "TestSansJP-Cond-Bold.ttf"
            _create_synthetic_ttfont(
                sample_cps,
                family_name="Test Sans JP Cond",
                weight=400,
                width_class=3,
                is_variable=False,
            ).save(reg_path, reorderTables=False)
            _create_synthetic_ttfont(
                sample_cps,
                family_name="Test Sans JP Cond",
                weight=700,
                width_class=3,
                is_variable=False,
            ).save(bold_path, reorderTables=False)

            static_out = Path(tmp) / "static_web"
            s_res = slice_fonts([reg_path, bold_path], out_dir=static_out, workers=2)
            self.assertTrue(s_res.css_path.exists())
            self.assertIn("font-weight: 400;", s_res.css_text)
            self.assertIn("font-weight: 700;", s_res.css_text)
            self.assertIn("font-stretch: 75%;", s_res.css_text)
            self.assertEqual(len(s_res.built_files), len(s_res.slice_specs) * 2)

    def test_multi_font_collision_and_family_mismatch_errors(self) -> None:
        """
        Verify that passing multiple fonts with different family names (without --family)
        or duplicate (weight, stretch, style) @font-face descriptors raises ValueError.
        """
        sample_cps = [ord("A"), ord("あ"), ord("日")]
        f1 = _create_synthetic_ttfont(sample_cps, family_name="Family A", weight=400, width_class=5)
        f2 = _create_synthetic_ttfont(sample_cps, family_name="Family B", weight=700, width_class=5)
        f3_dup = _create_synthetic_ttfont(sample_cps, family_name="Family A", weight=400, width_class=5)

        with tempfile.TemporaryDirectory() as tmp:
            # Mismatched family names without family_name override -> ValueError
            with self.assertRaisesRegex(ValueError, "different detected font-family names"):
                slice_fonts([f1, f2], out_dir=Path(tmp) / "mismatch", workers=1)

            # Duplicate (weight=400, stretch=None, style='normal') descriptors -> ValueError
            with self.assertRaisesRegex(ValueError, "Duplicate @font-face descriptors"):
                slice_fonts([f1, f3_dup], out_dir=Path(tmp) / "dup", workers=1)

    def test_cli_validation_and_dry_run(self) -> None:
        """
        Verify CLI argument parsing for --drop-tables, --layout-features, --dry-run,
        and validation errors for non-positive --freq-tiers / --rare-chunk-size.
        """
        from contextlib import redirect_stderr

        sample_cps = [ord("A"), ord("あ"), ord("日")]
        with tempfile.TemporaryDirectory() as tmp:
            font_path = Path(tmp) / "CliTest.ttf"
            _create_synthetic_ttfont(sample_cps, family_name="CLI Test").save(font_path, reorderTables=False)

            # 1. --drop-tables before positional input must not swallow positional input
            buf = StringIO()
            with redirect_stdout(buf):
                rc = cli_main(["--drop-tables", "DSIG,GPOS", "--layout-features", "kern,palt", "--dry-run", str(font_path)])
            self.assertEqual(rc, 0)
            self.assertIn("3 chars -> 3 slices", buf.getvalue())

            # 2. Invalid --freq-tiers (0 or negative) must fail with SystemExit(2)
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as cm:
                cli_main(["--freq-tiers", "200x0", "--dry-run", str(font_path)])
            self.assertEqual(cm.exception.code, 2)

            with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as cm2:
                cli_main(["--rare-chunk-size", "0", "--dry-run", str(font_path)])
            self.assertEqual(cm2.exception.code, 2)

    def test_subprocess_script_with_and_without_main_guard(self) -> None:
        """
        Verify that:
        1. A top-level script with `if __name__ == "__main__":` and `workers=2` succeeds.
        2. A top-level script WITHOUT `if __name__ == "__main__":` on a multi-MB font (triggering
           BrokenPipeError in the parent while sending initargs) exits with our wrapped RuntimeError
           as the final line of stderr.
        """
        from fontTools.ttLib.tables.DefaultTable import DefaultTable

        sample_cps = [ord("A"), ord("あ"), ord("日"), 0x4E00]
        with tempfile.TemporaryDirectory() as tmp:
            font_path = Path(tmp) / "SubprocFont.ttf"
            out_dir = Path(tmp) / "subproc_out"
            tt = _create_synthetic_ttfont(sample_cps, family_name="Subproc Font")
            pad = DefaultTable("PAD ")
            pad.data = b"\x00" * (4 * 1024 * 1024)
            tt["PAD "] = pad
            tt.save(font_path, reorderTables=False)

            src_dir = Path(__file__).resolve().parent.parent / "src"

            # 1. With `if __name__ == "__main__":` -> succeeds
            script_ok = Path(tmp) / "run_slice_ok.py"
            script_ok.write_text(
                f"import sys\n"
                f"sys.path.insert(0, {str(src_dir)!r})\n"
                f"from pathlib import Path\n"
                f"from cleome import slice_font\n"
                f"if __name__ == '__main__':\n"
                f"    res = slice_font({str(font_path)!r}, out_dir=Path({str(out_dir)!r}), workers=2)\n"
                f"    assert res.css_path.exists()\n",
                encoding="utf-8",
            )
            proc_ok = subprocess.run(
                [sys.executable, str(script_ok)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_ok.returncode, 0, f"Subprocess failed: {proc_ok.stderr}")

            # 2. Without `if __name__ == "__main__":` -> parent catches BrokenPipeError/BrokenProcessPool
            # and prints wrapped RuntimeError on the final line of stderr
            script_bad = Path(tmp) / "run_slice_no_guard.py"
            out_bad = Path(tmp) / "subproc_bad_out"
            script_bad.write_text(
                f"import sys\n"
                f"sys.path.insert(0, {str(src_dir)!r})\n"
                f"from pathlib import Path\n"
                f"from cleome import slice_font\n"
                f"slice_font({str(font_path)!r}, out_dir=Path({str(out_bad)!r}), workers=2)\n",
                encoding="utf-8",
            )
            proc_bad = subprocess.run(
                [sys.executable, str(script_bad)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(proc_bad.returncode, 0)
            stderr_lines = [ln for ln in proc_bad.stderr.strip().splitlines() if ln.strip()]
            self.assertTrue(stderr_lines, "Expected non-empty stderr from unguarded script")
            self.assertIn(
                "RuntimeError: Failed to run parallel WOFF2 worker processes.",
                stderr_lines[-1],
                f"Final stderr line did not contain wrapped RuntimeError. Got:\n{proc_bad.stderr}",
            )

    def test_layout_features_passed_to_workers(self) -> None:
        """
        Verify that `SliceConfig.layout_features` is propagated to worker processes and controls
        which GSUB features remain in generated `.woff2` slices (e.g. `kana-core`).
        """
        from fontTools.feaLib.builder import addOpenTypeFeaturesFromString

        sample_cps = [ord("A"), ord("あ"), ord("ー"), ord("日"), 0x4E00]
        tt = _create_synthetic_ttfont(sample_cps, family_name="Feature Font")
        fea_code = """
        languagesystem DFLT dflt;
        languagesystem kana dflt;
        feature vert {
            sub uni30FC by uni3042;
        } vert;
        feature liga {
            sub uni3042 uni30FC by uni3042;
        } liga;
        """
        addOpenTypeFeaturesFromString(tt, fea_code)

        with tempfile.TemporaryDirectory() as tmp:
            # 1. Retain only 'vert' across 2 parallel workers
            out_vert = Path(tmp) / "vert_only"
            res_vert = slice_font(
                tt,
                out_dir=out_vert,
                config=SliceConfig(layout_features=("vert",)),
                workers=2,
            )
            kana_vert = next(bf.path for bf in res_vert.built_files if bf.spec.slice_id == "kana-core")
            with TTFont(kana_vert) as woff2_tt:
                gsub_tags = [f.FeatureTag for f in woff2_tt["GSUB"].table.FeatureList.FeatureRecord]
                self.assertEqual(gsub_tags, ["vert"])

            # 2. Default '*' retains both 'liga' and 'vert'
            out_all = Path(tmp) / "all_features"
            res_all = slice_font(
                tt,
                out_dir=out_all,
                config=SliceConfig(layout_features=("*",)),
                workers=2,
            )
            kana_all = next(bf.path for bf in res_all.built_files if bf.spec.slice_id == "kana-core")
            with TTFont(kana_all) as woff2_tt:
                gsub_tags_all = sorted(f.FeatureTag for f in woff2_tt["GSUB"].table.FeatureList.FeatureRecord)
                self.assertEqual(gsub_tags_all, ["liga", "vert"])

    def test_multi_font_differing_cmaps_variant_slice_specs(self) -> None:
        """
        Verify that when bundling multiple fonts with differing `cmap`s, `variant_slice_specs`
        contains each font's own slice plan (matching `built_files`), while `slice_specs` holds the union plan.
        """
        f_reg = _create_synthetic_ttfont([ord("A"), ord("あ")], family_name="Diff Cmap", weight=400)
        f_bold = _create_synthetic_ttfont([ord("A"), ord("あ"), ord("日"), 0x4E02], family_name="Diff Cmap", weight=700)

        with tempfile.TemporaryDirectory() as tmp:
            res = slice_fonts([f_reg, f_bold], out_dir=Path(tmp) / "diff_cmap", workers=2)
            self.assertEqual(len(res.variant_slice_specs), 2)
            self.assertEqual(len(res.variant_slice_specs[0]), 2)  # latin + kana-core
            self.assertEqual(len(res.variant_slice_specs[1]), 4)  # rare + freq + kana-core + latin
            self.assertEqual(len(res.slice_specs), 4)  # union plan
            self.assertEqual(
                len(res.built_files),
                len(res.variant_slice_specs[0]) + len(res.variant_slice_specs[1]),
            )

    def test_issue1_uvs_format14_preserved_in_woff2_slices(self) -> None:
        """
        Issue 1 verification: Verify that Format 14 UVS mappings (both non-default UVS with
        a variant glyph and default UVS with glyphName=None) for base characters present in
        base cmap are preserved in the corresponding slice WOFF2 files across Path/TTFont
        inputs and single/multi-worker runs, without polluting base cmap, and keeping
        `patch_uvs_missing_base_cmap` distinct from Format 14 sequence retention.
        """
        sample_cps = [ord("A"), ord("あ"), ord("日"), 0x4E02, 0xFE00]

        def _make_uvs_font() -> TTFont:
            f = _create_synthetic_ttfont(sample_cps, family_name="UVS Keep Font")
            f.setGlyphOrder(f.getGlyphOrder() + ["uni65E5_ivs0", "uni4E02_ivs0", "uni553E_uvs"])
            for g, adv in (("uni65E5_ivs0", 910), ("uni4E02_ivs0", 820), ("uni553E_uvs", 730)):
                f["glyf"][g] = f["glyf"]["uni0041"]
                f["hmtx"][g] = (adv, 100)
            s14 = CmapSubtable.newSubtable(14)
            s14.platformID = 0
            s14.platEncID = 5
            s14.language = 0
            s14.cmap = {}
            s14.uvsDict = {
                0xE0100: [
                    (ord("日"), "uni65E5_ivs0"),
                    (0x4E02, "uni4E02_ivs0"),
                    (0x553E, "uni553E_uvs"),  # U+553E not in base cmap
                ],
                0xE0101: [(ord("日"), None)],
                0xFE00: [(ord("日"), "uni65E5_ivs0")],  # VS1 selector is also in base cmap (symbols-ext)
            }
            f["cmap"].tables.append(s14)
            return f

        with tempfile.TemporaryDirectory() as tmp:
            disk_path = Path(tmp) / "uvs_keep.ttf"
            _make_uvs_font().save(disk_path, reorderTables=False)

            for label, src, w, patch_missing in (
                ("path_w1", disk_path, 1, True),
                ("path_w2", disk_path, 2, True),
                ("ttfont_w1", _make_uvs_font(), 1, True),
                ("ttfont_w2", _make_uvs_font(), 2, True),
                ("no_patch_missing", _make_uvs_font(), 1, False),
            ):
                out_dir = Path(tmp) / label
                res = slice_font(
                    src,
                    out_dir=out_dir,
                    config=SliceConfig(patch_uvs_only_cmap=patch_missing),
                    workers=w,
                )

                freq_bf = next(bf for bf in res.built_files if bf.spec.category == "kanji-freq")
                rare_bf = next(bf for bf in res.built_files if bf.spec.category == "kanji-rare")
                sym_bf = next(bf for bf in res.built_files if bf.spec.category == "symbols-ext")

                with TTFont(freq_bf.path) as freq_tt:
                    expected_freq_cps = {ord("日"), 0x553E} if patch_missing else {ord("日")}
                    self.assertEqual(set(freq_tt.getBestCmap().keys()), expected_freq_cps)
                    self.assertNotIn(0xFE00, freq_tt.getBestCmap(), "VS1 must not leak into kanji-freq base cmap")
                    f14_tables = [t for t in freq_tt["cmap"].tables if t.format == 14]
                    self.assertEqual(len(f14_tables), 1, f"{label}: Format 14 missing in kanji-freq")
                    uvs_dict = f14_tables[0].uvsDict
                    self.assertEqual(uvs_dict[0xE0101], [(ord("日"), None)])
                    # Verify non-default UVS glyph for 日 has distinct advance width 910 (not base 1000 or 820)
                    hi_uvs_entries = dict(uvs_dict[0xE0100])
                    self.assertIn(ord("日"), hi_uvs_entries)
                    self.assertNotIn(0x4E02, hi_uvs_entries)
                    hi_ivs_gname = hi_uvs_entries[ord("日")]
                    self.assertIsNotNone(hi_ivs_gname)
                    self.assertNotEqual(hi_ivs_gname, freq_tt.getBestCmap()[ord("日")])
                    self.assertEqual(freq_tt["hmtx"][hi_ivs_gname][0], 910)
                    self.assertEqual(uvs_dict[0xFE00], [(ord("日"), hi_ivs_gname)])

                with TTFont(rare_bf.path) as rare_tt:
                    self.assertEqual(set(rare_tt.getBestCmap().keys()), {0x4E02})
                    f14_tables = [t for t in rare_tt["cmap"].tables if t.format == 14]
                    self.assertEqual(len(f14_tables), 1, f"{label}: Format 14 missing in kanji-rare")
                    uvs_dict = f14_tables[0].uvsDict
                    self.assertEqual(set(uvs_dict.keys()), {0xE0100})
                    self.assertEqual(len(uvs_dict[0xE0100]), 1)
                    cp_rare, rare_ivs_gname = uvs_dict[0xE0100][0]
                    self.assertEqual(cp_rare, 0x4E02)
                    self.assertIsNotNone(rare_ivs_gname)
                    self.assertEqual(rare_tt["hmtx"][rare_ivs_gname][0], 820)

                with TTFont(sym_bf.path) as sym_tt:
                    self.assertEqual(set(sym_tt.getBestCmap().keys()), {0xFE00})
                    f14_tables = [t for t in sym_tt["cmap"].tables if t.format == 14]
                    self.assertEqual(len(f14_tables), 0, "symbols-ext has no Format 14 base chars")

                # Verify HarfBuzz shaping before and after slicing for IVS/UVS sequences
                import copy
                import io
                import uharfbuzz as hb

                def _hb_shape_advances(font_obj: TTFont, text: str) -> list[int]:
                    buf_io = io.BytesIO()
                    c = copy.deepcopy(font_obj)
                    c.flavor = None
                    c.save(buf_io, reorderTables=False)
                    face = hb.Face(hb.Blob(buf_io.getvalue()))
                    hb_font = hb.Font(face)
                    hb_buf = hb.Buffer()
                    hb_buf.add_str(text)
                    hb_buf.guess_segment_properties()
                    hb.shape(hb_font, hb_buf)
                    return [pos.x_advance for pos in hb_buf.glyph_positions]

                orig_uvs = _make_uvs_font()
                with TTFont(freq_bf.path) as sliced_freq_tt:
                    for seq, expected_adv in (
                        ("日", [1000]),
                        ("日\U000E0100", [910]),
                        ("日\U000E0101", [1000]),
                        ("日\uFE00", [910]),
                    ):
                        self.assertEqual(_hb_shape_advances(orig_uvs, seq), expected_adv)
                        self.assertEqual(_hb_shape_advances(sliced_freq_tt, seq), expected_adv)


if __name__ == "__main__":
    unittest.main()
