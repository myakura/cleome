"""
WOFF2 subsetting and parallel export engine for Cleome.

Uses `fontTools.subset` while preserving OpenType Layout features (`GSUB`, `GPOS`, `GDEF`),
OFL/license metadata (`nameID` 0, 13, 14), source `head.modified` timestamps, and
Variable Font tables (`fvar`, `gvar`, `CFF2`, `HVAR`, `VVAR`, `MVAR`, `STAT`, `avar`).
"""

from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
import contextlib
import copy
from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path
import secrets
from typing import Iterator
from fontTools.ttLib import TTFont
from fontTools.subset import Subsetter, Options

from cleome.css import _ensure_directory_creatable
from cleome.metadata import (
    FontMetadata,
    _split_hashed_stem,
    _validate_filename_token,
    patch_uvs_missing_base_cmap,
)
from cleome.slicer import (
    SliceConfig,
    SliceSpec,
)


@dataclass(frozen=True, slots=True)
class BuiltSliceFile:
    """Result metadata for a single generated `.woff2` slice file."""

    spec: SliceSpec
    filename: str
    path: Path
    size_bytes: int
    glyph_count: int
    variant_suffix: str = ""


def _create_subset_options(
    layout_closure: bool = True,
    layout_features: tuple[str, ...] = ("*",),
    drop_tables: tuple[str, ...] = (),
) -> Options:
    options = Options()
    options.flavor = "woff2"
    options.desubroutinize = False
    # Retain specified layout features and all scripts so forward GSUB closure preserves
    # intra-slice lookups (single-glyph substitutions `vert`/`vrt2`, proportional metrics
    # `palt`/`vpal`/`halt`/`vhal`, kerning, ligatures, and `ccmp`/`mark`/`mkmk` composition
    # when both base and mark reside in the same slice).
    options.layout_features = list(layout_features)
    options.layout_scripts = ["*"]
    options.layout_closure = layout_closure
    # Preserve Copyright (0), Family/Subfamily/Version/PS (1..6), License Description (13),
    # License Info URL (14), and Typographic Family/Subfamily (16, 17).
    options.name_IDs = [0, 1, 2, 3, 4, 5, 6, 13, 14, 16, 17]
    options.name_legacy = True
    options.name_languages = ["*"]
    options.notdef_glyph = True
    options.notdef_outline = True
    options.recommended_glyphs = False
    options.hinting = True
    if drop_tables:
        options.drop_tables.extend(drop_tables)
    return options


@dataclass(slots=True)
class _CachedFontState:
    """Per-worker cached font bytes and pre-decompiled table templates for a single input font."""

    font_bytes: bytes
    glyph_order: list[str]
    rev_glyph_order: dict[str, int]
    cmap_table: object | None
    hmtx_table: object | None
    vmtx_table: object | None
    has_format14: bool


@dataclass(slots=True)
class _SubsetWorkerContext:
    """Worker state holding input font sources, cached table templates, and subsetter options."""

    sources: tuple[tuple[bytes | Path, bool], ...]
    cache: dict[int, _CachedFontState]
    options: Options
    filename_hash: bool = True


# Per-worker state initialized inside child processes via ProcessPoolExecutor(initializer=...)
_WORKER_CONTEXT: _SubsetWorkerContext | None = None


def _clone_cmap(src_cmap: object) -> object:
    """Shallow-clone a decompiled `cmap` table so per-slice filtering does not mutate the template."""
    if hasattr(src_cmap, "ensureDecompiled"):
        src_cmap.ensureDecompiled()
    cloned = copy.copy(src_cmap)
    new_tables = []
    for subtable in getattr(src_cmap, "tables", ()):
        if hasattr(subtable, "ensureDecompiled"):
            subtable.ensureDecompiled()
        nt = copy.copy(subtable)
        nt.data = None
        nt.ttFont = None
        if getattr(subtable, "format", None) == 14 and hasattr(subtable, "uvsDict"):
            nt.uvsDict = {sel: list(records) for sel, records in subtable.uvsDict.items()}
        elif hasattr(subtable, "cmap"):
            nt.cmap = subtable.cmap.copy()
        new_tables.append(nt)
    cloned.tables = new_tables
    return cloned


