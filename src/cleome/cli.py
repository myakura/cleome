"""
Command-line interface (`cleome`) for slicing Japanese, Korean, and Chinese Variable and Static fonts
into Web-optimized `unicode-range` WOFF2 chunks and `@font-face` CSS.
"""

import argparse
from collections.abc import Sequence
import json
import os
from pathlib import Path
import re
import struct
import sys
import time
from fontTools.ttLib import TTFont, TTLibError

from cleome.locale import SUPPORTED_LOCALES, detect_font_locale
from cleome.metadata import (
    FontMetadata,
    extract_font_metadata,
)
from cleome.slicer import SliceConfig, SliceSpec, _prepare_font_bundle

_DEFAULT_CONFIG = SliceConfig()

EXIT_OK = 0
EXIT_RUNTIME_ERROR = 1
EXIT_USAGE_ERROR = 2
EXIT_PERMISSION_ERROR = 3
EXIT_INPUT_ERROR = 4


def _is_input_path_error(exc: OSError, input_paths: Sequence[Path]) -> bool:
    err_file = getattr(exc, "filename", None)
    if not err_file or not input_paths:
        return False
    err_str = os.fspath(err_file)
    err_abs = os.path.abspath(err_str)
    return any(
        err_str == os.fspath(p) or err_abs == os.path.abspath(os.fspath(p))
        for p in input_paths
    )


