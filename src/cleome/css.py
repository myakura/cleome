"""
CSS `@font-face` stylesheet generator for Cleome.
"""

import hashlib
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

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

from cleome.metadata import (
    FontMetadata,
    _validate_filename_token,
)
from cleome.slicer import SliceConfig, SliceSpec

_CLEOME_CSS_OPTIONS = SerializationOptions.cleome()
_CACHE_FORMAT_VERSION = 1
_CACHE_COMMENT_RE = re.compile(
    r"^/\* cleome-cache: v(?P<version>\d+) key=(?P<key>[0-9a-f]{32}) out=(?P<out>[0-9a-f]{32}) \*/$"
)
_BLOCK_GLYPHS_RE = re.compile(
    r"^/\* \[[^\]]+\] .* \((?P<glyphs>\d+) glyphs, [0-9.]+ KB\) \*/$"
)


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


def _compute_slices_output_digest(built_files: Sequence[_SliceFileLike]) -> str:
    """Compute a single 32-hex-char SHA-256 digest over all built `.woff2` slice files in order."""
    hasher = hashlib.sha256()
    for bf in built_files:
        raw = bf.path.read_bytes()
        hasher.update(f"{bf.filename}:{len(raw)}:{bf.glyph_count}\n".encode())
        hasher.update(raw)
    return hasher.hexdigest()[:32]


def _format_cache_comment(cache_key: str, out_digest: str) -> str:
    return (
        f"/* cleome-cache: v{_CACHE_FORMAT_VERSION} key={cache_key} out={out_digest} */"
    )


