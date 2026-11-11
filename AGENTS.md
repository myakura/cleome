# AGENTS.md

Cleome (`cleome`) is a Python (`>= 3.15`) CLI tool and library that inspects Japanese, Korean, and Chinese (`jp`, `kr`, `sc`, `tc`, `hk`) variable and static fonts (`.ttf` / `.otf`) and generates 4-tier `unicode-range` `.woff2` slices and `@font-face` CSS.


## Commands

* Install dependencies (`uharfbuzz`) with `uv sync`.
* Check lint and formatting with `uv run ruff check` and `uv run ruff format --check`.
* Run the test suite (`pytest` and `uharfbuzz` shaping checks) with `uv run pytest`. Place new tests in `tests/test_slicer_and_locales.py` (`_plan`), `tests/test_subsetter_and_shaping.py` (`_font`, `_output`, `_pipeline`, `_plan.config`), or `tests/test_cli.py` (`cli` and end-to-end runs), or create `tests/test_<module>.py` when none fits.
* Preview a 4-tier slice plan without writing files (read-only) with `uv run cleome plan <font.ttf> [--json]`.
* Build `.woff2` slices and `@font-face` CSS with `uv run cleome build <font.ttf> -o dist/ [--css-out <path.css>] [--no-filename-hash] [--dry-run]`.


## Core rules

* **Entrypoint guard (`spawn` / `forkserver`).** Guard runnable scripts and Python examples in `README.md` or `skills/cleome/SKILL.md` with `if __name__ == "__main__":`, and pass worker configuration through process pool initializer arguments rather than module globals.
* **Deterministic, non-destructive font handling.** Open fonts with `recalcTimestamp=False` so repeated builds emit byte-identical `.woff2` files, retain license records (`nameID` `0`, `13`, `14`) in every slice, and never mutate caller-supplied `TTFont` objects in public APIs except `patch_uvs_missing_base_cmap`.
* **Delegate CSS handling to Stixis.** Delegate `@font-face` AST validation, PEP 750 `Template` (`t"..."`) escaping, `<urange>` parsing, formatting, and compaction, and CSS descriptor canonicalization to `stixis` (`SerializationOptions.cleome()`) rather than implementing custom CSS or math parsers in `cleome`.
* **CSS cascade shadowing and plane boundaries.** Order `@font-face` declarations from lowest priority (`Tier 4`) to highest (`Tier 1`), bridge `unicode-range` gaps in an earlier tier only across unmapped code points or code points shadowed by a higher-priority later tier, and never bridge ranges across different Unicode planes.
* **Intra-slice OpenType shaping and `cmap` Format 14 UVS.** Keep base characters in the same slice as their combining marks or paired brackets so `fontTools.subset` retains multi-glyph `GSUB` and `GPOS` lookups (verify partitioning or subsetting changes with `uharfbuzz`), and retain default and non-default Format 14 UVS/IVS records for slice base characters without adding standalone variation selectors to Format 4 or Format 12 `cmap` (separate from `patch_uvs_missing_base_cmap`).
* **Multi-font bundling and atomic slice pruning.** Build each bundled variant's slices from its own mapped code points, include `"normal"` in `font-style` only when the font's axis ranges cover an upright form (`ital < 0.5`, `0deg` within `slnt`), reject colliding canonicalized descriptors, colliding filename suffixes, or scalar single-font overrides across multiple fonts with `ValueError` rather than auto-numbering (`-2`, `-3`), and prune superseded content-hashed `.woff2` files only after the new CSS file has been written atomically.
* **CLI and public contract.** Keep `cleome plan` (`src/cleome/cli.py`) read-only (no output directories or subsetting worker imports), and update [`skills/cleome/SKILL.md`](skills/cleome/SKILL.md) (plus affected sections of `README.md`, [`docs/hosting-and-usage.md`](docs/hosting-and-usage.md), or [`docs/architecture-and-rationale.md`](docs/architecture-and-rationale.md)) whenever public APIs, accepted types, or CLI flags change.
