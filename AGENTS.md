# AGENTS.md

`cleome` is a Python (>= 3.15) CLI tool and library that dynamically inspects Japanese Variable and Static fonts (`.ttf` / `.otf`) and generates 4-tier `unicode-range` `.woff2` slices and `@font-face` CSS using CSS Cascade Shadowing and Inverted-Pyramid Kanji frequency granularity.


## Commands

* Package manager: `uv` (requires Python `>= 3.15`)
* Install with test dependencies (`uharfbuzz`):
  `uv venv && uv pip install -e ".[test]"`
* Run test suite (`unittest` + HarfBuzz shaping verification):
  `.venv/bin/python -m unittest discover -s tests -v`
* Preview a 4-tier slice plan without writing files:
  `.venv/bin/cleome <font.ttf> --dry-run`


## Core Rules

* **Multiprocessing (`spawn` / `forkserver`)**: Guard runnable scripts and `README.md` Python examples with `if __name__ == "__main__":`, and pass worker configuration through process pool initializer arguments rather than module globals.
* **Deterministic, non-destructive font handling**: Open fonts with `recalcTimestamp=False` so repeated builds produce byte-identical `.woff2` files, retain license records (`nameID` `0`, `13`, `14`) in every slice, and never mutate caller-supplied `TTFont` objects in public slicing APIs.


## Domain References

* [Architecture & Rationale](docs/architecture-and-rationale.md)
* [WOFF2 Subsetting, OpenType Shaping & Slicing Tiers](.agents/docs/subsetting-and-shaping.md)
* [`@font-face` CSS Generation, Variable Axes & Variant Bundling](.agents/docs/css-and-metadata.md)
