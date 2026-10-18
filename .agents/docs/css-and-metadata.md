---
type: Reference
title: "@font-face CSS Generation, Variable Axes & Variant Bundling"
description: Domain rules for extracting Variable and Static font descriptors and bundling multi-file @font-face stylesheets.
status: stable
updated: 2026-10-18
stale_after: 2027-10-18T00:00:00+09:00
---

# `@font-face` CSS Generation, Variable Axes & Variant Bundling

Domain rules for extracting font metadata and generating `@font-face` stylesheets across Variable and Static fonts.


## Variable & Static Axis Mapping

* Extract `font-weight`, `font-stretch`, and `font-style` descriptors from `fvar` axes (`wght`, `wdth`, `ital`, `slnt`) for Variable Fonts, and from `OS/2` (`usWeightClass`, `usWidthClass`, `fsSelection`) and `post` (`italicAngle`) tables for Static Fonts.
* When a Variable Font includes `ital` and/or `slnt` axes, emit `@font-face` rules for each supported style (`normal`, `italic`, `oblique ...`) pointing to the same `.woff2` slice files. Emit `font-style: normal` only when the font's axis ranges include an upright form (`ital < 0.5` and `0deg` within `slnt`).


## Multi-Font Variant Bundling

* Bundling multiple font files into a single stylesheet requires a shared `font-family` name (unless explicitly overridden) and a unique `(font-weight, font-stretch, font-style)` descriptor tuple for every emitted rule.
* Fail fast with a `ValueError` on family mismatches or descriptor collisions rather than auto-numbering colliding variants or filenames (`-2`, `-3`).
* When bundled variants have different `cmap` coverage, build and export each variant's slices from its own mapped codepoints rather than the cross-font union.