def _handle_cli_exception(exc: BaseException, *, input_paths: Sequence[Path] = ()) -> int:
    """Format a concise stderr diagnostic and return the Gate 3 exit code (`1`, `2`, `3`, or `4`)."""
    if isinstance(exc, PermissionError):
        target = f": {exc.filename}" if getattr(exc, "filename", None) else f": {exc}"
        print(f"Error: Permission denied{target}", file=sys.stderr)
        return EXIT_PERMISSION_ERROR
    if isinstance(exc, (FileNotFoundError, IsADirectoryError, NotADirectoryError)) and _is_input_path_error(
        exc, input_paths
    ):
        target = f": {exc.filename}" if getattr(exc, "filename", None) else f": {exc}"
        print(f"Error: Input font not found or is a directory{target}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    if isinstance(exc, (TTLibError, EOFError, struct.error)):
        print(f"Error: Invalid or unreadable font file: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    if isinstance(exc, ValueError):
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_USAGE_ERROR
    print(f"Error: {exc}", file=sys.stderr)
    return EXIT_RUNTIME_ERROR


def _load_cli_input_metas(args: argparse.Namespace, config: SliceConfig) -> list[FontMetadata]:
    """Open input font files and extract `FontMetadata` with CLI overrides applied."""
    metas: list[FontMetadata] = []
    for p in args.inputs:
        with TTFont(p, lazy=True, recalcTimestamp=False) as tt:
            metas.append(
                extract_font_metadata(
                    tt,
                    family_override=args.family,
                    slug_override=args.slug,
                    weight_override=args.weight,
                    stretch_override=args.stretch,
                    style_override=args.style,
                    uvs_base_repair=config.uvs_base_repair,
                )
            )
    return metas


def _positive_int(val_str: str) -> int:
    try:
        val = int(val_str)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Expected a positive integer, got '{val_str}'.") from exc
    if val <= 0:
        raise argparse.ArgumentTypeError(f"Value must be > 0, got {val}.")
    return val


def _parse_freq_chunks(spec_str: str) -> tuple[tuple[int, int], ...]:
    """Parse a progressive Tier 3 chunk spec like `"200x2,80x10"` or `"none"` into a tuple of `(size, count)` pairs."""
    cleaned = spec_str.strip()
    if cleaned.lower() == "none":
        return ()
    chunks: list[tuple[int, int]] = []
    for item in cleaned.split(","):
        item = item.strip()
        if not item:
            continue
        if "x" not in item.lower():
            raise argparse.ArgumentTypeError(
                f"Invalid --freq-chunks item '{item}'. Expected format 'SIZE'x'COUNT' (e.g. '200x2,80x10') or 'none'."
            )
        sz_s, cnt_s = item.lower().split("x", 1)
        try:
            sz, cnt = int(sz_s), int(cnt_s)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"Invalid --freq-chunks item '{item}'. Both SIZE and COUNT must be integers."
            ) from exc
        if sz <= 0 or cnt <= 0:
            raise argparse.ArgumentTypeError(
                f"Invalid --freq-chunks item '{item}'. Both SIZE and COUNT must be > 0."
            )
        chunks.append((sz, cnt))
    if not chunks:
        raise argparse.ArgumentTypeError("--freq-chunks cannot be empty (use 'none' for flat Tier 3 slicing).")
    return tuple(chunks)


def _format_freq_chunks(chunks: Sequence[tuple[int, int]]) -> str:
    """Format a tuple of `(size, count)` pairs into a CLI spec string like `'200x2,80x10'` or `'none'`."""
    return ",".join(f"{sz}x{cnt}" for sz, cnt in chunks) if chunks else "none"


def _parse_tag_list(spec_str: str) -> tuple[str, ...]:
    """Parse a comma- or whitespace-separated list of OpenType table or feature tags."""
    return tuple(t.strip() for t in re.split(r"[,\s]+", spec_str.strip()) if t.strip())


def _add_shared_arguments(parser: argparse.ArgumentParser, *, is_build: bool) -> None:
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="Input font file(s) (.ttf / .otf). Pass multiple files to bundle static weight/style variants together.",
    )

    slicing_group = parser.add_argument_group("Slicing options")
    slicing_group.add_argument(
        "--locale",
        dest="locale",
        choices=SUPPORTED_LOCALES,
        default=_DEFAULT_CONFIG.locale,
        help=(
            "Target CJK slicing profile: 'auto' (detect from family & cmap), "
            f"'jp', 'kr', 'sc', 'tc', or 'hk' (default: {_DEFAULT_CONFIG.locale!r})"
        ),
    )
    slicing_group.add_argument(
        "--freq-chunks",
        type=_parse_freq_chunks,
        default=_DEFAULT_CONFIG.freq_chunks,
        help=(
            "Progressive Tier 3 chunk schedule from highest frequency downward, or 'none' "
            f"(default: {_format_freq_chunks(_DEFAULT_CONFIG.freq_chunks)!r})"
        ),
    )
    slicing_group.add_argument(
        "--freq-tail-chunk-size",
        type=_positive_int,
        default=_DEFAULT_CONFIG.freq_tail_chunk_size,
        help=(
            "Target characters per slice for remaining Tier 3 characters "
            f"(default: {_DEFAULT_CONFIG.freq_tail_chunk_size})"
        ),
    )
    slicing_group.add_argument(
        "--rare-chunk-size",
        type=_positive_int,
        default=_DEFAULT_CONFIG.rare_chunk_size,
        help=(
            "Target characters per slice for Tier 4 rare/extended characters "
            f"(default: {_DEFAULT_CONFIG.rare_chunk_size})"
        ),
    )
    slicing_group.add_argument(
        "--symbols-chunk-size",
        type=_positive_int,
        default=_DEFAULT_CONFIG.symbols_ext_chunk_size,
        help=(
            "Target characters per slice for Tier 2 extended symbols "
            f"(default: {_DEFAULT_CONFIG.symbols_ext_chunk_size})"
        ),
    )
    slicing_group.add_argument(
        "--uvs-base-repair",
        action=argparse.BooleanOptionalAction,
        default=_DEFAULT_CONFIG.uvs_base_repair,
        help=(
            "Repair characters mapped in cmap Format 14 (UVS/IVS) but omitted from Format 4/12 "
            f"(default: {'enabled' if _DEFAULT_CONFIG.uvs_base_repair else 'disabled'})"
        ),
    )

    descriptor_group = parser.add_argument_group("CSS descriptor overrides")
    descriptor_group.add_argument(
        "--family",
        default=None,
        help="Override CSS `font-family` name (auto-detected from font `name` table by default)",
    )
    descriptor_group.add_argument(
        "--slug",
        default=None,
        help="Override filename slug prefix (default: kebab-case of font-family)",
    )
    descriptor_group.add_argument(
        "--weight",
        default=None,
        help="Override CSS `font-weight` descriptor (e.g. '250 900' or '400')",
    )
    descriptor_group.add_argument(
        "--stretch",
        default=None,
        help="Override CSS `font-stretch` descriptor (e.g. '70%% 100%%')",
    )
    descriptor_group.add_argument(
        "--style",
        default=None,
        help="Override CSS `font-style` descriptor (e.g. 'normal' or 'italic')",
    )
    if is_build:
        descriptor_group.add_argument(
            "--display",
            default=_DEFAULT_CONFIG.font_display,
            help=f"CSS `font-display` descriptor value (default: {_DEFAULT_CONFIG.font_display!r})",
        )

        output_group = parser.add_argument_group("Output options")
        output_group.add_argument(
            "-o",
            "--out-dir",
            type=Path,
            default=Path("dist"),
            help="Output directory for generated CSS and slices (default: ./dist)",
        )
        output_group.add_argument(
            "--slices-dir",
            default=_DEFAULT_CONFIG.slices_subdir,
            help=(
                "Subdirectory name under --out-dir for .woff2 files "
                f"(default: {_DEFAULT_CONFIG.slices_subdir!r}; use '.' for flat output)"
            ),
        )
        output_group.add_argument(
            "--url-prefix",
            default=_DEFAULT_CONFIG.url_prefix,
            help="Custom URL prefix in CSS `src: url('<prefix>/<file>.woff2')` (default: relative path from CSS to <slices-dir>)",
        )
        css_dest_group = output_group.add_mutually_exclusive_group()
        css_dest_group.add_argument(
            "--css-name",
            default=None,
            help="Custom filename for the output CSS file inside --out-dir (default: '<slug>.css')",
        )
        css_dest_group.add_argument(
            "--css-out",
            type=Path,
            default=None,
            help="Write the output CSS file to an explicit path (e.g. 'src/styles/fonts.css' while -o is 'public/fonts')",
        )
        output_group.add_argument(
            "--filename-hash",
            action=argparse.BooleanOptionalAction,
            default=_DEFAULT_CONFIG.filename_hash,
            help=(
                "Append an 8-character SHA-256 content hash to each .woff2 slice filename "
                f"(default: {'enabled' if _DEFAULT_CONFIG.filename_hash else 'disabled'})"
            ),
        )

        subset_group = parser.add_argument_group("Subsetting options")
        subset_group.add_argument(
            "--layout-features",
            type=_parse_tag_list,
            default=_DEFAULT_CONFIG.layout_features,
            help=(
                "Comma-separated OpenType layout feature tags to retain "
                f"(default: {','.join(_DEFAULT_CONFIG.layout_features)!r} keeps all features)"
            ),
        )
        subset_group.add_argument(
            "--layout-closure",
            action=argparse.BooleanOptionalAction,
            default=_DEFAULT_CONFIG.layout_closure,
            help=(
                "Expand glyph closure over GSUB substitution tables "
                f"(default: {'enabled' if _DEFAULT_CONFIG.layout_closure else 'disabled'})"
            ),
        )
        subset_group.add_argument(
            "--drop-tables",
            type=_parse_tag_list,
            default=_DEFAULT_CONFIG.drop_tables,
            help="Comma-separated OpenType table tags to drop during subsetting (e.g. 'GPOS,GSUB,vhea,vmtx')",
        )

    exec_group = parser.add_argument_group("Execution & output format")
    if is_build:
        exec_group.add_argument(
            "-j",
            "--workers",
            type=_positive_int,
            default=None,
            help="Number of parallel worker processes for WOFF2 compression (default: CPU count)",
        )
        exec_group.add_argument(
            "--force",
            action="store_true",
            help="Rebuild all WOFF2 slices and CSS even if cached outputs are up to date",
        )
        exec_group.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate inputs, output paths, and planned slices without creating directories or writing files",
        )
    exec_group.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit structured JSON summary to stdout",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cleome",
        allow_abbrev=False,
        description=(
            "Slice Japanese, Korean, or Chinese Variable and Static fonts (.ttf / .otf) into "
            "Web-optimized unicode-range WOFF2 chunks and @font-face CSS."
        ),
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    plan_parser = subparsers.add_parser(
        "plan",
        allow_abbrev=False,
        help="Read-only inspection: compute and output the 4-tier unicode-range slice plan without writing files",
    )
    _add_shared_arguments(plan_parser, is_build=False)

    build_subparser = subparsers.add_parser(
        "build",
        allow_abbrev=False,
        help="Write mode: slice input fonts into WOFF2 chunks and generate @font-face CSS",
    )
    _add_shared_arguments(build_subparser, is_build=True)

    return parser


