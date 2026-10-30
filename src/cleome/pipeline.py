"""
High-level font slicing pipeline (`slice_font` / `slice_fonts`) and bundle cache verification for Cleome.
"""

from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
import hashlib
import io
import json
from pathlib import Path
from fontTools.ttLib import TTFont

from cleome.css import (
    _BLOCK_GLYPHS_RE,
    _CACHE_COMMENT_RE,
    _CACHE_FORMAT_VERSION,
    _compute_slices_output_digest,
    _format_cache_comment,
    _resolve_and_validate_outputs,
    generate_css,
)
from cleome.metadata import (
    FontMetadata,
    _split_hashed_stem,
    _validate_filename_token,
    extract_font_metadata,
)
from cleome.slicer import (
    SliceConfig,
    SliceSpec,
    _prepare_font_bundle,
)
from cleome.subsetter import (
    BuiltSliceFile,
    _atomic_output_file,
    _export_woff2_slices_batch,
    _prune_superseded_hashed_slices,
    serialize_font_to_bytes,
)

__all__ = [
    "SliceBundleResult",
    "_BLOCK_GLYPHS_RE",
    "_CACHE_COMMENT_RE",
    "_CACHE_FORMAT_VERSION",
    "_compute_cache_key",
    "_compute_slices_output_digest",
    "_format_cache_comment",
    "_get_toolchain_versions",
    "_try_load_cached_bundle",
    "slice_font",
    "slice_fonts",
]


@dataclass(frozen=True, slots=True)
class _LoadedFont:
    """Opened probe `TTFont`, worker export source, flavor hint, and SHA-256 input digest."""

    probe_tt: TTFont
    export_source: Path | bytes
    is_woff_hint: bool | None
    sha256_hex: str


@dataclass(frozen=True, slots=True)
class SliceBundleResult:
    """
    Complete result returned by `slice_font` / `slice_fonts`.

    - `slice_specs`: Slice plan for a single font, or the union plan across all input
      codepoints when bundling multiple fonts.
    - `variant_slice_specs`: Per-font slice plans aligned 1:1 with `fonts_metadata`
      (matching the `BuiltSliceFile.spec` items in `built_files` for each variant).
    - `cached`: `True` when existing `.woff2` slices and CSS matched the input digest
      and configuration so `fontTools.subset` + WOFF2 compression was skipped.
    """

    family_name: str
    slug: str
    css_path: Path
    css_text: str
    slices_dir: Path
    slice_specs: list[SliceSpec]
    built_files: list[BuiltSliceFile]
    fonts_metadata: list[FontMetadata]
    variant_slice_specs: list[list[SliceSpec]] = field(default_factory=list)
    cached: bool = False

    @property
    def total_woff2_bytes(self) -> int:
        return sum(f.size_bytes for f in self.built_files)

    @property
    def css_bytes(self) -> int:
        return len(self.css_text.encode("utf-8"))


def _get_toolchain_versions() -> dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version as pkg_version

    versions: dict[str, str] = {}
    for dist_name in ("cleome", "fonttools", "brotli", "stixis"):
        try:
            versions[dist_name] = pkg_version(dist_name)
        except PackageNotFoundError:
            versions[dist_name] = "unknown"
    return versions


