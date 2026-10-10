# sremu paper

IEEEtran journal paper for **sremu** (DESIGN.md section 13). This directory becomes its own
git repository; it is pinned to an sremu commit through `sremu.lock`.

Status: **WP12 skeleton (stage A).** Sections I (Introduction) and II (Related work, Table I)
are drafted; Sections III-X and the appendices are stubs; no figure exists yet (none may be
generated before GATE-PHY and GATE-RT are green, DESIGN 10.13).

## Build

```powershell
# draft: no git calls, no figure scripts; ?? placeholders, grey stubs and figure boxes
D:\projects\ris\sremu\.venv\Scripts\python.exe tools\build_paper.py --mode draft
# final: refuses (non-zero exit, no PDF) while anything is outstanding
D:\projects\ris\sremu\.venv\Scripts\python.exe tools\build_paper.py --mode final --profile full --junit <pinned junit xml>
```

`latexmk` and `pdflatex` come from MiKTeX (its "unsupported Windows" banner is harmless).
`--no-latex` runs every step except LaTeX. `PROFILE=fast|full` is passed to figure scripts.

The final mode refuses unless:

1. `sremu.lock` holds a 40-hex commit equal to the sremu HEAD, both trees are clean, and
   the interpreter imports `sremu` from that checkout;
2. every id of `gates.yaml` (GATE-PHY, GATE-RT; DESIGN 10.13) passes in the pinned junit,
   and PH-17...PH-22 pass once per backend (`[numpy_ref]` and `[numba_cpu]` in the test ids).
   Only implementation tests count (not `tests/oracles` self-tests, not golden-harness tests
   without `sremu` in the name), and a skipped or xfailed matching test keeps an id red.
   Every junit file must record the commit it was produced at: testsuite properties
   `sremu_commit` (= `sremu.lock`) and `sremu_dirty` (= `false`), or a sidecar
   `<junit stem>.meta.json` with the same keys. A junit of unknown or other provenance is
   refused, and `check_claims --mode final` applies the same rule to claim evidence;
3. every figure F1-F12 of `figures/registry.yaml` is placed once in the sections with its
   registered width, label `fig:<Fid>` and every data class in the caption, and is current
   (content key = sha256 of sremu commit, sweep spec, input hashes and script; the meta
   carries the registry's data classes and the commit);
4. `generated/numbers.tex` has no placeholder, and no `\sremustub` remains;
5. `check_claims` (final), `check_refs` and `check_arxiv` pass; after LaTeX, `main.bbl` exists
   and no citation or reference is undefined.

## Layout

| Path | Content |
|---|---|
| `main.tex`, `sremu-macros.tex` | Document, author block, build-mode macros |
| `sections/*.tex`, `sections/tables/table1_tools.tex` | One file per section of DESIGN 13.3; Table I |
| `numbers.yaml` -> `generated/numbers.tex` | Every number quoted in the text (macros; placeholders until the pipeline fills them from `generated/results.json` = `{"sremu_commit": <sha>, "values": {macro: value}}`, which a final build accepts only from the pinned commit) |
| `generated/tables/table2_model_card.tex` | Table II, generated from `sremu/docs/model_card.yaml` |
| `claims.yaml` | Every claim with its evidence (tests, figures, citations) and human gates |
| `human_gates.yaml` | DESIGN 16 gates H-1...H-8 (A1: no AI-disclosure item for now) |
| `gates.yaml` | GATE-PHY / GATE-RT test ids |
| `figures/registry.yaml` | F1-F12: script, inputs, data classes, owner, status |
| `refs.bib`, `refs_verification.md`, `refs_verification.json`, `refs_sources/` | Bibliography and its verification record |
| `tools/` | `build_paper.py`, `check_claims.py`, `check_refs.py`, `check_arxiv.py`, `verify_refs_online.py`, `paper_common.py` |
| `tests/` | Tests of the tools (`python -m pytest`; the LaTeX test is marked `integration`) |

## Rules (DESIGN 1.6, 13.6)

- **Numbers.** Every number in the text is a macro from `numbers.yaml` or a literature
  number in a cited sentence/table row; a citation does not cover a sentence about sremu
  itself. Numbers glued to units (`5.2ms`, `3x`, `1e-3`) count as numbers; in math,
  decimals, integers >= 2 after a relation (`N = 1024`) and integers with a unit are
  flagged. A fixed `value` is allowed only for definitions (DESIGN 12.1) and
  dataset facts. Text macros carry a `max_chars` budget; `check_arxiv.py` bounds the abstract
  (<= 1920 characters, <= 200 words) with every placeholder at its budget.
- **Banned words** ("first", "only", "compliant", "digital twin", "accurately replicates",
  "no loss of functionality") need a claim with a `banned_word_uses` entry; "real-time" is
  allowed only in "real-time factor", "hard/soft real time" and "non-real-time".
- **Claims** are pending until their tests pass in the pinned junit, their figures exist and
  their human gates are closed. Draft builds list pending claims; final builds refuse them.
- **References.** Add an entry only with a primary-record check: a row in
  `refs_verification.md` (origin `new`) and, ideally, `python tools/verify_refs_online.py
  --keys <key>` (network). Every entry needs a doi, an arXiv eprint or an official URL;
  printed fields may not contain bare URLs.
- **Verification vs validation.** MATLAB agreement is code-to-code; digitized data is DIG
  with its uncertainty; the lab comparison is a "consistency check under geometric
  uncertainty" unless the lab supplies the geometry (H-1).
- **AI disclosure.** No generative-AI disclosure section for now (lead amendment A1); to be
  revisited at journal submission (IEEE requires it in the acknowledgments).
