"""
OpenType table inspection (`name`, `fvar`, `OS/2`, `post`, `cmap`) and CSS `@font-face`
descriptor extraction for Cleome.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from fontTools.ttLib import TTFont
from stixis import SerializationOptions, canonicalize

from cleome._font.uvs import _collect_uvs_missing_base_additions
from cleome._names import _validate_filename_token

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

_CLEOME_CSS_OPTIONS = SerializationOptions.cleome()

_CSS_WEIGHT_KEYWORDS: frozendict[str, str] = frozendict(
    {
        "normal": "400",
        "bold": "700",
    }
)

_CSS_WIDTH_KEYWORDS: frozendict[str, str] = frozendict(
    {
        "ultra-condensed": "50%",
        "extra-condensed": "62.5%",
        "condensed": "75%",
        "semi-condensed": "87.5%",
        "normal": "100%",
        "semi-expanded": "112.5%",
        "expanded": "125%",
        "extra-expanded": "150%",
        "ultra-expanded": "200%",
    }
)


def _reject_multi_font_axis_overrides(
    font_count: int,
    *,
    weight: str | None = None,
    stretch: str | None = None,
    style: str | None = None,
) -> None:
    """Reject scalar single-font axis overrides when bundling multiple input fonts."""
    if font_count <= 1:
        return
    overridden = [
        name
        for name, val in (("weight", weight), ("stretch", stretch), ("style", style))
        if val is not None
    ]
    if overridden:
        names = ", ".join(overridden)
        raise ValueError(
            f"Cannot apply single-font descriptor override ({names}) across {font_count} input fonts; "
            "omit weight/stretch/style overrides to use per-font OpenType detection, or slice each font separately."
        )


def _validate_css_descriptor(val: str, field_name: str) -> str:
    cleaned = val.strip()
    if not cleaned:
        raise ValueError(f"{field_name} cannot be empty")
    try:
        return canonicalize(
            field_name,
            cleaned,
            at_rule="@font-face",
            options=_CLEOME_CSS_OPTIONS,
        )
    except ValueError as exc:
        raise ValueError(f"Invalid CSS {field_name} descriptor value: {val!r}") from exc


def _normalize_weight_css(weight_css: str) -> str:
    cleaned = _validate_css_descriptor(weight_css, "font-weight").strip().lower()
    if "(" in cleaned:
        return cleaned
    tokens = [_CSS_WEIGHT_KEYWORDS.get(t, t) for t in cleaned.split()]
    if len(tokens) == 2 and tokens[0] == tokens[1]:
        return tokens[0]
    return " ".join(tokens)


def _normalize_stretch_css(stretch_css: str | None) -> str:
    if stretch_css is None:
        return "100%"
    cleaned = _validate_css_descriptor(stretch_css, "font-stretch").strip().lower()
    if "(" in cleaned:
        return cleaned
    tokens = [_CSS_WIDTH_KEYWORDS.get(t, t) for t in cleaned.split()]
    if len(tokens) == 2 and tokens[0] == tokens[1]:
        return tokens[0]
    return " ".join(tokens)


def _normalize_style_css(style_css: str) -> str:
    cleaned = _validate_css_descriptor(style_css, "font-style").strip().lower()
    if "(" not in cleaned and cleaned.startswith("oblique "):
        angles = cleaned[8:].split()
        if len(angles) == 2 and angles[0] == angles[1]:
            cleaned = f"oblique {angles[0]}"
    if cleaned == "oblique 0deg":
        return "normal"
    return cleaned


def _slug_axis_token(text: str, *, prefix: str = "") -> str:
    s = text.lower().replace("%", "").replace(".", "p")
    s = re.sub(r"(^|[\s(,])-", r"\1neg", s)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return f"{prefix}{s or 'custom'}"


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
                self,
                "stretch_css",
                _validate_css_descriptor(self.stretch_css, "font-stretch"),
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
                tuple(
                    _validate_css_descriptor(s, "font-style") for s in self.styles_css
                ),
            )


def slugify_family(name: str) -> str:
    """Convert a font family name (e.g. 'GenSou Sans JP') into a URL/filename slug ('gensou-sans-jp')."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip()).strip("-").lower()
    return s or "font"


