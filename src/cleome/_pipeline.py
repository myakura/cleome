"""
High-level font slicing pipeline (`slice_font` / `slice_fonts`) for Cleome.
"""

import io
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

from fontTools.ttLib import TTFont

from cleome._font.metadata import (
    FontMetadata,
    _reject_multi_font_axis_overrides,
    extract_font_metadata,
)
from cleome._font.subset import (
    _atomic_output_file,
    _export_woff2_slices_batch,
    _prune_superseded_hashed_slices,
    serialize_font_to_bytes,
)
from cleome._output.css import generate_css
from cleome._output.paths import _resolve_and_validate_outputs
from cleome._plan.bundle import _prepare_font_bundle
from cleome._plan.config import (
    BuiltSliceFile,
    SliceConfig,
    SliceSpec,
    _validate_workers,
)


@dataclass(frozen=True, slots=True)
class _LoadedFont:
    """Opened probe `TTFont`, worker export source, and flavor hint."""

    probe_tt: TTFont
    export_source: Path | bytes
    is_woff_hint: bool | None


@dataclass(frozen=True, slots=True)
class SliceBundleResult:
    """
    Complete result returned by `slice_font` / `slice_fonts`.

    - `slice_specs`: Slice plan for a single font, or the union plan across all input
      codepoints when bundling multiple fonts.
    - `variant_slice_specs`: Per-font slice plans aligned 1:1 with `fonts_metadata`
      (matching the `BuiltSliceFile.spec` items in `built_files` for each variant).
    - `locale`: Resolved CJK slicing locale (`"jp"`, `"kr"`, `"sc"`, `"tc"`, or `"hk"`).
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
    locale: str = "jp"

    @property
    def total_woff2_bytes(self) -> int:
        return sum(f.size_bytes for f in self.built_files)

    @property
    def css_bytes(self) -> int:
        return len(self.css_text.encode("utf-8"))


def slice_fonts(
    fonts: Sequence[Path | str | TTFont],
    out_dir: Path | str,
    *,
    family_name: str | None = None,
    slug: str | None = None,
    css_path: Path | str | None = None,
    weight: str | None = None,
    stretch: str | None = None,
    style: str | None = None,
    config: SliceConfig | None = None,
    workers: int | None = None,
) -> SliceBundleResult:
    """
    Slice one or more input fonts (Variable or Static `.ttf`/`.otf` paths or in-memory `TTFont`s)
    into optimized `unicode-range` `.woff2` slices and generate a unified `@font-face` CSS file.

    Important: When `workers` > 1 (or `None` on multi-core machines), `ProcessPoolExecutor` is used.
    When calling this function from a script on macOS/Windows/Linux (`spawn`/`forkserver`), guard
    the script's entry point with `if __name__ == "__main__":` (or pass `workers=1`).
    """
    if isinstance(fonts, (str, bytes, Path, TTFont)) or not fonts:
        raise ValueError("At least one input font must be provided in a sequence.")
    _validate_workers(workers)
    if config is None:
        config = SliceConfig()
    _reject_multi_font_axis_overrides(
        len(fonts), weight=weight, stretch=stretch, style=style
    )

    loaded_fonts: list[_LoadedFont] = []
    with ExitStack() as stack:
        for f in fonts:
            if isinstance(f, TTFont):
                raw_bytes = serialize_font_to_bytes(f)
                probe_tt = stack.enter_context(
                    TTFont(io.BytesIO(raw_bytes), lazy=True, recalcTimestamp=False)
                )
                loaded_fonts.append(_LoadedFont(probe_tt, raw_bytes, False))
            else:
                p = Path(f)
                probe_tt = stack.enter_context(
                    TTFont(p, lazy=True, recalcTimestamp=False)
                )
                loaded_fonts.append(
                    _LoadedFont(probe_tt, p, probe_tt.flavor is not None)
                )

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
            prepared.active_variant_plans,
            css_path=css_path,
        )

        outputs.out_path.mkdir(parents=True, exist_ok=True)
        outputs.css_path.parent.mkdir(parents=True, exist_ok=True)

        batch_variants: list[
            tuple[TTFont | bytes | Path | str, FontMetadata, list[SliceSpec]]
        ] = [
            (lf.export_source, m, variant_specs)
            for lf, m, variant_specs in zip(
                loaded_fonts, prepared.metas, prepared.active_variant_plans
            )
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

        css_text = generate_css(
            fonts_and_files,
            config=config,
            css_path=outputs.css_path,
            slices_dir=outputs.slices_dir,
        )
        with _atomic_output_file(outputs.css_path, mode="w", encoding="utf-8") as tf:
            tf.write(css_text)

        if config.filename_hash:
            _prune_superseded_hashed_slices(
                outputs.slices_dir,
                all_built_files,
                prepared.slug,
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
            locale=prepared.resolved_locale,
        )


def slice_font(
    font: Path | str | TTFont,
    out_dir: Path | str,
    *,
    family_name: str | None = None,
    slug: str | None = None,
    css_path: Path | str | None = None,
    weight: str | None = None,
    stretch: str | None = None,
    style: str | None = None,
    config: SliceConfig | None = None,
    workers: int | None = None,
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
        css_path=css_path,
        weight=weight,
        stretch=stretch,
        style=style,
        config=config,
        workers=workers,
    )
