"""
Command-line interface (`cleome`) for slicing Japanese Variable and Static fonts
into Web-optimized `unicode-range` WOFF2 chunks and `@font-face` CSS.
"""

import argparse
from dataclasses import replace
import json
from pathlib import Path
import re
import sys
import time
import tomllib
from typing import Any
from fontTools.ttLib import TTFont

from cleome.slicer import (
    SUPPORTED_LOCALES,
    FontMetadata,
    SliceConfig,
    SliceSpec,
    build_slice_plan,
    detect_font_locale,
    extract_font_metadata,
)
from cleome.css import slice_fonts, validate_and_compute_variant_suffixes


def _positive_int(val_str: str) -> int:
    try:
        val = int(val_str)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Expected a positive integer, got '{val_str}'.") from exc
    if val <= 0:
        raise argparse.ArgumentTypeError(f"Value must be > 0, got {val}.")
    return val


def _parse_freq_tiers(spec_str: str) -> tuple[tuple[int, int], ...]:
    """
    Parse a progressive tier spec string like `"200x2,80x10"` into `((200, 2), (80, 10))`.
    """
    tiers: list[tuple[int, int]] = []
    for item in spec_str.split(","):
        item = item.strip()
        if not item:
            continue
        if "x" not in item.lower():
            raise argparse.ArgumentTypeError(
                f"Invalid --freq-tiers item '{item}'. Expected format 'SIZE'x'COUNT' (e.g. '200x2,80x10')."
            )
        sz_s, cnt_s = item.lower().split("x", 1)
        try:
            sz, cnt = int(sz_s), int(cnt_s)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"Invalid --freq-tiers item '{item}'. Both SIZE and COUNT must be integers."
            ) from exc
        if sz <= 0 or cnt <= 0:
            raise argparse.ArgumentTypeError(
                f"Invalid --freq-tiers item '{item}'. Both SIZE and COUNT must be > 0."
            )
        tiers.append((sz, cnt))
    if not tiers:
        raise argparse.ArgumentTypeError("--freq-tiers cannot be empty.")
    return tuple(tiers)


def _parse_tag_list(spec_str: str) -> tuple[str, ...]:
    """Parse a comma- or whitespace-separated list of OpenType table or feature tags."""
    tags = [t.strip() for t in re.split(r"[,\s]+", spec_str.strip()) if t.strip()]
    return tuple(tags)


