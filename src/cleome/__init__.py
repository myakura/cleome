"""
cleome — Smart `unicode-range` WOFF2 slicer and `@font-face` CSS generator
for Japanese Variable and Static web fonts.

Uses CSS Cascade Shadowing (1-token hex ranges for rare Kanji) and
Progressive (Inverted-Pyramid) Kanji frequency granularity.
"""

from cleome.slicer import (
    FontMetadata,
    SliceConfig,
    SliceSpec,
    build_slice_plan,
    extract_font_metadata,
    format_unicode_range,
    parse_unicode_range,
    patch_uvs_missing_base_cmap,
    slugify_family,
)
from cleome.subsetter import (
    BuiltSliceFile,
    export_woff2_slices,
)
from cleome.css import (
    SliceBundleResult,
    generate_css,
    slice_font,
    slice_fonts,
)

__version__ = "0.1.0"

__all__ = [
    "BuiltSliceFile",
    "FontMetadata",
    "SliceBundleResult",
    "SliceConfig",
    "SliceSpec",
    "build_slice_plan",
    "export_woff2_slices",
    "extract_font_metadata",
    "format_unicode_range",
    "generate_css",
    "parse_unicode_range",
    "patch_uvs_missing_base_cmap",
    "slice_font",
    "slice_fonts",
    "slugify_family",
]
