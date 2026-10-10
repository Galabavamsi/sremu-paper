"""Shared helpers for the paper pipeline tools (WP12, DESIGN.md sections 13.5 and 13.6).

Standard library plus PyYAML only, so the tools run in the sremu virtual environment and in a
bare Python with PyYAML. Nothing here touches the network or runs git.
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PAPER_ROOT = Path(__file__).resolve().parents[1]

# arXiv and IEEE limits (DESIGN 13.1, 13.5).
ABSTRACT_MAX_CHARS = 1920
ABSTRACT_MAX_WORDS = 200
IMAGE_MAX_PIXELS = 34_000_000

# Honesty rules (DESIGN 1.6, rule 5): banned unless a mapped claim covers the occurrence.
BANNED_WORDS: tuple[str, ...] = (
    "first",
    "only",
    "compliant",
    "digital twin",
    "accurately replicates",
    "no loss of functionality",
)

# Tokens whose presence means the document is not final.
PLACEHOLDER_MACRO = "sremuPlaceholder"
STUB_MACRO = "sremustub"

MODES = ("draft", "final")


# --------------------------------------------------------------------------- reporting
@dataclass
class Report:
    """Errors and warnings of one check, printed in a stable format."""

    tool: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    def extend(self, other: Report) -> None:
        self.errors += [f"[{other.tool}] {m}" for m in other.errors]
        self.warnings += [f"[{other.tool}] {m}" for m in other.warnings]
        self.notes += [f"[{other.tool}] {m}" for m in other.notes]

    def render(self) -> str:
        lines = [f"== {self.tool}: {'OK' if self.ok else 'FAILED'} "
                 f"({len(self.errors)} errors, {len(self.warnings)} warnings)"]
        lines += [f"  note: {m}" for m in self.notes]
        lines += [f"  warning: {m}" for m in self.warnings]
        lines += [f"  ERROR: {m}" for m in self.errors]
        return "\n".join(lines)

    def emit(self) -> int:
        out = self.render()
        try:
            print(out)
        except UnicodeEncodeError:  # Windows console code pages
            sys.stdout.buffer.write((out + "\n").encode("utf-8", "replace"))
        return 0 if self.ok else 1


# --------------------------------------------------------------------------- files
def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def strip_tex_comments(text: str) -> str:
    """Remove TeX comments (an unescaped % to end of line), keeping the line structure."""
    out = []
    for line in text.splitlines():
        m = re.search(r"(?<!\\)%", line)
        out.append(line[: m.start()] if m else line)
    return "\n".join(out)


def tex_source_files(root: Path = PAPER_ROOT) -> list[Path]:
    """Hand-written TeX sources of the paper (main.tex, macros and sections)."""
    files = [root / "main.tex", root / "sremu-macros.tex"]
    files += sorted((root / "sections").rglob("*.tex"))
    return [f for f in files if f.is_file()]


def generated_tex_files(root: Path = PAPER_ROOT) -> list[Path]:
    return sorted((root / "generated").rglob("*.tex")) if (root / "generated").is_dir() else []


_CITE_RE = re.compile(r"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{([^}]*)\}")


def cite_keys(text: str) -> list[str]:
    keys: list[str] = []
    for m in _CITE_RE.finditer(strip_tex_comments(text)):
        keys += [k.strip() for k in m.group(1).split(",") if k.strip()]
    return keys


_LABEL_RE = re.compile(r"\\label\{([^}]*)\}")


def tex_labels(text: str) -> set[str]:
    return set(_LABEL_RE.findall(strip_tex_comments(text)))


# --------------------------------------------------------------------------- BibTeX
@dataclass
class BibEntry:
    type: str
    key: str
    fields: dict[str, str]
    line: int


def _match_brace(text: str, start: int, open_ch: str = "{", close_ch: str = "}") -> int:
    """Index of the brace that closes text[start] (which must be open_ch)."""
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "\\":
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i
    raise ValueError(f"unbalanced braces from offset {start}")


def _parse_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    i, n = 0, len(body)
    while i < n:
        m = re.compile(r"\s*,?\s*([A-Za-z][A-Za-z0-9_\-]*)\s*=\s*").match(body, i)
        if not m:
            break
        name = m.group(1).lower()
        j = m.end()
        if j >= n:
            break
        if body[j] == "{":
            k = _match_brace(body, j)
            value = body[j + 1 : k]
            i = k + 1
        elif body[j] == '"':
            k = body.index('"', j + 1)
            value = body[j + 1 : k]
            i = k + 1
        else:
            m2 = re.compile(r"([^,\s]+)").match(body, j)
            value = m2.group(1) if m2 else ""
            i = m2.end() if m2 else j + 1
        if name in fields:
            raise ValueError(f"duplicate field {name!r}")
        fields[name] = " ".join(value.split())
    return fields


def parse_bib(text: str) -> list[BibEntry]:
    """Minimal BibTeX parser for curated files.

    Lines starting with ``%`` are treated as comments (the curated source file comments
    out unverified entries that way). @comment/@string/@preamble blocks are skipped.
    """
    lines = text.splitlines()
    clean = "\n".join("" if ln.lstrip().startswith("%") else ln for ln in lines)
    entries: list[BibEntry] = []
    for m in re.finditer(r"@([A-Za-z]+)\s*\{", clean):
        etype = m.group(1).lower()
        open_idx = m.end() - 1
        close_idx = _match_brace(clean, open_idx)
        if etype in ("comment", "string", "preamble"):
            continue
        body = clean[open_idx + 1 : close_idx]
        key, _, rest = body.partition(",")
        line = clean.count("\n", 0, m.start()) + 1
        entries.append(BibEntry(etype, key.strip(), _parse_fields(rest), line))
    return entries


def bib_by_key(entries: Iterable[BibEntry]) -> dict[str, BibEntry]:
    out: dict[str, BibEntry] = {}
    for e in entries:
        if e.key in out:
            raise ValueError(f"duplicate BibTeX key {e.key!r}")
        out[e.key] = e
    return out


def official_url(fields: dict[str, str]) -> str | None:
    """The entry's URL: the ``url`` field, else a ``\\url{...}`` inside ``howpublished``."""
    if fields.get("url"):
        return fields["url"].strip()
    m = re.search(r"\\url\{([^}]*)\}", fields.get("howpublished", ""))
    return m.group(1).strip() if m else None