def _ensure_directory_creatable(dir_path: Path, label: str) -> None:
    """Raise `ValueError` if `dir_path` or any existing ancestor on disk is a non-directory file."""
    if dir_path.exists():
        if not dir_path.is_dir():
            raise ValueError(
                f"{label} must be a directory path, not an existing file: {dir_path!r}"
            )
        return
    for parent in dir_path.parents:
        if parent.exists():
            if not parent.is_dir():
                raise ValueError(
                    f"Cannot create {label} under existing file {parent!r}: {dir_path!r}"
                )
            return


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
        css_dir_abs = os.path.abspath(css_path.parent)
        slices_dir_abs = os.path.abspath(slices_dir)
        try:
            rel_posix = Path(os.path.relpath(slices_dir_abs, css_dir_abs)).as_posix()
        except ValueError as exc:
            raise ValueError(
                "Cannot compute a relative URL from css_path to slices_dir across different drives; "
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


@dataclass(frozen=True, slots=True)
class _ResolvedOutputs:
    """Validated output paths, resolved URL prefix, and dry-run preload summary."""

    out_path: Path
    slices_dir: Path
    css_path: Path
    css_filename: str
    explicit_css: bool
    url_prefix: str
    total_active_slices: int
    preload_slices: list[dict[str, str]]


def _resolve_and_validate_outputs(
    out_dir: Path | str,
    config: SliceConfig,
    metas: Sequence[FontMetadata],
    variant_plans: Sequence[Sequence[SliceSpec]],
    *,
    css_filename: str | None = None,
    css_path: Path | str | None = None,
) -> _ResolvedOutputs:
    """Validate all output directories, CSS path, URL prefix, and slice filenames without mutating disk."""
    if css_filename is not None and css_path is not None:
        raise ValueError("Cannot specify both css_filename and css_path.")
    if css_filename is not None:
        css_filename = _validate_filename_token(css_filename, "css_filename")

    if css_path is not None:
        css_path_str = str(css_path).strip()
        if not css_path_str or "\x00" in css_path_str:
            raise ValueError(f"Invalid css_path: {css_path!r}")
        explicit_css_path = Path(css_path_str)
        if not explicit_css_path.name or explicit_css_path.name in (".", ".."):
            raise ValueError(f"css_path must point to a file path, got {css_path!r}")
        if explicit_css_path.exists() and explicit_css_path.is_dir():
            raise ValueError(
                f"css_path must be a file path, not an existing directory: {css_path!r}"
            )
    else:
        explicit_css_path = None

    out_path = Path(out_dir)
    _ensure_directory_creatable(out_path, "out_dir")
    out_resolved = out_path.resolve()

    subdir = config.slices_subdir.strip().rstrip("/")
    slices_dir = out_path if (not subdir or subdir == ".") else (out_path / subdir)
    slices_resolved = slices_dir.resolve()
    if not slices_resolved.is_relative_to(out_resolved):
        raise ValueError(
            f"slices_dir {slices_dir!r} resolves outside out_dir {out_path!r}"
        )
    _ensure_directory_creatable(slices_dir, "slices_dir")

    unified_slug = metas[0].slug
    if explicit_css_path is not None:
        resolved_css_path = explicit_css_path
        resolved_css_name = resolved_css_path.name
    else:
        resolved_css_name = _validate_filename_token(
            css_filename or f"{unified_slug}.css", "css_filename"
        )
        resolved_css_path = out_path / resolved_css_name
        if not resolved_css_path.resolve().is_relative_to(out_resolved):
            raise ValueError(
                f"css_path {resolved_css_path!r} resolves outside out_dir {out_path!r}"
            )

    _ensure_directory_creatable(resolved_css_path.parent, "css_path parent directory")
    if resolved_css_path.exists() and resolved_css_path.is_dir():
        raise ValueError(
            f"css_path must be a file path, not an existing directory: {resolved_css_path!r}"
        )
    css_resolved = resolved_css_path.resolve()
    if css_resolved in (out_resolved, slices_resolved):
        raise ValueError(
            f"css_path {resolved_css_path!r} collides with output directory"
        )

    url_prefix = _resolve_url_prefix(
        config, css_path=resolved_css_path, slices_dir=slices_dir
    )

    total_active_slices = 0
    preload_slices: list[dict[str, str]] = []
    seen_slice_paths: set[str] = set()
    hash_placeholder = "<hash8>" if config.filename_hash else ""

    for m, plan in zip(metas, variant_plans):
        for spec in plan:
            if not (spec.binary_codepoints & m.codepoints):
                continue
            base_fname = _validate_filename_token(
                spec.filename(m.slug, m.variant_suffix), "slice filename"
            )
            slice_out = slices_dir / base_fname
            slice_resolved = slice_out.resolve()
            if not slice_resolved.is_relative_to(slices_resolved):
                raise ValueError(
                    f"Slice output path {slice_out!r} resolves outside slices_dir {slices_dir!r}"
                )
            if not config.filename_hash:
                if slice_out.exists() and slice_out.is_dir():
                    raise ValueError(
                        f"Slice output path must be a file, not an existing directory: {slice_out!r}"
                    )
                if slice_resolved == css_resolved:
                    raise ValueError(
                        f"css_path {resolved_css_path!r} collides with planned slice output {slice_out!r}"
                    )
            slice_str = str(slice_out)
            if slice_str in seen_slice_paths:
                raise ValueError(
                    f"Duplicate slice output path across variants: {slice_str}"
                )
            seen_slice_paths.add(slice_str)
            total_active_slices += 1
            if spec.tier == 1:
                preview_fname = spec.filename(
                    m.slug, m.variant_suffix, hash_placeholder
                )
                preload_slices.append(
                    {
                        "slice_id": spec.slice_id,
                        "variant_suffix": m.variant_suffix,
                        "filename": preview_fname,
                        "url": f"{url_prefix}{preview_fname}",
                    }
                )

    return _ResolvedOutputs(
        out_path=out_path,
        slices_dir=slices_dir,
        css_path=resolved_css_path,
        css_filename=resolved_css_name,
        explicit_css=explicit_css_path is not None,
        url_prefix=url_prefix,
        total_active_slices=total_active_slices,
        preload_slices=preload_slices,
    )


def generate_css(
    fonts_and_files: Sequence[tuple[FontMetadata, Sequence[_SliceFileLike]]],
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
            resolved_out = (
                out_digest
                if out_digest is not None
                else _compute_slices_output_digest(all_files)
            )
            rules.append(CommentNode(_format_cache_comment(cache_key, resolved_out)))

        for meta, built_files in fonts_and_files:
            styles = meta.styles_css
            if len(fonts_and_files) > 1:
                v_label = f" ({meta.variant_suffix})" if meta.variant_suffix else ""
                stretch_info = (
                    f", stretch: {meta.stretch_css}" if meta.stretch_css else ""
                )
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


__all__ = [
    "generate_css",
]