def _summarize_plan_tiers(plan: list[SliceSpec]) -> dict[int, tuple[int, int, int]]:
    """Return `{tier_num: (slice_count, total_codepoints, total_unicode_range_chars)}` for Tiers 4..1."""
    by_tier: dict[int, list[SliceSpec]] = {4: [], 3: [], 2: [], 1: []}
    for s in plan:
        by_tier[s.tier].append(s)
    return {
        tier_num: (
            len(t_slices),
            sum(len(s.binary_codepoints) for s in t_slices),
            sum(len(s.css_unicode_range) for s in t_slices),
        )
        for tier_num, t_slices in by_tier.items()
    }


def _run_plan(args: argparse.Namespace, config: SliceConfig) -> int:
    try:
        raw_metas = _load_cli_input_metas(args, config)
        prepared = _prepare_font_bundle(
            raw_metas,
            config,
            family_override=args.family,
            slug_override=args.slug,
        )
    except (ValueError, TTLibError, EOFError, struct.error, OSError) as exc:
        return _handle_cli_exception(exc, input_paths=args.inputs)

    first = prepared.metas[0]
    resolved_loc = prepared.resolved_locale

    if args.json_output:
        payload = {
            "ok": True,
            "command": "plan",
            "family_name": prepared.family_name,
            "slug": prepared.slug,
            "locale": resolved_loc,
            "configured_locale": config.locale,
            "is_variable": first.is_variable,
            "file_count": len(prepared.metas),
            "variants": [
                {
                    "weight": meta.weight_css,
                    "stretch": meta.stretch_css or "normal",
                    "styles": list(meta.styles_css or (meta.style_css,)),
                    "variant_suffix": meta.variant_suffix,
                    "codepoints": len(meta.codepoints),
                    "total_slices": len(plan),
                    "tiers": {
                        str(t): {"slices": sc, "codepoints": cp_c, "unicode_range_chars": ur_c}
                        for t, (sc, cp_c, ur_c) in _summarize_plan_tiers(plan).items()
                    },
                }
                for meta, plan in zip(prepared.metas, prepared.variant_plans)
            ],
        }
        print(json.dumps(payload, ensure_ascii=False))
        return EXIT_OK

    print(f"Font Family : {prepared.family_name} (slug: {prepared.slug})")
    print(f"Locale      : {resolved_loc} (config: {config.locale})")
    print(f"Type        : {'Variable' if first.is_variable else 'Static'} ({len(prepared.metas)} file(s))")
    for m, plan in zip(prepared.metas, prepared.variant_plans):
        style_label = " + ".join(m.styles_css or (m.style_css,))
        print(
            f"Variant     : weight={m.weight_css}, stretch={m.stretch_css or 'normal'}, "
            f"style={style_label} | {len(m.codepoints):,d} chars -> {len(plan)} slices"
        )
        for tier_num, (sc, t_chars, t_ur_len) in _summarize_plan_tiers(plan).items():
            print(
                f"  Tier {tier_num}: {sc:2d} slices | {t_chars:6,d} chars | "
                f"unicode-range len: {t_ur_len:6,d} chars ({t_ur_len/1024:.1f} KB)"
            )
    return EXIT_OK


