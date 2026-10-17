"""
WOFF2 subsetting and parallel export engine for cleome.

Uses `fontTools.subset` while preserving OpenType Layout features (`GSUB`, `GPOS`, `GDEF`),
OFL/license metadata (`nameID` 0, 13, 14), source `head.modified` timestamps, and
Variable Font tables (`fvar`, `gvar`, `CFF2`, `HVAR`, `VVAR`, `MVAR`, `STAT`, `avar`).
"""

from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
import io
import os
from pathlib import Path
from fontTools.ttLib import TTFont
from fontTools.subset import Subsetter, Options

from cleome.slicer import (
    FontMetadata,
    SliceConfig,
    SliceSpec,
    patch_uvs_missing_base_cmap,
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


# Per-worker cached font bytes and options to avoid re-reading disk on every slice
_WORKER_FONT_BYTES: bytes | None = None
_WORKER_PATCH_UVS: bool = False
_WORKER_OPTIONS: Options | None = None


def _init_subset_worker(
    font_bytes: bytes,
    patch_uvs: bool,
    retain_closure: bool,
    layout_features: tuple[str, ...],
    drop_tables: tuple[str, ...],
) -> None:
    global _WORKER_FONT_BYTES, _WORKER_PATCH_UVS, _WORKER_OPTIONS
    _WORKER_FONT_BYTES = font_bytes
    _WORKER_PATCH_UVS = patch_uvs
    _WORKER_OPTIONS = _create_subset_options(
        retain_layout_closure=retain_closure,
        layout_features=layout_features,
        drop_tables=drop_tables,
    )


def _clear_subset_worker() -> None:
    global _WORKER_FONT_BYTES, _WORKER_OPTIONS
    _WORKER_FONT_BYTES = None
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
    task: tuple[list[int], str],
) -> tuple[str, int, int]:
    """Worker function: subset cached font bytes to `codepoints` and write WOFF2 to `out_path_str`."""
    codepoints, out_path_str = task
    assert _WORKER_FONT_BYTES is not None and _WORKER_OPTIONS is not None
    with TTFont(
        io.BytesIO(_WORKER_FONT_BYTES), lazy=True, recalcTimestamp=False
    ) as font:
        if _WORKER_PATCH_UVS:
            patch_uvs_missing_base_cmap(font)

        populate_unicodes = _prepare_uvs_for_slice(font, codepoints)
        subsetter = Subsetter(options=_WORKER_OPTIONS)
        subsetter.populate(unicodes=populate_unicodes)
        subsetter.subset(font)

        font.flavor = "woff2"
        out_path = Path(out_path_str)
        font.save(out_path)
        num_glyphs = len(font.getGlyphOrder())
    size_bytes = out_path.stat().st_size
    return out_path_str, size_bytes, num_glyphs


def serialize_font_to_bytes(font: TTFont) -> bytes:
    """Serialize an in-memory `TTFont` (as uncommitted SFNT) into bytes for parallel workers."""
    buf = io.BytesIO()
    orig_flavor = font.flavor
    orig_recalc = font.recalcTimestamp
    font.flavor = None
    font.recalcTimestamp = False
    try:
        font.save(buf)
    finally:
        font.flavor = orig_flavor
        font.recalcTimestamp = orig_recalc
    return buf.getvalue()


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
    if config is None:
        config = SliceConfig()

    slices_dir.mkdir(parents=True, exist_ok=True)

    worker_needs_uvs_patch = False
    if isinstance(font_or_bytes, (str, Path)):
        p = Path(font_or_bytes)
        with TTFont(p, lazy=True, recalcTimestamp=False) as probe:
            is_woff = probe.flavor is not None
        if not is_woff and not (config.patch_uvs_only_cmap and meta.patched_uvs_codepoints):
            # Fast path: raw SFNT (.ttf/.otf) on disk with no missing UVS cmap entries
            font_bytes = p.read_bytes()
        elif not is_woff and config.patch_uvs_only_cmap and meta.patched_uvs_codepoints:
            # Fast path: read raw SFNT bytes once and let workers apply lightweight cmap dict patch
            font_bytes = p.read_bytes()
            worker_needs_uvs_patch = True
        else:
            with TTFont(p, recalcTimestamp=False) as tmp_font:
                if config.patch_uvs_only_cmap:
                    patch_uvs_missing_base_cmap(tmp_font)
                font_bytes = serialize_font_to_bytes(tmp_font)
    elif isinstance(font_or_bytes, TTFont):
        font_bytes = serialize_font_to_bytes(font_or_bytes)
        if config.patch_uvs_only_cmap:
            worker_needs_uvs_patch = True
    else:
        font_bytes = font_or_bytes
        if config.patch_uvs_only_cmap and meta.patched_uvs_codepoints:
            worker_needs_uvs_patch = True

    tasks: list[tuple[list[int], str]] = []
    spec_by_path: dict[str, tuple[SliceSpec, str]] = {}
    for spec in slice_specs:
        # Filter to codepoints actually present in this font variant
        cps = sorted(spec.binary_codepoints & meta.codepoints)
        if not cps:
            continue
        fname = spec.filename(meta.slug, meta.variant_suffix)
        out_path = slices_dir / fname
        out_str = str(out_path)
        tasks.append((cps, out_str))
        spec_by_path[out_str] = (spec, fname)

    if not tasks:
        return []

    max_workers = (
        workers if workers is not None else min(len(tasks), os.process_cpu_count() or 4)
    )
    results_map: dict[str, tuple[int, int]] = {}

    if max_workers <= 1 or len(tasks) <= 2:
        _init_subset_worker(
            font_bytes,
            worker_needs_uvs_patch,
            config.retain_layout_closure,
            config.layout_features,
            config.drop_tables,
        )
        try:
            for t in tasks:
                out_str, sz, ng = _subset_single_slice_worker(t)
                results_map[out_str] = (sz, ng)
        finally:
            _clear_subset_worker()
    else:
        chunksize = max(1, len(tasks) // (max_workers * 2))
        try:
            with ProcessPoolExecutor(
                max_workers=max_workers,
                initializer=_init_subset_worker,
                initargs=(
                    font_bytes,
                    worker_needs_uvs_patch,
                    config.retain_layout_closure,
                    config.layout_features,
                    config.drop_tables,
                ),
            ) as pool:
                for out_str, sz, ng in pool.map(
                    _subset_single_slice_worker, tasks, chunksize=chunksize
                ):
                    results_map[out_str] = (sz, ng)
        except (BrokenProcessPool, BrokenPipeError) as exc:
            raise RuntimeError(
                "Failed to run parallel WOFF2 worker processes. When calling `slice_font()` "
                "or `slice_fonts()` from a top-level script on macOS/Windows/Linux (spawn/forkserver), "
                "wrap the call inside `if __name__ == '__main__':` or pass `workers=1`."
            ) from exc

    built_files: list[BuiltSliceFile] = []
    for _, out_str in tasks:
        spec, fname = spec_by_path[out_str]
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
    return built_files
