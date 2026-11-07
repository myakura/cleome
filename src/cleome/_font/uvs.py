"""
Unicodevariation sequence (`cmap` Format 14) base `cmap` repair helpers for Cleome.
"""

from fontTools.ttLib import TTFont


def _uvs_selector_sort_key(
    item: tuple[int, list[tuple[int, str | None]]],
) -> tuple[int, int]:
    """Prioritize Ideographic Variation Selectors (U+E0100..U+E01EF) in ascending order before VS1..VS16."""
    sel = item[0]
    return (0 if 0xE0100 <= sel <= 0xE01EF else 1, sel)


def _collect_uvs_missing_base_additions(font: TTFont) -> dict[int, str]:
    """
    Compute `{cp: glyph_name}` for base characters that exist in `cmap` Format 14 (UVS)
    with a concrete variant glyph (`gname is not None`), are absent from `getBestCmap()`,
    and can be represented in at least one Format 4 or Format 12 subtable of `font`,
    without mutating `font`.
    """
    if "cmap" not in font:
        return {}
    tables = font["cmap"].tables
    if not any(
        getattr(t, "format", None) == 14 and hasattr(t, "uvsDict") for t in tables
    ):
        return {}
    has_format4 = any(
        getattr(t, "format", None) == 4 and hasattr(t, "cmap") for t in tables
    )
    has_format12 = any(
        getattr(t, "format", None) == 12 and hasattr(t, "cmap") for t in tables
    )
    if not has_format4 and not has_format12:
        return {}
    best_cmap = font.getBestCmap() or {}
    uvs_additions: dict[int, str] = {}
    for table in tables:
        if getattr(table, "format", None) == 14 and hasattr(table, "uvsDict"):
            for _uvs_selector, records in sorted(
                table.uvsDict.items(), key=_uvs_selector_sort_key
            ):
                for cp, gname in records:
                    if (
                        cp not in best_cmap
                        and cp not in uvs_additions
                        and gname is not None
                        and (has_format12 or cp <= 0xFFFF)
                    ):
                        uvs_additions[cp] = gname
    return uvs_additions


def patch_uvs_missing_base_cmap(font: TTFont) -> list[int]:
    """
    Detect characters that exist in cmap Format 14 (UVS) with a concrete variant glyph
    but were accidentally omitted from Format 4/12 (such as U+553E '唾' in Adobe SourceHanSansJP-VF.otf v2.005)
    and patch them into the base cmap tables in-memory.
    Prioritizes IVS selectors (U+E0100..U+E01EF) in ascending order so U+E0100 (standard base form) always wins.
    """
    uvs_additions = _collect_uvs_missing_base_additions(font)
    if not uvs_additions:
        return []

    patched: set[int] = set()
    for table in font["cmap"].tables:
        if getattr(table, "format", None) in (4, 12) and hasattr(table, "cmap"):
            for cp, gname in uvs_additions.items():
                if table.format == 4 and cp > 0xFFFF:
                    continue
                table.cmap[cp] = gname
                patched.add(cp)
    return sorted(patched)
