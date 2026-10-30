# Cleome

Cleome (`cleome`) is a Python (`>= 3.15`) CLI tool and library that inspects Japanese, Korean, and Chinese (`jp`, `kr`, `sc`, `tc`, `hk`) variable and static fonts (`.ttf` / `.otf`) and generates 4-tier `unicode-range` `.woff2` slices and `@font-face` CSS using CSS cascade shadowing and inverted-pyramid frequency granularity.

## Commands

* Install dependencies (`uharfbuzz`): `uv sync`
* Run test suite (`unittest` + HarfBuzz shaping verification, fail-fast): `uv run python -m unittest discover -s tests -v -f`
* Preview a 4-tier slice plan without writing files (read-only): `uv run cleome plan <font.ttf> [--json]`
* Build `.woff2` slices and `@font-face` CSS: `uv run cleome build <font.ttf> -o dist/ [--css-out <path.css>] [--no-filename-hash] [--dry-run]`

## Architecture and invariants

* **Entrypoint guard (`spawn` / `forkserver`).** Guard runnable scripts and `README.md` or `skills/cleome/SKILL.md` Python examples with `if __name__ == "__main__":`, and pass worker configuration through process pool initializer arguments rather than module globals.
* **Deterministic, non-destructive font handling.** Open fonts with `recalcTimestamp=False` so repeated builds produce byte-identical `.woff2` files, retain license records (`nameID` `0`, `13`, `14`) in every slice, and never mutate caller-supplied `TTFont` objects in public inspection or slicing APIs (only `patch_uvs_missing_base_cmap` mutates in place).
* **CSS serialization via Stixis.** Delegate `@font-face` AST validation, PEP 750 `Template` (`t"..."`) escaping, and `<urange>` parsing, formatting, and compaction to `stixis` (`SerializationOptions.cleome()`) rather than formatting raw CSS strings by hand.
* **CLI contract.** `cleome plan` (`src/cleome/cli.py`) is read-only and never creates output directories or imports subsetting workers. Never mix `nargs="*"` or `"+"` optional flags with positional arguments.
* **Public API sync.** When changing public APIs, CLI flags, or slicing behavior, update [`skills/cleome/SKILL.md`](skills/cleome/SKILL.md), `README.md`, and the relevant guide under `.agents/docs/`.

## Domain guides

* Read [.agents/docs/subsetting-and-shaping.md](.agents/docs/subsetting-and-shaping.md) when modifying `unicode-range` compaction (`src/cleome/urange.py`), the 4-tier slicing planner (`src/cleome/slicer.py`), locale detection (`src/cleome/locale.py`), or `fontTools.subset` and `cmap` Format 14 UVS handling (`src/cleome/subsetter.py`).
* Read [.agents/docs/css-and-metadata.md](.agents/docs/css-and-metadata.md) when modifying OpenType descriptor extraction (`src/cleome/metadata.py`), `@font-face` stylesheet generation (`src/cleome/css.py`), or cached multi-variant bundling (`src/cleome/pipeline.py`).