def _clone_metrics(src_mtx: object) -> object:
    """Shallow-clone a decompiled `hmtx` or `vmtx` table with an independent `metrics` dict."""
    cloned = copy.copy(src_mtx)
    cloned.metrics = src_mtx.metrics.copy()
    return cloned


def _clone_subset_options(src_options: Options) -> Options:
    """Return a shallow copy of `Options` with independent list attributes."""
    opts = copy.copy(src_options)
    opts.layout_features = list(src_options.layout_features)
    opts.layout_scripts = list(src_options.layout_scripts)
    opts.name_IDs = list(src_options.name_IDs)
    opts.name_languages = list(src_options.name_languages)
    opts.drop_tables = list(src_options.drop_tables)
    return opts


def _load_cached_font_state(
    ctx: _SubsetWorkerContext, font_idx: int
) -> _CachedFontState:
    state = ctx.cache.get(font_idx)
    if state is not None:
        return state

    src, patch_uvs = ctx.sources[font_idx]
    font_bytes = src.read_bytes() if isinstance(src, Path) else src
    with TTFont(io.BytesIO(font_bytes), lazy=True, recalcTimestamp=False) as tpl:
        glyph_order = list(tpl.getGlyphOrder())
        rev_glyph_order = dict(tpl.getReverseGlyphMap(rebuild=True))
        if patch_uvs:
            patch_uvs_missing_base_cmap(tpl)
        cmap_table = _clone_cmap(tpl["cmap"]) if "cmap" in tpl else None
        hmtx_table = _clone_metrics(tpl["hmtx"]) if "hmtx" in tpl else None
        vmtx_table = _clone_metrics(tpl["vmtx"]) if "vmtx" in tpl else None
        has_format14 = False
        if cmap_table is not None:
            has_format14 = any(
                getattr(t, "format", None) == 14 for t in getattr(cmap_table, "tables", ())
            )

    state = _CachedFontState(
        font_bytes=font_bytes,
        glyph_order=glyph_order,
        rev_glyph_order=rev_glyph_order,
        cmap_table=cmap_table,
        hmtx_table=hmtx_table,
        vmtx_table=vmtx_table,
        has_format14=has_format14,
    )
    ctx.cache[font_idx] = state
    return state


def _create_worker_context(
    sources: tuple[tuple[bytes | Path, bool], ...],
    layout_closure: bool,
    layout_features: tuple[str, ...],
    drop_tables: tuple[str, ...],
    filename_hash: bool = True,
) -> _SubsetWorkerContext:
    return _SubsetWorkerContext(
        sources=sources,
        cache={},
        options=_create_subset_options(
            layout_closure=layout_closure,
            layout_features=layout_features,
            drop_tables=drop_tables,
        ),
        filename_hash=filename_hash,
    )


def _init_subset_worker(
    sources: tuple[tuple[bytes | Path, bool], ...],
    layout_closure: bool,
    layout_features: tuple[str, ...],
    drop_tables: tuple[str, ...],
    filename_hash: bool = True,
) -> None:
    global _WORKER_CONTEXT
    _WORKER_CONTEXT = _create_worker_context(
        sources,
        layout_closure,
        layout_features,
        drop_tables,
        filename_hash,
    )


def _prepare_uvs_for_slice(font: TTFont, codepoints: list[int]) -> list[int]:
    """
    Filter `cmap` Format 14 (`uvsDict`) to base characters present in `codepoints`
    and return `codepoints` extended with the active Variation Selector codepoints so
    `fontTools.subset` retains both default UVS (`glyphName=None`) and non-default UVS
    mappings and their variant glyphs without adding standalone VS codepoints to Format 4/12.
    """
    if "cmap" not in font:
        return codepoints

    cp_set = set(codepoints)
    uvs_selectors: set[int] = set()
    for table in font["cmap"].tables:
        if getattr(table, "format", None) == 14 and hasattr(table, "uvsDict"):
            filtered_uvs: dict[int, list[tuple[int, str | None]]] = {}
            for sel, records in table.uvsDict.items():
                kept = [(u, g) for u, g in records if u in cp_set]
                if kept:
                    filtered_uvs[sel] = kept
                    uvs_selectors.add(sel)
            table.uvsDict = filtered_uvs

    extra_selectors = uvs_selectors - cp_set
    if not extra_selectors:
        return codepoints

    for table in font["cmap"].tables:
        if getattr(table, "format", None) != 14 and hasattr(table, "cmap"):
            for sel in extra_selectors:
                table.cmap.pop(sel, None)

    return [*codepoints, *sorted(extra_selectors)]


