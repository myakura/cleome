# Cleome

Cleome (`cleome`) is a Python (`>= 3.15`) CLI tool and library that inspects Japanese, Korean, and Chinese (`jp`, `kr`, `sc`, `tc`, `hk`) variable and static fonts (`.ttf` / `.otf`) and generates 4-tier `unicode-range` `.woff2` slices and `@font-face` CSS using CSS cascade shadowing and inverted-pyramid frequency granularity.

## Commands

* Install dependencies (`uharfbuzz`): `uv sync`
* Run test suite (`unittest` + HarfBuzz shaping verification, fail-fast): `uv run python -m unittest discover -s tests -v -f`
* Preview a 4-tier slice plan without writing files (read-only): `uv run cleome plan <font.ttf> [--json]`
* Build `.woff2` slices and `@font-face` CSS: `uv run cleome build <font.ttf> -o dist/`

## Architecture and invariants

* **Entrypoint guard (`spawn` / `forkserver`).** Guard runnable scripts and `README.md` or `skills/cleome/SKILL.md` Python examples with `if __name__ == "__main__":`, and pass worker configuration through process pool initializer arguments rather than module globals.
* **Deterministic, non-destructive font handling.** Open fonts with `recalcTimestamp=False` so repeated builds produce byte-identical `.woff2` files, retain license records (`nameID` `0`, `13`, `14`) in every slice, and never mutate caller-supplied `TTFont` objects in public slicing APIs.
* **CSS serialization via Stixis.** Delegate `@font-face` AST validation, PEP 750 `Template` (`t"..."`) escaping, and `<urange>` parsing/formatting/compaction to `stixis` (`SerializationOptions.cleome()`) rather than formatting raw CSS strings by hand.
* **CLI contract.** `cleome plan` (`src/cleome/cli.py`) is strictly read-only and never creates output directories or imports subsetting workers. Never mix `nargs="*"` or `"+"` optional flags with positional arguments.

## Issue triage and release workflow

* **Verify premises first.** Before implementing an Issue, check every factual claim and reproduction snippet against live runtime output (`uv run python -m unittest discover -s tests -v -f`, `uv run cleome plan`) and the relevant OpenType, WOFF2, or W3C CSS Fonts specification. If a claim contradicts the specification or observed font tables, close the Issue or scope out that item, and cite the spec section or table behavior.
* **Label and link Issues and PRs.**
  * Add appropriate labels (`bug`, `enhancement`, `documentation`) to every Issue and PR.
  * Use a dedicated branch per Issue, include `Closes #<n>` in the PR body, and run `codex review` and `uv run python -m unittest discover -s tests -v -f` before merging.
* **Keep the consumer skill and releases in sync.**
  * When you add or change public APIs, CLI flags, or slicing behavior, update [`skills/cleome/SKILL.md`](skills/cleome/SKILL.md), `README.md`, and the relevant domain guide under `docs/` or `.agents/docs/`.
  * To cut a release, bump the version in `pyproject.toml` and `src/cleome/__init__.py`, run `uv sync`, commit `chore(release): bump version to X.Y.Z`, push tag `vX.Y.Z`, and run `gh release create vX.Y.Z --title "vX.Y.Z" --generate-notes`.

## Domain guides

* Read [docs/hosting-and-usage.md](docs/hosting-and-usage.md) for deploying generated `<slug>.css` and `.woff2` slice files, configuring `--url-prefix` and `--slices-dir`, and setting preload, CORS, and `Cache-Control` headers.
* Read [docs/architecture-and-rationale.md](docs/architecture-and-rationale.md) and [docs/cjk-slicing-analysis.md](docs/cjk-slicing-analysis.md) for the empirical analysis of Google Fonts' `JP`, `KR`, `SC`, `TC`, and `HK` templates and the 4-tier simulation benchmarks.
* Read [.agents/docs/subsetting-and-shaping.md](.agents/docs/subsetting-and-shaping.md) when modifying `unicode-range` compaction (`src/cleome/urange.py`), the 4-tier slicing planner (`src/cleome/slicer.py`), locale detection (`src/cleome/locale.py`), or `fontTools.subset` / `cmap` Format 14 UVS handling (`src/cleome/subsetter.py`).
* Read [.agents/docs/css-and-metadata.md](.agents/docs/css-and-metadata.md) when modifying OpenType descriptor extraction (`src/cleome/metadata.py`), `@font-face` stylesheet generation (`src/cleome/css.py`), or cached multi-variant bundling (`src/cleome/pipeline.py`).
