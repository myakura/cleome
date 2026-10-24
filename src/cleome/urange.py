"""
CSS `unicode-range` formatting, parsing, and cascade-shadowing range compaction utilities.
"""

from bisect import bisect_left, bisect_right

type CodepointSet = set[int] | frozenset[int]

__all__ = [
    "CodepointSet",
    "compact_unicode_range",
    "format_unicode_range",
    "parse_unicode_range",
]


def format_unicode_range(codepoints: CodepointSet | list[int]) -> str:
    """Format a collection of integer codepoints into a minimal CSS `unicode-range` string."""
    sorted_cps = sorted(set(codepoints))
    if not sorted_cps:
        return ""
    ranges: list[tuple[int, int]] = []
    start = prev = sorted_cps[0]
    for cp in sorted_cps[1:]:
        if cp == prev + 1:
            prev = cp
        else:
            ranges.append((start, prev))
            start = prev = cp
    ranges.append((start, prev))
    return ", ".join(
        f"U+{s:X}" if s == e else f"U+{s:X}-{e:X}" for s, e in ranges
    )


def parse_unicode_range(ur_str: str) -> set[int]:
    """Parse a CSS `unicode-range` string back into a set of integer codepoints."""
    cps: set[int] = set()
    for part in ur_str.split(","):
        part = part.strip()
        if not part:
            continue
        if part.upper().startswith("U+"):
            part = part[2:]
        if "?" in part:
            lo = int(part.replace("?", "0"), 16)
            hi = int(part.replace("?", "F"), 16)
            cps.update(range(lo, hi + 1))
        elif "-" in part:
            s_str, e_str = part.split("-", 1)
            cps.update(range(int(s_str, 16), int(e_str, 16) + 1))
        else:
            cps.add(int(part, 16))
    return cps


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
    ranges: list[tuple[int, int]] = []
    start = prev = sorted_target_cps[0]
    for cp in sorted_target_cps[1:]:
        if cp == prev + 1 or (
            (cp - prev) <= max_gap
            and bisect_right(forbidden_sorted, prev) == bisect_left(forbidden_sorted, cp)
        ):
            prev = cp
        else:
            ranges.append((start, prev))
            start = prev = cp
    ranges.append((start, prev))

    return ", ".join(
        f"U+{s:X}" if s == e else f"U+{s:X}-{e:X}" for s, e in ranges
    )
