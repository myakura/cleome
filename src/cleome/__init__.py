"""
Cleome: `unicode-range` WOFF2 slicer and `@font-face` CSS generator for
Japanese, Korean, and Chinese variable and static web fonts.

Uses CSS cascade shadowing (1-token hex ranges for rare ideographs and syllables)
and inverted-pyramid frequency granularity.
"""

from importlib.metadata import version as _metadata_version
from typing import TYPE_CHECKING, Any

from cleome._font.metadata import (
    FontMetadata,
    extract_font_metadata,
)
from cleome._plan.builder import build_slice_plan
from cleome._plan.config import BuiltSliceFile, SliceConfig, SliceSpec
from cleome._plan.locale import (
    SUPPORTED_LOCALES,
    detect_font_locale,
)

if TYPE_CHECKING:
    from cleome._font.subset import export_woff2_slices
    from cleome._output.css import generate_css
    from cleome._pipeline import SliceBundleResult, slice_font, slice_fonts

__version__ = _metadata_version("cleome")

__all__ = [
    "SUPPORTED_LOCALES",
    "BuiltSliceFile",
    "FontMetadata",
    "SliceBundleResult",
    "SliceConfig",
    "SliceSpec",
    "build_slice_plan",
    "detect_font_locale",
    "export_woff2_slices",
    "extract_font_metadata",
    "generate_css",
    "slice_font",
    "slice_fonts",
]


def __getattr__(name: str) -> Any:
    if name == "export_woff2_slices":
        from cleome._font import subset as _subset

        return getattr(_subset, name)
    if name == "generate_css":
        from cleome._output import css as _css

        return getattr(_css, name)
    if name in ("SliceBundleResult", "slice_font", "slice_fonts"):
        from cleome import _pipeline

        return getattr(_pipeline, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
