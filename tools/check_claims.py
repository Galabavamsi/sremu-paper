"""check_claims.py -- claims, honesty rules and number provenance (DESIGN 1.6, 13.6; WP12).

Checks (both modes unless noted)
  1. claims.yaml schema: unique ids, known evidence kinds, spec ids well formed, figure ids
     registered, cite keys in refs.bib, table labels defined, gates known.
  2. Evidence status: test ids must pass in the pinned junit, figures must have their meta
     and PDF, gates must be closed. Pending claims are notes in draft and errors in final.
     In final mode every junit file must record the sremu.lock commit and a clean tree.
  3. Banned words (DESIGN 1.6 rule 5) in the paper text: an error unless the text unit is
     mapped by a claim's banned_word_uses (and, in final mode, that claim is supported).
  4. "real-time" never unqualified (rule 3): allowed in "real-time factor", "hard real
     time", "soft real time" and "non-real-time"; anything else needs a mapped claim.
  5. Numbers (rule 7): in every text unit (sentence, list item or table row) without a
     citation, a number must come from a macro. A citation does not cover a unit that
     talks about sremu itself. Identifiers (v2, 6G, PH-02, GS RIS 008), years and dates are
     allowed; numbers glued to units (5.2ms, 3x, 1e-3) are numbers. Inside math, decimals,
     integers >= 2 after a relation (N = 1024) and integers with a unit are flagged.
  6. Every \\claim{ID} marker names a claim in claims.yaml.
Planned-content stubs (\\sremustub{...}) are excluded from 3-5; a final build refuses
them anyway.

Usage: python tools/check_claims.py [--mode draft|final] [--junit FILE ...] [--paper-root DIR]
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_common import (
    BANNED_WORDS,
    FIGURE_ID_RE,
    MODES,
    PAPER_ROOT,
    SPEC_ID_RE,
    STUB_MACRO,
    CaseResult,
    Report,
    bib_by_key,
    generated_tex_files,
    junit_provenance_errors,
    load_junit,
    load_numbers_spec,
    load_results,
    parse_bib,
    read_text,
    read_yaml,
    spec_id_status,
    strip_tex_comments,
    tex_labels,
    tex_source_files,
)

EVIDENCE_KINDS = ("test", "fig", "cite", "table", "spec")
REALTIME_RE = re.compile(r"real[\s~-]*time", re.I)
REALTIME_ALLOWED = [
    re.compile(r"real[\s~-]*time[\s~-]+factor", re.I),
    re.compile(r"hard[\s~-]+real[\s~-]*time", re.I),
    re.compile(r"soft[\s~-]+real[\s~-]*time", re.I),
    re.compile(r"non[\s~-]*real[\s~-]*time", re.I),
]
MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
# Names of standards documents are identifiers, not quantities.
STANDARD_ID_RE = re.compile(
    r"\b(?:(?:GS|GR)[\s~]+RIS[\s~]+\d{3}(?:\s*/\s*\d{3})*"
    r"|TR[\s~]+\d+\.\d+|TS[\s~]+\d+\.\d+|STD[\s~]+\d+"
    r"|ITU-R[\s~]+[A-Z]+\.\d+(?:-\d+)?)"
)


# --------------------------------------------------------------------------- text preparation
def remove_command(text: str, name: str, nargs: int = 1) -> str:
    """Remove \\name[opt]{arg}... (nargs brace groups, nesting-aware)."""
    out, i = [], 0
    pat = re.compile(r"\\" + re.escape(name) + r"(?![A-Za-z])\*?")
    while True:
        m = pat.search(text, i)
        if not m:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i : m.start()])
        j = m.end()
        while j < len(text) and text[j] in " \t":
            j += 1
        if j < len(text) and text[j] == "[":
            j = text.index("]", j) + 1
        for _ in range(nargs):
            while j < len(text) and text[j] in " \t\n":
                j += 1
            if j < len(text) and text[j] == "[":  # optional argument between groups
                j = text.index("]", j) + 1
            if j < len(text) and text[j] == "{":
                depth = 0
                for k in range(j, len(text)):
                    if text[k] == "{" and text[k - 1] != "\\":
                        depth += 1
                    elif text[k] == "}" and text[k - 1] != "\\":
                        depth -= 1
                        if depth == 0:
                            j = k + 1
                            break
        i = j


STRIP_COMMANDS = {  # command -> number of brace arguments removed with it
    "label": 1, "ref": 1, "eqref": 1, "pageref": 1, "input": 1, "include": 1,
    "includegraphics": 1, "url": 1, "href": 1, "claim": 1, STUB_MACRO: 1,
    "setlength": 2, "renewcommand": 2, "newcolumntype": 2, "vspace": 1, "hspace": 1,
    "bibliographystyle": 1, "bibliography": 1, "usepackage": 1, "documentclass": 1,
    "markboth": 2, "thanks": 1, "IEEEPARstart": 2,
}


def _flat_math(body: str) -> str:
    """Display-math body on one line: line breaks (\\\\) and labels do not end the unit."""
    body = re.sub(r"\\label\{[^}]*\}", " ", body)
    return " ".join(body.replace("\\\\", " ").split())


def prepare(text: str) -> str:
    """Paper text for the word and number rules: comments, stubs and markup args removed."""
    s = strip_tex_comments(text)
    s = re.sub(  # display math -> inline math, so the math rule applies
        r"\\begin\{(equation|align|gather|multline)(\*?)\}(.*?)\\end\{\1\2\}",
        lambda m: " $" + _flat_math(m.group(3)) + "$ ",
        s,
        flags=re.S,
    )
    s = re.sub(r"\\\[(.*?)\\\]", lambda m: " $" + _flat_math(m.group(1)) + "$ ", s, flags=re.S)
    s = re.sub(r"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{[^}]*\}", " CITEMARK ", s)
    s = re.sub(r"\\begin\{tabular\*?\}", r"\\sremutabularspec", s)
    s = remove_command(s, "sremutabularspec", 1)  # column spec, nesting-aware
    s = re.sub(r"\\(begin|end)\{[^}]*\}(\[[^\]]*\])?", " ", s)
    for name, nargs in STRIP_COMMANDS.items():
        s = remove_command(s, name, nargs)
    s = re.sub(r"\d*\.?\d+\\(columnwidth|textwidth|linewidth)", " ", s)
    s = re.sub(r"\\parbox(\[[^\]]*\])*", " ", s)
    return s


def split_units(prepared: str) -> list[str]:
    """Sentences, list items and table rows."""
    s = re.sub(r"\\\\(\[[^\]]*\])?", "\n\n", prepared)  # table rows / line breaks
    s = s.replace("\\item", "\n\n")
    units: list[str] = []
    for para in re.split(r"\n\s*\n", s):
        flat = " ".join(para.split())
        units += [u for u in re.split(r"(?<=[.!?])\s+(?=[A-Z\\(])", flat) if u.strip()]
    return units


def _math_spans(unit: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    i = 0
    while True:
        a = unit.find("$", i)
        if a < 0:
            return spans
        b = unit.find("$", a + 1)
        if b < 0:
            return spans
        spans.append((a, b + 1))
        i = b + 1


# A sentence about sremu's own results is not covered by a citation in the same sentence.
OWN_WORK_RE = re.compile(
    r"\\sremu(?![A-Za-z])|\bsremu\b|\bthis (?:work|paper)\b"
    r"|\bour (?:emulator|tool|implementation|engine|kernel|results?|measurements?)\b",
    re.I,
)
# A number glued to a unit or written in scientific notation is a number, not an identifier
# (5.2ms, 10dB, 3x, 1e-3). 5G, 6G, 2D, v2, R2026a stay identifiers.
UNIT_GLUED_RE = re.compile(
    r"\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
    r"(?:[kMGT]?Hz|[mun]?s|dB[mi]?|[mck]?m|M?S/s|[mk]?W|x|X)"
    r"|\d+(?:\.\d+)?[eE][-+]?\d+"
)
# Inside math, an integer >= 2 after a relation (N = 1024, L > 64) or before a unit
# (20\,\mathrm{ms}) is a quoted quantity; 0 and 1 after a relation are structural.
MATH_REL_INT_RE = re.compile(
    r"(?:=|<|>|\\leq?|\\geq?|\\approx|\\sim|\\neq|\\ll|\\gg)\s*-?\s*(\d+)(?![\d.])"
)
MATH_UNIT_INT_RE = re.compile(
    r"(?<![\w.])(\d+)\s*(?:\\[,;:! ]|~)?\s*\\(?:mathrm|text|textrm|mbox)\s*\{\s*"
    r"(?:[kMGT]?Hz|[mun]?s|dB[mi]?|[mck]?m|M?S/s|[mk]?W)\s*\}"
)


def flag_numbers(unit: str) -> list[str]:
    """Numbers in a text unit that are neither cited nor macros (rule 7).

    A citation in the unit exempts quoted literature numbers, but not when the unit talks
    about sremu itself (a result next to a citation is still a result)."""
    if "CITEMARK" in unit and not OWN_WORK_RE.search(unit):
        return []
    u = re.sub(rf"\b\d{{1,2}}\s+{MONTHS}\s+\d{{4}}\b", " ", unit)  # 10 Oct 2026
    u = re.sub(rf"\b{MONTHS}\s+\d{{4}}\b", " ", u)  # Dec 2022
    u = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", u)  # ISO dates
    u = STANDARD_ID_RE.sub(" ", u)  # document names such as GS RIS 008, TR 38.867
    flagged: list[str] = []
    spans = _math_spans(u)
    for a, b in spans:  # math: decimals, related integers, integers with units
        math = u[a:b]
        flagged += re.findall(r"(?<![\w.])\d+\.\d+(?![\w.])", math)
        flagged += [n for n in MATH_REL_INT_RE.findall(math) if int(n) >= 2]
        flagged += MATH_UNIT_INT_RE.findall(math)
    outside = "".join(" " if any(a <= i < b for a, b in spans) else ch for i, ch in enumerate(u))
    outside = re.sub(r"\\[A-Za-z]+", " ", outside)  # macro names (numbers come from macros)
    for tok in re.findall(r"[A-Za-z0-9]+(?:[-./:][A-Za-z0-9]+)*", outside):
        if UNIT_GLUED_RE.fullmatch(tok):
            flagged.append(tok)
            continue
        if re.search(r"[A-Za-z]", tok):
            continue  # identifier: v2, 6G, PH-02, R2026a, i.i.d
        if re.fullmatch(r"(19|20)\d{2}", tok):
            continue  # a year
        flagged.append(tok)
    return flagged


# --------------------------------------------------------------------------- claims model
@dataclass
class Claim:
    cid: str
    text: str
    where: list[str]
    evidence: list[str]
    gates: list[str]
    banned_word_uses: list[dict[str, str]] = field(default_factory=list)
    status: str = "pending"
    reasons: list[str] = field(default_factory=list)


def load_claims(path: Path) -> list[Claim]:
    raw = read_yaml(path) or {}
    out = []
    for cid, c in (raw.get("claims") or {}).items():
        out.append(
            Claim(
                cid=str(cid),
                text=str(c.get("text", "")),
                where=list(c.get("where") or []),
                evidence=[str(e) for e in (c.get("evidence") or [])],
                gates=[str(g) for g in (c.get("gates") or [])],
                banned_word_uses=list(c.get("banned_word_uses") or []),
            )
        )
    return out


def evaluate_claims(
    claims: list[Claim],
    *,
    paper_root: Path,
    bib_keys: set[str],
    labels: set[str],
    registry: dict,
    gates: dict,
    cases: list[CaseResult] | None,
    rep: Report,
) -> None:
    ids = [c.cid for c in claims]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        rep.error(f"duplicate claim id {dup}")
    for c in claims:
        if not re.fullmatch(r"C-[A-Z0-9-]+", c.cid):
            rep.error(f"{c.cid}: claim ids look like C-NAME")
        if not c.text:
            rep.error(f"{c.cid}: empty text")
        for w in c.where:
            if not (paper_root / w).is_file():
                rep.error(f"{c.cid}: where-file {w} does not exist")
        for g in c.gates:
            if g not in gates:
                rep.error(f"{c.cid}: unknown human gate {g}")
            elif gates[g].get("status") != "closed":
                c.reasons.append(f"gate {g} open")
        for ev in c.evidence:
            kind, _, ident = ev.partition(":")
            if kind not in EVIDENCE_KINDS or not ident:
                rep.error(f"{c.cid}: malformed evidence {ev!r}")
                continue
            if kind == "test":
                if not SPEC_ID_RE.match(ident):
                    rep.error(f"{c.cid}: {ident!r} is not a spec test id")
                    continue
                st = "missing" if cases is None else spec_id_status(ident, cases)
                if st != "passed":
                    c.reasons.append(f"test {ident} {st if cases is not None else 'no junit'}")
            elif kind == "fig":
                if not FIGURE_ID_RE.match(ident) or ident not in registry:
                    rep.error(f"{c.cid}: figure {ident} not in figures/registry.yaml")
                    continue
                meta = paper_root / "figures" / "data" / f"{ident}.meta.json"
                pdf = paper_root / "figures" / "out" / f"{ident}.pdf"
                drawn = registry[ident].get("source") == "tikz"
                if not (pdf.is_file() and (meta.is_file() or drawn)):
                    c.reasons.append(f"figure {ident} not generated")
            elif kind == "cite":
                if ident not in bib_keys:
                    rep.error(f"{c.cid}: cite key {ident} not in refs.bib")
            elif kind == "table":
                if ident not in labels:
                    rep.error(f"{c.cid}: table label {ident} not defined in the TeX sources")
        for use in c.banned_word_uses:
            if not use.get("word") or not use.get("context"):
                rep.error(f"{c.cid}: banned_word_uses entries need word and context")
        c.status = "supported" if not c.reasons else "pending"


# --------------------------------------------------------------------------- text rules
def _banned_pattern(word: str) -> re.Pattern[str]:
    return re.compile(r"\b" + r"[\s~]+".join(map(re.escape, word.split())) + r"\b", re.I)


def _mapped(word: str, unit: str, claims: list[Claim]) -> Claim | None:
    flat = " ".join(unit.split()).lower()
    for c in claims:
        for use in c.banned_word_uses:
            context = " ".join(use["context"].split()).lower()
            if use.get("word", "").lower() == word and context in flat:
                return c
    return None


def check_tex_string(
    rel: str, raw: str, claims: list[Claim], mode: str, rep: Report, numbers: bool = True
) -> None:
    """Word, real-time and number rules on one TeX string (a file or a macro value)."""
    claim_ids = {c.cid for c in claims}
    for m in re.finditer(r"\\claim\{([^}]*)\}", strip_tex_comments(raw)):
        if m.group(1) not in claim_ids:
            rep.error(f"{rel}: \\claim{{{m.group(1)}}} not in claims.yaml")
    for unit in split_units(prepare(raw)):
        snippet = unit[:90] + ("..." if len(unit) > 90 else "")
        for word in BANNED_WORDS:
            if _banned_pattern(word).search(unit):
                c = _mapped(word, unit, claims)
                if c is None:
                    rep.error(f"{rel}: banned word '{word}' without a mapped claim: {snippet}")
                elif c.status != "supported":
                    (rep.error if mode == "final" else rep.note)(
                        f"{rel}: '{word}' mapped to pending claim {c.cid}"
                    )
        for m in REALTIME_RE.finditer(unit):
            window = unit[max(0, m.start() - 12) : m.end() + 12]
            if any(p.search(window) for p in REALTIME_ALLOWED):
                continue
            c = _mapped("real-time", unit, claims)
            if c is None:
                rep.error(f"{rel}: unqualified 'real-time' (rule 3): {snippet}")
            elif c.status != "supported" and mode == "final":
                rep.error(f"{rel}: 'real-time' mapped to pending claim {c.cid}")
        nums = flag_numbers(unit) if numbers else []
        if nums:
            rep.error(f"{rel}: number(s) {nums} neither cited nor macros: {snippet}")


def check_text(
    files: list[Path],
    paper_root: Path,
    claims: list[Claim],
    mode: str,
    rep: Report,
    numbers: bool = True,
) -> None:
    for f in files:
        rel = f.relative_to(paper_root).as_posix()
        check_tex_string(rel, read_text(f), claims, mode, rep, numbers)


def check_macro_values(paper_root: Path, claims: list[Claim], mode: str, rep: Report) -> None:
    """Values the pipeline inserts into the text (results.json, fixed definitions): their
    numbers are pipeline output, but a result phrase must obey the word rules as well."""
    try:
        spec = load_numbers_spec(paper_root / "numbers.yaml")
        results, _ = load_results(paper_root)
    except (OSError, ValueError, KeyError) as exc:
        rep.error(f"cannot load macro values: {exc}")
        return
    for m in spec:
        value = m.value if m.value is not None else results.get(m.macro)
        if value is not None:
            check_tex_string(f"numbers:{m.macro}", value, claims, mode, rep, numbers=False)


# --------------------------------------------------------------------------- driver
def default_junit(paper_root: Path) -> list[Path]:
    return sorted((paper_root / "generated" / "junit").glob("*.xml"))


def check_claims(
    paper_root: Path = PAPER_ROOT, mode: str = "draft", junit: list[Path] | None = None
) -> Report:
    rep = Report("check_claims")
    if mode not in MODES:
        rep.error(f"unknown mode {mode!r}")
        return rep
    try:
        claims = load_claims(paper_root / "claims.yaml")
        gates = (read_yaml(paper_root / "human_gates.yaml") or {}).get("gates") or {}
        registry = (read_yaml(paper_root / "figures" / "registry.yaml") or {}).get("figures") or {}
        bib_keys = set(bib_by_key(parse_bib(read_text(paper_root / "refs.bib"))))
    except (OSError, ValueError) as exc:
        rep.error(f"cannot load inputs: {exc}")
        return rep
    tex = tex_source_files(paper_root)
    labels: set[str] = set()
    for f in tex + generated_tex_files(paper_root):
        labels |= tex_labels(read_text(f))
    junit = default_junit(paper_root) if junit is None else junit
    cases = load_junit(junit) if junit else None
    rep.note(f"junit: {len(junit)} file(s), {0 if cases is None else len(cases)} test cases")
    if mode == "final":  # evidence counts only from the junit of the pinned commit
        lock = read_yaml(paper_root / "sremu.lock") or {}
        for msg in junit_provenance_errors(junit, str(lock.get("commit", ""))):
            rep.error(msg)

    evaluate_claims(claims, paper_root=paper_root, bib_keys=bib_keys, labels=labels,
                    registry=registry, gates=gates, cases=cases, rep=rep)
    pending = [c for c in claims if c.status != "supported"]
    n_sup = len(claims) - len(pending)
    rep.note(f"{len(claims)} claims: {n_sup} supported, {len(pending)} pending")
    for c in pending:
        msg = f"{c.cid} pending: {'; '.join(c.reasons[:6])}{' ...' if len(c.reasons) > 6 else ''}"
        (rep.error if mode == "final" else rep.note)(msg)

    text_files = [f for f in tex if f.name != "sremu-macros.tex"]
    check_text(text_files, paper_root, claims, mode, rep)
    # Generated tables: their numbers come from the pipeline, but the word rules still apply.
    gen_tables = sorted((paper_root / "generated" / "tables").glob("*.tex"))
    check_text(gen_tables, paper_root, claims, mode, rep, numbers=False)
    check_macro_values(paper_root, claims, mode, rep)
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check claims and honesty rules of the paper.")
    ap.add_argument("--mode", choices=MODES, default="draft")
    ap.add_argument("--junit", type=Path, nargs="*", default=None)
    ap.add_argument("--paper-root", type=Path, default=PAPER_ROOT)
    args = ap.parse_args(argv)
    return check_claims(args.paper_root, args.mode, args.junit).emit()


if __name__ == "__main__":
    raise SystemExit(main())