def norm_title(s: str) -> str:
    s = re.sub(r"\\[a-zA-Z]+", " ", s)
    s = re.sub(r"[{}\\]", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


# --------------------------------------------------------------------------- spec ids and junit
SPEC_ID_RE = re.compile(
    r"^(PH-\d{2}|ST-\d{2}|RT-\d{2}|IO-\d{2}|VA-\d|G[1-8]|W[1-6]|I[1-8]|B[1-6]|"
    r"V-(?:TANG|LAB|CFO|WCL|FM)(?:-\d)?|E-[A-G]\d?)$"
)
FIGURE_ID_RE = re.compile(r"^F(?:[1-9]|1[0-2])$")


def spec_id_pattern(spec_id: str) -> re.Pattern[str]:
    """Regex that finds a spec id inside a pytest node id (``test_ph02_x`` matches PH-02).

    A numeric last part must not continue with a digit (PH-02 is not PH-20); an alphabetic
    last part must not continue with a letter (E-B is not the 'eb' of ``test_ebn0``).
    """
    parts = re.findall(r"[A-Za-z]+|\d+", spec_id)
    pieces = []
    for p in parts:
        pieces.append(f"0*{int(p)}(?!\\d)" if p.isdigit() else re.escape(p.lower()))
    tail = "" if parts[-1].isdigit() else "(?![a-z])"
    return re.compile(r"(?<![a-z0-9])" + r"[-_]?".join(pieces) + tail)


@dataclass(frozen=True)
class CaseResult:
    nodeid: str
    status: str  # passed | failed | skipped


def load_junit(paths: Iterable[Path]) -> list[CaseResult]:
    cases: list[CaseResult] = []
    for path in paths:
        root = ET.parse(path).getroot()
        for tc in root.iter("testcase"):
            nodeid = f"{tc.get('classname', '')}::{tc.get('name', '')}".lower()
            if tc.find("failure") is not None or tc.find("error") is not None:
                status = "failed"
            elif tc.find("skipped") is not None:
                status = "skipped"
            else:
                status = "passed"
            cases.append(CaseResult(nodeid, status))
    return cases


# Tests that are NOT evidence that sremu meets a spec id (DESIGN 10.1): the oracle
# self-tests of tests/oracles check the independent closed forms against
# physics_model_checks.json, and the tests/golden harness checks (golden integrity,
# MATLAB-vs-oracle) check the goldens. Only golden tests that run sremu (WP4 names them
# test_g<n>_sremu_*) count. Without this filter, GATE-PHY could look green while the
# implementation of PH-08/10/11 or ST-02 does not exist yet.
NON_EVIDENCE_PACKAGES = ("oracles",)
GOLDEN_PACKAGE = "golden"


def is_implementation_test(nodeid: str) -> bool:
    """False for oracle self-tests and golden-harness tests (see above). The package is
    read from the junit classname (``tests.oracles.test_x``; also ``oracles.test_x`` when
    pytest ran from inside tests/, or a path form)."""
    module, _, name = nodeid.lower().partition("::")
    packages = re.split(r"[./\\]", module)[:-1]
    if any(p in NON_EVIDENCE_PACKAGES for p in packages):
        return False
    return not (GOLDEN_PACKAGE in packages and "sremu" not in name)


def spec_id_status(
    spec_id: str,
    cases: Iterable[CaseResult],
    backends: Sequence[str] = (),
    reference: tuple[str, Sequence[str]] | None = None,
) -> str:
    """Status of a spec id in a junit, counting implementation tests only.

    'missing' (no implementation test carries the id), 'failed' (any hit failed),
    'skipped' (any hit skipped or xfailed: an open item is not green), else 'passed'.
    With ``backends`` (DESIGN 10.13: "PH-17...PH-22 on both backends"), every backend name
    must also appear in at least one passed node id of the spec id (pytest parametrization
    ids such as ``[numba_cpu]``); otherwise the status is 'missing <backend>'.
    With ``reference = (backend, others)`` (DESIGN 10.13: GATE-PHY's PH ids "on
    numpy_ref"), at least one passed node id must run on ``backend``: it names ``backend``
    or names none of ``others`` (an unparametrized physics test runs the numpy reference);
    otherwise the status is 'missing <backend>'. Failures on any backend still count.
    """
    pat = spec_id_pattern(spec_id)
    hits = [c for c in cases if pat.search(c.nodeid) and is_implementation_test(c.nodeid)]
    if not hits:
        return "missing"
    if any(c.status == "failed" for c in hits):
        return "failed"
    if any(c.status == "skipped" for c in hits):
        return "skipped"
    for b in backends:
        if not any(c.status == "passed" and b.lower() in c.nodeid for c in hits):
            return f"missing {b}"
    if reference is not None:
        ref, others = reference[0].lower(), [o.lower() for o in reference[1]]
        if not any(
            c.status == "passed"
            and (ref in c.nodeid or not any(o in c.nodeid for o in others))
            for c in hits
        ):
            return f"missing {reference[0]}"
    return "passed"


# --------------------------------------------------------------------------- junit provenance
JUNIT_COMMIT_KEY = "sremu_commit"
JUNIT_DIRTY_KEY = "sremu_dirty"


def junit_provenance(path: Path) -> dict[str, str]:
    """Commit metadata of one junit file (DESIGN 10.13: "the junit XML of the pinned commit").

    Read from the testsuite-level ``<properties>`` (pytest ``record_testsuite_property``) or,
    failing that, from a sidecar ``<stem>.meta.json``. Test-case properties are ignored.
    """
    props: dict[str, str] = {}
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    for suite in suites:
        block = suite.find("properties")
        for p in [] if block is None else block.findall("property"):
            props.setdefault(p.get("name", ""), p.get("value", ""))
    sidecar = path.with_suffix(".meta.json")
    if sidecar.is_file():
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
        for key in (JUNIT_COMMIT_KEY, JUNIT_DIRTY_KEY):
            if key in meta:
                val = meta[key]
                props.setdefault(key, str(val).lower() if isinstance(val, bool) else str(val))
    return props


def junit_provenance_errors(paths: Iterable[Path], commit: str) -> list[str]:
    """Errors unless every junit file records the pinned sremu commit and a clean tree."""
    errors: list[str] = []
    for path in paths:
        props = junit_provenance(path)
        got = props.get(JUNIT_COMMIT_KEY)
        if not got:
            errors.append(f"junit {path.name}: no {JUNIT_COMMIT_KEY} property (unknown commit)")
        elif got != commit:
            errors.append(f"junit {path.name}: from sremu {got}, but sremu.lock pins {commit}")
        if props.get(JUNIT_DIRTY_KEY, "").lower() != "false":
            errors.append(f"junit {path.name}: {JUNIT_DIRTY_KEY} is "
                          f"{props.get(JUNIT_DIRTY_KEY)!r}, not 'false' (clean tree required)")
    return errors


# --------------------------------------------------------------------------- numbers
@dataclass(frozen=True)
class NumberMacro:
    macro: str
    kind: str  # number | text
    description: str
    source_type: str
    source_id: str
    unit: str | None
    value: str | None  # set only for definitional / data constants
    max_chars: int


NUMBER_SOURCE_TYPES = ("test", "figure", "study", "benchmark", "experiment", "definition", "data")


def load_numbers_spec(path: Path | None = None) -> list[NumberMacro]:
    path = path or PAPER_ROOT / "numbers.yaml"
    raw = read_yaml(path)
    out: list[NumberMacro] = []
    seen: set[str] = set()
    for item in raw["macros"]:
        name = item["macro"]
        if not re.fullmatch(r"[A-Za-z]+", name):
            raise ValueError(f"macro name {name!r} must be letters only")
        if name in seen:
            raise ValueError(f"duplicate macro {name!r}")
        seen.add(name)
        src = item["source"]
        if src["type"] not in NUMBER_SOURCE_TYPES:
            raise ValueError(f"{name}: unknown source type {src['type']!r}")
        value = item.get("value")
        if value is not None and src["type"] not in ("definition", "data"):
            raise ValueError(f"{name}: a fixed value is allowed only for definition/data sources")
        kind = item.get("kind", "number")
        out.append(
            NumberMacro(
                macro=name,
                kind=kind,
                description=item["description"],
                source_type=src["type"],
                source_id=str(src["id"]),
                unit=item.get("unit"),
                value=None if value is None else str(value),
                max_chars=int(item.get("max_chars", 8 if kind == "number" else 80)),
            )
        )
    return out


def load_results(paper_root: Path) -> tuple[dict[str, str], str | None]:
    """Pipeline results written by the study/benchmark stages, and the sremu commit they
    were produced at. Format of generated/results.json:
    {"sremu_commit": "<40-hex>", "values": {"<macro>": "<LaTeX value>", ...}}."""
    path = paper_root / "generated" / "results.json"
    if not path.is_file():
        return {}, None
    doc = json.loads(read_text(path))
    if not isinstance(doc, dict) or not isinstance(doc.get("values"), dict):
        raise ValueError("generated/results.json must be {sremu_commit, values: {macro: value}}")
    values = {str(k): str(v) for k, v in doc["values"].items()}
    return values, (str(doc["sremu_commit"]) if doc.get("sremu_commit") else None)


def render_numbers_tex(spec: list[NumberMacro], results: dict[str, str] | None = None) -> str:
    """generated/numbers.tex: one \\newcommand per macro; unresolved values are placeholders."""
    results = results or {}
    lines = [
        "% generated/numbers.tex -- GENERATED by tools/build_paper.py from numbers.yaml.",
        "% Do not edit by hand. Unresolved values expand to \\sremuPlaceholder, which prints",
        "% a bold ?? in draft builds and stops a final build.",
    ]
    for m in spec:
        val = m.value if m.value is not None else results.get(m.macro)
        body = val if val is not None else f"\\{PLACEHOLDER_MACRO}{{{m.macro}}}"
        lines.append(f"\\newcommand{{\\{m.macro}}}{{{body}}}% {m.source_type}:{m.source_id}")
    return "\n".join(lines) + "\n"


def placeholder_count(text: str) -> int:
    return len(re.findall(r"\\" + PLACEHOLDER_MACRO + r"\b", strip_tex_comments(text)))


def stub_count(text: str) -> int:
    return len(re.findall(r"\\" + STUB_MACRO + r"\b", strip_tex_comments(text)))
