"""
WOFF2 subsetting and parallel export engine for cleome.

Uses `fontTools.subset` while preserving OpenType Layout features (`GSUB`, `GPOS`, `GDEF`),
OFL/license metadata (`nameID` 0, 13, 14), source `head.modified` timestamps, and
Variable Font tables (`fvar`, `gvar`, `CFF2`, `HVAR`, `VVAR`, `MVAR`, `STAT`, `avar`).
"""

from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
import copy
from dataclasses import dataclass
import io
import os
from pathlib import Path
from fontTools.ttLib import TTFont
from fontTools.subset import Subsetter, Options

from cleome.metadata import (
    FontMetadata,
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
    retain_layout_closure: bool = True,
    layout_features: tuple[str, ...] = ("*",),
    drop_tables: tuple[str, ...] = (),
) -> Options:
    options = Options()
    options.flavor = "woff2"
    options.desubroutinize = False
    # Retain specified layout features and all scripts so intra-slice lookups —
    # single-glyph substitutions (`vert`/`vrt2`), proportional metrics (`palt`/`vpal`/`halt`/`vhal`),
    # kerning, ligatures, and base + combining mark composition (`ccmp`/`mark`/`mkmk` when both
    # base and mark reside in the same slice) — are preserved via forward GSUB closure.
    options.layout_features = list(layout_features)
    options.layout_scripts = ["*"]
    options.layout_closure = retain_layout_closure
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


# Per-worker state initialized via ProcessPoolExecutor(initializer=...)
_WORKER_SOURCES: tuple[tuple[bytes | Path, bool], ...] = ()
_WORKER_CACHE: dict[int, _CachedFontState] = {}
_WORKER_OPTIONS: Options | None = None


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


def _load_cached_font_state(font_idx: int) -> _CachedFontState:
    state = _WORKER_CACHE.get(font_idx)
    if state is not None:
        return state

    src, patch_uvs = _WORKER_SOURCES[font_idx]
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
    _WORKER_CACHE[font_idx] = state
    return state


def _init_subset_worker(
    sources: tuple[tuple[bytes | Path, bool], ...],
    retain_closure: bool,
    layout_features: tuple[str, ...],
    drop_tables: tuple[str, ...],
) -> None:
    global _WORKER_SOURCES, _WORKER_CACHE, _WORKER_OPTIONS
    _WORKER_SOURCES = sources
    _WORKER_CACHE = {}
    _WORKER_OPTIONS = _create_subset_options(
        retain_layout_closure=retain_closure,
        layout_features=layout_features,
        drop_tables=drop_tables,
    )


def _clear_subset_worker() -> None:
    global _WORKER_SOURCES, _WORKER_CACHE, _WORKER_OPTIONS
    _WORKER_SOURCES = ()
    _WORKER_CACHE = {}
    _WORKER_OPTIONS = None


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


def _subset_single_slice_worker(
    task: tuple[int, list[int], str],
) -> tuple[str, int, int]:
    """Worker function: subset cached font `font_idx` to `codepoints` and atomically write WOFF2."""
    font_idx, codepoints, out_path_str = task
    assert _WORKER_OPTIONS is not None
    state = _load_cached_font_state(font_idx)
    out_path = Path(out_path_str)
    tmp_path = out_path.with_name(f".{out_path.stem}.{os.getpid()}.tmp.woff2")
    try:
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

            subsetter = Subsetter(options=_clone_subset_options(_WORKER_OPTIONS))
            subsetter.populate(unicodes=populate_unicodes)
            subsetter.subset(font)

            font.flavor = "woff2"
            font.save(tmp_path)
            num_glyphs = len(font.getGlyphOrder())
        os.replace(tmp_path, out_path)
    except BaseException:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise

    size_bytes = out_path.stat().st_size
    return out_path_str, size_bytes, num_glyphs


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
            needs_patch = bool(config.patch_uvs_only_cmap and meta.patched_uvs_codepoints)
            return p, needs_patch

        with TTFont(p, recalcTimestamp=False) as tmp_font:
            if config.patch_uvs_only_cmap:
                patch_uvs_missing_base_cmap(tmp_font)
            return serialize_font_to_bytes(tmp_font), False

    if isinstance(font_or_bytes, TTFont):
        return serialize_font_to_bytes(font_or_bytes), bool(config.patch_uvs_only_cmap)

    return font_or_bytes, bool(config.patch_uvs_only_cmap and meta.patched_uvs_codepoints)


def _export_woff2_slices_batch(
    variants: list[tuple[TTFont | bytes | Path | str, FontMetadata, list[SliceSpec]]],
    slices_dir: Path,
    config: SliceConfig | None = None,
    workers: int | None = None,
    *,
    is_woff_hints: list[bool | None] | None = None,
) -> list[list[BuiltSliceFile]]:
    """
    Generate `.woff2` slice files in `slices_dir` for one or more font variants using a single
    shared worker pool and heavy-first task scheduling.
    """
    if not variants:
        return []
    if config is None:
        config = SliceConfig()

    slices_dir.mkdir(parents=True, exist_ok=True)
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
            fname = spec.filename(meta.slug, meta.variant_suffix)
            out_path = slices_dir / fname
            out_str = str(out_path)
            if out_str in seen_out_paths:
                raise ValueError(f"Duplicate slice output path across variants: {out_str}")
            seen_out_paths.add(out_str)
            sortable_tasks.append((-len(cps), font_idx, spec_idx, cps, out_str))
            font_items.append((spec, fname, out_str))
        per_font_items.append(font_items)

    if not sortable_tasks:
        return [[] for _ in variants]

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
    results_map: dict[str, tuple[int, int]] = {}

    if max_workers <= 1 or len(scheduled_tasks) <= 2:
        _init_subset_worker(
            tuple(sources),
            config.retain_layout_closure,
            config.layout_features,
            config.drop_tables,
        )
        try:
            for t in scheduled_tasks:
                out_str, sz, ng = _subset_single_slice_worker(t)
                results_map[out_str] = (sz, ng)
        finally:
            _clear_subset_worker()
    else:
        try:
            with ProcessPoolExecutor(
                max_workers=max_workers,
                initializer=_init_subset_worker,
                initargs=(
                    tuple(sources),
                    config.retain_layout_closure,
                    config.layout_features,
                    config.drop_tables,
                ),
            ) as pool:
                for out_str, sz, ng in pool.map(
                    _subset_single_slice_worker, scheduled_tasks, chunksize=1
                ):
                    results_map[out_str] = (sz, ng)
        except (BrokenProcessPool, BrokenPipeError) as exc:
            raise RuntimeError(
                "Failed to run parallel WOFF2 worker processes. When calling `slice_font()` "
                "or `slice_fonts()` from a top-level script on macOS/Windows/Linux (spawn/forkserver), "
                "wrap the call inside `if __name__ == '__main__':` or pass `workers=1`."
            ) from exc

    all_variant_files: list[list[BuiltSliceFile]] = []
    for (_, meta, _), font_items in zip(variants, per_font_items):
        built_files: list[BuiltSliceFile] = []
        for spec, fname, out_str in font_items:
            sz, ng = results_map[out_str]
            built_files.append(
                BuiltSliceFile(
                    spec=spec,
                    filename=fname,
                    path=Path(out_str),
                    size_bytes=sz,
                    glyph_count=ng,
                    variant_suffix=meta.variant_suffix,
                )
            )
        all_variant_files.append(built_files)
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

