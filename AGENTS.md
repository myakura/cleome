# AGENTS.md

Cleome is a Python (>= 3.15) CLI tool and library that dynamically inspects Japanese, Korean, and Chinese (`jp` / `kr` / `sc` / `tc` / `hk`) Variable and Static fonts (`.ttf` / `.otf`) and generates 4-tier `unicode-range` `.woff2` slices and `@font-face` CSS using CSS Cascade Shadowing and Inverted-Pyramid frequency granularity.


## Commands

* Package manager: `uv` (requires Python `>= 3.15`)
* Install dependencies (`uharfbuzz`):
  `uv sync`
* Run test suite (`unittest` + HarfBuzz shaping verification, fail-fast):
  `uv run python -m unittest discover -s tests -v -f`
* Preview a 4-tier slice plan without writing files (read-only):
  `uv run cleome plan <font.ttf> [--json]`
* Build `.woff2` slices and `@font-face` CSS:
  `uv run cleome build <font.ttf> -o dist/`


## Core Rules

* **Multiprocessing (`spawn` / `forkserver`)**: Guard runnable scripts and `README.md` Python examples with `if __name__ == "__main__":`, and pass worker configuration through process pool initializer arguments rather than module globals.
* **Deterministic, non-destructive font handling**: Open fonts with `recalcTimestamp=False` so repeated builds produce byte-identical `.woff2` files, retain license records (`nameID` `0`, `13`, `14`) in every slice, and never mutate caller-supplied `TTFont` objects in public slicing APIs.


## Issue Triage & Release Workflow

* **Verify premises first**: Before implementing an Issue, check every factual claim and reproduction snippet against live runtime output (`uv run python -m unittest discover -s tests -v -f`, `uv run cleome plan`) and the relevant OpenType, WOFF2, or W3C CSS Fonts specification. If a claim contradicts the specification or observed font tables, close the Issue or scope out that item, and cite the spec section or table behavior.
* **Label and link Issues and PRs**:
  * Add appropriate labels (`bug`, `enhancement`, `documentation`) to every Issue and PR.
  * Use a dedicated branch per Issue, include `Closes #<n>` in the PR body, and run `codex review` and `uv run python -m unittest discover -s tests -v -f` before merging.
* **Keep documentation and releases in sync**:
  * When you add or change public APIs, CLI flags, or slicing behavior, update `README.md` and the relevant domain guide under `docs/` or `.agents/docs/`.
  * To cut a release, bump the version in `pyproject.toml` and `src/cleome/__init__.py`, run `uv sync`, commit `chore(release): bump version to X.Y.Z`, push tag `vX.Y.Z`, and run `gh release create vX.Y.Z --title "vX.Y.Z" --generate-notes`.


## Domain References

* [Architecture & Rationale](docs/architecture-and-rationale.md)
* [Chinese & Korean (`KR` / `SC` / `TC` / `HK`) Slicing Analysis](docs/cjk-slicing-analysis.md)
* [WOFF2 Subsetting, OpenType Shaping & Slicing Tiers](.agents/docs/subsetting-and-shaping.md)
* [`@font-face` CSS Generation, Variable Axes & Variant Bundling](.agents/docs/css-and-metadata.md)
