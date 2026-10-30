"""
Cleome: `unicode-range` WOFF2 slicer and `@font-face` CSS generator for
Japanese, Korean, and Chinese variable and static web fonts.

Uses CSS cascade shadowing (1-token hex ranges for rare ideographs and syllables)
and inverted-pyramid frequency granularity.
"""

from importlib.metadata import version as _metadata_version
from typing import TYPE_CHECKING, Any

from cleome.locale import (
    SUPPORTED_LOCALES,
    detect_font_locale,
)
from cleome.metadata import (
    FontFaceDescriptors,
    FontMetadata,
    extract_font_face_descriptors,
    extract_font_metadata,
    patch_uvs_missing_base_cmap,
    slugify_family,
)
from cleome.slicer import (
    SliceConfig,
    SliceSpec,
    build_slice_plan,
)
from cleome.urange import (
    compact_unicode_range,
    format_unicode_range,
    parse_unicode_range,
)

if TYPE_CHECKING:
    from cleome.css import generate_css
    from cleome.pipeline import SliceBundleResult, slice_font, slice_fonts
    from cleome.subsetter import (
        BuiltSliceFile,
        export_woff2_slices,
    )

__version__ = _metadata_version("cleome")

__all__ = [
    "BuiltSliceFile",
    "FontFaceDescriptors",
    "FontMetadata",
    "SUPPORTED_LOCALES",
    "SliceBundleResult",
    "SliceConfig",
    "SliceSpec",
    "build_slice_plan",
    "compact_unicode_range",
    "detect_font_locale",
    "export_woff2_slices",
    "extract_font_face_descriptors",
    "extract_font_metadata",
    "format_unicode_range",
    "generate_css",
    "parse_unicode_range",
    "patch_uvs_missing_base_cmap",
    "slice_font",
    "slice_fonts",
    "slugify_family",
]


def __getattr__(name: str) -> Any:
    if name in ("BuiltSliceFile", "export_woff2_slices"):
        from cleome import subsetter as _subsetter

        return getattr(_subsetter, name)
    if name == "generate_css":
        from cleome import css as _css

        return getattr(_css, name)
    if name in ("SliceBundleResult", "slice_font", "slice_fonts"):
        from cleome import pipeline as _pipeline

        return getattr(_pipeline, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
