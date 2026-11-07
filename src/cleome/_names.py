"""
Filename token and content-hash stem helpers for Cleome.
"""

import re

_HASH_SUFFIX_RE = re.compile(r"^[0-9a-f]{8}$")


def _split_hashed_stem(stem: str) -> tuple[str, str] | None:
    """Split `<base_stem>-<hash8>` into `(base_stem, hash8)`, or return `None` if not hashed."""
    parts = stem.rsplit("-", 1)
    if len(parts) == 2 and parts[0] and _HASH_SUFFIX_RE.match(parts[1]):
        return parts[0], parts[1]
    return None


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
