"""
Command-line interface (`cleome`) for slicing Japanese Variable and Static fonts
into Web-optimized `unicode-range` WOFF2 chunks and `@font-face` CSS.
"""

import argparse
from pathlib import Path
import sys
import time
from fontTools.ttLib import TTFont

from cleome.slicer import (
    SliceConfig,
    build_slice_plan,
    extract_font_metadata,
)
from cleome.css import slice_fonts, validate_and_compute_variant_suffixes


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
        tiers.append((int(sz_s), int(cnt_s)))
    return tuple(tiers)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cleome",
        description=(
            "Slice Japanese Variable or Static fonts (.ttf / .otf) into Web-optimized "
            "unicode-range WOFF2 chunks and @font-face CSS using CSS Cascade Shadowing "
            "and Progressive (Inverted-Pyramid) Kanji frequency granularity."
        ),
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="Input font file(s) (.ttf / .otf). Pass multiple files to bundle static weight/style variants together.",
    )
    parser.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=Path("dist"),
        help="Output directory for generated CSS and slices (default: ./dist)",
    )
    parser.add_argument(
        "--slices-dir",
        default="slices",
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
        default="swap",
        help="CSS `font-display` descriptor value (default: 'swap')",
    )
    parser.add_argument(
        "--freq-tiers",
        type=_parse_freq_tiers,
        default=((200, 2), (80, 10)),
        help="Progressive Tier 3 Kanji chunk spec from highest frequency downward (default: '200x2,80x10')",
    )
    parser.add_argument(
        "--freq-tail-chunk-size",
        type=int,
        default=70,
        help="Target characters per slice for remaining Tier 3 Kanji (default: 70)",
    )
    parser.add_argument(
        "--rare-chunk-size",
        type=int,
        default=250,
        help="Target characters per slice for Tier 4 rare/extended Kanji (default: 250)",
    )
    parser.add_argument(
        "--symbols-chunk-size",
        type=int,
        default=350,
        help="Target characters per slice for Tier 2 extended symbols (default: 350)",
    )
    parser.add_argument(
        "--no-layout-closure",
        action="store_true",
        help="Do not expand glyph closure over GSUB substitution tables",
    )
    parser.add_argument(
        "--drop-tables",
        nargs="*",
        default=(),
        help="Optional OpenType table tags to drop during subsetting (e.g. GPOS GSUB vhea vmtx)",
    )
    parser.add_argument(
        "-j",
        "--workers",
        type=int,
        default=None,
        help="Number of parallel worker processes for WOFF2 compression (default: CPU count)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze font cmap and print the 4-tier slice plan without writing WOFF2 files",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    for p in args.inputs:
        if not p.exists():
            print(f"Error: Input font not found: {p}", file=sys.stderr)
            return 1

    config = SliceConfig(
        freq_tiers=args.freq_tiers,
        freq_tail_chunk_size=args.freq_tail_chunk_size,
        rare_kanji_chunk_size=args.rare_chunk_size,
        symbols_ext_chunk_size=args.symbols_chunk_size,
        retain_layout_closure=not args.no_layout_closure,
        drop_tables=tuple(args.drop_tables),
        slices_subdir=args.slices_dir,
        url_prefix=args.url_prefix,
        font_display=args.display,
    )

    if args.dry_run:
        metas = []
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
            validate_and_compute_variant_suffixes(metas, family_override=args.family)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        first = metas[0]
        print(f"Font Family : {first.family_name} (slug: {first.slug})")
        print(f"Type        : {'Variable' if first.is_variable else 'Static'} ({len(metas)} file(s))")
        for m in metas:
            plan = build_slice_plan(m.codepoints, config=config)
            print(
                f"Variant     : weight={m.weight_css}, stretch={m.stretch_css or 'normal'}, "
                f"style={m.style_css} | {len(m.codepoints):,d} chars -> {len(plan)} slices"
            )
            by_tier: dict[int, list] = {4: [], 3: [], 2: [], 1: []}
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
    elapsed = time.time() - t0

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