def _add_shared_arguments(parser: argparse.ArgumentParser, *, is_build: bool) -> None:
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="Input font file(s) (.ttf / .otf). Pass multiple files to bundle static weight/style variants together.",
    )
    if is_build:
        parser.add_argument(
            "-o",
            "--out-dir",
            type=Path,
            default=Path("dist"),
            help="Output directory for generated CSS and slices (default: ./dist)",
        )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        help="Optional path to a JSON or TOML SliceConfig override file",
    )
    parser.add_argument(
        "--preset",
        choices=SUPPORTED_LOCALES,
        default=None,
        help="Alias for --locale: target CJK slicing profile ('auto', 'jp', 'kr', 'sc', 'tc', or 'hk')",
    )
    parser.add_argument(
        "--locale",
        choices=SUPPORTED_LOCALES,
        default=None,
        help="Target CJK slicing profile: 'auto' (detect from family & cmap), 'jp', 'kr', 'sc', 'tc', or 'hk' (default: 'auto')",
    )
    parser.add_argument(
        "--slices-dir",
        default=None,
        help="Subdirectory name under --out-dir for .woff2 files (default: 'slices'; use '.' for flat output)",
    )
    parser.add_argument(
        "--url-prefix",
        default=None,
        help="Custom URL prefix in CSS `src: url('<prefix>/<file>.woff2')` (default: './<slices-dir>')",
    )
    parser.add_argument(
        "--css-name",
        default=None,
        help="Custom filename for the output CSS file (default: '<slug>.css')",
    )
    parser.add_argument(
        "--family",
        default=None,
        help="Override CSS `font-family` name (auto-detected from font `name` table by default)",
    )
    parser.add_argument(
        "--slug",
        default=None,
        help="Override filename slug prefix (default: kebab-case of font-family)",
    )
    parser.add_argument(
        "--weight",
        default=None,
        help="Override CSS `font-weight` descriptor (e.g. '250 900' or '400')",
    )
    parser.add_argument(
        "--stretch",
        default=None,
        help="Override CSS `font-stretch` descriptor (e.g. '70%% 100%%')",
    )
    parser.add_argument(
        "--style",
        default=None,
        help="Override CSS `font-style` descriptor (e.g. 'normal' or 'italic')",
    )
    parser.add_argument(
        "--display",
        default=None,
        help="CSS `font-display` descriptor value (default: 'swap')",
    )
    parser.add_argument(
        "--freq-tiers",
        type=_parse_freq_tiers,
        default=None,
        help="Progressive Tier 3 chunk spec from highest frequency downward (default: '200x2,80x10')",
    )
    parser.add_argument(
        "--freq-tail-chunk-size",
        type=_positive_int,
        default=None,
        help="Target characters per slice for remaining Tier 3 characters (default: 70)",
    )
    parser.add_argument(
        "--rare-chunk-size",
        type=_positive_int,
        default=None,
        help="Target characters per slice for Tier 4 rare/extended characters (default: 250)",
    )
    parser.add_argument(
        "--symbols-chunk-size",
        type=_positive_int,
        default=None,
        help="Target characters per slice for Tier 2 extended symbols (default: 350)",
    )
    parser.add_argument(
        "--layout-features",
        type=_parse_tag_list,
        default=None,
        help=(
            "Comma-separated OpenType layout feature tags to retain "
            "(default: '*' keeps all features; specifying tags replaces the default list, "
            "so include vertical/composition tags like 'ccmp,locl,kern,mark,mkmk,liga,palt,halt,vpal,vhal,vkrn,vert,vrt2,ruby')"
        ),
    )
    parser.add_argument(
        "--no-layout-closure",
        action="store_true",
        default=False,
        help="Do not expand glyph closure over GSUB substitution tables",
    )
    parser.add_argument(
        "--drop-tables",
        type=_parse_tag_list,
        default=None,
        help="Comma-separated OpenType table tags to drop during subsetting (e.g. 'GPOS,GSUB,vhea,vmtx')",
    )
    if is_build:
        parser.add_argument(
            "-j",
            "--workers",
            type=_positive_int,
            default=None,
            help="Number of parallel worker processes for WOFF2 compression (default: CPU count)",
        )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        default=False,
        help="Emit structured JSON output to stdout",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cleome",
        description=(
            "Slice Japanese, Korean, or Chinese Variable and Static fonts (.ttf / .otf) into "
            "Web-optimized unicode-range WOFF2 chunks and @font-face CSS using CSS Cascade Shadowing "
            "and Progressive (Inverted-Pyramid) frequency granularity."
        ),
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    plan_parser = subparsers.add_parser(
        "plan",
        help="Read-only inspection: compute and output the 4-tier unicode-range slice plan without writing files",
        description="Read-only inspection: compute and output the 4-tier unicode-range slice plan without writing files.",
    )
    _add_shared_arguments(plan_parser, is_build=False)

    build_subparser = subparsers.add_parser(
        "build",
        help="Write mode: slice input fonts into WOFF2 chunks and generate @font-face CSS",
        description="Write mode: slice input fonts into WOFF2 chunks and generate @font-face CSS.",
    )
    _add_shared_arguments(build_subparser, is_build=True)

    return parser


def _coerce_config_int(val: Any, key: str) -> int:
    if isinstance(val, bool) or not isinstance(val, (int, str)):
        raise ValueError(f"Invalid integer for {key!r} in config: {val!r}")
    try:
        return int(val)
    except ValueError as exc:
        raise ValueError(f"Invalid integer for {key!r} in config: {val!r}") from exc


