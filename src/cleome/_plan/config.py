"""
Configuration and specification types for dynamic `unicode-range` slicing.
"""

from dataclasses import dataclass
from pathlib import Path

from stixis import canonicalize

from cleome._plan.locale import SUPPORTED_LOCALES


def _require_positive_int(val: object, name: str) -> int:
    if isinstance(val, bool) or not isinstance(val, int):
        raise TypeError(f"{name} must be an int, got {type(val).__name__}")
    if val <= 0:
        raise ValueError(f"{name} must be > 0, got {val}")
    return val


def _validate_workers(workers: int | None) -> None:
    if workers is None:
        return
    if isinstance(workers, bool) or not isinstance(workers, int):
        raise TypeError(f"workers must be an int or None, got {type(workers).__name__}")
    if workers < 1:
        raise ValueError(f"workers must be >= 1, got {workers}")


@dataclass(frozen=True, slots=True)
class SliceConfig:
    """Configuration parameters for dynamic unicode-range slicing."""

    # Target CJK slicing profile: "auto" (detect from family name & cmap), "jp", "kr", "sc", "tc", or "hk".
    locale: str = "auto"

    # Progressive (Inverted-Pyramid) chunk schedule for Tier 3 (frequency-ranked Kanji/Hanzi/Hangul):
    # List of (chunk_size, num_chunks) from highest frequency (rank 1) downward.
    # Any remaining frequency-ranked characters in the font's cmap after these chunks
    # are sliced using `freq_tail_chunk_size`.
    freq_chunks: tuple[tuple[int, int], ...] = (
        (200, 2),  # Rank 1..400: 200 chars x 2 slices
        (80, 10),  # Rank 401..1200: 80 chars x 10 slices
    )
    freq_tail_chunk_size: int = 70  # Rank 1201..N: ~70 chars/slice

    # Target character count per slice for Tier 4 (rare Kanji/Hanzi/Hanja/Hangul) and Tier 2 (`symbols-ext`):
    rare_chunk_size: int = 250
    symbols_ext_chunk_size: int = 350

    # Subsetting & WOFF2 options:
    layout_closure: bool = True
    layout_features: tuple[str, ...] = ("*",)
    drop_tables: tuple[str, ...] = ()
    uvs_base_repair: bool = True

    # CSS generation options:
    slices_subdir: str = "slices"
    url_prefix: str | None = None
    font_display: str = "swap"
    filename_hash: bool = True

    def __post_init__(self) -> None:
        norm_locale = self.locale.strip().lower()
        if norm_locale not in SUPPORTED_LOCALES:
            raise ValueError(
                f"Unsupported locale {self.locale!r}; expected one of {SUPPORTED_LOCALES}"
            )
        object.__setattr__(self, "locale", norm_locale)
        for chunk_size, num_chunks in self.freq_chunks:
            _require_positive_int(chunk_size, "freq_chunks chunk_size")
            _require_positive_int(num_chunks, "freq_chunks num_chunks")
        _require_positive_int(self.freq_tail_chunk_size, "freq_tail_chunk_size")
        _require_positive_int(self.rare_chunk_size, "rare_chunk_size")
        _require_positive_int(self.symbols_ext_chunk_size, "symbols_ext_chunk_size")
        cleaned_features = tuple(
            t.strip() for t in self.layout_features if isinstance(t, str) and t.strip()
        )
        if not cleaned_features:
            raise ValueError("layout_features cannot be empty")
        object.__setattr__(self, "layout_features", cleaned_features)
        subdir_str = self.slices_subdir.strip()
        if not subdir_str:
            raise ValueError("slices_subdir cannot be empty")
        subdir_norm = subdir_str.rstrip("/") or "."
        subdir_path = Path(subdir_norm)
        if (
            "\\" in subdir_str
            or "\x00" in subdir_str
            or subdir_str.startswith("/")
            or subdir_path.is_absolute()
            or bool(subdir_path.drive)
            or ".." in subdir_path.parts
        ):
            raise ValueError(
                f"slices_subdir must be a relative path inside out_dir without '..', got {self.slices_subdir!r}"
            )
        object.__setattr__(self, "slices_subdir", subdir_path.as_posix())
        display_str = self.font_display.strip()
        if not display_str:
            raise ValueError("font_display cannot be empty")
        try:
            norm_display = canonicalize(
                "font-display",
                display_str,
                at_rule="@font-face",
            )
        except ValueError as exc:
            raise ValueError(
                f"Invalid CSS font-display value: {self.font_display!r}"
            ) from exc
        object.__setattr__(self, "font_display", norm_display)


@dataclass(frozen=True, slots=True)
class SliceSpec:
    """Specification of a single unicode-range slice."""

    slice_id: str
    tier: int
    category: str
    description: str
    binary_codepoints: frozenset[int]
    css_unicode_range: str
    uses_cascade_shadowing: bool = False

    def filename(
        self, slug: str, variant_suffix: str = "", content_hash: str = ""
    ) -> str:
        stem = (
            f"{slug}-{variant_suffix}-{self.slice_id}"
            if variant_suffix
            else f"{slug}-{self.slice_id}"
        )
        if content_hash:
            return f"{stem}-{content_hash}.woff2"
        return f"{stem}.woff2"
