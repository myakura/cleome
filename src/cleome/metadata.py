"""
OpenType table inspection (`name`, `fvar`, `OS/2`, `post`, `cmap`), UVS Format 14 base
character auto-patching, and CSS `@font-face` metadata extraction for cleome.
"""

from dataclasses import dataclass, field
import re
from fontTools.ttLib import TTFont

# OpenType OS/2.usWidthClass (1..9) to CSS font-stretch percentage (5 = 100% normal is omitted)
OS2_WIDTH_CLASS_TO_CSS: frozendict[int, str] = frozendict(
    {
        1: "50%",
        2: "62.5%",
        3: "75%",
        4: "87.5%",
        6: "112.5%",
        7: "125%",
        8: "150%",
        9: "200%",
    }
)


_CSS_DESCRIPTOR_RE = re.compile(r"^[a-zA-Z0-9 %.+-]+$")


def _validate_filename_token(val: str, field_name: str) -> str:
    cleaned = val.strip()
    if not cleaned:
        raise ValueError(f"{field_name} cannot be empty")
    if (
        "/" in cleaned
        or "\\" in cleaned
        or "\x00" in cleaned
        or cleaned in (".", "..")
        or ".." in cleaned
    ):
        raise ValueError(
            f"{field_name} must not contain path separators or '..', got {val!r}"
        )
    return cleaned


def _validate_css_descriptor(val: str, field_name: str) -> str:
    cleaned = val.strip()
    if not cleaned:
        raise ValueError(f"{field_name} cannot be empty")
    if not _CSS_DESCRIPTOR_RE.fullmatch(cleaned) or ".." in cleaned:
        raise ValueError(f"Invalid CSS {field_name} descriptor value: {val!r}")
    return cleaned


@dataclass(frozen=True, slots=True)
class FontMetadata:
    """Auto-detected or user-overridden CSS @font-face metadata for a single font."""

    family_name: str
    slug: str
    is_variable: bool
    weight_css: str
    stretch_css: str | None
    style_css: str
    variant_suffix: str = ""
    codepoints: frozenset[int] = field(default_factory=frozenset)
    patched_uvs_codepoints: tuple[int, ...] = ()
    styles_css: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "slug", _validate_filename_token(self.slug, "slug"))
        if self.variant_suffix:
            object.__setattr__(
                self,
                "variant_suffix",
                _validate_filename_token(self.variant_suffix, "variant_suffix"),
            )
        object.__setattr__(
            self, "weight_css", _validate_css_descriptor(self.weight_css, "font-weight")
        )
        if self.stretch_css is not None:
            object.__setattr__(
                self, "stretch_css", _validate_css_descriptor(self.stretch_css, "font-stretch")
            )
        object.__setattr__(
            self, "style_css", _validate_css_descriptor(self.style_css, "font-style")
        )
        if not self.styles_css:
            object.__setattr__(self, "styles_css", (self.style_css,))
        else:
            object.__setattr__(
                self,
                "styles_css",
                tuple(_validate_css_descriptor(s, "font-style") for s in self.styles_css),
            )