_ALLOWED_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "locale",
        "preset",
        "freq_tiers",
        "freq_tail_chunk_size",
        "rare_kanji_chunk_size",
        "rare_chunk_size",
        "symbols_ext_chunk_size",
        "symbols_chunk_size",
        "retain_layout_closure",
        "patch_uvs_only_cmap",
        "layout_features",
        "drop_tables",
        "slices_subdir",
        "slices_dir",
        "url_prefix",
        "font_display",
        "display",
    }
)


def _load_config_file(config_path: Path) -> SliceConfig:
    if not config_path.exists():
        raise ValueError(f"Config file not found: {config_path}")
    text = config_path.read_text(encoding="utf-8")
    if config_path.suffix.lower() == ".toml":
        raw = tomllib.loads(text)
    else:
        raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError(f"Config file must contain a JSON/TOML object, got {type(raw).__name__}")

    # Allow nesting under `[cleome]` in TOML (e.g. pyproject.toml style)
    if "cleome" in raw and isinstance(raw["cleome"], dict):
        raw = raw["cleome"]

    unknown_keys = sorted(set(raw.keys()) - _ALLOWED_CONFIG_KEYS)
    if unknown_keys:
        raise ValueError(
            f"Unsupported key(s) in config file: {', '.join(repr(k) for k in unknown_keys)}"
        )

    kwargs: dict[str, Any] = {}
    if "locale" in raw or "preset" in raw:
        loc_val = raw.get("locale") if "locale" in raw else raw.get("preset")
        if not isinstance(loc_val, str) or not loc_val.strip():
            raise ValueError(f"Invalid locale/preset in config: {loc_val!r}")
        kwargs["locale"] = loc_val
    if "freq_tiers" in raw:
        val = raw["freq_tiers"]
        if isinstance(val, str):
            try:
                kwargs["freq_tiers"] = _parse_freq_tiers(val)
            except argparse.ArgumentTypeError as exc:
                raise ValueError(str(exc)) from exc
        elif isinstance(val, list):
            if not val:
                raise ValueError("freq_tiers in config cannot be empty")
            parsed_tiers: list[tuple[int, int]] = []
            for item in val:
                if isinstance(item, dict):
                    sz_raw = item.get("chunk_size", item.get("size"))
                    cnt_raw = item.get("num_chunks", item.get("count"))
                    sz = _coerce_config_int(sz_raw, "freq_tiers.chunk_size")
                    cnt = _coerce_config_int(cnt_raw, "freq_tiers.num_chunks")
                    parsed_tiers.append((sz, cnt))
                elif isinstance(item, (list, tuple)) and len(item) == 2:
                    sz = _coerce_config_int(item[0], "freq_tiers[0]")
                    cnt = _coerce_config_int(item[1], "freq_tiers[1]")
                    parsed_tiers.append((sz, cnt))
                else:
                    raise ValueError(f"Invalid freq_tiers entry in config: {item!r}")
            kwargs["freq_tiers"] = tuple(parsed_tiers)
        else:
            raise ValueError(f"Invalid freq_tiers in config: {val!r}")
    for int_key, cfg_field in (
        ("freq_tail_chunk_size", "freq_tail_chunk_size"),
        ("rare_kanji_chunk_size", "rare_kanji_chunk_size"),
        ("rare_chunk_size", "rare_kanji_chunk_size"),
        ("symbols_ext_chunk_size", "symbols_ext_chunk_size"),
        ("symbols_chunk_size", "symbols_ext_chunk_size"),
    ):
        if int_key in raw:
            kwargs[cfg_field] = _coerce_config_int(raw[int_key], int_key)
    for bool_key in ("retain_layout_closure", "patch_uvs_only_cmap"):
        if bool_key in raw:
            if not isinstance(raw[bool_key], bool):
                raise ValueError(f"Invalid boolean for {bool_key!r} in config: {raw[bool_key]!r}")
            kwargs[bool_key] = raw[bool_key]
    for seq_key in ("layout_features", "drop_tables"):
        if seq_key in raw:
            val = raw[seq_key]
            if isinstance(val, str):
                kwargs[seq_key] = _parse_tag_list(val)
            elif isinstance(val, list) and all(isinstance(x, str) for x in val):
                kwargs[seq_key] = tuple(val)
            else:
                raise ValueError(f"Invalid {seq_key} in config: {val!r}")
    for str_key, cfg_field in (
        ("slices_subdir", "slices_subdir"),
        ("slices_dir", "slices_subdir"),
        ("url_prefix", "url_prefix"),
        ("font_display", "font_display"),
        ("display", "font_display"),
    ):
        if str_key in raw:
            s_val = raw[str_key]
            if s_val is not None and not isinstance(s_val, str):
                raise ValueError(f"Invalid string for {str_key!r} in config: {s_val!r}")
            if s_val is not None or cfg_field == "url_prefix":
                kwargs[cfg_field] = s_val
            else:
                raise ValueError(f"Invalid string for {str_key!r} in config: {s_val!r}")

    return SliceConfig(**kwargs)


