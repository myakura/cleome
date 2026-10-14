"""
WOFF2 subsetting and parallel export engine for cleome.

Uses `fontTools.subset` while preserving all OpenType Layout features (`GSUB`, `GPOS`, `GDEF`)
and Variable Font tables (`fvar`, `gvar`, `CFF2`, `HVAR`, `VVAR`, `MVAR`, `STAT`, `avar`).
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
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
    drop_tables: tuple[str, ...] = (),
) -> Options:
    options = Options()
    options.flavor = "woff2"
    options.desubroutinize = False
    # Retain all layout features and scripts so kerning, vertical forms (`vert`/`vrt2`),
    # proportional metrics (`palt`/`vpal`/`halt`/`vhal`), and ligatures work within each slice.
    options.layout_features = ["*"]
    options.layout_scripts = ["*"]
    options.layout_closure = retain_layout_closure
    options.name_IDs = [0, 1, 2, 3, 4, 5, 6, 16, 17]
    options.name_legacy = True
    options.name_languages = ["*"]
    options.notdef_glyph = True
    options.notdef_outline = True
    options.recommended_glyphs = False
    options.hinting = True
    if drop_tables:
        options.drop_tables.extend(drop_tables)
    return options


# Per-worker cached font bytes to avoid re-reading disk on every slice in ProcessPoolExecutor
_WORKER_FONT_BYTES: bytes | None = None
_WORKER_PATCH_UVS: bool = True
_WORKER_RETAIN_CLOSURE: bool = True
_WORKER_DROP_TABLES: tuple[str, ...] = ()


def _init_subset_worker(
    font_bytes: bytes,
    patch_uvs: bool,
    retain_closure: bool,
    drop_tables: tuple[str, ...],
) -> None:
    global _WORKER_FONT_BYTES, _WORKER_PATCH_UVS, _WORKER_RETAIN_CLOSURE, _WORKER_DROP_TABLES
    _WORKER_FONT_BYTES = font_bytes
    _WORKER_PATCH_UVS = patch_uvs
    _WORKER_RETAIN_CLOSURE = retain_closure
    _WORKER_DROP_TABLES = drop_tables


def _subset_single_slice_worker(
    task: tuple[list[int], str],
) -> tuple[str, int, int]:
    """Worker function: subset cached font bytes to `codepoints` and write WOFF2 to `out_path_str`."""
    codepoints, out_path_str = task
    assert _WORKER_FONT_BYTES is not None
    font = TTFont(io.BytesIO(_WORKER_FONT_BYTES))
    if _WORKER_PATCH_UVS:
        patch_uvs_missing_base_cmap(font)

    options = _create_subset_options(
        retain_layout_closure=_WORKER_RETAIN_CLOSURE,
        drop_tables=_WORKER_DROP_TABLES,
    )
    subsetter = Subsetter(options=options)
    subsetter.populate(unicodes=codepoints)
    subsetter.subset(font)

    font.flavor = "woff2"
    out_path = Path(out_path_str)
    font.save(out_path)
    num_glyphs = len(font.getGlyphOrder())
    size_bytes = out_path.stat().st_size
    font.close()
    return out_path_str, size_bytes, num_glyphs


def serialize_font_to_bytes(font: TTFont) -> bytes:
    """Serialize an in-memory `TTFont` (as uncommitted SFNT) into bytes for parallel workers."""
    buf = io.BytesIO()
    orig_flavor = font.flavor
    font.flavor = None
    font.save(buf)
    font.flavor = orig_flavor
    return buf.getvalue()


def export_woff2_slices(
    font_or_bytes: TTFont | bytes | Path,
    meta: FontMetadata,
    slice_specs: list[SliceSpec],
    slices_dir: Path,
    config: SliceConfig | None = None,
    workers: int | None = None,
) -> list[BuiltSliceFile]:
    """
    Generate `.woff2` slice files in `slices_dir` for all `slice_specs` using parallel workers.
    """
    if config is None:
        config = SliceConfig()

    slices_dir.mkdir(parents=True, exist_ok=True)

    if isinstance(font_or_bytes, (str, Path)):
        p = Path(font_or_bytes)
        # If input is already a WOFF2 file, decompress to SFNT bytes once so workers load fast
        tmp_font = TTFont(p)
        if config.patch_uvs_only_cmap:
            patch_uvs_missing_base_cmap(tmp_font)
        font_bytes = serialize_font_to_bytes(tmp_font)
        tmp_font.close()
    elif isinstance(font_or_bytes, TTFont):
        if config.patch_uvs_only_cmap:
            patch_uvs_missing_base_cmap(font_or_bytes)
        font_bytes = serialize_font_to_bytes(font_or_bytes)
    else:
        font_bytes = font_or_bytes

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

    max_workers = workers if workers is not None else min(len(tasks), os.cpu_count() or 4)
    results_map: dict[str, tuple[int, int]] = {}

    if max_workers <= 1 or len(tasks) <= 2:
        _init_subset_worker(
            font_bytes,
            False,  # already patched before serialization
            config.retain_layout_closure,
            config.drop_tables,
        )
        for t in tasks:
            out_str, sz, ng = _subset_single_slice_worker(t)
            results_map[out_str] = (sz, ng)
    else:
        with ProcessPoolExecutor(
            max_workers=max_workers,
            initializer=_init_subset_worker,
            initargs=(
                font_bytes,
                False,
                config.retain_layout_closure,
                config.drop_tables,
            ),
        ) as pool:
            for out_str, sz, ng in pool.map(_subset_single_slice_worker, tasks):
                results_map[out_str] = (sz, ng)

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