def _run_build_dry_run(args: argparse.Namespace, config: SliceConfig) -> int:
    from cleome.css import _resolve_and_validate_outputs

    try:
        raw_metas = _load_cli_input_metas(args, config)
        prepared = _prepare_font_bundle(
            raw_metas,
            config,
            family_override=args.family,
            slug_override=args.slug,
        )
        outputs = _resolve_and_validate_outputs(
            args.out_dir,
            config,
            prepared.metas,
            prepared.variant_plans,
            css_filename=args.css_name,
            css_path=args.css_out,
        )
    except (ValueError, TTLibError, EOFError, struct.error, OSError) as exc:
        return _handle_cli_exception(exc, input_paths=args.inputs)

    total_slices = outputs.total_active_slices
    preload_slices = outputs.preload_slices
    resolved_loc = prepared.resolved_locale
    if args.json_output:
        payload = {
            "ok": True,
            "command": "build",
            "dry_run": True,
            "cached": False,
            "family_name": prepared.family_name,
            "slug": prepared.slug,
            "locale": resolved_loc,
            "filename_hash": config.filename_hash,
            "css_path": str(outputs.css_path),
            "slices_dir": str(outputs.slices_dir),
            "slice_count": total_slices,
            "preload_slices": preload_slices,
        }
        print(json.dumps(payload, ensure_ascii=False))
        return EXIT_OK

    print(
        f"[cleome] Dry run: would build {total_slices} WOFF2 slices + "
        f"{outputs.css_path.name} (no files written)"
    )
    print(f"         CSS   : {outputs.css_path}")
    print(f"         Slices: {outputs.slices_dir}/")
    if preload_slices:
        print("         Preload (Tier 1):")
        for item in preload_slices:
            print(
                f'           <link rel="preload" href="{item["url"]}" '
                f'as="font" type="font/woff2" crossorigin>'
            )
    return EXIT_OK


