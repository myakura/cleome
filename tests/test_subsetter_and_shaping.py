"""
Tests for WOFF2 export, UVS Format 14 autopatching & retention, HarfBuzz OpenType shaping,
Variable Font (`ital`/`slnt`/`wght`/`wdth`) CSS generation, TTFont immutability, and worker caching.
"""

import copy
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
from fontTools.ttLib.tables._f_v_a_r import Axis
import uharfbuzz as hb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from cleome import (
    SliceConfig,
    extract_font_metadata,
    slice_font,
    slice_fonts,
)
from cleome.subsetter import _export_woff2_slices_batch

from helpers import create_synthetic_ttfont


class TestSubsetterAndShaping(unittest.TestCase):
    def test_uvs_format14_missing_base_cmap_autopatch_and_e2e_woff2(self) -> None:
        """
        Verify that a character present in cmap Format 14 (UVS) but missing from
        base Format 4/12 (like U+553E '唾' in SourceHanSansJP-VF.otf v2.005) is
        automatically patched into the generated WOFF2 slice both when slicing a
        TTFont in memory (without mutating the caller's TTFont) and when slicing a Path.
        """
        tt = create_synthetic_ttfont(
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

    def test_uvs_selector_deterministic_priority_ivs_over_vs(self) -> None:
        """
        Verify that when multiple UVS selectors exist in Format 14 for an unmapped base character,
        IVS selectors (U+E0100..U+E01EF) take precedence over VS1..VS16 (U+FE00..U+FE0F), and
        U+E0100 takes precedence over U+E0102 regardless of dict insertion order.
        """
        tt = create_synthetic_ttfont([ord("A")])
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

        vf_tt = create_synthetic_ttfont(
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
            create_synthetic_ttfont(
                sample_cps,
                family_name="Test Sans JP Cond",
                weight=400,
                width_class=3,
                is_variable=False,
            ).save(reg_path, reorderTables=False)
            create_synthetic_ttfont(
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
        f1 = create_synthetic_ttfont(sample_cps, family_name="Family A", weight=400, width_class=5)
        f2 = create_synthetic_ttfont(sample_cps, family_name="Family B", weight=700, width_class=5)
        f3_dup = create_synthetic_ttfont(sample_cps, family_name="Family A", weight=400, width_class=5)

        with tempfile.TemporaryDirectory() as tmp:
            # Mismatched family names without family_name override -> ValueError
            with self.assertRaisesRegex(ValueError, "different detected font-family names"):
                slice_fonts([f1, f2], out_dir=Path(tmp) / "mismatch", workers=1)

            # Duplicate (weight=400, stretch=None, style='normal') descriptors -> ValueError
            with self.assertRaisesRegex(ValueError, "Duplicate @font-face descriptors"):
                slice_fonts([f1, f3_dup], out_dir=Path(tmp) / "dup", workers=1)

    def test_layout_features_passed_to_workers(self) -> None:
        """
        Verify that `SliceConfig.layout_features` is propagated to worker processes and controls
        which GSUB features remain in generated `.woff2` slices (e.g. `kana-core`).
        """
        sample_cps = [ord("A"), ord("あ"), ord("ー"), ord("日"), 0x4E00]
        tt = create_synthetic_ttfont(sample_cps, family_name="Feature Font")
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
        f_reg = create_synthetic_ttfont([ord("A"), ord("あ")], family_name="Diff Cmap", weight=400)
        f_bold = create_synthetic_ttfont([ord("A"), ord("あ"), ord("日"), 0x4E02], family_name="Diff Cmap", weight=700)

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
            f = create_synthetic_ttfont(sample_cps, family_name="UVS Keep Font")
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

    def test_issue2_combining_marks_and_base_chars_retain_gsub_ccmp(self) -> None:
        """
        Issue 2 verification: Verify that Latin base characters (e.g. 'A' U+0041) and
        Combining Diacritical Marks (e.g. U+0301) stay in the `latin` slice so OpenType
        `ccmp` composition (`A + U+0301 -> Á`) retains its GSUB lookup and all input/output
        glyphs (both when the precomposed glyph is in `cmap` and when it is unmapped).
        """
        def _hb_shape_glyphs_and_advances(font_obj: TTFont, text: str) -> list[tuple[str, int]]:
            buf_io = io.BytesIO()
            c = copy.deepcopy(font_obj)
            c.flavor = None
            c.save(buf_io, reorderTables=False)
            order = c.getGlyphOrder()
            face = hb.Face(hb.Blob(buf_io.getvalue()))
            hb_font = hb.Font(face)
            hb_buf = hb.Buffer()
            hb_buf.add_str(text)
            hb_buf.guess_segment_properties()
            hb.shape(hb_font, hb_buf)
            return [
                (order[info.codepoint], pos.x_advance)
                for info, pos in zip(hb_buf.glyph_infos, hb_buf.glyph_positions)
            ]

        # 1. Precomposed glyph in cmap (exact reproduction case)
        sample_cps = [ord("A"), 0x0301, 0x00C1, ord("あ"), ord("日")]
        tt = create_synthetic_ttfont(sample_cps, family_name="Combining Font")
        tt["hmtx"]["uni00C1"] = (950, 100)
        fea_code = """
        languagesystem DFLT dflt;
        languagesystem latn dflt;
        feature ccmp {
            sub uni0041 uni0301 by uni00C1;
        } ccmp;
        """
        addOpenTypeFeaturesFromString(tt, fea_code)
        self.assertEqual(len(tt["GSUB"].table.LookupList.Lookup), 1)
        orig_shaped = _hb_shape_glyphs_and_advances(tt, "A\u0301")
        self.assertEqual(orig_shaped, [("uni00C1", 950)])

        with tempfile.TemporaryDirectory() as tmp:
            res = slice_font(
                tt,
                out_dir=Path(tmp) / "ccmp_out",
                config=SliceConfig(layout_features=("*",)),
                workers=1,
            )
            latin_bf = next(bf for bf in res.built_files if bf.spec.slice_id == "latin")
            self.assertIn(ord("A"), latin_bf.spec.binary_codepoints)
            self.assertIn(0x0301, latin_bf.spec.binary_codepoints)
            self.assertIn(0x00C1, latin_bf.spec.binary_codepoints)

            with TTFont(latin_bf.path) as latin_tt:
                cmap = latin_tt.getBestCmap()
                g_a, g_acute, g_aacute = cmap[ord("A")], cmap[0x0301], cmap[0x00C1]
                gsub = latin_tt["GSUB"].table
                tags = [f.FeatureTag for f in gsub.FeatureList.FeatureRecord]
                self.assertIn("ccmp", tags)
                self.assertEqual(len(gsub.LookupList.Lookup), 1)
                subtable = gsub.LookupList.Lookup[0].SubTable[0]
                ligs = subtable.ligatures.get(g_a, [])
                self.assertEqual(len(ligs), 1)
                self.assertEqual(ligs[0].Component, [g_acute])
                self.assertEqual(ligs[0].LigGlyph, g_aacute)
                self.assertEqual(latin_tt["hmtx"][g_aacute][0], 950)
                self.assertEqual(_hb_shape_glyphs_and_advances(latin_tt, "A\u0301"), [(g_aacute, 950)])

            # 2. Unmapped precomposed glyph reachable only via GSUB ccmp closure
            tt_unmapped = create_synthetic_ttfont([ord("A"), 0x0301, ord("あ")], family_name="Unmapped Lig Font")
            tt_unmapped.setGlyphOrder(tt_unmapped.getGlyphOrder() + ["Aacute_lig"])
            tt_unmapped["glyf"]["Aacute_lig"] = tt_unmapped["glyf"]["uni0041"]
            tt_unmapped["hmtx"]["Aacute_lig"] = (940, 100)
            addOpenTypeFeaturesFromString(
                tt_unmapped,
                """
                languagesystem DFLT dflt;
                languagesystem latn dflt;
                feature ccmp {
                    sub uni0041 uni0301 by Aacute_lig;
                } ccmp;
                """,
            )
            self.assertEqual(_hb_shape_glyphs_and_advances(tt_unmapped, "A\u0301"), [("Aacute_lig", 940)])
            res_unmapped = slice_font(tt_unmapped, out_dir=Path(tmp) / "ccmp_unmapped", workers=1)
            latin_unmapped_bf = next(bf for bf in res_unmapped.built_files if bf.spec.slice_id == "latin")
            with TTFont(latin_unmapped_bf.path) as lu_tt:
                cmap_u = lu_tt.getBestCmap()
                gsub_u = lu_tt["GSUB"].table
                self.assertEqual(len(gsub_u.LookupList.Lookup), 1)
                lig_u = gsub_u.LookupList.Lookup[0].SubTable[0].ligatures[cmap_u[ord("A")]][0]
                self.assertEqual(lig_u.Component, [cmap_u[0x0301]])
                self.assertIn(lig_u.LigGlyph, lu_tt.getGlyphOrder())
                self.assertEqual(lu_tt["hmtx"][lig_u.LigGlyph][0], 940)
                shaped_unmapped = _hb_shape_glyphs_and_advances(lu_tt, "A\u0301")
                self.assertEqual(shaped_unmapped, [(lig_u.LigGlyph, 940)])

    def test_issue3_variable_font_ital_and_slnt_axes_css_generation(self) -> None:
        """
        Issue 3 verification: Verify that Variable Fonts with `ital` (0..1, fixed 0..0, fixed 1..1),
        `slnt` (variable -12..0, variable -12..-5, fixed -12..-12, fixed 0..0), or both `ital` + `slnt`
        generate the right `style_css` / `styles_css` metadata and `@font-face` declarations without
        emitting bogus `normal` rules when the font has no upright 0deg form.
        """
        sample_cps = [ord("A"), ord("あ"), ord("日")]

        def _make_axis(tag: str, mn: float, df: float, mx: float) -> Axis:
            ax = Axis()
            ax.axisTag = tag
            ax.minValue = mn
            ax.defaultValue = df
            ax.maxValue = mx
            ax.axisNameID = 256
            return ax

        def _make_vf_with_axes(family: str, *extra_axes: Axis) -> TTFont:
            f = create_synthetic_ttfont(sample_cps, family_name=family, is_variable=True)
            f["fvar"].axes = [f["fvar"].axes[0], *extra_axes]
            return f

        # 1. ital axis 0..1 -> normal + italic @font-face rules sharing the same WOFF2 files
        tt_ital_01 = _make_vf_with_axes("Ital VF", _make_axis("ital", 0.0, 0.0, 1.0))
        meta_01 = extract_font_metadata(tt_ital_01)
        self.assertEqual(meta_01.style_css, "normal")
        self.assertEqual(meta_01.styles_css, ("normal", "italic"))

        # 2. Fixed ital axis 1..1 -> italic only; fixed ital axis 0..0 -> normal only
        tt_ital_11 = _make_vf_with_axes("Ital Fixed 1", _make_axis("ital", 1.0, 1.0, 1.0))
        meta_11 = extract_font_metadata(tt_ital_11)
        self.assertEqual(meta_11.style_css, "italic")
        self.assertEqual(meta_11.styles_css, ("italic",))

        tt_ital_00 = _make_vf_with_axes("Ital Fixed 0", _make_axis("ital", 0.0, 0.0, 0.0))
        meta_00 = extract_font_metadata(tt_ital_00)
        self.assertEqual(meta_00.style_css, "normal")
        self.assertEqual(meta_00.styles_css, ("normal",))

        # 3. Variable slnt (-12..0) vs Fixed slnt (-12..-12 and 0..0)
        tt_slnt_var = _make_vf_with_axes("Slnt VF", _make_axis("slnt", -12.0, 0.0, 0.0))
        meta_slnt_var = extract_font_metadata(tt_slnt_var)
        self.assertEqual(meta_slnt_var.style_css, "oblique 0deg 12deg")
        self.assertEqual(meta_slnt_var.styles_css, ("oblique 0deg 12deg",))

        tt_slnt_fix = _make_vf_with_axes("Slnt Fixed", _make_axis("slnt", -12.0, -12.0, -12.0))
        meta_slnt_fix = extract_font_metadata(tt_slnt_fix)
        self.assertEqual(meta_slnt_fix.style_css, "oblique 12deg")
        self.assertEqual(meta_slnt_fix.styles_css, ("oblique 12deg",))

        tt_slnt_zero = _make_vf_with_axes("Slnt Zero", _make_axis("slnt", 0.0, 0.0, 0.0))
        meta_slnt_zero = extract_font_metadata(tt_slnt_zero)
        self.assertEqual(meta_slnt_zero.style_css, "normal")
        self.assertEqual(meta_slnt_zero.styles_css, ("normal",))

        # 4. Combined ital + slnt matrix (variable and fixed combinations)
        tt_both = _make_vf_with_axes(
            "Both VF",
            _make_axis("slnt", -12.0, 0.0, 0.0),
            _make_axis("ital", 0.0, 0.0, 1.0),
        )
        meta_both = extract_font_metadata(tt_both)
        self.assertEqual(meta_both.style_css, "normal")
        self.assertEqual(meta_both.styles_css, ("normal", "italic", "oblique 0deg 12deg"))

        combo_cases = [
            # (ital_range, slnt_range, expected_styles_css)
            ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), ("normal",)),
            ((0.0, 0.0, 0.0), (-12.0, -12.0, -12.0), ("oblique 12deg",)),
            ((0.0, 0.0, 0.0), (-12.0, -8.0, -5.0), ("oblique 5deg 12deg",)),
            ((0.0, 0.0, 0.0), (-12.0, 0.0, 0.0), ("oblique 0deg 12deg",)),
            ((1.0, 1.0, 1.0), (0.0, 0.0, 0.0), ("italic",)),
            ((1.0, 1.0, 1.0), (-12.0, -12.0, -12.0), ("italic", "oblique 12deg")),
            ((1.0, 1.0, 1.0), (-12.0, 0.0, 0.0), ("italic", "oblique 0deg 12deg")),
            ((0.0, 0.0, 1.0), (0.0, 0.0, 0.0), ("normal", "italic")),
            ((0.0, 0.0, 1.0), (-12.0, -12.0, -12.0), ("italic", "oblique 12deg")),
            ((0.0, 0.0, 1.0), (-12.0, -8.0, -5.0), ("italic", "oblique 5deg 12deg")),
            ((0.0, 0.0, 1.0), (-12.0, 0.0, 0.0), ("normal", "italic", "oblique 0deg 12deg")),
        ]
        for ital_rng, slnt_rng, expected_styles in combo_cases:
            tt_c = _make_vf_with_axes(
                "Combo VF",
                _make_axis("ital", *ital_rng),
                _make_axis("slnt", *slnt_rng),
            )
            meta_c = extract_font_metadata(tt_c)
            self.assertEqual(
                meta_c.styles_css,
                expected_styles,
                f"Failed for ital={ital_rng}, slnt={slnt_rng}",
            )
            self.assertEqual(meta_c.style_css, expected_styles[0])

        with tempfile.TemporaryDirectory() as tmp:
            res_01 = slice_font(tt_ital_01, out_dir=Path(tmp) / "ital_vf", workers=1)
            self.assertEqual(len(res_01.built_files), 3)  # Only 1 set of WOFF2 files built
            self.assertEqual(res_01.css_text.count("font-style: normal;"), 3)
            self.assertEqual(res_01.css_text.count("font-style: italic;"), 3)

            res_both = slice_font(tt_both, out_dir=Path(tmp) / "both_vf", workers=1)
            self.assertEqual(len(res_both.built_files), 3)
            self.assertEqual(res_both.css_text.count("font-style: normal;"), 3)
            self.assertEqual(res_both.css_text.count("font-style: italic;"), 3)
            self.assertEqual(res_both.css_text.count("font-style: oblique 0deg 12deg;"), 3)

            # Pairing an upright font (ital=0, slnt=0) with a fixed oblique font (ital=0, slnt=-12)
            # of the same family and weight must succeed without descriptor collision
            tt_upright = _make_vf_with_axes(
                "Paired Family",
                _make_axis("ital", 0.0, 0.0, 0.0),
                _make_axis("slnt", 0.0, 0.0, 0.0),
            )
            tt_oblique_12 = _make_vf_with_axes(
                "Paired Family",
                _make_axis("ital", 0.0, 0.0, 0.0),
                _make_axis("slnt", -12.0, -12.0, -12.0),
            )
            res_paired = slice_fonts(
                [tt_upright, tt_oblique_12],
                out_dir=Path(tmp) / "paired_family",
                workers=1,
            )
            self.assertEqual(res_paired.css_text.count("font-style: normal;"), 3)
            self.assertEqual(res_paired.css_text.count("font-style: oblique 12deg;"), 3)

            # Collision check: bundling ital 0..1 VF with an italic-only VF of same weight must raise ValueError
            tt_ital_conflict = _make_vf_with_axes("Ital VF", _make_axis("ital", 1.0, 1.0, 1.0))
            with self.assertRaisesRegex(ValueError, "Duplicate @font-face descriptors"):
                slice_fonts([tt_ital_01, tt_ital_conflict], out_dir=Path(tmp) / "conflict", workers=1)

    def test_issue4_ttfont_input_not_mutated_on_success_or_error(self) -> None:
        """
        Issue 4 verification: Verify that passing an in-memory or lazy `TTFont` to
        `slice_font` or `slice_fonts` does not mutate `maxp` (`numGlyphs`, `maxPoints`),
        `head`, `hhea`, `OS/2`, `cmap`, or any other table on the caller's `TTFont`
        object on either success or error.
        """
        def _structural_snapshot(obj: object) -> object:
            if isinstance(obj, (int, float, str, bytes, bool, type(None))):
                return obj
            if isinstance(obj, (list, tuple)):
                return type(obj)(_structural_snapshot(x) for x in obj)
            if isinstance(obj, (set, frozenset)):
                return type(obj)(_structural_snapshot(x) for x in obj)
            if isinstance(obj, dict):
                return {k: _structural_snapshot(v) for k, v in obj.items()}
            if hasattr(obj, "__dict__"):
                return (type(obj), _structural_snapshot(vars(obj)))
            return repr(obj)

        sample_cps = [ord("A"), ord("あ")]
        tt = create_synthetic_ttfont(
            sample_cps,
            family_name="Immutable Font",
            uvs_only_cp=(0x553E, 0xE0100),
        )
        self.assertEqual(tt["maxp"].numGlyphs, 0)
        self.assertEqual(tt["maxp"].maxPoints, 0)
        snapshot_tables = {tag: _structural_snapshot(tt[tag]) for tag in tt.keys() if tag != "GlyphOrder"}
        snapshot_glyph_order = list(tt.getGlyphOrder())
        snapshot_flavor = tt.flavor
        snapshot_recalc = tt.recalcTimestamp

        def _assert_tt_unchanged() -> None:
            self.assertEqual(tt.flavor, snapshot_flavor)
            self.assertEqual(tt.recalcTimestamp, snapshot_recalc)
            self.assertEqual(list(tt.getGlyphOrder()), snapshot_glyph_order)
            self.assertEqual(tt["maxp"].numGlyphs, 0, "Caller's TTFont maxp.numGlyphs was mutated!")
            self.assertEqual(tt["maxp"].maxPoints, 0, "Caller's TTFont maxp.maxPoints was mutated!")
            for tag, orig_snap in snapshot_tables.items():
                self.assertEqual(
                    _structural_snapshot(tt[tag]),
                    orig_snap,
                    f"Table {tag!r} was mutated on caller's TTFont!",
                )

        with tempfile.TemporaryDirectory() as tmp:
            slice_font(tt, out_dir=Path(tmp) / "ok_out", workers=1)
            _assert_tt_unchanged()

            tt_dup = create_synthetic_ttfont(sample_cps, family_name="Immutable Font")
            with self.assertRaises(ValueError):
                slice_fonts([tt, tt_dup], out_dir=Path(tmp) / "err_out", workers=1)
            _assert_tt_unchanged()

    def test_worker_cache_batch_scheduling_and_byte_identity(self) -> None:
        """
        Verify that per-worker table caching (`cmap`, `hmtx`, `glyphOrder`, `_reverseGlyphOrderDict`),
        heavy-first task scheduling, and multi-font batch export:
        1. Produce byte-identical `.woff2` and `.css` outputs across `workers=1` and `workers=2`
           for fonts with `post` Format 3.0, Format 12 non-BMP cmaps, and Format 14 UVS.
        2. Preserve strict Tier 4 -> Tier 1 cascade order in `built_files` and CSS even when
           Tier 1 slices have more codepoints than Tier 3/4 slices.
        3. Reject duplicate output paths in `_export_woff2_slices_batch` with `ValueError`.
        """
        # Make Tier 1 (latin) larger than Tier 3/4 so heavy-first scheduling runs Tier 1 first
        cps = sorted(
            {
                *range(0x0021, 0x007F),  # 94 Latin chars (heaviest slice)
                ord("あ"),
                ord("い"),
                ord("う"),
                ord("日"),
                0x4E02,
                0x20B9F,  # non-BMP in kanji-freq (Format 12)
                0x20BB7,
            }
        )

        def _make_post3_font(weight: int) -> TTFont:
            f = create_synthetic_ttfont(
                cps,
                family_name="Post3 Cache Font",
                weight=weight,
                uvs_only_cp=(0x553E, 0xE0100),
            )
            f["post"].formatType = 3.0
            return f

        with tempfile.TemporaryDirectory() as tmp:
            p400 = Path(tmp) / "Post3-Regular.ttf"
            p700 = Path(tmp) / "Post3-Bold.ttf"
            _make_post3_font(400).save(p400, reorderTables=False)
            _make_post3_font(700).save(p700, reorderTables=False)

            out_w1 = Path(tmp) / "out_w1"
            out_w2 = Path(tmp) / "out_w2"
            res_w1 = slice_fonts([p400, p700], out_dir=out_w1, workers=1)
            res_w2 = slice_fonts([p400, p700], out_dir=out_w2, workers=2)

            self.assertEqual(res_w1.css_text, res_w2.css_text)
            self.assertEqual(
                [bf.filename for bf in res_w1.built_files],
                [bf.filename for bf in res_w2.built_files],
            )
            for bf1, bf2 in zip(res_w1.built_files, res_w2.built_files):
                self.assertEqual(
                    bf1.path.read_bytes(),
                    bf2.path.read_bytes(),
                    f"WOFF2 bytes differed between workers=1 and workers=2 for {bf1.filename}",
                )
                with TTFont(bf2.path) as woff2_tt:
                    self.assertEqual(
                        set(woff2_tt.getBestCmap().keys()),
                        set(bf2.spec.binary_codepoints),
                        f"Unexpected cmap keys in {bf2.filename}",
                    )

            # Verify cascade order per variant: kanji-rare -> kanji-freq -> kana-core -> latin
            per_variant_len = len(res_w2.built_files) // 2
            for variant_files in (
                res_w2.built_files[:per_variant_len],
                res_w2.built_files[per_variant_len:],
            ):
                categories = [bf.spec.category for bf in variant_files]
                self.assertEqual(
                    categories,
                    ["kanji-rare", "kanji-freq", "kana-core", "latin"],
                )

            # Duplicate output path across variants in _export_woff2_slices_batch -> ValueError
            dup_meta = res_w1.fonts_metadata[0]
            dup_specs = res_w1.variant_slice_specs[0]
            with self.assertRaisesRegex(ValueError, "Duplicate slice output path"):
                _export_woff2_slices_batch(
                    [(p400, dup_meta, dup_specs), (p700, dup_meta, dup_specs)],
                    slices_dir=Path(tmp) / "dup_slices",
                    workers=1,
                )

    def test_css_escaping_and_path_traversal_hardening(self) -> None:
        """
        Verify that:
        1. Font `name` table strings containing `*/` comment closers, single quotes, backslashes,
           or raw newlines (`\\n`, `\\r`, `\\f`) cannot break out of CSS comments or `font-family`
           string tokens in single- or multi-variant `@font-face` output.
        2. Path traversal attempts in `slug`, `css_filename`, or `slices_subdir` raise `ValueError`.
        3. Temporary files created via `NamedTemporaryFile` leave no `.tmp` files behind.
        """
        malicious_family = "Evil */ @import url('https://evil.example/a.css'); /* Font'\n  src: url('x');\r\f\\End"
        f400 = create_synthetic_ttfont([ord("A"), ord("あ")], family_name=malicious_family, weight=400)
        f700 = create_synthetic_ttfont([ord("A"), ord("あ")], family_name=malicious_family, weight=700)

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "hardened_out"
            old_umask = os.umask(0o022)
            try:
                res = slice_fonts(
                    [f400, f700],
                    out_dir=out_dir,
                    config=SliceConfig(url_prefix="https://cdn.example/fonts'\n/v1"),
                    workers=1,
                )
            finally:
                os.umask(old_umask)
            # Every comment line must have exactly one opening `/*` and one closing `*/` at the end
            for line in res.css_text.splitlines():
                stripped = line.strip()
                if stripped.startswith("/*"):
                    self.assertTrue(stripped.endswith("*/"), f"Unclosed comment line: {line!r}")
                    inner = stripped[2:-2]
                    self.assertNotIn("*/", inner, f"Unescaped comment closer inside comment: {line!r}")
                if stripped.startswith("font-family:") or stripped.startswith("src:"):
                    self.assertNotIn("\n", stripped)
                    self.assertNotIn("\r", stripped)
                    self.assertNotIn("\f", stripped)

            self.assertIn(r"\'\a   src: url(\'x\');\d \c \\End", res.css_text)
            self.assertIn(r"url('https://cdn.example/fonts\'\a /v1/", res.css_text)

            # Verify published CSS and WOFF2 files are world-readable (0o644 under umask 0o022)
            for p in (res.css_path, *(bf.path for bf in res.built_files)):
                self.assertEqual(p.stat().st_mode & 0o777, 0o644, f"Unexpected mode for {p.name}")

            # No leftover temporary files
            leftover_tmps = list(out_dir.rglob("*.tmp*"))
            self.assertEqual(leftover_tmps, [])

            # Path traversal rejections
            with self.assertRaisesRegex(ValueError, "slices_subdir"):
                SliceConfig(slices_subdir="../escape")
            with self.assertRaisesRegex(ValueError, "slices_subdir"):
                SliceConfig(slices_subdir="/etc/escape")
            with self.assertRaisesRegex(ValueError, "slug"):
                slice_font(f400, out_dir=out_dir, slug="../escape", workers=1)
            with self.assertRaisesRegex(ValueError, "css_filename"):
                slice_font(f400, out_dir=out_dir, css_filename="../escape.css", workers=1)

    def test_build_cache_skip_subsetting_and_invalidation(self) -> None:
        """
        Verify build caching across single- and multi-font bundles:
        1. Second invocation with identical inputs and `SliceConfig` skips `_export_woff2_slices_batch`,
           returns `cached=True`, and preserves `built_files` (`size_bytes`, `glyph_count`, `spec`,
           `variant_suffix`), `slice_specs`, `variant_slice_specs`, and `css_text` without creating
           extra files in `out_dir` or `slices_dir`.
        2. Changing `SliceConfig` (e.g. `rare_kanji_chunk_size`, `locale`, `url_prefix`), override args
           (`weight`, `family_name`), or input font bytes invalidates the cache and triggers a rebuild.
        3. Deleting or truncating/corrupting any `.woff2` slice or modifying the CSS file invalidates
           the cache and rebuilds the intact files.
        4. `force=True` bypasses a valid cache and rebuilds.
        """
        from unittest.mock import patch

        cps_reg = [ord("A"), ord("あ"), ord("日"), 0x4E02, 0x4E04]
        cps_bold = [ord("A"), ord("あ"), ord("日"), 0x4E02, 0x4E04, 0x4E05]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            p_reg = tmp_path / "Cache-Regular.ttf"
            p_bold = tmp_path / "Cache-Bold.ttf"
            create_synthetic_ttfont(cps_reg, family_name="Cache Sans", weight=400).save(
                p_reg, reorderTables=False
            )
            create_synthetic_ttfont(cps_bold, family_name="Cache Sans", weight=700).save(
                p_bold, reorderTables=False
            )

            out_dir = tmp_path / "dist_cache"

            # 1. Initial build -> cached=False
            res1 = slice_fonts([p_reg, p_bold], out_dir=out_dir, workers=1)
            self.assertFalse(res1.cached)
            self.assertTrue(res1.css_path.exists())
            css_lines = res1.css_text.splitlines()
            self.assertEqual(len(css_lines[1]), 96)
            self.assertRegex(
                css_lines[1],
                r"^/\* cleome-cache: v1 key=[0-9a-f]{32} out=[0-9a-f]{32} \*/$",
            )
            self.assertIn(" glyphs, ", css_lines[4])
            # Ensure no dotfile or JSON manifest polluted out_dir or slices_dir
            self.assertEqual([p.name for p in out_dir.iterdir() if p.is_file()], [res1.css_path.name])
            self.assertTrue(all(p.suffix == ".woff2" for p in res1.slices_dir.iterdir()))

            # 2. Second call -> skips _export_woff2_slices_batch completely and returns identical metadata
            with patch(
                "cleome.css._export_woff2_slices_batch",
                side_effect=AssertionError("_export_woff2_slices_batch should be skipped on cache hit"),
            ) as mock_export:
                res2 = slice_fonts([p_reg, p_bold], out_dir=out_dir, workers=1)
                self.assertEqual(mock_export.call_count, 0)

            self.assertTrue(res2.cached)
            self.assertEqual(res2.css_text, res1.css_text)
            self.assertEqual(res2.total_woff2_bytes, res1.total_woff2_bytes)
            self.assertEqual(res2.slice_specs, res1.slice_specs)
            self.assertEqual(res2.variant_slice_specs, res1.variant_slice_specs)
            self.assertEqual(res2.built_files, res1.built_files)

            # 3. Changing SliceConfig (e.g. rare_kanji_chunk_size=1) invalidates cache -> cached=False
            res_cfg = slice_fonts(
                [p_reg, p_bold],
                out_dir=out_dir,
                config=SliceConfig(rare_kanji_chunk_size=1),
                workers=1,
            )
            self.assertFalse(res_cfg.cached)
            self.assertGreater(len(res_cfg.built_files), len(res1.built_files))

            # Repeat with rare_kanji_chunk_size=1 -> cached=True
            res_cfg_hit = slice_fonts(
                [p_reg, p_bold],
                out_dir=out_dir,
                config=SliceConfig(rare_kanji_chunk_size=1),
                workers=1,
            )
            self.assertTrue(res_cfg_hit.cached)

            # 4. Missing .woff2 slice invalidates cache and restores the deleted file
            victim = res_cfg_hit.built_files[0].path
            victim.unlink()
            res_missing = slice_fonts(
                [p_reg, p_bold],
                out_dir=out_dir,
                config=SliceConfig(rare_kanji_chunk_size=1),
                workers=1,
            )
            self.assertFalse(res_missing.cached)
            self.assertTrue(victim.exists())

            # 5. Truncated / corrupted .woff2 slice (both size mismatch and same-size content corruption)
            orig_bytes = victim.read_bytes()
            victim.write_bytes(orig_bytes[:10])
            res_corrupt = slice_fonts(
                [p_reg, p_bold],
                out_dir=out_dir,
                config=SliceConfig(rare_kanji_chunk_size=1),
                workers=1,
            )
            self.assertFalse(res_corrupt.cached)
            self.assertEqual(victim.read_bytes(), orig_bytes)

            # 5b. Same-size content modification (e.g. overwritten by another build with identical byte length)
            flipped = bytes([orig_bytes[0] ^ 0xFF]) + orig_bytes[1:]
            self.assertEqual(len(flipped), len(orig_bytes))
            victim.write_bytes(flipped)
            res_same_size_corrupt = slice_fonts(
                [p_reg, p_bold],
                out_dir=out_dir,
                config=SliceConfig(rare_kanji_chunk_size=1),
                workers=1,
            )
            self.assertFalse(res_same_size_corrupt.cached)
            self.assertEqual(victim.read_bytes(), orig_bytes)

            # 6. Modified input font binary invalidates cache
            create_synthetic_ttfont([*cps_reg, ord("B")], family_name="Cache Sans", weight=400).save(
                p_reg, reorderTables=False
            )
            res_font_changed = slice_fonts(
                [p_reg, p_bold],
                out_dir=out_dir,
                config=SliceConfig(rare_kanji_chunk_size=1),
                workers=1,
            )
            self.assertFalse(res_font_changed.cached)

            # 7. force=True bypasses cache even when everything is up to date
            res_forced = slice_fonts(
                [p_reg, p_bold],
                out_dir=out_dir,
                config=SliceConfig(rare_kanji_chunk_size=1),
                workers=1,
                force=True,
            )
            self.assertFalse(res_forced.cached)

            # 8. Custom slugs containing ',', ':', or '*' hit the cache on second build
            for special_slug in ("cache,font", "cache:font", "cache*font"):
                special_out = tmp_path / f"out_{special_slug}"
                r1 = slice_font(p_reg, out_dir=special_out, slug=special_slug, workers=1)
                r2 = slice_font(p_reg, out_dir=special_out, slug=special_slug, workers=1)
                self.assertFalse(r1.cached)
                self.assertTrue(r2.cached, f"Expected cache hit for slug={special_slug!r}")


if __name__ == "__main__":
    unittest.main()
