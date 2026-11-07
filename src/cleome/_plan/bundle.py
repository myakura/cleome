"""
Font bundle preparation and active slice filtering for Cleome.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace

from cleome._font.metadata import FontMetadata, validate_and_compute_variant_suffixes
from cleome._plan.builder import _resolve_locale, build_slice_plan
from cleome._plan.config import SliceConfig, SliceSpec


@dataclass(frozen=True, slots=True)
class _PreparedBundle:
    """Unified metadata, resolved locale, per-variant slice plans, and union plan across input fonts."""

    family_name: str
    slug: str
    resolved_locale: str
    metas: list[FontMetadata]
    variant_plans: list[list[SliceSpec]]
    active_variant_plans: list[list[SliceSpec]]
    union_plan: list[SliceSpec]


def _filter_active_slices(
    meta: FontMetadata, slice_specs: Sequence[SliceSpec]
) -> list[tuple[SliceSpec, list[int]]]:
    """Return `(spec, sorted_codepoints)` for each slice in `slice_specs` that intersects `meta.codepoints`."""
    active: list[tuple[SliceSpec, list[int]]] = []
    for spec in slice_specs:
        cps = sorted(spec.binary_codepoints & meta.codepoints)
        if cps:
            active.append((spec, cps))
    return active


def _prepare_font_bundle(
    raw_metas: Sequence[FontMetadata],
    config: SliceConfig,
    *,
    family_override: str | None = None,
    slug_override: str | None = None,
) -> _PreparedBundle:
    """Validate variants, unify family and slug, and compute cached 4-tier slice plans without I/O."""
    if not raw_metas:
        raise ValueError("At least one FontMetadata item is required.")
    variant_suffixes = validate_and_compute_variant_suffixes(
        raw_metas, family_override=family_override
    )
    unified_family = family_override or raw_metas[0].family_name
    unified_slug = slug_override or raw_metas[0].slug

    metas: list[FontMetadata] = [
        replace(m, family_name=unified_family, slug=unified_slug, variant_suffix=v_suf)
        for m, v_suf in zip(raw_metas, variant_suffixes)
    ]
    union_codepoints = frozenset({*m.codepoints for m in metas})

    resolved_locale = _resolve_locale(
        union_codepoints, config, family_name=unified_family
    )
    resolved_config = (
        config
        if config.locale == resolved_locale
        else replace(config, locale=resolved_locale)
    )

    plan_cache: dict[frozenset[int], list[SliceSpec]] = {}
    for m in metas:
        if m.codepoints not in plan_cache:
            plan_cache[m.codepoints] = build_slice_plan(
                m.codepoints, config=resolved_config, family_name=unified_family
            )

    variant_plans = [plan_cache[m.codepoints] for m in metas]
    active_variant_plans = [
        [spec for spec, _ in _filter_active_slices(m, plan)]
        for m, plan in zip(metas, variant_plans)
    ]
    union_plan = plan_cache.get(union_codepoints) or build_slice_plan(
        union_codepoints, config=resolved_config, family_name=unified_family
    )
    return _PreparedBundle(
        family_name=unified_family,
        slug=unified_slug,
        resolved_locale=resolved_locale,
        metas=metas,
        variant_plans=variant_plans,
        active_variant_plans=active_variant_plans,
        union_plan=union_plan,
    )
