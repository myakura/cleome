"""
Tests for the `cleome` CLI (`plan` and `build` subcommands, JSON output, and multiprocessing guards).
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from fontTools.ttLib.tables.DefaultTable import DefaultTable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from helpers import create_synthetic_ttfont

from cleome.cli import main as cli_main


class TestCleomeCLI(unittest.TestCase):
    def test_cli_validation_and_plan(self) -> None:
        """
        Verify CLI argument parsing for `cleome plan` and `cleome build`, `--freq-chunks none`,
        `allow_abbrev=False`, mutually exclusive `--css-name`/`--css-out`, and validation errors.
        """
        sample_cps = [ord("A"), ord("あ"), ord("日")]
        with tempfile.TemporaryDirectory() as tmp:
            font_path = Path(tmp) / "CliTest.ttf"
            create_synthetic_ttfont(sample_cps, family_name="CLI Test").save(
                font_path, reorderTables=False
            )

            # 1. --freq-chunks none and --no-uvs-base-repair on `plan`
            buf = StringIO()
            with redirect_stdout(buf):
                rc = cli_main(
                    [
                        "plan",
                        "--freq-chunks",
                        "none",
                        "--no-uvs-base-repair",
                        str(font_path),
                    ]
                )
            self.assertEqual(rc, 0)
            self.assertIn("3 chars -> 3 slices", buf.getvalue())

            # 1b. Build-only flags (--drop-tables, --display, --slices-dir) are rejected on `plan` with SystemExit(2)
            for build_only_flag in (
                ["--drop-tables", "DSIG"],
                ["--display", "optional"],
                ["--slices-dir", "sub"],
            ):
                with (
                    redirect_stderr(StringIO()),
                    self.assertRaises(SystemExit) as cm_bo,
                ):
                    cli_main(["plan", *build_only_flag, str(font_path)])
                self.assertEqual(cm_bo.exception.code, 2)

            # 1c. Abbreviated flags (--freq-chunk) are rejected when allow_abbrev=False
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as cm_abbr:
                cli_main(["plan", "--freq-chunk", "200x2", str(font_path)])
            self.assertEqual(cm_abbr.exception.code, 2)

            # 2. Invalid --freq-chunks (0 or negative) must fail with SystemExit(2)
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as cm:
                cli_main(["plan", "--freq-chunks", "200x0", str(font_path)])
            self.assertEqual(cm.exception.code, 2)

            with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as cm2:
                cli_main(["plan", "--rare-chunk-size", "0", str(font_path)])
            self.assertEqual(cm2.exception.code, 2)

            # 3. Invoking without a subcommand (`plan` or `build`) fails with SystemExit(2)
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as cm3:
                cli_main([str(font_path)])
            self.assertEqual(cm3.exception.code, 2)

    def test_cli_plan_and_build_subcommands(self) -> None:
        """
        Verify agent-first CLI subcommands:
        1. `cleome plan <inputs...> [--locale LOCALE] [--json]` is strictly read-only
           and never creates `./dist`.
        2. `cleome plan --json` returns a structured JSON envelope with Tier 1-4 slice counts,
           codepoint counts, `unicode-range` character lengths, and detected locale.
        3. `cleome build <inputs...> -o <dir> [--locale LOCALE] [--json]` writes
           the CSS and `.woff2` slices and supports `--json`.
        """
        sample_cps = [ord("A"), ord("あ"), ord("日"), 0x4E02]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            font_path = tmp_path / "SubcmdTest.ttf"
            create_synthetic_ttfont(sample_cps, family_name="Subcmd Test").save(
                font_path, reorderTables=False
            )

            orig_cwd = Path.cwd()
            try:
                os.chdir(tmp_path)

                # 1. `cleome plan` must NOT create ./dist or import css / subsetter / pipeline
                buf_plan = StringIO()
                with redirect_stdout(buf_plan):
                    rc_plan = cli_main(["plan", str(font_path)])
                self.assertEqual(rc_plan, 0)
                self.assertIn(
                    "Font Family : Subcmd Test (slug: subcmd-test)", buf_plan.getvalue()
                )
                self.assertIn("4 chars -> 4 slices", buf_plan.getvalue())
                self.assertFalse(
                    (tmp_path / "dist").exists(), "`cleome plan` must not create ./dist"
                )

                src_dir = Path(__file__).resolve().parent.parent / "src"
                uvs_font_path = tmp_path / "SubcmdUvsTest.ttf"
                create_synthetic_ttfont(
                    sample_cps,
                    family_name="Subcmd Test",
                    uvs_only_cp=(0x908A, 0xE0100),
                ).save(uvs_font_path, reorderTables=False)
                plan_iso = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        (
                            f"import sys; sys.path.insert(0, {str(src_dir)!r}); "
                            f"import cleome; "
                            f"_ = cleome.patch_uvs_missing_base_cmap; "
                            f"from cleome.cli import main; "
                            f"rc = main(['plan', {str(uvs_font_path)!r}]); "
                            f"assert rc == 0; "
                            f"assert 'cleome.css' not in sys.modules; "
                            f"assert 'cleome.subsetter' not in sys.modules; "
                            f"assert 'cleome.pipeline' not in sys.modules; "
                            f"from cleome.urange import compact_unicode_range, format_unicode_range, parse_unicode_range; "
                            f"from cleome.metadata import extract_font_face_descriptors, patch_uvs_missing_base_cmap, validate_and_compute_variant_suffixes"
                        ),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(
                    plan_iso.returncode,
                    0,
                    f"Plan import isolation failed: {plan_iso.stderr}",
                )

                # 2. `cleome plan --json --locale kr`
                buf_plan_json = StringIO()
                with redirect_stdout(buf_plan_json):
                    rc_plan_json = cli_main(
                        [
                            "plan",
                            str(font_path),
                            "--locale",
                            "kr",
                            "--rare-chunk-size",
                            "120",
                            "--json",
                        ]
                    )
                self.assertEqual(rc_plan_json, 0)
                plan_data = json.loads(buf_plan_json.getvalue())
                self.assertTrue(plan_data["ok"])
                self.assertEqual(plan_data["command"], "plan")
                self.assertEqual(plan_data["locale"], "kr")
                self.assertEqual(plan_data["configured_locale"], "kr")
                self.assertEqual(plan_data["file_count"], 1)
                variant_info = plan_data["variants"][0]
                self.assertEqual(variant_info["codepoints"], 4)
                for tier_key in ("1", "2", "3", "4"):
                    self.assertIn(tier_key, variant_info["tiers"])
                    self.assertIn("slices", variant_info["tiers"][tier_key])
                    self.assertIn("codepoints", variant_info["tiers"][tier_key])
                    self.assertIn(
                        "unicode_range_chars", variant_info["tiers"][tier_key]
                    )
                self.assertFalse(
                    (tmp_path / "dist").exists(),
                    "`cleome plan --json` must not create ./dist",
                )

                # 3. `cleome build <input> -o <dir> --json` (content-hashed filenames by default + preload_slices)
                build_out = tmp_path / "built_subcmd"
                buf_build_json = StringIO()
                with redirect_stdout(buf_build_json):
                    rc_build = cli_main(
                        [
                            "build",
                            str(font_path),
                            "-o",
                            str(build_out),
                            "--locale",
                            "jp",
                            "-j",
                            "1",
                            "--json",
                        ]
                    )
                self.assertEqual(rc_build, 0)
                build_data = json.loads(buf_build_json.getvalue())
                self.assertTrue(build_data["ok"])
                self.assertEqual(build_data["command"], "build")
                self.assertFalse(build_data["cached"])
                self.assertEqual(build_data["locale"], "jp")
                self.assertTrue(build_data["filename_hash"])
                self.assertEqual(build_data["slice_count"], 4)
                self.assertEqual(
                    [p["slice_id"] for p in build_data["preload_slices"]],
                    ["kana-core", "latin"],
                )
                for p in build_data["preload_slices"]:
                    self.assertRegex(
                        p["filename"],
                        r"^subcmd-test-(kana-core|latin)-[0-9a-f]{8}\.woff2$",
                    )
                    self.assertEqual(p["url"], f"./slices/{p['filename']}")
                self.assertTrue((build_out / "subcmd-test.css").exists())
                self.assertEqual(len(list((build_out / "slices").glob("*.woff2"))), 4)

                # 3b. Second `cleome build` hits the cache (`cached: true` in JSON, `Cached ... (up to date)` in text)
                buf_cached_json = StringIO()
                with redirect_stdout(buf_cached_json):
                    rc_cached = cli_main(
                        [
                            "build",
                            str(font_path),
                            "-o",
                            str(build_out),
                            "--locale",
                            "jp",
                            "-j",
                            "1",
                            "--json",
                        ]
                    )
                self.assertEqual(rc_cached, 0)
                cached_data = json.loads(buf_cached_json.getvalue())
                self.assertTrue(cached_data["cached"])
                self.assertEqual(cached_data["slice_count"], 4)
                self.assertEqual(
                    cached_data["total_woff2_bytes"], build_data["total_woff2_bytes"]
                )

                buf_cached_text = StringIO()
                with redirect_stdout(buf_cached_text):
                    rc_cached_txt = cli_main(
                        [
                            "build",
                            str(font_path),
                            "-o",
                            str(build_out),
                            "--locale",
                            "jp",
                            "-j",
                            "1",
                        ]
                    )
                self.assertEqual(rc_cached_txt, 0)
                self.assertIn(
                    "[cleome] Cached 4 WOFF2 slices", buf_cached_text.getvalue()
                )
                self.assertIn("(up to date)", buf_cached_text.getvalue())
                self.assertIn("Preload (Tier 1):", buf_cached_text.getvalue())
                self.assertIn(
                    'as="font" type="font/woff2" crossorigin>',
                    buf_cached_text.getvalue(),
                )

                # 3c. `cleome build --no-filename-hash` writes unhashed filenames and hits cache on repeat
                unhashed_out = tmp_path / "built_unhashed"
                buf_unhashed_json = StringIO()
                with redirect_stdout(buf_unhashed_json):
                    rc_unhashed = cli_main(
                        [
                            "build",
                            str(font_path),
                            "-o",
                            str(unhashed_out),
                            "--locale",
                            "jp",
                            "--no-filename-hash",
                            "-j",
                            "1",
                            "--json",
                        ]
                    )
                self.assertEqual(rc_unhashed, 0)
                unhashed_data = json.loads(buf_unhashed_json.getvalue())
                self.assertFalse(unhashed_data["filename_hash"])
                self.assertEqual(
                    [p["filename"] for p in unhashed_data["preload_slices"]],
                    ["subcmd-test-kana-core.woff2", "subcmd-test-latin.woff2"],
                )
                buf_unhashed_cached = StringIO()
                with redirect_stdout(buf_unhashed_cached):
                    rc_unhashed_2 = cli_main(
                        [
                            "build",
                            str(font_path),
                            "-o",
                            str(unhashed_out),
                            "--locale",
                            "jp",
                            "--no-filename-hash",
                            "-j",
                            "1",
                            "--json",
                        ]
                    )
                self.assertEqual(rc_unhashed_2, 0)
                self.assertTrue(json.loads(buf_unhashed_cached.getvalue())["cached"])

                # 3d. `cleome build --css-out` writes stylesheet to a separate directory and hits cache on repeat
                sep_slices_out = tmp_path / "public_fonts"
                sep_css_out = tmp_path / "src" / "styles" / "custom-fonts.css"
                buf_css_out = StringIO()
                with redirect_stdout(buf_css_out):
                    rc_css_out = cli_main(
                        [
                            "build",
                            str(font_path),
                            "-o",
                            str(sep_slices_out),
                            "--url-prefix",
                            "/fonts/slices",
                            "--css-out",
                            str(sep_css_out),
                            "-j",
                            "1",
                            "--json",
                        ]
                    )
                self.assertEqual(rc_css_out, 0)
                css_out_data = json.loads(buf_css_out.getvalue())
                self.assertFalse(css_out_data["cached"])
                self.assertEqual(Path(css_out_data["css_path"]), sep_css_out)
                self.assertTrue(sep_css_out.is_file())
                self.assertIn(
                    "url('/fonts/slices/subcmd-test-",
                    sep_css_out.read_text(encoding="utf-8"),
                )

                buf_css_out_2 = StringIO()
                with redirect_stdout(buf_css_out_2):
                    rc_css_out_2 = cli_main(
                        [
                            "build",
                            str(font_path),
                            "-o",
                            str(sep_slices_out),
                            "--url-prefix",
                            "/fonts/slices",
                            "--css-out",
                            str(sep_css_out),
                            "-j",
                            "1",
                            "--json",
                        ]
                    )
                self.assertEqual(rc_css_out_2, 0)
                self.assertTrue(json.loads(buf_css_out_2.getvalue())["cached"])

                # 3d-rel. `cleome build --css-out` without `--url-prefix` computes a relative URL prefix to `slices_dir`
                rel_css_out = tmp_path / "src" / "styles" / "rel-fonts.css"
                buf_rel_css = StringIO()
                with redirect_stdout(buf_rel_css):
                    rc_rel_css = cli_main(
                        [
                            "build",
                            str(font_path),
                            "-o",
                            str(sep_slices_out),
                            "--css-out",
                            str(rel_css_out),
                            "-j",
                            "1",
                            "--json",
                        ]
                    )
                self.assertEqual(rc_rel_css, 0)
                rel_css_data = json.loads(buf_rel_css.getvalue())
                self.assertIn(
                    "url('../../public_fonts/slices/subcmd-test-",
                    rel_css_out.read_text(encoding="utf-8"),
                )
                for item in rel_css_data["preload_slices"]:
                    self.assertTrue(
                        item["url"].startswith("../../public_fonts/slices/")
                    )
                    self.assertTrue((rel_css_out.parent / item["url"]).is_file())

                # 3d-sym. `css_out` pointing to a symlink or directory with `#` / `?` encodes URL segments and resolves parent
                hash_slices_out = tmp_path / "fonts#v1?test"
                sym_target_dir = tmp_path / "deep" / "nested"
                sym_target_dir.mkdir(parents=True, exist_ok=True)
                old_target_css = sym_target_dir / "old.css"
                old_target_css.write_text("/* old */", encoding="utf-8")
                sym_css_out = tmp_path / "src" / "styles" / "sym-fonts.css"
                sym_css_out.symlink_to(old_target_css)
                buf_sym_css = StringIO()
                with redirect_stdout(buf_sym_css):
                    rc_sym_css = cli_main(
                        [
                            "build",
                            str(font_path),
                            "-o",
                            str(hash_slices_out),
                            "--css-out",
                            str(sym_css_out),
                            "-j",
                            "1",
                            "--json",
                        ]
                    )
                self.assertEqual(rc_sym_css, 0)
                self.assertFalse(sym_css_out.is_symlink())
                self.assertIn(
                    "url('../../fonts%23v1%3Ftest/slices/subcmd-test-",
                    sym_css_out.read_text(encoding="utf-8"),
                )

                # 3d-symdir. Symlinked parent directory preserves logical URL path; cross-drive raises ValueError
                from unittest.mock import patch as mock_patch

                shared_styles = tmp_path / "shared" / "styles"
                shared_styles.mkdir(parents=True, exist_ok=True)
                site_dir = tmp_path / "site"
                site_dir.mkdir(parents=True, exist_ok=True)
                (site_dir / "styles").symlink_to(
                    shared_styles, target_is_directory=True
                )
                symdir_css = site_dir / "styles" / "site-fonts.css"
                buf_symdir = StringIO()
                with redirect_stdout(buf_symdir):
                    rc_symdir = cli_main(
                        [
                            "build",
                            str(font_path),
                            "-o",
                            str(site_dir / "fonts"),
                            "--css-out",
                            str(symdir_css),
                            "-j",
                            "1",
                            "--json",
                        ]
                    )
                self.assertEqual(rc_symdir, 0)
                self.assertIn(
                    "url('../fonts/slices/subcmd-test-",
                    symdir_css.read_text(encoding="utf-8"),
                )

                err_xdrive = StringIO()
                with (
                    mock_patch(
                        "os.path.relpath", side_effect=ValueError("cross-drive")
                    ),
                    redirect_stderr(err_xdrive),
                ):
                    rc_xdrive = cli_main(
                        [
                            "build",
                            str(font_path),
                            "-o",
                            str(site_dir / "fonts"),
                            "--css-out",
                            str(symdir_css),
                            "-j",
                            "1",
                        ]
                    )
                self.assertEqual(rc_xdrive, 2)
                self.assertIn("pass an explicit --url-prefix", err_xdrive.getvalue())

                # 3e. `cleome build --force` bypasses cache and rebuilds (`cached: false`)
                buf_force_json = StringIO()
                with redirect_stdout(buf_force_json):
                    rc_force = cli_main(
                        [
                            "build",
                            str(font_path),
                            "-o",
                            str(build_out),
                            "--locale",
                            "jp",
                            "-j",
                            "1",
                            "--force",
                            "--json",
                        ]
                    )
                self.assertEqual(rc_force, 0)
                force_data = json.loads(buf_force_json.getvalue())
                self.assertFalse(force_data["cached"])
                self.assertFalse(force_data["dry_run"])

                # 3f. `cleome build --dry-run` validates inputs and paths without creating directories or files
                dry_out = tmp_path / "never_created_dir"
                dry_css = tmp_path / "never_created_css" / "fonts.css"
                buf_dry_json = StringIO()
                with redirect_stdout(buf_dry_json):
                    rc_dry_json = cli_main(
                        [
                            "build",
                            str(font_path),
                            "-o",
                            str(dry_out),
                            "--css-out",
                            str(dry_css),
                            "--dry-run",
                            "--json",
                        ]
                    )
                self.assertEqual(rc_dry_json, 0)
                self.assertFalse(
                    dry_out.exists(), "build --dry-run must not create --out-dir"
                )
                self.assertFalse(
                    dry_css.parent.exists(),
                    "build --dry-run must not create --css-out parent",
                )
                dry_data = json.loads(buf_dry_json.getvalue())
                self.assertTrue(dry_data["ok"])
                self.assertTrue(dry_data["dry_run"])
                self.assertEqual(dry_data["slice_count"], 4)
                self.assertEqual(
                    [p["filename"] for p in dry_data["preload_slices"]],
                    [
                        "subcmd-test-kana-core-<hash8>.woff2",
                        "subcmd-test-latin-<hash8>.woff2",
                    ],
                )

                buf_dry_txt = StringIO()
                with redirect_stdout(buf_dry_txt):
                    rc_dry_txt = cli_main(
                        ["build", str(font_path), "-o", str(dry_out), "--dry-run"]
                    )
                self.assertEqual(rc_dry_txt, 0)
                self.assertIn(
                    "Dry run: would build 4 WOFF2 slices", buf_dry_txt.getvalue()
                )
                self.assertFalse(dry_out.exists())

                # 4. Exit codes: 2 for invalid arguments/CSS/path tokens, 3 for PermissionError, 4 for missing/corrupt input, 1 for runtime error
                with (
                    redirect_stderr(StringIO()),
                    self.assertRaises(SystemExit) as cm_mutex,
                ):
                    cli_main(
                        [
                            "build",
                            str(font_path),
                            "-o",
                            str(build_out),
                            "--css-name",
                            "a.css",
                            "--css-out",
                            str(tmp_path / "b.css"),
                        ]
                    )
                self.assertEqual(cm_mutex.exception.code, 2)

                blocker_file = tmp_path / "blocker_file"
                blocker_file.write_text("file", encoding="utf-8")
                slices_conflict_dir = tmp_path / "slices_conflict_out"
                slices_conflict_dir.mkdir(exist_ok=True)
                (slices_conflict_dir / "slices").write_text(
                    "not a dir", encoding="utf-8"
                )
                (build_out / "existing_dir.css").mkdir(exist_ok=True)

                for bad_args, expected_err in (
                    (
                        [
                            "build",
                            str(font_path),
                            "-o",
                            str(build_out),
                            "--display",
                            "   ",
                        ],
                        "font_display cannot be empty",
                    ),
                    (
                        [
                            "build",
                            str(font_path),
                            "-o",
                            str(build_out),
                            "--slices-dir",
                            "../outside",
                        ],
                        "slices_subdir",
                    ),
                    (["plan", str(font_path), "--slug", "../outside"], "slug"),
                    (
                        ["plan", str(font_path), "--weight", "400; } @import url(x)"],
                        "font-weight",
                    ),
                    (
                        [
                            "build",
                            str(font_path),
                            "-o",
                            str(build_out),
                            "--css-name",
                            "../outside.css",
                        ],
                        "css_filename",
                    ),
                    (
                        [
                            "build",
                            str(font_path),
                            "-o",
                            str(build_out),
                            "--css-out",
                            str(build_out),
                        ],
                        "css_path must be a file path, not an existing directory",
                    ),
                    (
                        [
                            "build",
                            str(font_path),
                            "-o",
                            str(build_out),
                            "--css-out",
                            str(build_out),
                            "--dry-run",
                        ],
                        "css_path must be a file path, not an existing directory",
                    ),
                    (
                        [
                            "build",
                            str(font_path),
                            "-o",
                            str(blocker_file / "sub"),
                            "--dry-run",
                        ],
                        "Cannot create out_dir under existing file",
                    ),
                    (
                        [
                            "build",
                            str(font_path),
                            "-o",
                            str(slices_conflict_dir),
                            "--dry-run",
                        ],
                        "slices_dir must be a directory path, not an existing file",
                    ),
                    (
                        [
                            "build",
                            str(font_path),
                            "-o",
                            str(build_out),
                            "--css-name",
                            "existing_dir.css",
                            "--dry-run",
                        ],
                        "css_path must be a file path, not an existing directory",
                    ),
                    (
                        [
                            "build",
                            str(font_path),
                            "-o",
                            str(dry_out),
                            "--slices-dir",
                            ".",
                            "--no-filename-hash",
                            "--css-name",
                            "subcmd-test-latin.woff2",
                            "--dry-run",
                        ],
                        "collides with planned slice output",
                    ),
                ):
                    err_buf = StringIO()
                    with redirect_stderr(err_buf):
                        rc_bad = cli_main(bad_args)
                    self.assertEqual(rc_bad, 2, f"Expected exit code 2 for {bad_args}")
                    self.assertIn(expected_err, err_buf.getvalue())

                # Exit code 4: missing input file, directory passed as input, or corrupt font file
                corrupt_font = tmp_path / "corrupt.ttf"
                corrupt_font.write_bytes(b"not a valid opentype font")
                for input_err_args in (
                    ["plan", str(tmp_path / "missing.ttf")],
                    ["build", str(tmp_path), "-o", str(build_out)],
                    ["plan", str(corrupt_font)],
                    ["build", str(corrupt_font), "-o", str(tmp_path / "corrupt_out")],
                ):
                    with redirect_stderr(StringIO()):
                        self.assertEqual(
                            cli_main(input_err_args),
                            4,
                            f"Expected exit code 4 for {input_err_args}",
                        )
                self.assertFalse((tmp_path / "corrupt_out").exists())

                # Exit code 3: PermissionError
                with (
                    mock_patch(
                        "cleome.pipeline.slice_fonts",
                        side_effect=PermissionError(13, "Denied", str(build_out)),
                    ),
                    redirect_stderr(StringIO()),
                ):
                    self.assertEqual(
                        cli_main(["build", str(font_path), "-o", str(build_out)]), 3
                    )

                # Exit code 1: RuntimeError or output-side OSError (not input font)
                for runtime_exc in (
                    RuntimeError("worker pool failed"),
                    FileNotFoundError(
                        2,
                        "No such file or directory",
                        str(build_out / "missing_tmp.woff2"),
                    ),
                ):
                    with (
                        mock_patch(
                            "cleome.pipeline.slice_fonts", side_effect=runtime_exc
                        ),
                        redirect_stderr(StringIO()),
                    ):
                        self.assertEqual(
                            cli_main(["build", str(font_path), "-o", str(build_out)]), 1
                        )
            finally:
                os.chdir(orig_cwd)

    def test_subprocess_script_with_and_without_main_guard(self) -> None:
        """
        Verify that:
        1. A top-level script with `if __name__ == "__main__":` and `workers=2` succeeds.
        2. A top-level script WITHOUT `if __name__ == "__main__":` on a multi-MB font (triggering
           BrokenPipeError in the parent while sending initargs) exits with our wrapped RuntimeError
           as the final line of stderr.
        """
        sample_cps = [ord("A"), ord("あ"), ord("日"), 0x4E00]
        with tempfile.TemporaryDirectory() as tmp:
            font_path = Path(tmp) / "SubprocFont.ttf"
            out_dir = Path(tmp) / "subproc_out"
            tt = create_synthetic_ttfont(sample_cps, family_name="Subproc Font")
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
            self.assertEqual(
                proc_ok.returncode, 0, f"Subprocess failed: {proc_ok.stderr}"
            )

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
            stderr_lines = [
                ln
                for ln in proc_bad.stderr.strip().splitlines()
                if ln.strip()
                and "resource_tracker:" not in ln
                and not ln.lstrip().startswith("warnings.warn(")
            ]
            self.assertTrue(
                stderr_lines, "Expected non-empty stderr from unguarded script"
            )
            self.assertIn(
                "RuntimeError: Failed to run parallel WOFF2 worker processes.",
                stderr_lines[-1],
                f"Final stderr line did not contain wrapped RuntimeError. Got:\n{proc_bad.stderr}",
            )

    def test_package_version_matches_metadata(self) -> None:
        """
        Verify that `cleome.__version__` matches the installed package metadata version.
        """
        import importlib.metadata

        import cleome

        self.assertEqual(cleome.__version__, importlib.metadata.version("cleome"))

    def test_consumer_skill_and_readme_stay_in_sync_with_public_api(self) -> None:
        """
        Verify that `skills/cleome/SKILL.md` and `README.md` stay synchronized with `cleome.__all__`,
        public function/dataclass signatures, and runnable Python code blocks.
        """
        import ast
        import inspect
        import re

        import cleome

        repo_root = Path(__file__).resolve().parent.parent
        src_dir = repo_root / "src"
        skill_path = repo_root / "skills" / "cleome" / "SKILL.md"
        readme_path = repo_root / "README.md"
        skill_text = skill_path.read_text(encoding="utf-8")
        readme_text = readme_path.read_text(encoding="utf-8")

        # 1. Every runtime symbol in `cleome.__all__` must be documented in `skills/cleome/SKILL.md`
        missing_symbols = [sym for sym in cleome.__all__ if sym not in skill_text]
        self.assertFalse(
            missing_symbols,
            f"Public exports in cleome.__all__ missing from skills/cleome/SKILL.md: {missing_symbols}",
        )

        # 2. Every public parameter of core entry points and dataclasses must appear in `skills/cleome/SKILL.md`
        core_callables = (
            cleome.SliceConfig,
            cleome.slice_font,
            cleome.slice_fonts,
            cleome.build_slice_plan,
            cleome.detect_font_locale,
            cleome.extract_font_face_descriptors,
            cleome.extract_font_metadata,
            cleome.export_woff2_slices,
            cleome.generate_css,
            cleome.compact_unicode_range,
            cleome.format_unicode_range,
            cleome.parse_unicode_range,
            cleome.patch_uvs_missing_base_cmap,
            cleome.slugify_family,
        )
        missing_params: list[str] = []
        for fn in core_callables:
            sig = inspect.signature(fn)
            for param_name in sig.parameters:
                if param_name.startswith("_"):
                    continue
                if param_name not in skill_text:
                    missing_params.append(f"{fn.__name__}({param_name}=...)")
        self.assertFalse(
            missing_params,
            f"Public parameters missing from skills/cleome/SKILL.md: {missing_params}",
        )

        # 3. Every fenced `python` code block in `SKILL.md` and `README.md` must parse and execute cleanly
        code_block_re = re.compile(r"```python\n(.*?)```", re.DOTALL)
        sample_cps = [ord("A"), ord("あ"), ord("日"), 0x4E02]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_synthetic_ttfont(
                sample_cps, family_name="Source Han Sans JP", is_variable=True
            ).save(tmp_path / "SourceHanSansJP-VF.otf", reorderTables=False)
            create_synthetic_ttfont(
                sample_cps, family_name="My Font JP", is_variable=False, weight=400
            ).save(tmp_path / "MyFontJP-Regular.ttf", reorderTables=False)
            create_synthetic_ttfont(
                sample_cps, family_name="My Font JP", is_variable=False, weight=700
            ).save(tmp_path / "MyFontJP-Bold.ttf", reorderTables=False)

            env = os.environ.copy()
            existing_pypath = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = (
                f"{src_dir}{os.pathsep}{existing_pypath}"
                if existing_pypath
                else str(src_dir)
            )

            for doc_name, doc_content in (
                ("SKILL.md", skill_text),
                ("README.md", readme_text),
            ):
                blocks = code_block_re.findall(doc_content)
                self.assertTrue(
                    blocks, f"Expected at least one ```python block in {doc_name}"
                )
                for idx, block in enumerate(blocks):
                    tree = ast.parse(block, filename=f"{doc_name}#block-{idx}")
                    self.assertTrue(tree.body)
                    res = subprocess.run(
                        [sys.executable, "-c", block],
                        cwd=tmp_path,
                        env=env,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(
                        res.returncode,
                        0,
                        f"{doc_name}#block-{idx} failed:\n{res.stderr}",
                    )


if __name__ == "__main__":
    unittest.main()