def _fmt_num(val: float) -> str:
    if abs(val - round(val)) < 1e-4:
        return str(round(val))
    return f"{val:.2f}".rstrip("0").rstrip(".")


@dataclass(frozen=True, slots=True)
class FontFaceDescriptors:
    """Pure OpenType-to-CSS `@font-face` descriptors extracted from `name`, `fvar`, `OS/2`, and `post`."""

    family_name: str
    is_variable: bool
    weight_css: str
    stretch_css: str | None
    style_css: str
    styles_css: tuple[str, ...]


def extract_font_face_descriptors(
    font: TTFont,
    *,
    family_override: str | None = None,
    weight_override: str | None = None,
    stretch_override: str | None = None,
    style_override: str | None = None,
) -> FontFaceDescriptors:
    """Inspect TTFont tables (`name`, `fvar`, `OS/2`, `post`) to extract CSS `@font-face` descriptors."""
    # 1. Detect family name (prefer Typographic Family Name ID 16, then Family Name ID 1)
    detected_family = "Custom Font"
    if "name" in font:
        name_table = font["name"]
        for name_id in (16, 1, 4):
            rec = (
                name_table.getName(name_id, 3, 1, 0x0409)
                or name_table.getName(name_id, 3, 10, 0x0409)
                or name_table.getName(name_id, 1, 0, 0)
                or name_table.getDebugName(name_id)
            )
            if rec:
                val = rec.toUnicode() if hasattr(rec, "toUnicode") else str(rec)
                if val.strip():
                    detected_family = val.strip()
                    break

    family_name = family_override or detected_family

    # 2. Detect Variable axes (`fvar`) vs Static (`OS/2`)
    is_variable = "fvar" in font
    axes: dict[str, tuple[float, float, float]] = {}
    if is_variable:
        for ax in font["fvar"].axes:
            axes[ax.axisTag] = (
                float(ax.minValue),
                float(ax.defaultValue),
                float(ax.maxValue),
            )

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
    static_oblique = "OS/2" in font and bool(font["OS/2"].fsSelection & 0x0200)
    static_italic = ("OS/2" in font and bool(font["OS/2"].fsSelection & 0x01)) or (
        "post" in font and getattr(font["post"], "italicAngle", 0.0) != 0.0
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

        if has_ital or has_slnt:
            ital_has_normal = (axes["ital"][0] < 0.5) if has_ital else True
            ital_has_italic = (axes["ital"][2] >= 0.5) if has_ital else False
            styles_list: list[str] = []
            if ital_has_normal and slnt_has_zero:
                styles_list.append("normal")
            if ital_has_italic:
                styles_list.append("italic")
            if slnt_oblique is not None:
                styles_list.append(slnt_oblique)
            if not styles_list:
                styles_list.append("normal")
            styles_css = tuple(styles_list)
        elif static_oblique:
            styles_css = ("oblique",)
        elif static_italic:
            styles_css = ("italic",)
        else:
            styles_css = ("normal",)

    style_css = styles_css[0]
    return FontFaceDescriptors(
        family_name=family_name,
        is_variable=is_variable,
        weight_css=weight_css,
        stretch_css=stretch_css,
        style_css=style_css,
        styles_css=styles_css,
    )


def extract_font_metadata(
    font: TTFont,
    *,
    family_override: str | None = None,
    slug_override: str | None = None,
    weight_override: str | None = None,
    stretch_override: str | None = None,
    style_override: str | None = None,
    variant_suffix: str = "",
    uvs_base_repair: bool = True,
) -> FontMetadata:
    """Inspect TTFont tables (`name`, `fvar`, `OS/2`, `post`, `cmap`) to build FontMetadata without mutating `font`."""
    patched_uvs = (
        tuple(sorted(_collect_uvs_missing_base_additions(font)))
        if uvs_base_repair
        else ()
    )

    desc = extract_font_face_descriptors(
        font,
        family_override=family_override,
        weight_override=weight_override,
        stretch_override=stretch_override,
        style_override=style_override,
    )
    slug = slug_override or slugify_family(desc.family_name)

    cmap = font.getBestCmap() or {}
    codepoints = frozenset(cmap.keys()) | frozenset(patched_uvs)

    return FontMetadata(
        family_name=desc.family_name,
        slug=slug,
        is_variable=desc.is_variable,
        weight_css=desc.weight_css,
        stretch_css=desc.stretch_css,
        style_css=desc.style_css,
        variant_suffix=variant_suffix,
        codepoints=codepoints,
        patched_uvs_codepoints=patched_uvs,
        styles_css=desc.styles_css,
    )


def validate_and_compute_variant_suffixes(
    raw_metas: Sequence[FontMetadata],
    *,
    family_override: str | None = None,
) -> list[str]:
    """
    Validate that multiple input fonts can be safely bundled into a single `@font-face`
    stylesheet without silent family mismatches or duplicate `(weight, stretch, style)` descriptors,
    and return collision-free filename suffixes (`400`, `700`, `w75-400`, `w75_100-400`, `400-italic`, etc.).
    """
    if len(raw_metas) <= 1:
        return [m.variant_suffix for m in raw_metas]

    distinct_families = sorted({m.family_name for m in raw_metas})
    if family_override is None and len(distinct_families) > 1:
        raise ValueError(
            f"Input fonts have different detected font-family names ({', '.join(repr(f) for f in distinct_families)}). "
            "Slice each family separately, or pass `--family` / `family_name=...` explicitly if unifying them."
        )

    normalized_axes: list[tuple[str, str, tuple[str, ...]]] = []
    seen_descriptors: set[tuple[str, str, str]] = set()
    for m in raw_metas:
        w_key = _normalize_weight_css(m.weight_css)
        s_key = _normalize_stretch_css(m.stretch_css)
        raw_styles = m.styles_css or (m.style_css,)
        st_keys = tuple(dict.fromkeys(_normalize_style_css(st) for st in raw_styles))
        for st_raw, st_key in zip(
            raw_styles, (_normalize_style_css(s) for s in raw_styles)
        ):
            desc_key = (w_key, s_key, st_key)
            if desc_key in seen_descriptors:
                raise ValueError(
                    f"Duplicate @font-face descriptors across input fonts: "
                    f"weight={m.weight_css!r}, stretch={m.stretch_css or 'normal'!r}, style={st_raw!r}. "
                    "Each font in a multi-file bundle must have a unique (weight, stretch, style) combination."
                )
        for st_key in st_keys:
            seen_descriptors.add((w_key, s_key, st_key))
        normalized_axes.append((w_key, s_key, st_keys))

    has_multiple_stretches = len({s_key for _, s_key, _ in normalized_axes}) > 1
    has_multiple_styles = (
        len({frozenset(st_keys) for _, _, st_keys in normalized_axes}) > 1
    )

    suffixes: list[str] = []
    seen_suffixes: set[str] = set()
    for m, (w_key, s_key, st_keys) in zip(raw_metas, normalized_axes):
        if m.variant_suffix:
            suf = m.variant_suffix
        else:
            parts: list[str] = []
            if has_multiple_stretches:
                parts.append(_slug_axis_token(s_key, prefix="w"))
            parts.append(_slug_axis_token(w_key))
            non_normal_styles = [sk for sk in st_keys if sk != "normal"]
            if has_multiple_styles and non_normal_styles:
                parts.append("-".join(_slug_axis_token(sk) for sk in non_normal_styles))
            suf = "-".join(parts)
        if suf in seen_suffixes:
            raise ValueError(
                f"Duplicate variant_suffix across input fonts: {suf!r}. "
                "Each font in a multi-file bundle must produce a unique slice filename suffix."
            )
        seen_suffixes.add(suf)
        suffixes.append(suf)
    return suffixes


__all__ = [
    "OS2_WIDTH_CLASS_TO_CSS",
    "FontFaceDescriptors",
    "FontMetadata",
    "extract_font_face_descriptors",
    "extract_font_metadata",
    "slugify_family",
    "validate_and_compute_variant_suffixes",
]
