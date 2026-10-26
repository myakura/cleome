"""
CSS `@font-face` stylesheet generator for Cleome.
"""

from pathlib import Path
from typing import Any, Protocol
from stixis import (
    CommentNode,
    FontFace,
    FontSrc,
    Rule,
    SerializationOptions,
    StyleSheet,
    dumps,
    using_options,
)

from cleome.metadata import FontMetadata, validate_and_compute_variant_suffixes
from cleome.slicer import SliceConfig, SliceSpec

_CLEOME_CSS_OPTIONS = SerializationOptions.cleome()


class _SliceFileLike(Protocol):
    @property
    def spec(self) -> SliceSpec: ...

    @property
    def filename(self) -> str: ...

    @property
    def path(self) -> Path: ...

    @property
    def size_bytes(self) -> int: ...

    @property
    def glyph_count(self) -> int: ...


def _resolve_url_prefix(
    config: SliceConfig,
    *,
    css_path: Path | None = None,
    slices_dir: Path | None = None,
) -> str:
    if config.url_prefix is not None:
        prefix = config.url_prefix.rstrip("/")
        return f"{prefix}/" if prefix else ""
    if css_path is not None and slices_dir is not None:
        import os
        from urllib.parse import quote

        css_dir_abs = os.path.abspath(css_path.parent)
        slices_dir_abs = os.path.abspath(slices_dir)
        try:
            rel_posix = Path(os.path.relpath(slices_dir_abs, css_dir_abs)).as_posix()
        except ValueError as exc:
            raise ValueError(
                "Cannot compute a relative URL from css_out to slices_dir across different drives; "
                "pass an explicit --url-prefix."
            ) from exc
        if rel_posix == ".":
            return "./"
        encoded_parts = [
            part if part in (".", "..") else quote(part, safe="-._~:@!$&()*+,;=")
            for part in rel_posix.split("/")
            if part
        ]
        encoded_rel = "/".join(encoded_parts)
        if not encoded_rel.startswith("."):
            return f"./{encoded_rel}/"
        return f"{encoded_rel}/"
    subdir = config.slices_subdir.strip().strip("/")
    if not subdir or subdir == ".":
        return "./"
    return f"./{subdir}/"


def generate_css(
    fonts_and_files: list[tuple[FontMetadata, list[_SliceFileLike]]],
    config: SliceConfig | None = None,
    *,
    cache_key: str | None = None,
    out_digest: str | None = None,
    css_path: Path | None = None,
    slices_dir: Path | None = None,
) -> str:
    """
    Generate the complete `@font-face` CSS text for one or more font variants.
    Preserves the strict Tier 4 -> Tier 3 -> Tier 2 -> Tier 1 cascade order for each variant.
    """
    if config is None:
        config = SliceConfig()
    if not fonts_and_files:
        return "\n"

    url_prefix = _resolve_url_prefix(config, css_path=css_path, slices_dir=slices_dir)
    first_meta = fonts_and_files[0][0]
    all_files = [f for _, files in fonts_and_files for f in files]
    total_slices = len(all_files)
    total_kb = sum(f.size_bytes for f in all_files) / 1024.0

    with using_options(_CLEOME_CSS_OPTIONS):
        rules: list[Rule] = []
        if cache_key is not None:
            from cleome.pipeline import (
                _compute_slices_output_digest,
                _format_cache_comment,
            )

            resolved_out = (
                out_digest
                if out_digest is not None
                else _compute_slices_output_digest(all_files)
            )
            rules.append(CommentNode(_format_cache_comment(cache_key, resolved_out)))

        for meta, built_files in fonts_and_files:
            styles = meta.styles_css or (meta.style_css,)
            if len(fonts_and_files) > 1:
                v_label = f" ({meta.variant_suffix})" if meta.variant_suffix else ""
                stretch_info = f", stretch: {meta.stretch_css}" if meta.stretch_css else ""
                style_info = " + ".join(styles)
                rules.append(
                    CommentNode(
                        t"=== Variant: {meta.family_name}{v_label} "
                        t"[weight: {meta.weight_css}{stretch_info}, "
                        t"style: {style_info}] ===",
                        leading_newline=True,
                    )
                )
            for style_val in styles:
                for bf in built_files:
                    spec = bf.spec
                    size_kb = bf.size_bytes / 1024.0
                    url_val = f"{url_prefix}{bf.filename}"
                    rules.append(
                        FontFace(
                            comment=t"[{spec.slice_id}] {spec.description} ({bf.glyph_count} glyphs, {size_kb:.1f} KB)",
                            font_family=meta.family_name,
                            font_style=style_val,
                            font_weight=meta.weight_css,
                            font_stretch=meta.stretch_css,
                            font_display=config.font_display,
                            src=FontSrc(url=url_val, format="woff2"),
                            unicode_range=spec.css_unicode_range,
                        )
                    )

        sheet = StyleSheet(
            rules=rules,
            header_comment=(
                t"Generated by Cleome: {first_meta.family_name} "
                t"({total_slices} slices, {total_kb:.1f} KB total WOFF2)"
            ),
        )
        return dumps(sheet)


_PIPELINE_REEXPORTS = (
    "SliceBundleResult",
    "slice_font",
    "slice_fonts",
    "_BLOCK_GLYPHS_RE",
    "_CACHE_COMMENT_RE",
    "_CACHE_FORMAT_VERSION",
    "_compute_cache_key",
    "_compute_slices_output_digest",
    "_format_cache_comment",
    "_get_toolchain_versions",
    "_try_load_cached_bundle",
)

__all__ = [
    "SliceBundleResult",
    "generate_css",
    "slice_font",
    "slice_fonts",
    "validate_and_compute_variant_suffixes",
]


def __getattr__(name: str) -> Any:
    if name in _PIPELINE_REEXPORTS:
        import cleome.pipeline as _pipeline

        return getattr(_pipeline, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