def _resolve_slice_config(args: argparse.Namespace) -> SliceConfig:
    cfg = _load_config_file(args.config) if args.config is not None else SliceConfig()

    overrides: dict[str, Any] = {}
    chosen_locale = args.locale if args.locale is not None else args.preset
    if chosen_locale is not None:
        overrides["locale"] = chosen_locale
    if args.freq_tiers is not None:
        overrides["freq_tiers"] = args.freq_tiers
    if args.freq_tail_chunk_size is not None:
        overrides["freq_tail_chunk_size"] = args.freq_tail_chunk_size
    if args.rare_chunk_size is not None:
        overrides["rare_kanji_chunk_size"] = args.rare_chunk_size
    if args.symbols_chunk_size is not None:
        overrides["symbols_ext_chunk_size"] = args.symbols_chunk_size
    if args.no_layout_closure:
        overrides["retain_layout_closure"] = False
    if args.layout_features is not None:
        overrides["layout_features"] = tuple(args.layout_features)
    if args.drop_tables is not None:
        overrides["drop_tables"] = tuple(args.drop_tables)
    if args.slices_dir is not None:
        overrides["slices_subdir"] = args.slices_dir
    if args.url_prefix is not None:
        overrides["url_prefix"] = args.url_prefix
    if args.display is not None:
        overrides["font_display"] = args.display

    return replace(cfg, **overrides) if overrides else cfg


