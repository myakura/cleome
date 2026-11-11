"""
CSS `unicode-range` formatting, parsing, and cascade-shadowing range compaction utilities.
"""

from stixis import (
    SerializationOptions,
    compact_urange,
    format_urange,
    parse_urange_codepoints,
)

type CodepointSet = set[int] | frozenset[int]

_CLEOME_CSS_OPTIONS = SerializationOptions.cleome()

__all__ = [
    "CodepointSet",
    "compact_unicode_range",
    "format_unicode_range",
    "parse_unicode_range",
]


def format_unicode_range(
    codepoints: CodepointSet | list[int] | tuple[int, int],
) -> str:
    """Format a collection of integer codepoints (coalescing contiguous runs) or an inclusive `(start, end)` interval into a minimal CSS `unicode-range` string."""
    if isinstance(codepoints, tuple):
        if len(codepoints) != 2:
            raise ValueError(
                f"tuple codepoints must be a 2-element (start, end) interval, got {len(codepoints)} elements"
            )
        return format_urange(codepoints, options=_CLEOME_CSS_OPTIONS)

    if not isinstance(codepoints, (set, frozenset, list)):
        raise TypeError(
            f"codepoints must be a set, frozenset, list, or 2-element tuple of ints, got {type(codepoints).__name__}"
        )

    if not codepoints:
        return ""

    return format_urange(codepoints, options=_CLEOME_CSS_OPTIONS)


def parse_unicode_range(ur_str: str) -> set[int]:
    """Parse a CSS `unicode-range` string back into a set of integer codepoints."""
    if not ur_str or not ur_str.strip():
        return set()
    return parse_urange_codepoints(ur_str)


def compact_unicode_range(
    sorted_target_cps: list[int],
    forbidden_sorted: list[int],
    max_gap: int = 0x80,
) -> str:
    """
    Merge target codepoints into contiguous hex ranges `[start, end]` whenever:
    1. Adjacent codepoints are contiguous (`cp - prev == 1`) or separated by `cp - prev <= max_gap` (`max_gap >= 0`), AND
    2. The open interval `(prev, cp)` contains ZERO codepoints from `forbidden_sorted`
       (verified in O(log F) via binary search).
    """
    if isinstance(max_gap, bool) or not isinstance(max_gap, int):
        raise TypeError(f"max_gap must be an int, got {type(max_gap).__name__}")
    if max_gap < 0:
        raise ValueError(f"max_gap must be >= 0, got {max_gap}")
    if (
        isinstance(sorted_target_cps, (list, tuple, set, frozenset))
        and not sorted_target_cps
    ):
        return ""
    return compact_urange(
        sorted_target_cps,
        forbidden_sorted,
        max_gap=max(0, max_gap - 1),
        options=_CLEOME_CSS_OPTIONS,
    )
