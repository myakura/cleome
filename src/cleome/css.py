"""
CSS `@font-face` stylesheet generator and high-level Python API (`slice_font` / `slice_fonts`) for Cleome.
"""

from dataclasses import asdict, dataclass, field
import hashlib
import io
import json
from pathlib import Path
import re
from string.templatelib import Interpolation, Template, convert
from fontTools.ttLib import TTFont

from cleome.metadata import (
    FontMetadata,
    _validate_filename_token,
    extract_font_metadata,
)
from cleome.slicer import (
    SliceConfig,
    SliceSpec,
    build_slice_plan,
)
from cleome.subsetter import (
    BuiltSliceFile,
    _atomic_output_file,
    export_woff2_slices,
    _export_woff2_slices_batch,
    serialize_font_to_bytes,
)

_CACHE_FORMAT_VERSION = 1
_CACHE_COMMENT_RE = re.compile(
    r"^/\* cleome-cache: v(?P<version>\d+) key=(?P<key>[0-9a-f]{32}) out=(?P<out>[0-9a-f]{32}) \*/$"
)
_BLOCK_GLYPHS_RE = re.compile(
    r"^/\* \[[^\]]+\] .* \((?P<glyphs>\d+) glyphs, [0-9.]+ KB\) \*/$"
)


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


def _escape_css_str(val: str) -> str:
    """Escape backslashes, single quotes, and CSS newlines for safe embedding inside single-quoted CSS strings."""
    return (
        val.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\r", "\\d ")
        .replace("\n", "\\a ")
        .replace("\f", "\\c ")
    )


def _escape_css_comment(val: str) -> str:
    """Sanitize a string for safe embedding inside a single-line `/* ... */` CSS comment."""
    cleaned = (
        val.replace("\r\n", " ")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("\f", " ")
    )
    while "*/" in cleaned:
        cleaned = cleaned.replace("*/", "* /")
    return cleaned


def _render_css(tpl: Template | str) -> str:
    """Render a `t"..."` Template, auto-escaping interpolations inside `/* ... */` or `'...'`."""
    if isinstance(tpl, str):
        return tpl
    out: list[str] = []
    in_comment = False
    in_quote = False
    for item in tpl:
        if isinstance(item, str):
            out.append(item)
            for i, ch in enumerate(item):
                if not in_quote and item[i : i + 2] == "/*":
                    in_comment = True
                elif in_comment and item[max(0, i - 1) : i + 1] == "*/":
                    in_comment = False
                elif not in_comment and ch == "'":
                    in_quote = not in_quote
        elif isinstance(item, Interpolation):
            val = convert(item.value, item.conversion)
            text = format(val, item.format_spec) if item.format_spec else str(val)
            if in_comment:
                out.append(_escape_css_comment(text))
            elif in_quote:
                out.append(_escape_css_str(text))
            else:
                out.append(text)
    return "".join(out)


def _resolve_url_prefix(config: SliceConfig) -> str:
    if config.url_prefix is not None:
        prefix = config.url_prefix.rstrip("/")
        return f"{prefix}/" if prefix else ""
    subdir = config.slices_subdir.strip().strip("/")
    if not subdir or subdir == ".":
        return "./"
    return f"./{subdir}/"


def _format_font_face(
    comment: Template | str,
    descriptors: dict[str, Template | str | None],
) -> str:
    """Format a single commented `@font-face` rule, omitting `None` descriptors."""
    body = "\n".join(
        f"  {prop}: {_render_css(val)};"
        for prop, val in descriptors.items()
        if val is not None
    )
    return f"{_render_css(comment)}\n@font-face {{\n{body}\n}}"


def _compute_slices_output_digest(built_files: list[BuiltSliceFile]) -> str:
    """Compute a single 32-hex-char SHA-256 digest over all built `.woff2` slice files in order."""
    hasher = hashlib.sha256()
    for bf in built_files:
        raw = bf.path.read_bytes()
        hasher.update(f"{bf.filename}:{len(raw)}:{bf.glyph_count}\n".encode("utf-8"))
        hasher.update(raw)
    return hasher.hexdigest()[:32]


def _format_cache_comment(cache_key: str, out_digest: str) -> str:
    return (
        f"/* cleome-cache: v{_CACHE_FORMAT_VERSION} "
        f"key={cache_key} out={out_digest} */"
    )