def _format_plan_json(
    metas: list[FontMetadata],
    suffixes: list[str],
    plans: list[list[SliceSpec]],
    *,
    resolved_locale: str,
    config: SliceConfig,
) -> dict[str, Any]:
    first = metas[0]
    variants_payload: list[dict[str, Any]] = []
    for meta, suf, plan in zip(metas, suffixes, plans):
        by_tier: dict[int, list[SliceSpec]] = {4: [], 3: [], 2: [], 1: []}
        for s in plan:
            by_tier[s.tier].append(s)

        tiers_summary: dict[str, dict[str, int]] = {}
        for tier_num in (4, 3, 2, 1):
            t_slices = by_tier[tier_num]
            t_chars = sum(len(s.binary_codepoints) for s in t_slices)
            t_ur_len = sum(len(s.css_unicode_range) for s in t_slices)
            tiers_summary[str(tier_num)] = {
                "slices": len(t_slices),
                "codepoints": t_chars,
                "unicode_range_chars": t_ur_len,
            }

        variants_payload.append(
            {
                "weight": meta.weight_css,
                "stretch": meta.stretch_css or "normal",
                "styles": list(meta.styles_css or (meta.style_css,)),
                "variant_suffix": suf,
                "codepoints": len(meta.codepoints),
                "total_slices": len(plan),
                "tiers": tiers_summary,
                "slices": [
                    {
                        "slice_id": s.slice_id,
                        "tier": s.tier,
                        "category": s.category,
                        "filename": s.filename(meta.slug, suf),
                        "codepoints": len(s.binary_codepoints),
                        "unicode_range_chars": len(s.css_unicode_range),
                        "uses_cascade_shadowing": s.uses_cascade_shadowing,
                    }
                    for s in plan
                ],
            }
        )

    return {
        "ok": True,
        "command": "plan",
        "family_name": first.family_name,
        "slug": first.slug,
        "locale": resolved_locale,
        "configured_locale": config.locale,
        "is_variable": first.is_variable,
        "file_count": len(metas),
        "variants": variants_payload,
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    for p in args.inputs:
        if not p.exists():
            print(f"Error: Input font not found: {p}", file=sys.stderr)
            return 1

    try:
        config = _resolve_slice_config(args)
    except (ValueError, TypeError, OSError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.subcommand == "plan":
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
                        patch_uvs=config.patch_uvs_only_cmap,
                    )
                )
        try:
            suffixes = validate_and_compute_variant_suffixes(metas, family_override=args.family)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        first = metas[0]
        resolved_loc = (
            detect_font_locale(first.codepoints, family_name=first.family_name)
            if config.locale == "auto"
            else config.locale
        )
        plans = [
            build_slice_plan(m.codepoints, config=config, family_name=m.family_name)
            for m in metas
        ]

        if args.json_output:
            payload = _format_plan_json(
                metas,
                suffixes,
                plans,
                resolved_locale=resolved_loc,
                config=config,
            )
            print(json.dumps(payload, ensure_ascii=False))
            return 0

        print(f"Font Family : {first.family_name} (slug: {first.slug})")
        print(f"Locale      : {resolved_loc} (config: {config.locale})")
        print(f"Type        : {'Variable' if first.is_variable else 'Static'} ({len(metas)} file(s))")
        for m, plan in zip(metas, plans):
            style_label = " + ".join(m.styles_css or (m.style_css,))
            print(
                f"Variant     : weight={m.weight_css}, stretch={m.stretch_css or 'normal'}, "
                f"style={style_label} | {len(m.codepoints):,d} chars -> {len(plan)} slices"
            )
            by_tier: dict[int, list[SliceSpec]] = {4: [], 3: [], 2: [], 1: []}
            for s in plan:
                by_tier[s.tier].append(s)
            for tier_num in (4, 3, 2, 1):
                t_slices = by_tier[tier_num]
                t_chars = sum(len(s.binary_codepoints) for s in t_slices)
                t_ur_len = sum(len(s.css_unicode_range) for s in t_slices)
                print(
                    f"  Tier {tier_num}: {len(t_slices):2d} slices | {t_chars:6,d} chars | "
                    f"unicode-range len: {t_ur_len:6,d} chars ({t_ur_len/1024:.1f} KB)"
                )
        return 0

    t0 = time.time()
    try:
        result = slice_fonts(
            fonts=args.inputs,
            out_dir=args.out_dir,
            family_name=args.family,
            slug=args.slug,
            css_filename=args.css_name,
            weight=args.weight,
            stretch=args.stretch,
            style=args.style,
            config=config,
            workers=args.workers,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    elapsed = time.time() - t0

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
            "family_name": result.family_name,
            "slug": result.slug,
            "locale": resolved_loc,
            "css_path": str(result.css_path),
            "css_bytes": result.css_bytes,
            "slices_dir": str(result.slices_dir),
            "slice_count": len(result.built_files),
            "total_woff2_bytes": result.total_woff2_bytes,
            "elapsed_seconds": round(elapsed, 3),
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    print(
        f"[cleome] Built {len(result.built_files)} WOFF2 slices "
        f"({result.total_woff2_bytes / (1024 * 1024):.2f} MB total) + "
        f"{result.css_path.name} ({result.css_bytes / 1024:.1f} KB) in {elapsed:.2f}s"
    )
    print(f"         CSS   : {result.css_path}")
    print(f"         Slices: {result.slices_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

