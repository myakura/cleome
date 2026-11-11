"""
CSS `@font-face` stylesheet generator for Cleome.
"""

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from stixis import (
    CommentNode,
    FontFace,
    FontSrc,
    Rule,
    SerializationOptions,
    StyleSheet,
    dumps,
    using_options,
)

from cleome._font.metadata import FontMetadata
from cleome._output.paths import _resolve_url_prefix
from cleome._plan.config import SliceConfig, SliceSpec

_CLEOME_CSS_OPTIONS = SerializationOptions.cleome()
_CACHE_FORMAT_VERSION = 1
_CACHE_COMMENT_RE = re.compile(
    r"^/\* cleome-cache: v(?P<version>\d+) key=(?P<key>[0-9a-f]{32}) out=(?P<out>[0-9a-f]{32}) \*/$"
)
_BLOCK_GLYPHS_RE = re.compile(
    r"^/\* \[(?P<slice_id>[^\]]+)\] .* \((?P<glyphs>\d+) glyphs, [0-9.]+ KB\) \*/$"
)
_SRC_WOFF2_URL_RE = re.compile(
    r"""^src:\s*url\((?:'(?P<surl>(?:\\.|[^'\\])*)'|"(?P<durl>(?:\\.|[^"\\])*)")\)\s*format\(['"]woff2['"]\);$"""
)
_CSS_ESCAPE_RE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})[ \t]?|(.))")


@dataclass(frozen=True, slots=True)
class _ParsedCssBundle:
    version: int
    cache_key: str
    out_digest: str
    detected_slug: str | None
    blocks: list[tuple[str, int, str]]


def _unescape_css_string(escaped: str) -> str:
    def _repl(m: re.Match[str]) -> str:
        hex_part = m.group(1)
        if hex_part is not None:
            return chr(int(hex_part, 16))
        return m.group(2)

    return _CSS_ESCAPE_RE.sub(_repl, escaped)


def _extract_slug_prefix_for_slice(stem: str, slice_id: str) -> str | None:
    hashed_match = re.search(rf"-{re.escape(slice_id)}-[0-9a-f]{{8}}$", stem)
    if hashed_match is not None and hashed_match.start() > 0:
        return stem[: hashed_match.start()]
    suffix = f"-{slice_id}"
    if stem.endswith(suffix) and len(stem) > len(suffix):
        return stem[: -len(suffix)]
    return None


def _parse_cleome_css_bundle(css_text: str) -> _ParsedCssBundle | None:
    """
    Parse a Cleome-generated `@font-face` stylesheet carrying a `cleome-cache` header on line 2.
    Returns `None` if the header or `@font-face` comment/`src` structure is missing or malformed.
    """
    lines = css_text.splitlines()
    if len(lines) < 2:
        return None
    cache_match = _CACHE_COMMENT_RE.match(lines[1].strip())
    if cache_match is None:
        return None
    version = int(cache_match.group("version"))
    cache_key = cache_match.group("key")
    out_digest = cache_match.group("out")

    detected_slug: str | None = None
    current_variant_suffix = ""
    pending_block: tuple[str, int] | None = None
    blocks: list[tuple[str, int, str]] = []

    for raw_line in lines[2:]:
        stripped = raw_line.strip()
        if stripped.startswith("/* === Variant: ") and stripped.endswith(" === */"):
            idx_weight = stripped.rfind(" [weight: ")
            if idx_weight != -1 and idx_weight > 0 and stripped[idx_weight - 1] == ")":
                idx_open = stripped.rfind(" (", 0, idx_weight - 1)
                current_variant_suffix = (
                    stripped[idx_open + 2 : idx_weight - 1] if idx_open != -1 else ""
                )
            else:
                current_variant_suffix = ""
            continue

        bm = _BLOCK_GLYPHS_RE.match(stripped)
        if bm is not None:
            if pending_block is not None:
                return None
            pending_block = (bm.group("slice_id"), int(bm.group("glyphs")))
            continue

        src_match = _SRC_WOFF2_URL_RE.match(stripped)
        if src_match is None:
            continue
        if pending_block is None:
            return None
        slice_id, glyphs = pending_block
        pending_block = None

        raw_escaped = src_match.group("surl")
        if raw_escaped is None:
            raw_escaped = src_match.group("durl") or ""
        unescaped_url = _unescape_css_string(raw_escaped)
        fname = unescaped_url.rsplit("/", 1)[-1]
        if not fname.endswith(".woff2"):
            return None
        blocks.append((slice_id, glyphs, fname))

        if detected_slug is None:
            prefix = _extract_slug_prefix_for_slice(Path(fname).stem, slice_id)
            if prefix is not None:
                if not current_variant_suffix:
                    detected_slug = prefix
                elif (
                    prefix.endswith(f"-{current_variant_suffix}")
                    and len(prefix) > len(current_variant_suffix) + 1
                ):
                    detected_slug = prefix[: -(len(current_variant_suffix) + 1)]

    if pending_block is not None:
        return None

    return _ParsedCssBundle(
        version=version,
        cache_key=cache_key,
        out_digest=out_digest,
        detected_slug=detected_slug,
        blocks=blocks,
    )