def _get_toolchain_versions() -> dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version as pkg_version

    versions: dict[str, str] = {}
    for dist_name in ("cleome", "fonttools", "brotli"):
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
        len(m.styles_css or (m.style_css,))
        * sum(1 for spec in plan if spec.binary_codepoints & m.codepoints)
        for m, plan in zip(metas, variant_plans)
    )
    if len(block_glyphs) != expected_block_count:
        return None

    slices_dir_resolved = slices_dir.resolve()
    per_meta_files: dict[int, list[BuiltSliceFile]] = {i: [] for i in range(len(metas))}
    all_built_files: list[BuiltSliceFile] = []
    out_hasher = hashlib.sha256()

    block_cursor = 0
    for meta_idx, (m, plan) in enumerate(zip(metas, variant_plans)):
        active_specs = [spec for spec in plan if spec.binary_codepoints & m.codepoints]
        num_styles = len(m.styles_css or (m.style_css,))
        n_specs = len(active_specs)
        # First style block carries the glyph counts for this variant's active slices
        variant_glyph_counts = block_glyphs[block_cursor : block_cursor + n_specs]
        block_cursor += num_styles * n_specs

        for spec, glyph_count in zip(active_specs, variant_glyph_counts):
            if glyph_count <= 0:
                return None
            expected_fname = _validate_filename_token(
                spec.filename(m.slug, m.variant_suffix), "slice filename"
            )
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
            per_meta_files[meta_idx].append(bf)
            all_built_files.append(bf)

    if out_hasher.hexdigest()[:32] != expected_out_digest:
        return None

    fonts_and_files = [(m, per_meta_files[i]) for i, m in enumerate(metas)]
    expected_css = generate_css(
        fonts_and_files,
        config=config,
        cache_key=cache_key,
        out_digest=expected_out_digest,
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


def generate_css(
    fonts_and_files: list[tuple[FontMetadata, list[BuiltSliceFile]]],
    config: SliceConfig | None = None,
    *,
    cache_key: str | None = None,
    out_digest: str | None = None,
) -> str:
    """
    Generate the complete `@font-face` CSS text for one or more font variants.
    Preserves the strict Tier 4 -> Tier 3 -> Tier 2 -> Tier 1 cascade order for each variant.
    """
    if config is None:
        config = SliceConfig()

    url_prefix = _resolve_url_prefix(config)
    blocks: list[str] = []

    if fonts_and_files:
        first_meta = fonts_and_files[0][0]
        all_files = [f for _, files in fonts_and_files for f in files]
        total_slices = len(all_files)
        total_kb = sum(f.size_bytes for f in all_files) / 1024.0
        blocks.append(
            _render_css(
                t"/* Generated by Cleome: {first_meta.family_name} "
                t"({total_slices} slices, {total_kb:.1f} KB total WOFF2) */"
            )
        )
        if cache_key is not None:
            resolved_out = (
                out_digest
                if out_digest is not None
                else _compute_slices_output_digest(all_files)
            )
            blocks.append(_format_cache_comment(cache_key, resolved_out))

    for meta, built_files in fonts_and_files:
        styles = meta.styles_css or (meta.style_css,)
        if len(fonts_and_files) > 1:
            v_label = f" ({meta.variant_suffix})" if meta.variant_suffix else ""
            stretch_info = f", stretch: {meta.stretch_css}" if meta.stretch_css else ""
            style_info = " + ".join(styles)
            blocks.append(
                _render_css(
                    t"\n/* === Variant: {meta.family_name}{v_label} "
                    t"[weight: {meta.weight_css}{stretch_info}, "
                    t"style: {style_info}] === */"
                )
            )
        for style_val in styles:
            for bf in built_files:
                spec = bf.spec
                size_kb = bf.size_bytes / 1024.0
                url_val = f"{url_prefix}{bf.filename}"
                blocks.append(
                    _format_font_face(
                        t"/* [{spec.slice_id}] {spec.description} ({bf.glyph_count} glyphs, {size_kb:.1f} KB) */",
                        {
                            "font-family": t"'{meta.family_name}'",
                            "font-style": style_val,
                            "font-weight": meta.weight_css,
                            "font-stretch": meta.stretch_css,
                            "font-display": config.font_display,
                            "src": t"url('{url_val}') format('woff2')",
                            "unicode-range": spec.css_unicode_range,
                        },
                    )
                )

    return "\n".join(blocks) + "\n"


def validate_and_compute_variant_suffixes(
    raw_metas: list[FontMetadata],
    *,
    family_override: str | None = None,
) -> list[str]:
    """
    Validate that multiple input fonts can be safely bundled into a single `@font-face`
    stylesheet without silent family mismatches or duplicate `(weight, stretch, style)` descriptors,
    and return collision-free filename suffixes (`400`, `700`, `75-400`, `400-italic`, etc.).
    """
    if len(raw_metas) <= 1:
        return [m.variant_suffix for m in raw_metas]

    distinct_families = sorted({m.family_name for m in raw_metas})
    if family_override is None and len(distinct_families) > 1:
        raise ValueError(
            f"Input fonts have different detected font-family names ({', '.join(repr(f) for f in distinct_families)}). "
            "Slice each family separately, or pass `--family` / `family_name=...` explicitly if unifying them."
        )

    seen_descriptors: set[tuple[str, str, str]] = set()
    for m in raw_metas:
        for st in (m.styles_css or (m.style_css,)):
            desc_key = (m.weight_css, m.stretch_css or "100%", st)
            if desc_key in seen_descriptors:
                raise ValueError(
                    f"Duplicate @font-face descriptors across input fonts: "
                    f"weight={m.weight_css!r}, stretch={m.stretch_css or 'normal'!r}, style={st!r}. "
                    "Each font in a multi-file bundle must have a unique (weight, stretch, style) combination."
                )
            seen_descriptors.add(desc_key)

    has_multiple_styles = len({m.styles_css for m in raw_metas}) > 1
    has_multiple_stretches = len({m.stretch_css for m in raw_metas}) > 1

    suffixes: list[str] = []
    for m in raw_metas:
        if m.variant_suffix:
            suf = m.variant_suffix
        else:
            parts: list[str] = []
            if has_multiple_stretches:
                stretch_label = (m.stretch_css or "100%").replace("%", "").replace(".", "_").replace(" ", "-").lower()
                parts.append(f"w{stretch_label}")
            parts.append(m.weight_css.replace(".", "_").replace(" ", "-"))
            if has_multiple_styles and m.style_css != "normal":
                parts.append(m.style_css.replace(".", "_").replace(" ", "-").lower())
            suf = "-".join(parts)
        suffixes.append(suf)
    return suffixes


def slice_fonts(
    fonts: list[Path | str | TTFont],
    out_dir: Path | str,
    *,
    family_name: str | None = None,
    slug: str | None = None,
    css_filename: str | None = None,
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
    if not fonts:
        raise ValueError("At least one input font must be provided.")
    if config is None:
        config = SliceConfig()
    if css_filename is not None:
        css_filename = _validate_filename_token(css_filename, "css_filename")

    out_path = Path(out_dir)
    out_resolved = out_path.resolve()
    subdir = config.slices_subdir.strip().rstrip("/")
    slices_dir = out_path if (not subdir or subdir == ".") else (out_path / subdir)
    if not slices_dir.resolve().is_relative_to(out_resolved):
        raise ValueError(f"slices_dir {slices_dir!r} resolves outside out_dir {out_path!r}")

    out_path.mkdir(parents=True, exist_ok=True)

    # Tuple of (probe_tt, source_for_export, is_woff_hint, should_close, sha256_hex)
    loaded_fonts: list[tuple[TTFont, Path | bytes, bool | None, bool, str]] = []
    try:
        for f in fonts:
            if isinstance(f, TTFont):
                # Serialize once without mutating caller's TTFont instance
                raw_bytes = serialize_font_to_bytes(f)
                digest = hashlib.sha256(raw_bytes).hexdigest()
                probe_tt = TTFont(io.BytesIO(raw_bytes), lazy=True, recalcTimestamp=False)
                loaded_fonts.append((probe_tt, raw_bytes, False, True, digest))
            else:
                p = Path(f)
                raw_bytes = p.read_bytes()
                digest = hashlib.sha256(raw_bytes).hexdigest()
                probe_tt = TTFont(io.BytesIO(raw_bytes), lazy=True, recalcTimestamp=False)
                loaded_fonts.append((probe_tt, p, probe_tt.flavor is not None, True, digest))

        # 1. Extract metadata from each font
        raw_metas: list[FontMetadata] = []
        for tt, _, _, _, _ in loaded_fonts:
            m = extract_font_metadata(
                tt,
                family_override=family_name,
                slug_override=slug,
                weight_override=weight,
                stretch_override=stretch,
                style_override=style,
                patch_uvs=config.patch_uvs_only_cmap,
            )
            raw_metas.append(m)

        variant_suffixes = validate_and_compute_variant_suffixes(
            raw_metas, family_override=family_name
        )

        # Unify family_name and slug across all variants (using the first font if not overridden)
        unified_family = family_name or raw_metas[0].family_name
        unified_slug = slug or raw_metas[0].slug

        metas: list[FontMetadata] = [
            FontMetadata(
                family_name=unified_family,
                slug=unified_slug,
                is_variable=m.is_variable,
                weight_css=m.weight_css,
                stretch_css=m.stretch_css,
                style_css=m.style_css,
                variant_suffix=v_suf,
                codepoints=m.codepoints,
                patched_uvs_codepoints=m.patched_uvs_codepoints,
                styles_css=m.styles_css,
            )
            for m, v_suf in zip(raw_metas, variant_suffixes)
        ]

        # 2. Build dynamic 4-tier slice plan (cached by codepoints frozenset so variants with
        # identical cmaps share the plan, while variants with differing cmaps get exact ranges)
        plan_cache: dict[frozenset[int], list[SliceSpec]] = {}
        for m in metas:
            if m.codepoints not in plan_cache:
                plan_cache[m.codepoints] = build_slice_plan(
                    m.codepoints, config=config, family_name=unified_family
                )

        variant_plans = [plan_cache[m.codepoints] for m in metas]
        union_codepoints = frozenset({*m.codepoints for m in metas})
        slice_specs = plan_cache.get(union_codepoints) or build_slice_plan(
            union_codepoints, config=config, family_name=unified_family
        )

        resolved_css_name = _validate_filename_token(
            css_filename or f"{unified_slug}.css", "css_filename"
        )
        css_path = out_path / resolved_css_name
        if not css_path.resolve().is_relative_to(out_resolved):
            raise ValueError(f"css_path {css_path!r} resolves outside out_dir {out_path!r}")

        font_digests = [d for _, _, _, _, d in loaded_fonts]
        cache_key = _compute_cache_key(
            font_digests,
            metas,
            variant_plans,
            config,
            family_name=family_name,
            slug=slug,
            css_filename=resolved_css_name,
            weight=weight,
            stretch=stretch,
            style=style,
        )

        if not force:
            cached_res = _try_load_cached_bundle(
                css_path,
                slices_dir,
                cache_key,
                metas,
                variant_plans,
                slice_specs,
                config,
            )
            if cached_res is not None:
                return cached_res

        # 3. Export WOFF2 slices across all input fonts in a single batch
        batch_variants: list[tuple[TTFont | bytes | Path | str, FontMetadata, list[SliceSpec]]] = [
            (export_src, m, variant_specs)
            for (_, export_src, _, _, _), m, variant_specs in zip(loaded_fonts, metas, variant_plans)
        ]
        woff_hints = [is_woff for _, _, is_woff, _, _ in loaded_fonts]
        variant_built_lists = _export_woff2_slices_batch(
            batch_variants,
            slices_dir=slices_dir,
            config=config,
            workers=workers,
            is_woff_hints=woff_hints,
        )

        fonts_and_files: list[tuple[FontMetadata, list[BuiltSliceFile]]] = []
        all_built_files: list[BuiltSliceFile] = []
        for m, built in zip(metas, variant_built_lists):
            fonts_and_files.append((m, built))
            all_built_files.extend(built)

        # 4. Generate CSS file atomically
        css_text = generate_css(fonts_and_files, config=config, cache_key=cache_key)
        with _atomic_output_file(css_path, mode="w", encoding="utf-8") as tf:
            tf.write(css_text)

        return SliceBundleResult(
            family_name=unified_family,
            slug=unified_slug,
            css_path=css_path,
            css_text=css_text,
            slices_dir=slices_dir,
            slice_specs=slice_specs,
            built_files=all_built_files,
            fonts_metadata=metas,
            variant_slice_specs=variant_plans,
            cached=False,
        )
    finally:
        for tt, _, _, should_close, _ in loaded_fonts:
            if should_close:
                tt.close()


def slice_font(
    font: Path | str | TTFont,
    out_dir: Path | str,
    *,
    family_name: str | None = None,
    slug: str | None = None,
    css_filename: str | None = None,
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
        weight=weight,
        stretch=stretch,
        style=style,
        config=config,
        workers=workers,
        force=force,
    )
