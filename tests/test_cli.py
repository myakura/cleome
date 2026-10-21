"""
Tests for the `cleome` CLI (`plan` and `build` subcommands, JSON output, and multiprocessing guards).
"""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from fontTools.ttLib.tables.DefaultTable import DefaultTable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from cleome.cli import main as cli_main

from helpers import create_synthetic_ttfont


class TestCleomeCLI(unittest.TestCase):
    def test_cli_validation_and_plan(self) -> None:
        """
        Verify CLI argument parsing for `cleome plan`, --drop-tables, --layout-features,
        and validation errors for non-positive --freq-tiers / --rare-chunk-size or missing subcommand.
        """
        sample_cps = [ord("A"), ord("あ"), ord("日")]
        with tempfile.TemporaryDirectory() as tmp:
            font_path = Path(tmp) / "CliTest.ttf"
            create_synthetic_ttfont(sample_cps, family_name="CLI Test").save(font_path, reorderTables=False)

            # 1. --drop-tables before positional input must not swallow positional input
            buf = StringIO()
            with redirect_stdout(buf):
                rc = cli_main(["plan", "--drop-tables", "DSIG,GPOS", "--layout-features", "kern,palt", str(font_path)])
            self.assertEqual(rc, 0)
            self.assertIn("3 chars -> 3 slices", buf.getvalue())

            # 2. Invalid --freq-tiers (0 or negative) must fail with SystemExit(2)
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as cm:
                cli_main(["plan", "--freq-tiers", "200x0", str(font_path)])
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
        1. `cleome plan <inputs...> [--locale/--preset PRESET] [--json]` is strictly read-only
           and never creates `./dist`.
        2. `cleome plan --json` returns a structured JSON envelope with Tier 1-4 slice counts,
           codepoint counts, `unicode-range` character lengths, and detected locale.
        3. `cleome build <inputs...> -o <dir> [--locale/--preset PRESET] [--json]` writes
           the CSS and `.woff2` slices and supports `--json`.
        """
        sample_cps = [ord("A"), ord("あ"), ord("日"), 0x4E02]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            font_path = tmp_path / "SubcmdTest.ttf"
            create_synthetic_ttfont(sample_cps, family_name="Subcmd Test").save(font_path, reorderTables=False)

            orig_cwd = Path.cwd()
            try:
                os.chdir(tmp_path)

                # 1. `cleome plan` must NOT create ./dist
                buf_plan = StringIO()
                with redirect_stdout(buf_plan):
                    rc_plan = cli_main(["plan", str(font_path)])
                self.assertEqual(rc_plan, 0)
                self.assertIn("Font Family : Subcmd Test (slug: subcmd-test)", buf_plan.getvalue())
                self.assertIn("4 chars -> 4 slices", buf_plan.getvalue())
                self.assertFalse((tmp_path / "dist").exists(), "`cleome plan` must not create ./dist")

                # 2. `cleome plan --json --preset kr`
                buf_plan_json = StringIO()
                with redirect_stdout(buf_plan_json):
                    rc_plan_json = cli_main(
                        ["plan", str(font_path), "--preset", "kr", "--rare-chunk-size", "120", "--json"]
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
                    self.assertIn("unicode_range_chars", variant_info["tiers"][tier_key])
                self.assertFalse((tmp_path / "dist").exists(), "`cleome plan --json` must not create ./dist")

                # 3. `cleome build <input> -o <dir> --json`
                build_out = tmp_path / "built_subcmd"
                buf_build_json = StringIO()
                with redirect_stdout(buf_build_json):
                    rc_build = cli_main(
                        ["build", str(font_path), "-o", str(build_out), "--locale", "jp", "-j", "1", "--json"]
                    )
                self.assertEqual(rc_build, 0)
                build_data = json.loads(buf_build_json.getvalue())
                self.assertTrue(build_data["ok"])
                self.assertEqual(build_data["command"], "build")
                self.assertEqual(build_data["locale"], "jp")
                self.assertEqual(build_data["slice_count"], 4)
                self.assertTrue((build_out / "subcmd-test.css").exists())
                self.assertEqual(len(list((build_out / "slices").glob("*.woff2"))), 4)

                # 4. Empty string validation on --display returns exit code 1 with error on stderr
                empty_display_buf = StringIO()
                with redirect_stderr(empty_display_buf):
                    rc_empty_disp = cli_main(["plan", str(font_path), "--display", "   "])
                self.assertEqual(rc_empty_disp, 1)
                self.assertIn("font_display cannot be empty", empty_display_buf.getvalue())
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


if __name__ == "__main__":
    unittest.main()