def _compute_cache_key(
    font_digests: list[str],
    metas: list[FontMetadata],
    variant_plans: list[list[SliceSpec]],
    config: SliceConfig,
    *,
    family_name: str | None,
    slug: str | None,
    css_filename: str,
    effective_url_prefix: str = "",
    weight: str | None,
    stretch: str | None,
    style: str | None,
) -> str:
    payload = {
        "cache_format": _CACHE_FORMAT_VERSION,
        "toolchain": _get_toolchain_versions(),
        "font_digests": font_digests,
        "config": asdict(config),
        "overrides": {
            "family_name": family_name,
            "slug": slug,
            "css_filename": css_filename,
            "effective_url_prefix": effective_url_prefix,
            "weight": weight,
            "stretch": stretch,
            "style": style,
        },
        "variants": [
            {
                "family_name": m.family_name,
                "slug": m.slug,
                "is_variable": m.is_variable,
                "weight_css": m.weight_css,
                "stretch_css": m.stretch_css,
                "style_css": m.style_css,
                "styles_css": list(m.styles_css),
                "variant_suffix": m.variant_suffix,
                "plan": [
                    {
                        "slice_id": s.slice_id,
                        "tier": s.tier,
                        "category": s.category,
                        "description": s.description,
                        "css_unicode_range": s.css_unicode_range,
                        "cps": sorted(s.binary_codepoints & m.codepoints),
                    }
                    for s in plan
                    if s.binary_codepoints & m.codepoints
                ],
            }
            for m, plan in zip(metas, variant_plans)
        ],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def _try_load_cached_bundle(
    css_path: Path,
    slices_dir: Path,
    cache_key: str,
    metas: list[FontMetadata],
    variant_plans: list[list[SliceSpec]],
    slice_specs: list[SliceSpec],
    config: SliceConfig,
) -> SliceBundleResult | None:
    if not css_path.is_file() or not slices_dir.is_dir():
        return None
    try:
        css_text = css_path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return None

    lines = css_text.splitlines()
    if len(lines) < 2:
        return None
    match = _CACHE_COMMENT_RE.match(lines[1].strip())
    if (
        match is None
        or int(match.group("version")) != _CACHE_FORMAT_VERSION
        or match.group("key") != cache_key
    ):
        return None
    expected_out_digest = match.group("out")

    # Extract per-block glyph counts from `/* [slice-id] ... (N glyphs, M.K KB) */` lines
    block_glyphs: list[int] = []
    for line in lines[2:]:
        bm = _BLOCK_GLYPHS_RE.match(line.strip())
        if bm is not None:
            block_glyphs.append(int(bm.group("glyphs")))

    expected_block_count = sum(
        len(m.styles_css)
        * sum(1 for spec in plan if spec.binary_codepoints & m.codepoints)
        for m, plan in zip(metas, variant_plans)
    )
    if len(block_glyphs) != expected_block_count:
        return None

    slices_dir_resolved = slices_dir.resolve()
    hashed_by_stem: dict[str, str] = {}
    if config.filename_hash:
        try:
            for existing in slices_dir.iterdir():
                if not existing.is_file() or existing.suffix != ".woff2":
                    continue
                split_res = _split_hashed_stem(existing.stem)
                if split_res is not None:
                    base_stem = split_res[0]
                    if base_stem in hashed_by_stem:
                        # Ambiguous multiple hashed files for the same stem
                        hashed_by_stem[base_stem] = ""
                    else:
                        hashed_by_stem[base_stem] = existing.name
        except OSError:
            return None

    per_meta_files: list[list[BuiltSliceFile]] = []
    all_built_files: list[BuiltSliceFile] = []
    out_hasher = hashlib.sha256()

    block_cursor = 0
    for m, plan in zip(metas, variant_plans):
        active_specs = [spec for spec in plan if spec.binary_codepoints & m.codepoints]
        num_styles = len(m.styles_css)
        n_specs = len(active_specs)
        # First style block carries the glyph counts for this variant's active slices
        variant_glyph_counts = block_glyphs[block_cursor : block_cursor + n_specs]
        block_cursor += num_styles * n_specs
        variant_files: list[BuiltSliceFile] = []

        for spec, glyph_count in zip(active_specs, variant_glyph_counts):
            if glyph_count <= 0:
                return None
            base_fname = _validate_filename_token(
                spec.filename(m.slug, m.variant_suffix), "slice filename"
            )
            if config.filename_hash:
                expected_fname = hashed_by_stem.get(Path(base_fname).stem, "")
                if not expected_fname:
                    return None
                expected_fname = _validate_filename_token(expected_fname, "slice filename")
            else:
                expected_fname = base_fname
            slice_path = slices_dir / expected_fname
            try:
                if not slice_path.resolve().is_relative_to(slices_dir_resolved):
                    return None
                raw_slice = slice_path.read_bytes()
            except OSError:
                return None
            size_bytes = len(raw_slice)
            if size_bytes <= 0:
                return None
            if config.filename_hash:
                actual_hash8 = hashlib.sha256(raw_slice).hexdigest()[:8]
                if not expected_fname.endswith(f"-{actual_hash8}.woff2"):
                    return None

            out_hasher.update(
                f"{expected_fname}:{size_bytes}:{glyph_count}\n".encode("utf-8")
            )
            out_hasher.update(raw_slice)

            bf = BuiltSliceFile(
                spec=spec,
                filename=expected_fname,
                path=slice_path,
                size_bytes=size_bytes,
                glyph_count=glyph_count,
                variant_suffix=m.variant_suffix,
            )
            variant_files.append(bf)
            all_built_files.append(bf)
        per_meta_files.append(variant_files)

    if out_hasher.hexdigest()[:32] != expected_out_digest:
        return None

    fonts_and_files = list(zip(metas, per_meta_files))
    expected_css = generate_css(
        fonts_and_files,
        config=config,
        cache_key=cache_key,
        out_digest=expected_out_digest,
        css_path=css_path,
        slices_dir=slices_dir,
    )
    if css_text != expected_css:
        return None

    return SliceBundleResult(
        family_name=metas[0].family_name,
        slug=metas[0].slug,
        css_path=css_path,
        css_text=css_text,
        slices_dir=slices_dir,
        slice_specs=slice_specs,
        built_files=all_built_files,
        fonts_metadata=metas,
        variant_slice_specs=variant_plans,
        cached=True,
    )


def slice_fonts(
    fonts: Sequence[Path | str | TTFont],
    out_dir: Path | str,
    *,
    family_name: str | None = None,
    slug: str | None = None,
    css_filename: str | None = None,
    css_path: Path | str | None = None,
    weight: str | None = None,
    stretch: str | None = None,
    style: str | None = None,
    config: SliceConfig | None = None,
    workers: int | None = None,
    force: bool = False,
) -> SliceBundleResult:
    """
    Slice one or more input fonts (Variable or Static `.ttf`/`.otf` paths or in-memory `TTFont`s)
    into optimized `unicode-range` `.woff2` slices and generate a unified `@font-face` CSS file.

    When `force=False` (default), if the output CSS and all planned `.woff2` slice files already
    exist on disk with matching SHA-256 input digests, configuration, and byte sizes,
    `fontTools.subset` + WOFF2 compression is skipped and `result.cached` is `True`.

    Important: When `workers` > 1 (or `None` on multi-core machines), `ProcessPoolExecutor` is used.
    When calling this function from a script on macOS/Windows/Linux (`spawn`/`forkserver`), guard
    the script's entry point with `if __name__ == "__main__":` (or pass `workers=1`).
    """
    if isinstance(fonts, (str, bytes, Path, TTFont)) or not fonts:
        raise ValueError("At least one input font must be provided in a sequence.")
    if workers is not None and workers <= 0:
        raise ValueError(f"workers must be >= 1, got {workers}")
    if config is None:
        config = SliceConfig()

    loaded_fonts: list[_LoadedFont] = []
    with ExitStack() as stack:
        for f in fonts:
            if isinstance(f, TTFont):
                raw_bytes = serialize_font_to_bytes(f)
                digest = hashlib.sha256(raw_bytes).hexdigest()
                probe_tt = stack.enter_context(
                    TTFont(io.BytesIO(raw_bytes), lazy=True, recalcTimestamp=False)
                )
                loaded_fonts.append(_LoadedFont(probe_tt, raw_bytes, False, digest))
            else:
                p = Path(f)
                raw_bytes = p.read_bytes()
                digest = hashlib.sha256(raw_bytes).hexdigest()
                probe_tt = stack.enter_context(
                    TTFont(io.BytesIO(raw_bytes), lazy=True, recalcTimestamp=False)
                )
                loaded_fonts.append(_LoadedFont(probe_tt, p, probe_tt.flavor is not None, digest))

        raw_metas = [
            extract_font_metadata(
                lf.probe_tt,
                family_override=family_name,
                slug_override=slug,
                weight_override=weight,
                stretch_override=stretch,
                style_override=style,
                uvs_base_repair=config.uvs_base_repair,
            )
            for lf in loaded_fonts
        ]
        prepared = _prepare_font_bundle(
            raw_metas,
            config,
            family_override=family_name,
            slug_override=slug,
        )
        outputs = _resolve_and_validate_outputs(
            out_dir,
            config,
            prepared.metas,
            prepared.variant_plans,
            css_filename=css_filename,
            css_path=css_path,
        )

        font_digests = [lf.sha256_hex for lf in loaded_fonts]
        cache_key = _compute_cache_key(
            font_digests,
            prepared.metas,
            prepared.variant_plans,
            config,
            family_name=family_name,
            slug=slug,
            css_filename=outputs.css_filename,
            effective_url_prefix=outputs.url_prefix,
            weight=weight,
            stretch=stretch,
            style=style,
        )

        if not force:
            cached_res = _try_load_cached_bundle(
                outputs.css_path,
                outputs.slices_dir,
                cache_key,
                prepared.metas,
                prepared.variant_plans,
                prepared.union_plan,
                config,
            )
            if cached_res is not None:
                return cached_res

        outputs.out_path.mkdir(parents=True, exist_ok=True)
        if outputs.explicit_css:
            outputs.css_path.parent.mkdir(parents=True, exist_ok=True)

        batch_variants: list[tuple[TTFont | bytes | Path | str, FontMetadata, list[SliceSpec]]] = [
            (lf.export_source, m, variant_specs)
            for lf, m, variant_specs in zip(loaded_fonts, prepared.metas, prepared.variant_plans)
        ]
        woff_hints = [lf.is_woff_hint for lf in loaded_fonts]
        variant_built_lists = _export_woff2_slices_batch(
            batch_variants,
            slices_dir=outputs.slices_dir,
            config=config,
            workers=workers,
            is_woff_hints=woff_hints,
            prune_superseded=False,
        )

        fonts_and_files = list(zip(prepared.metas, variant_built_lists))
        all_built_files = [bf for built in variant_built_lists for bf in built]

        out_digest = _compute_slices_output_digest(all_built_files)
        css_text = generate_css(
            fonts_and_files,
            config=config,
            cache_key=cache_key,
            out_digest=out_digest,
            css_path=outputs.css_path,
            slices_dir=outputs.slices_dir,
        )
        with _atomic_output_file(outputs.css_path, mode="w", encoding="utf-8") as tf:
            tf.write(css_text)

        if config.filename_hash:
            _prune_superseded_hashed_slices(
                outputs.slices_dir,
                all_built_files,
                prepared.metas,
            )

        return SliceBundleResult(
            family_name=prepared.family_name,
            slug=prepared.slug,
            css_path=outputs.css_path,
            css_text=css_text,
            slices_dir=outputs.slices_dir,
            slice_specs=prepared.union_plan,
            built_files=all_built_files,
            fonts_metadata=prepared.metas,
            variant_slice_specs=prepared.variant_plans,
            cached=False,
        )


def slice_font(
    font: Path | str | TTFont,
    out_dir: Path | str,
    *,
    family_name: str | None = None,
    slug: str | None = None,
    css_filename: str | None = None,
    css_path: Path | str | None = None,
    weight: str | None = None,
    stretch: str | None = None,
    style: str | None = None,
    config: SliceConfig | None = None,
    workers: int | None = None,
    force: bool = False,
) -> SliceBundleResult:
    """
    Convenience Python API to slice a single font (Variable or Static `.ttf`/`.otf` path or `TTFont`)
    into optimized `unicode-range` `.woff2` slices and generate `@font-face` CSS.

    Important: When `workers` > 1 (or `None` on multi-core machines), `ProcessPoolExecutor` is used.
    When calling this function from a script on macOS/Windows/Linux (`spawn`/`forkserver`), guard
    the script's entry point with `if __name__ == "__main__":` (or pass `workers=1`).
    """
    return slice_fonts(
        [font],
        out_dir=out_dir,
        family_name=family_name,
        slug=slug,
        css_filename=css_filename,
        css_path=css_path,
        weight=weight,
        stretch=stretch,
        style=style,
        config=config,
        workers=workers,
        force=force,
    )
