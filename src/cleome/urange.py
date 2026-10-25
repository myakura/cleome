"""
CSS `unicode-range` formatting, parsing, and cascade-shadowing range compaction utilities.
"""

from stixis import compact_urange, format_urange, parse_urange_codepoints

type CodepointSet = set[int] | frozenset[int]

__all__ = [
    "CodepointSet",
    "compact_unicode_range",
    "format_unicode_range",
    "parse_unicode_range",
]


def format_unicode_range(codepoints: CodepointSet | list[int]) -> str:
    """Format a collection of integer codepoints into a minimal CSS `unicode-range` string."""
    cp_set = codepoints if isinstance(codepoints, (set, frozenset)) else set(codepoints)
    if not cp_set:
        return ""
    return format_urange(cp_set)


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
    Merge adjacent target codepoints into contiguous hex ranges `[start, end]` whenever:
    1. The gap between consecutive target codepoints is `<= max_gap`, AND
    2. The open interval `(prev, cp)` contains ZERO codepoints from `forbidden_sorted`
       (verified in O(log F) via binary search).
    """
    if not sorted_target_cps:
        return ""
    return compact_urange(
        sorted_target_cps,
        forbidden_sorted,
        max_gap=max(0, max_gap - 1),
    )