@contextlib.contextmanager
def _atomic_output_file(
    target_path: Path, mode: str = "wb", encoding: str | None = None
) -> Iterator[io.IOBase]:
    """
    Create a sibling temporary file next to `target_path` with `O_CREAT | O_EXCL` and
    mode `0o644` (subject to the process `umask`), yield the open file stream, and
    atomically `os.replace` `target_path` on clean exit.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    token = secrets.token_hex(16)
    tmp_path = target_path.with_name(
        f".{target_path.stem}.{token}.tmp{target_path.suffix}"
    )
    fd = os.open(tmp_path, flags, 0o644)
    try:
        with os.fdopen(fd, mode, encoding=encoding) as f:
            yield f
        os.replace(tmp_path, target_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _subset_single_slice_worker(
    task: tuple[int, list[int], str],
    ctx: _SubsetWorkerContext | None = None,
) -> tuple[str, str, int, int]:
    """Worker function: subset cached font `font_idx` to `codepoints` and atomically write WOFF2."""
    font_idx, codepoints, base_out_path_str = task
    active_ctx = ctx if ctx is not None else _WORKER_CONTEXT
    assert active_ctx is not None
    state = _load_cached_font_state(active_ctx, font_idx)
    base_out_path = Path(base_out_path_str)
    buf = io.BytesIO()
    with TTFont(
        io.BytesIO(state.font_bytes), lazy=True, recalcTimestamp=False
    ) as font:
        font.setGlyphOrder(list(state.glyph_order))
        font._reverseGlyphOrderDict = dict(state.rev_glyph_order)
        if state.cmap_table is not None:
            font.tables["cmap"] = _clone_cmap(state.cmap_table)
        if state.hmtx_table is not None:
            font.tables["hmtx"] = _clone_metrics(state.hmtx_table)
        if state.vmtx_table is not None:
            font.tables["vmtx"] = _clone_metrics(state.vmtx_table)

        if state.has_format14:
            populate_unicodes = _prepare_uvs_for_slice(font, codepoints)
        else:
            populate_unicodes = codepoints

        subsetter = Subsetter(options=_clone_subset_options(active_ctx.options))
        subsetter.populate(unicodes=populate_unicodes)
        subsetter.subset(font)

        font.flavor = "woff2"
        font.save(buf)
        num_glyphs = len(font.getGlyphOrder())

    woff2_bytes = buf.getvalue()
    if active_ctx.filename_hash:
        digest8 = hashlib.sha256(woff2_bytes).hexdigest()[:8]
        final_out_path = base_out_path.with_name(
            f"{base_out_path.stem}-{digest8}{base_out_path.suffix}"
        )
    else:
        final_out_path = base_out_path

    if final_out_path.exists() and final_out_path.is_dir():
        raise ValueError(
            f"Slice output path must be a file, not an existing directory: {final_out_path!r}"
        )

    with _atomic_output_file(final_out_path, mode="wb") as tf:
        tf.write(woff2_bytes)

    return base_out_path_str, final_out_path.name, len(woff2_bytes), num_glyphs


def serialize_font_to_bytes(font: TTFont) -> bytes:
    """Serialize an in-memory `TTFont` (as uncommitted SFNT) into bytes without mutating `font`."""
    buf = io.BytesIO()
    clone = copy.deepcopy(font)
    try:
        clone.flavor = None
        clone.recalcTimestamp = False
        clone.save(buf)
    finally:
        clone.close()
    return buf.getvalue()


def _prepare_worker_source(
    font_or_bytes: TTFont | bytes | Path | str,
    meta: FontMetadata,
    config: SliceConfig,
    *,
    is_woff_hint: bool | None = None,
) -> tuple[bytes | Path, bool]:
    """
    Prepare a worker input source `(bytes_or_path, needs_uvs_patch)`.
    Uncompressed `.ttf`/`.otf` files on disk are passed by `Path` so workers read them directly
    from the OS page cache without pickling multi-MB buffers over IPC pipes.
    """
    if isinstance(font_or_bytes, (str, Path)):
        p = Path(font_or_bytes)
        if is_woff_hint is None:
            with TTFont(p, lazy=True, recalcTimestamp=False) as probe:
                is_woff = probe.flavor is not None
        else:
            is_woff = is_woff_hint

        if not is_woff:
            needs_patch = bool(config.uvs_base_repair and meta.patched_uvs_codepoints)
            return p, needs_patch

        with TTFont(p, recalcTimestamp=False) as tmp_font:
            if config.uvs_base_repair:
                patch_uvs_missing_base_cmap(tmp_font)
            return serialize_font_to_bytes(tmp_font), False

    if isinstance(font_or_bytes, TTFont):
        return serialize_font_to_bytes(font_or_bytes), bool(config.uvs_base_repair)

    return font_or_bytes, bool(config.uvs_base_repair and meta.patched_uvs_codepoints)


def _prune_superseded_hashed_slices(
    slices_dir: Path,
    built_files: list[BuiltSliceFile],
    metas: list[FontMetadata],
) -> None:
    """Delete superseded `<stem>-<hash8>.woff2` files in `slices_dir` for the stems in `built_files`."""
    if not built_files:
        return
    active_filenames = {bf.filename for bf in built_files}
    meta_by_suffix = {m.variant_suffix: m for m in metas}
    target_base_stems = {
        Path(
            bf.spec.filename(
                meta_by_suffix[bf.variant_suffix].slug,
                bf.variant_suffix,
            )
        ).stem
        for bf in built_files
        if bf.variant_suffix in meta_by_suffix
    }
    if not target_base_stems:
        return
    for existing in slices_dir.iterdir():
        if (
            not existing.is_file()
            or existing.suffix != ".woff2"
            or existing.name in active_filenames
        ):
            continue
        split_res = _split_hashed_stem(existing.stem)
        if split_res is not None and split_res[0] in target_base_stems:
            existing.unlink(missing_ok=True)


def _export_woff2_slices_batch(
    variants: list[tuple[TTFont | bytes | Path | str, FontMetadata, list[SliceSpec]]],
    slices_dir: Path,
    config: SliceConfig | None = None,
    workers: int | None = None,
    *,
    is_woff_hints: list[bool | None] | None = None,
    prune_superseded: bool = True,
) -> list[list[BuiltSliceFile]]:
    """
    Generate `.woff2` slice files in `slices_dir` for one or more font variants using a single
    shared worker pool and heavy-first task scheduling.
    """
    if workers is not None and workers <= 0:
        raise ValueError(f"workers must be >= 1, got {workers}")
    if is_woff_hints is not None and len(is_woff_hints) != len(variants):
        raise ValueError(
            f"is_woff_hints length ({len(is_woff_hints)}) must match variants length ({len(variants)})"
        )
    if not variants:
        return []
    if config is None:
        config = SliceConfig()

    _ensure_directory_creatable(slices_dir, "slices_dir")
    slices_dir_resolved = slices_dir.resolve()
    hints = is_woff_hints if is_woff_hints is not None else [None] * len(variants)

    sources: list[tuple[bytes | Path, bool]] = []
    # (sort_weight, font_idx, spec_idx, cps, out_str)
    sortable_tasks: list[tuple[int, int, int, list[int], str]] = []
    per_font_items: list[list[tuple[SliceSpec, str, str]]] = []
    seen_out_paths: set[str] = set()

    for font_idx, ((font_or_bytes, meta, slice_specs), woff_hint) in enumerate(
        zip(variants, hints)
    ):
        sources.append(
            _prepare_worker_source(
                font_or_bytes, meta, config, is_woff_hint=woff_hint
            )
        )
        font_items: list[tuple[SliceSpec, str, str]] = []
        for spec_idx, spec in enumerate(slice_specs):
            cps = sorted(spec.binary_codepoints & meta.codepoints)
            if not cps:
                continue
            fname = _validate_filename_token(
                spec.filename(meta.slug, meta.variant_suffix), "slice filename"
            )
            out_path = slices_dir / fname
            if not out_path.resolve().is_relative_to(slices_dir_resolved):
                raise ValueError(
                    f"Slice output path {out_path!r} resolves outside slices_dir {slices_dir!r}"
                )
            if not config.filename_hash and out_path.exists() and out_path.is_dir():
                raise ValueError(
                    f"Slice output path must be a file, not an existing directory: {out_path!r}"
                )
            out_str = str(out_path)
            if out_str in seen_out_paths:
                raise ValueError(f"Duplicate slice output path across variants: {out_str}")
            seen_out_paths.add(out_str)
            sortable_tasks.append((-len(cps), font_idx, spec_idx, cps, out_str))
            font_items.append((spec, fname, out_str))
        per_font_items.append(font_items)

    if not sortable_tasks:
        return [[] for _ in variants]

    slices_dir.mkdir(parents=True, exist_ok=True)

    # Schedule largest slices first with deterministic (font_idx, spec_idx) tie-breaking
    sortable_tasks.sort(key=lambda item: (item[0], item[1], item[2]))
    scheduled_tasks: list[tuple[int, list[int], str]] = [
        (font_idx, cps, out_str) for _, font_idx, _, cps, out_str in sortable_tasks
    ]

    max_workers = (
        workers
        if workers is not None
        else min(len(scheduled_tasks), os.process_cpu_count() or 4)
    )
    results_map: dict[str, tuple[str, int, int]] = {}

    if max_workers <= 1 or len(scheduled_tasks) <= 2:
        serial_ctx = _create_worker_context(
            tuple(sources),
            config.layout_closure,
            config.layout_features,
            config.drop_tables,
            config.filename_hash,
        )
        for t in scheduled_tasks:
            base_out_str, final_fname, sz, ng = _subset_single_slice_worker(
                t, serial_ctx
            )
            results_map[base_out_str] = (final_fname, sz, ng)
    else:
        try:
            with ProcessPoolExecutor(
                max_workers=max_workers,
                initializer=_init_subset_worker,
                initargs=(
                    tuple(sources),
                    config.layout_closure,
                    config.layout_features,
                    config.drop_tables,
                    config.filename_hash,
                ),
            ) as pool:
                for base_out_str, final_fname, sz, ng in pool.map(
                    _subset_single_slice_worker, scheduled_tasks, chunksize=1
                ):
                    results_map[base_out_str] = (final_fname, sz, ng)
        except (BrokenProcessPool, BrokenPipeError) as exc:
            raise RuntimeError(
                "Failed to run parallel WOFF2 worker processes. When calling `slice_font()` "
                "or `slice_fonts()` from a top-level script on macOS/Windows/Linux (spawn/forkserver), "
                "wrap the call inside `if __name__ == '__main__':` or pass `workers=1`."
            ) from exc

    all_variant_files: list[list[BuiltSliceFile]] = []
    all_built: list[BuiltSliceFile] = []
    for (_, meta, _), font_items in zip(variants, per_font_items):
        built_files: list[BuiltSliceFile] = []
        for spec, _base_fname, base_out_str in font_items:
            final_fname, sz, ng = results_map[base_out_str]
            final_path = slices_dir / final_fname
            bf = BuiltSliceFile(
                spec=spec,
                filename=final_fname,
                path=final_path,
                size_bytes=sz,
                glyph_count=ng,
                variant_suffix=meta.variant_suffix,
            )
            built_files.append(bf)
            all_built.append(bf)
        all_variant_files.append(built_files)

    if prune_superseded and config.filename_hash and all_built:
        _prune_superseded_hashed_slices(
            slices_dir,
            all_built,
            [meta for _, meta, _ in variants],
        )

    return all_variant_files


def export_woff2_slices(
    font_or_bytes: TTFont | bytes | Path | str,
    meta: FontMetadata,
    slice_specs: list[SliceSpec],
    slices_dir: Path,
    config: SliceConfig | None = None,
    workers: int | None = None,
) -> list[BuiltSliceFile]:
    """
    Generate `.woff2` slice files in `slices_dir` for all `slice_specs` using parallel workers.

    Note: When `workers` > 1 (or `None` on multi-core systems), `ProcessPoolExecutor` is used.
    On platforms that use `spawn` or `forkserver` (macOS, Windows, Python 3.14+ Linux),
    top-level scripts calling this function must guard their entry point with
    `if __name__ == "__main__":` (or pass `workers=1`).
    """
    batch_res = _export_woff2_slices_batch(
        [(font_or_bytes, meta, slice_specs)],
        slices_dir=slices_dir,
        config=config,
        workers=workers,
    )
    return batch_res[0] if batch_res else []