def slugify_family(name: str) -> str:
    """Convert a font family name (e.g. 'GenSou Sans JP') into a URL/filename slug ('gensou-sans-jp')."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip()).strip("-").lower()
    return s or "font"


def _uvs_selector_sort_key(item: tuple[int, list[tuple[int, str | None]]]) -> tuple[int, int]:
    """Prioritize Ideographic Variation Selectors (U+E0100..U+E01EF) in ascending order before VS1..VS16."""
    sel = item[0]
    return (0 if 0xE0100 <= sel <= 0xE01EF else 1, sel)


def patch_uvs_missing_base_cmap(font: TTFont) -> list[int]:
    """
    Detect characters that exist in cmap Format 14 (UVS) with a concrete variant glyph
    but were accidentally omitted from Format 4/12 (such as U+553E '唾' in Adobe SourceHanSansJP-VF.otf v2.005)
    and patch them into the base cmap tables in-memory.
    Prioritizes IVS selectors (U+E0100..U+E01EF) in ascending order so U+E0100 (standard base form) always wins.
    """
    if "cmap" not in font:
        return []
    best_cmap = font.getBestCmap() or {}
    uvs_additions: dict[int, str] = {}
    for table in font["cmap"].tables:
        if getattr(table, "format", None) == 14 and hasattr(table, "uvsDict"):
            for _uvs_selector, records in sorted(table.uvsDict.items(), key=_uvs_selector_sort_key):
                for cp, gname in records:
                    if cp not in best_cmap and cp not in uvs_additions and gname is not None:
                        uvs_additions[cp] = gname
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


def _fmt_num(val: float) -> str:
    if abs(val - round(val)) < 1e-4:
        return str(int(round(val)))
    return f"{val:.2f}".rstrip("0").rstrip(".")


def extract_font_metadata(
    font: TTFont,
    *,
    family_override: str | None = None,
    slug_override: str | None = None,
    weight_override: str | None = None,
    stretch_override: str | None = None,
    style_override: str | None = None,
    variant_suffix: str = "",
    patch_uvs: bool = True,
) -> FontMetadata:
    """Inspect TTFont tables (`name`, `fvar`, `OS/2`, `post`, `cmap`) to build FontMetadata."""
    patched_uvs = tuple(patch_uvs_missing_base_cmap(font)) if patch_uvs else ()

    # 1. Detect family name (prefer Typographic Family Name ID 16, then Family Name ID 1)
    detected_family = "Custom Font"
    if "name" in font:
        name_table = font["name"]
        for name_id in (16, 1, 4):
            rec = (
                name_table.getName(name_id, 3, 1, 0x0409)
                or name_table.getName(name_id, 1, 0, 0)
                or name_table.getDebugName(name_id)
            )
            if rec:
                val = rec.toUnicode() if hasattr(rec, "toUnicode") else str(rec)
                if val.strip():
                    detected_family = val.strip()
                    break

    family_name = family_override or detected_family
    slug = slug_override or slugify_family(family_name)

    # 2. Detect Variable axes (`fvar`) vs Static (`OS/2`)
    is_variable = "fvar" in font
    axes: dict[str, tuple[float, float, float]] = {}
    if is_variable:
        for ax in font["fvar"].axes:
            axes[ax.axisTag] = (float(ax.minValue), float(ax.defaultValue), float(ax.maxValue))

    # Weight
    if weight_override is not None:
        weight_css = weight_override
    elif "wght" in axes:
        w_min, _, w_max = axes["wght"]
        if abs(w_min - w_max) < 1e-4:
            weight_css = _fmt_num(w_min)
        else:
            weight_css = f"{_fmt_num(w_min)} {_fmt_num(w_max)}"
    elif "OS/2" in font:
        weight_css = str(int(font["OS/2"].usWeightClass))
    else:
        weight_css = "400"

    # Stretch (wdth axis for Variable Fonts, or OS/2.usWidthClass for Static Fonts)
    if stretch_override is not None:
        stretch_css = stretch_override
    elif "wdth" in axes:
        d_min, _, d_max = axes["wdth"]
        if abs(d_min - d_max) < 1e-4:
            stretch_css = f"{_fmt_num(d_min)}%" if abs(d_min - 100.0) > 1e-4 else None
        else:
            stretch_css = f"{_fmt_num(d_min)}% {_fmt_num(d_max)}%"
    elif not is_variable and "OS/2" in font:
        stretch_css = OS2_WIDTH_CLASS_TO_CSS.get(int(font["OS/2"].usWidthClass))
    else:
        stretch_css = None

    # Style (ital / slnt / OS/2 / post)
    static_italic = (
        ("OS/2" in font and bool(font["OS/2"].fsSelection & 0x01))
        or ("post" in font and getattr(font["post"], "italicAngle", 0.0) != 0.0)
    )
    if style_override is not None:
        styles_css: tuple[str, ...] = (style_override,)
    else:
        has_ital = "ital" in axes
        has_slnt = "slnt" in axes
        slnt_oblique: str | None = None
        slnt_has_zero = True
        if has_slnt:
            s_min, _, s_max = axes["slnt"]
            if abs(s_min - s_max) < 1e-4:
                if abs(s_min) >= 1e-4:
                    slnt_oblique = f"oblique {_fmt_num(-s_min)}deg"
                    slnt_has_zero = False
                else:
                    slnt_oblique = None
                    slnt_has_zero = True
            else:
                lo_deg = min(-s_min, -s_max)
                hi_deg = max(-s_min, -s_max)
                slnt_oblique = f"oblique {_fmt_num(lo_deg)}deg {_fmt_num(hi_deg)}deg"
                slnt_has_zero = lo_deg - 1e-4 <= 0.0 <= hi_deg + 1e-4

        if has_ital:
            i_min, _, i_max = axes["ital"]
            ital_has_normal = i_min < 0.5
            ital_has_italic = i_max >= 0.5
            styles_list: list[str] = []
            if slnt_oblique is None:
                if ital_has_normal:
                    styles_list.append("normal")
                if ital_has_italic:
                    styles_list.append("italic")
                if not styles_list:
                    styles_list.append("normal")
            else:
                if ital_has_italic:
                    if ital_has_normal and slnt_has_zero:
                        styles_list.append("normal")
                    styles_list.append("italic")
                    styles_list.append(slnt_oblique)
                else:
                    styles_list.append(slnt_oblique)
            styles_css = tuple(styles_list)
        elif slnt_oblique is not None:
            styles_css = (slnt_oblique,)
        elif static_italic:
            styles_css = ("italic",)
        else:
            styles_css = ("normal",)

    style_css = styles_css[0]

    cmap = font.getBestCmap() or {}
    codepoints = frozenset(cmap.keys())

    return FontMetadata(
        family_name=family_name,
        slug=slug,
        is_variable=is_variable,
        weight_css=weight_css,
        stretch_css=stretch_css,
        style_css=style_css,
        variant_suffix=variant_suffix,
        codepoints=codepoints,
        patched_uvs_codepoints=patched_uvs,
        styles_css=styles_css,
    )
