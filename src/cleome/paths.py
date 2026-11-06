"""
Output directory, CSS path, slice path validation, and URL prefix resolution for Cleome.
"""

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from cleome.metadata import (
    FontMetadata,
    _validate_filename_token,
)
from cleome.slicer import SliceConfig, SliceSpec


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
        if not config.url_prefix:
            return ""
        prefix = config.url_prefix.rstrip("/")
        return f"{prefix}/" if prefix else "/"
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


def _validate_slice_output_path(
    meta: FontMetadata,
    spec: SliceSpec,
    slices_dir: Path,
    slices_resolved: Path,
    seen_slice_paths: set[str],
    *,
    filename_hash: bool,
    css_resolved: Path | None = None,
    css_path_for_err: Path | None = None,
) -> tuple[str, str]:
    """Validate a single planned slice output path and return `(base_fname, str(slice_out))`."""
    base_fname = _validate_filename_token(
        spec.filename(meta.slug, meta.variant_suffix), "slice filename"
    )
    slice_out = slices_dir / base_fname
    slice_resolved = slice_out.resolve()
    if not slice_resolved.is_relative_to(slices_resolved):
        raise ValueError(
            f"Slice output path {slice_out!r} resolves outside slices_dir {slices_dir!r}"
        )
    if not filename_hash:
        if slice_out.exists() and slice_out.is_dir():
            raise ValueError(
                f"Slice output path must be a file, not an existing directory: {slice_out!r}"
            )
        if css_resolved is not None and slice_resolved == css_resolved:
            raise ValueError(
                f"css_path {css_path_for_err!r} collides with planned slice output {slice_out!r}"
            )
    slice_str = str(slice_out)
    if slice_str in seen_slice_paths:
        raise ValueError(f"Duplicate slice output path across variants: {slice_str}")
    seen_slice_paths.add(slice_str)
    return base_fname, slice_str


def _build_preload_slices(
    items: Sequence[tuple[SliceSpec, str, str]],
    url_prefix: str,
) -> list[dict[str, str]]:
    """Build the Tier 1 preload summary entries from `(spec, variant_suffix, filename)` tuples."""
    return [
        {
            "slice_id": spec.slice_id,
            "variant_suffix": variant_suffix,
            "filename": filename,
            "url": f"{url_prefix}{filename}",
        }
        for spec, variant_suffix, filename in items
        if spec.tier == 1
    ]


def _resolve_and_validate_outputs(
    out_dir: Path | str,
    config: SliceConfig,
    metas: Sequence[FontMetadata],
    active_variant_plans: Sequence[Sequence[SliceSpec]],
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
    preload_items: list[tuple[SliceSpec, str, str]] = []
    seen_slice_paths: set[str] = set()
    hash_placeholder = "<hash8>" if config.filename_hash else ""

    for m, active_plan in zip(metas, active_variant_plans):
        for spec in active_plan:
            _validate_slice_output_path(
                m,
                spec,
                slices_dir,
                slices_resolved,
                seen_slice_paths,
                filename_hash=config.filename_hash,
                css_resolved=css_resolved,
                css_path_for_err=resolved_css_path,
            )
            total_active_slices += 1
            preload_items.append(
                (
                    spec,
                    m.variant_suffix,
                    spec.filename(m.slug, m.variant_suffix, hash_placeholder),
                )
            )

    preload_slices = _build_preload_slices(preload_items, url_prefix)

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