def _run_build(args: argparse.Namespace, config: SliceConfig) -> int:
    if args.dry_run:
        return _run_build_dry_run(args, config)

    from cleome.css import _resolve_url_prefix
    from cleome.pipeline import slice_fonts

    t0 = time.time()
    try:
        result = slice_fonts(
            fonts=args.inputs,
            out_dir=args.out_dir,
            family_name=args.family,
            slug=args.slug,
            css_filename=args.css_name,
            css_path=args.css_out,
            weight=args.weight,
            stretch=args.stretch,
            style=args.style,
            config=config,
            workers=args.workers,
            force=args.force,
        )
    except (ValueError, TTLibError, EOFError, struct.error, RuntimeError, OSError) as exc:
        return _handle_cli_exception(exc, input_paths=args.inputs)

    elapsed = time.time() - t0
    url_prefix = _resolve_url_prefix(
        config, css_path=result.css_path, slices_dir=result.slices_dir
    )
    preload_slices = [
        {
            "slice_id": bf.spec.slice_id,
            "variant_suffix": bf.variant_suffix,
            "filename": bf.filename,
            "url": f"{url_prefix}{bf.filename}",
        }
        for bf in result.built_files
        if bf.spec.tier == 1
    ]
    if args.json_output:
        first_meta = result.fonts_metadata[0]
        resolved_loc = (
            detect_font_locale(first_meta.codepoints, family_name=first_meta.family_name)
            if config.locale == "auto"
            else config.locale
        )
        payload = {
            "ok": True,
            "command": "build",
            "dry_run": False,
            "cached": result.cached,
            "family_name": result.family_name,
            "slug": result.slug,
            "locale": resolved_loc,
            "filename_hash": config.filename_hash,
            "css_path": str(result.css_path),
            "css_bytes": result.css_bytes,
            "slices_dir": str(result.slices_dir),
            "slice_count": len(result.built_files),
            "total_woff2_bytes": result.total_woff2_bytes,
            "preload_slices": preload_slices,
            "elapsed_seconds": round(elapsed, 3),
        }
        print(json.dumps(payload, ensure_ascii=False))
        return EXIT_OK

    action_label = "Cached" if result.cached else "Built"
    cache_suffix = " (up to date)" if result.cached else ""
    print(
        f"[cleome] {action_label} {len(result.built_files)} WOFF2 slices "
        f"({result.total_woff2_bytes / (1024 * 1024):.2f} MB total) + "
        f"{result.css_path.name} ({result.css_bytes / 1024:.1f} KB) in {elapsed:.2f}s{cache_suffix}"
    )
    print(f"         CSS   : {result.css_path}")
    print(f"         Slices: {result.slices_dir}/")
    if preload_slices:
        print("         Preload (Tier 1):")
        for item in preload_slices:
            print(
                f'           <link rel="preload" href="{item["url"]}" '
                f'as="font" type="font/woff2" crossorigin>'
            )
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    for p in args.inputs:
        if not p.exists():
            print(f"Error: Input font not found: {p}", file=sys.stderr)
            return EXIT_INPUT_ERROR
        if p.is_dir():
            print(f"Error: Input path is a directory, expected a font file: {p}", file=sys.stderr)
            return EXIT_INPUT_ERROR

    try:
        config = SliceConfig(
            locale=args.locale,
            freq_chunks=args.freq_chunks,
            freq_tail_chunk_size=args.freq_tail_chunk_size,
            rare_chunk_size=args.rare_chunk_size,
            symbols_ext_chunk_size=args.symbols_chunk_size,
            layout_closure=getattr(args, "layout_closure", _DEFAULT_CONFIG.layout_closure),
            layout_features=tuple(getattr(args, "layout_features", _DEFAULT_CONFIG.layout_features)),
            drop_tables=tuple(getattr(args, "drop_tables", _DEFAULT_CONFIG.drop_tables)),
            uvs_base_repair=args.uvs_base_repair,
            slices_subdir=getattr(args, "slices_dir", _DEFAULT_CONFIG.slices_subdir),
            url_prefix=getattr(args, "url_prefix", _DEFAULT_CONFIG.url_prefix),
            font_display=getattr(args, "display", _DEFAULT_CONFIG.font_display),
            filename_hash=getattr(args, "filename_hash", _DEFAULT_CONFIG.filename_hash),
        )
    except ValueError as exc:
        return _handle_cli_exception(exc)

    if args.subcommand == "plan":
        return _run_plan(args, config)
    return _run_build(args, config)


if __name__ == "__main__":
    raise SystemExit(main())
