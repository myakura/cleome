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


_CONFIG_KEY_TO_FIELD: dict[str, str] = {
    "locale": "locale",
    "preset": "locale",
    "freq_tiers": "freq_tiers",
    "freq_tail_chunk_size": "freq_tail_chunk_size",
    "rare_kanji_chunk_size": "rare_kanji_chunk_size",
    "rare_chunk_size": "rare_kanji_chunk_size",
    "symbols_ext_chunk_size": "symbols_ext_chunk_size",
    "symbols_chunk_size": "symbols_ext_chunk_size",
    "retain_layout_closure": "retain_layout_closure",
    "patch_uvs_only_cmap": "patch_uvs_only_cmap",
    "layout_features": "layout_features",
    "drop_tables": "drop_tables",
    "slices_subdir": "slices_subdir",
    "slices_dir": "slices_subdir",
    "url_prefix": "url_prefix",
    "font_display": "font_display",
    "display": "font_display",
}


def _coerce_config_int(val: Any, key: str) -> int:
    if isinstance(val, bool) or not isinstance(val, (int, str)):
        raise ValueError(f"Invalid integer for {key!r} in config: {val!r}")
    try:
        return int(val)
    except ValueError as exc:
        raise ValueError(f"Invalid integer for {key!r} in config: {val!r}") from exc


def _load_config_file(config_path: Path) -> SliceConfig:
    if not config_path.exists():
        raise ValueError(f"Config file not found: {config_path}")
    text = config_path.read_text(encoding="utf-8")
    raw = tomllib.loads(text) if config_path.suffix.lower() == ".toml" else json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError(f"Config file must contain a JSON/TOML object, got {type(raw).__name__}")
    if "cleome" in raw and isinstance(raw["cleome"], dict):
        raw = raw["cleome"]

    unknown_keys = sorted(set(raw.keys()) - _CONFIG_KEY_TO_FIELD.keys())
    if unknown_keys:
        raise ValueError(
            f"Unsupported key(s) in config file: {', '.join(repr(k) for k in unknown_keys)}"
        )

    kwargs: dict[str, Any] = {}
    for key, val in raw.items():
        field = _CONFIG_KEY_TO_FIELD[key]
        if key != field and field in raw:
            continue
        if field == "locale":
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"Invalid locale/preset in config: {val!r}")
            kwargs[field] = val
        elif field == "freq_tiers":
            if isinstance(val, str):
                try:
                    kwargs[field] = _parse_freq_tiers(val)
                except argparse.ArgumentTypeError as exc:
                    raise ValueError(str(exc)) from exc
            elif isinstance(val, list) and val:
                tiers: list[tuple[int, int]] = []
                for item in val:
                    if isinstance(item, dict):
                        sz = _coerce_config_int(item.get("chunk_size", item.get("size")), "freq_tiers.chunk_size")
                        cnt = _coerce_config_int(item.get("num_chunks", item.get("count")), "freq_tiers.num_chunks")
                        tiers.append((sz, cnt))
                    elif isinstance(item, (list, tuple)) and len(item) == 2:
                        tiers.append((_coerce_config_int(item[0], "freq_tiers[0]"), _coerce_config_int(item[1], "freq_tiers[1]")))
                    else:
                        raise ValueError(f"Invalid freq_tiers entry in config: {item!r}")
                kwargs[field] = tuple(tiers)
            else:
                raise ValueError(f"Invalid freq_tiers in config: {val!r}")
        elif field in ("freq_tail_chunk_size", "rare_kanji_chunk_size", "symbols_ext_chunk_size"):
            kwargs[field] = _coerce_config_int(val, key)
        elif field in ("retain_layout_closure", "patch_uvs_only_cmap"):
            if not isinstance(val, bool):
                raise ValueError(f"Invalid boolean for {key!r} in config: {val!r}")
            kwargs[field] = val
        elif field in ("layout_features", "drop_tables"):
            if isinstance(val, str):
                kwargs[field] = _parse_tag_list(val)
            elif isinstance(val, list) and all(isinstance(x, str) for x in val):
                kwargs[field] = tuple(val)
            else:
                raise ValueError(f"Invalid {key} in config: {val!r}")
        elif field in ("slices_subdir", "url_prefix", "font_display"):
            if val is not None and not isinstance(val, str):
                raise ValueError(f"Invalid string for {key!r} in config: {val!r}")
            if val is None and field != "url_prefix":
                raise ValueError(f"Invalid string for {key!r} in config: {val!r}")
            kwargs[field] = val

    return SliceConfig(**kwargs)


def _resolve_slice_config(args: argparse.Namespace) -> SliceConfig:
    cfg = _load_config_file(args.config) if args.config is not None else SliceConfig()
    overrides: dict[str, Any] = {
        field: val
        for field, val in (
            ("locale", args.locale if args.locale is not None else args.preset),
            ("freq_tiers", args.freq_tiers),
            ("freq_tail_chunk_size", args.freq_tail_chunk_size),
            ("rare_kanji_chunk_size", args.rare_chunk_size),
            ("symbols_ext_chunk_size", args.symbols_chunk_size),
            ("layout_features", tuple(args.layout_features) if args.layout_features is not None else None),
            ("drop_tables", tuple(args.drop_tables) if args.drop_tables is not None else None),
            ("slices_subdir", args.slices_dir),
            ("url_prefix", args.url_prefix),
            ("font_display", args.display),
        )
        if val is not None
    }
    if args.no_layout_closure:
        overrides["retain_layout_closure"] = False
    return replace(cfg, **overrides) if overrides else cfg


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
        variants_payload = []
        for meta, suf, plan in zip(metas, suffixes, plans):
            tier_stats = _summarize_plan_tiers(plan)
            variants_payload.append(
                {
                    "weight": meta.weight_css,
                    "stretch": meta.stretch_css or "normal",
                    "styles": list(meta.styles_css or (meta.style_css,)),
                    "variant_suffix": suf,
                    "codepoints": len(meta.codepoints),
                    "total_slices": len(plan),
                    "tiers": {
                        str(t): {"slices": sc, "codepoints": cp_c, "unicode_range_chars": ur_c}
                        for t, (sc, cp_c, ur_c) in tier_stats.items()
                    },
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
        payload = {
            "ok": True,
            "command": "plan",
            "family_name": first.family_name,
            "slug": first.slug,
            "locale": resolved_loc,
            "configured_locale": config.locale,
            "is_variable": first.is_variable,
            "file_count": len(metas),
            "variants": variants_payload,
        }
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
        for tier_num, (sc, t_chars, t_ur_len) in _summarize_plan_tiers(plan).items():
            print(
                f"  Tier {tier_num}: {sc:2d} slices | {t_chars:6,d} chars | "
                f"unicode-range len: {t_ur_len:6,d} chars ({t_ur_len/1024:.1f} KB)"
            )
    return 0


def _run_build(args: argparse.Namespace, config: SliceConfig) -> int:
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

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
        return _run_plan(args, config)
    return _run_build(args, config)


if __name__ == "__main__":
    raise SystemExit(main())