def _read_cleome_css_bundle_slices(
    css_file: Path,
) -> tuple[str | None, set[str]] | None:
    """
    Read a Cleome-generated `@font-face` stylesheet from `css_file` and return
    `(bundle_slug, slice_filenames)`, or `None` if `css_file` is unreadable or does not carry
    a valid `cleome-cache` header on line 2.
    """
    try:
        with css_file.open("r", encoding="utf-8") as fh:
            first_line = fh.readline()
            second_line = fh.readline()
            if not first_line or not second_line:
                return None
            if _CACHE_COMMENT_RE.match(second_line.strip()) is None:
                return None
            rest = fh.read()
    except OSError, UnicodeDecodeError:
        return None

    parsed = _parse_cleome_css_bundle(f"{first_line}{second_line}{rest}")
    if parsed is None:
        return None
    return parsed.detected_slug, {fname for _, _, fname in parsed.blocks}


class _SliceFileLike(Protocol):
    @property
    def spec(self) -> SliceSpec: ...

    @property
    def filename(self) -> str: ...

    @property
    def path(self) -> Path: ...

    @property
    def size_bytes(self) -> int: ...

    @property
    def glyph_count(self) -> int: ...


def _compute_slices_output_digest(built_files: Sequence[_SliceFileLike]) -> str:
    """Compute a single 32-hex-char SHA-256 digest over all built `.woff2` slice files in order."""
    hasher = hashlib.sha256()
    for bf in built_files:
        raw = bf.path.read_bytes()
        hasher.update(f"{bf.filename}:{len(raw)}:{bf.glyph_count}\n".encode())
        hasher.update(raw)
    return hasher.hexdigest()[:32]


def _format_cache_comment(cache_key: str, out_digest: str) -> str:
    return (
        f"/* cleome-cache: v{_CACHE_FORMAT_VERSION} key={cache_key} out={out_digest} */"
    )


def generate_css(
    fonts_and_files: Sequence[tuple[FontMetadata, Sequence[_SliceFileLike]]],
    config: SliceConfig | None = None,
    *,
    cache_key: str | None = None,
    out_digest: str | None = None,
    css_path: Path | None = None,
    slices_dir: Path | None = None,
) -> str:
    """
    Generate the complete `@font-face` CSS text for one or more font variants.
    Preserves the strict Tier 4 -> Tier 3 -> Tier 2 -> Tier 1 cascade order for each variant.
    """
    if config is None:
        config = SliceConfig()
    if not fonts_and_files:
        return "\n"

    url_prefix = _resolve_url_prefix(config, css_path=css_path, slices_dir=slices_dir)
    first_meta = fonts_and_files[0][0]
    all_files = [f for _, files in fonts_and_files for f in files]
    total_slices = len(all_files)
    total_kb = sum(f.size_bytes for f in all_files) / 1024.0

    with using_options(_CLEOME_CSS_OPTIONS):
        rules: list[Rule] = []
        if cache_key is not None:
            resolved_out = (
                out_digest
                if out_digest is not None
                else _compute_slices_output_digest(all_files)
            )
            rules.append(CommentNode(_format_cache_comment(cache_key, resolved_out)))

        for meta, built_files in fonts_and_files:
            styles = meta.styles_css
            if len(fonts_and_files) > 1:
                v_label = f" ({meta.variant_suffix})" if meta.variant_suffix else ""
                stretch_info = (
                    f", stretch: {meta.stretch_css}" if meta.stretch_css else ""
                )
                style_info = " + ".join(styles)
                rules.append(
                    CommentNode(
                        t"=== Variant: {meta.family_name}{v_label} "
                        t"[weight: {meta.weight_css}{stretch_info}, "
                        t"style: {style_info}] ===",
                        leading_newline=True,
                    )
                )
            for style_val in styles:
                for bf in built_files:
                    spec = bf.spec
                    size_kb = bf.size_bytes / 1024.0
                    url_val = f"{url_prefix}{bf.filename}"
                    rules.append(
                        FontFace(
                            comment=t"[{spec.slice_id}] {spec.description} ({bf.glyph_count} glyphs, {size_kb:.1f} KB)",
                            font_family=meta.family_name,
                            font_style=style_val,
                            font_weight=meta.weight_css,
                            font_stretch=meta.stretch_css,
                            font_display=config.font_display,
                            src=FontSrc(url=url_val, format="woff2"),
                            unicode_range=spec.css_unicode_range,
                        )
                    )

        sheet = StyleSheet(
            rules=rules,
            header_comment=(
                t"Generated by Cleome: {first_meta.family_name} "
                t"({total_slices} slices, {total_kb:.1f} KB total WOFF2)"
            ),
        )
        return dumps(sheet)


__all__ = [
    "generate_css",
]
