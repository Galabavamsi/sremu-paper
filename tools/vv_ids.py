"""vv_ids.py -- verification identifiers (Appendix G) and the provenance summary (Appendix H).

Called by gen_results.py. Two parts:

* ``ids_tables``: the tables of every verification identifier the paper cites
  (``generated/tables/tableA_ids.tex``: Tables ``tab:ids-ph`` and ``tab:ids-rt``), rendered
  from ``verification_ids.yaml``. The yaml is a compact transcription; the facts stay in the
  emulator: every number of a row must appear in sremu ``docs/DESIGN.md`` (the lines that name
  the identifier, lead amendments included), in ``tests/oracles/tables.py`` (oracle values and
  tolerances of that spec id) or in the docstrings of the test modules that carry it. An
  identifier cited in the paper without a row, a row that did not pass in the test record
  (unless marked experimental or study) and the words legacy/v1/v2 (framing D24) are refused.
  It also writes ``numVaFourRelTol`` (Section III-B), read from the VA-4 test itself.
* ``provenance_values``: the commit summary of Appendix H, grouped from the rows of the
  provenance table (Appendix I), so that text and table cannot disagree; and the check of the
  regeneration job's V-LAB file comparison (sweeps/results/V-LAB-provenance).

Standard library plus PyYAML (through paper_common); never imports sremu, never runs git.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paper_common import (
    is_implementation_test,
    load_numbers_spec,
    read_yaml,
    spec_id_pattern,
    spec_id_status,
)

IDS_FILE = "verification_ids.yaml"
IDS_TABLE = "tableA_ids.tex"
VLAB_CHECK = "sweeps/results/V-LAB-provenance/vlab_check_regen_20261011T235822Z.json"
VLAB_META = "sweeps/results/V-LAB-provenance/meta.json"

# identifiers in the paper text: singles and ranges (PH-02--07, PH-01--PH-11, G1--3, VA-3--VA-5)
_ID_SINGLE = re.compile(r"(?<![\w-])(?:(?:PH|RT|IO|ST)-\d{2}|VA-\d|V-CFO-\d|G[1-8])(?![\w])")
_ID_RANGE = re.compile(r"(?<![\w-])(PH|RT|IO|ST)-(\d{2})--(?:(?:PH|RT|IO|ST)-)?(\d{2})(?!\d)")
_G_RANGE = re.compile(r"(?<![\w-])G([1-8])--G?([1-8])(?!\d)")
_VA_RANGE = re.compile(r"(?<![\w-])VA-(\d)--(?:VA-)?(\d)(?!\d)")
_BANNED = re.compile(r"\b(legacy|v1|v2)\b", re.IGNORECASE)
_NUM = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?:[eE]([-+]?\d+))?")
_SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")
TIERS_A = ("PH", "G")
TIERS_B = ("RT", "IO", "ST", "VA", "V-CFO")


def _tier(ident: str) -> str:
    return "V-CFO" if ident.startswith("V-CFO") else re.match(r"[A-Z]+", ident).group(0)


def _strip_comments(tex: str) -> str:
    return "\n".join(re.sub(r"(?<!\\)%.*", "", line) for line in tex.splitlines())


def cited_ids(paper_root: Path) -> dict[str, list[str]]:
    """Identifier -> files that cite it (sections, hand tables, generated tables except the
    identifier tables themselves); ranges are expanded."""
    files = sorted((paper_root / "sections").rglob("*.tex")) + sorted(
        p for p in (paper_root / "generated" / "tables").glob("*.tex") if p.name != IDS_TABLE)
    out: dict[str, list[str]] = {}
    for f in files:
        tex = _strip_comments(f.read_text(encoding="utf-8")).replace(r"\_", "_")
        found: set[str] = set()
        for m in _ID_RANGE.finditer(tex):
            found |= {f"{m.group(1)}-{i:02d}" for i in range(int(m.group(2)), int(m.group(3)) + 1)}
        for m in _G_RANGE.finditer(tex):
            found |= {f"G{i}" for i in range(int(m.group(1)), int(m.group(2)) + 1)}
        for m in _VA_RANGE.finditer(tex):
            found |= {f"VA-{i}" for i in range(int(m.group(1)), int(m.group(2)) + 1)}
        found |= set(_ID_SINGLE.findall(tex))
        for i in found:
            out.setdefault(i, []).append(f.relative_to(paper_root).as_posix())
    return out


# --------------------------------------------------------------------------- sources
def _norm(text: str) -> str:
    text = text.replace("−", "-").replace("–", "-")
    return re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁻]+", lambda m: "^" + m.group(0).translate(_SUP), text)


def numbers(text: str) -> list[float]:
    """Unsigned numbers of ``text`` (1e-12 -> 1e-12; also the mantissa of an e-number)."""
    out: list[float] = []
    for m in _NUM.finditer(_norm(text)):
        out.append(float(m.group(1)) * (10.0 ** int(m.group(2)) if m.group(2) else 1.0))
        if m.group(2):
            out.append(float(m.group(1)))
    return out


def _docstrings(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = [ast.get_docstring(tree) or ""]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            docs.append(ast.get_docstring(node) or "")
    return "\n".join(docs)


@dataclass(frozen=True)
class _Oracle:
    spec_id: str
    value: float
    tol: float
    unit: str
    what: str


def _arith(node: ast.AST) -> Any:
    """Literal or +-*/ arithmetic of literals (the oracle table writes e.g. 0.15 - 0.123)."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _arith(node.operand)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult,
                                                            ast.Div)):
        a, b = _arith(node.left), _arith(node.right)
        return {ast.Add: a + b, ast.Sub: a - b, ast.Mult: a * b,
                ast.Div: a / b if b else math.nan}[type(node.op)]
    raise ValueError(ast.dump(node))


def _oracle_table(sremu_root: Path) -> dict[str, _Oracle]:
    """``ORACLE`` of sremu tests/oracles/tables.py, parsed (not imported): every entry is a
    call ``_v(spec_id, value, tol, unit, what)`` of literals."""
    path = sremu_root / "tests" / "oracles" / "tables.py"
    out: dict[str, _Oracle] = {}
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.AnnAssign | ast.Assign) and isinstance(node.value, ast.Dict):
            targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            if not any(isinstance(t, ast.Name) and t.id == "ORACLE" for t in targets):
                continue
            for k, v in zip(node.value.keys, node.value.values, strict=True):
                if not (isinstance(k, ast.Constant) and isinstance(v, ast.Call)):
                    raise SystemExit(f"vv_ids: unexpected ORACLE entry in {path}")
                out[str(k.value)] = _Oracle(*[_arith(a) for a in v.args])
    if not out:
        raise SystemExit(f"vv_ids: no ORACLE table in {path}")
    return out


def source_texts(sremu_root: Path) -> tuple[str, dict[str, Any], dict[str, list[Path]]]:
    design = (sremu_root / "docs" / "DESIGN.md").read_text(encoding="utf-8")
    oracle = _oracle_table(sremu_root)
    by_token: dict[str, list[Path]] = {}
    for p in sorted((sremu_root / "tests").rglob("test_*.py")):
        for tok in p.stem.split("_")[1:]:
            by_token.setdefault(tok, []).append(p)
    return design, oracle, by_token


def _id_source(ident: str, row: dict[str, Any], design: str, oracle: dict[str, Any],
               by_token: dict[str, list[Path]], sremu_root: Path) -> str:
    pat = re.compile(r"(?<![\w-])" + re.escape(ident) + r"(?![\w])")
    parts = [line for line in design.splitlines() if pat.search(line)]
    parts += [f"{o.value} {o.tol} {o.unit} {o.what}" for o in oracle.values()
              if o.spec_id == ident]
    compact = ident.replace("-", "").lower()
    tests = list(by_token.get(compact, [])) + [sremu_root / t for t in row.get("tests") or []]
    parts += [_docstrings(p) for p in tests]
    return "\n".join(parts)


def _close(x: float, allowed: list[float]) -> bool:
    return any(math.isclose(x, a, rel_tol=1e-9, abs_tol=0.0) for a in allowed)


# --------------------------------------------------------------------------- rendering
def tex(text: str) -> str:
    """The yaml text conventions as LaTeX ($...$ kept; see verification_ids.yaml)."""
    def conv(seg: str) -> str:
        seg = seg.replace("%", r"\%")

        def sci(m: re.Match[str]) -> str:
            mant, exp = m.group(1), int(m.group(2))
            return f"${'' if mant == '1' else mant + r'\times'}10^{{{exp}}}$"

        seg = re.sub(r"(?<![\w.])(\d+(?:\.\d+)?)e([-+]?\d+)(?![\w.])", sci, seg)
        seg = seg.replace("+-", r"$\pm$").replace("<=", r"$\le$").replace(">=", r"$\ge$")
        return re.sub(r"\s*\bdeg\b", r"$^\\circ$", seg)

    pieces = re.split(r"(\$[^$]*\$)", text)
    out = "".join(p if p.startswith("$") and p.endswith("$") and len(p) > 1 else conv(p)
                  for p in pieces)
    return out.replace("$$", "")  # adjacent math segments (never display math)


def _table(label: str, caption: str, rows: list[dict[str, str]]) -> list[str]:
    lines = [
        r"\begin{table*}[t]",
        r"\caption{" + caption + "}",
        r"\label{" + label + "}",
        r"\centering",
        r"\scriptsize",
        r"\setlength{\tabcolsep}{3pt}",
        r"\renewcommand{\arraystretch}{1.05}",
        r"\begin{tabular}{@{}>{\raggedright\arraybackslash}p{0.42in}"
        r">{\raggedright\arraybackslash}p{1.95in}>{\raggedright\arraybackslash}p{2.68in}"
        r">{\raggedright\arraybackslash}p{1.45in}>{\raggedleft\arraybackslash}p{0.3in}@{}}",
        r"\toprule",
        r"Id & Property & Oracle or reference & Tolerance & Tests \\",
    ]
    tier = None
    for r in rows:
        if _tier(r["id"]) != tier:
            tier = _tier(r["id"])
            lines.append(r"\midrule")
        lines.append(" & ".join([r["id"], tex(r["property"]), tex(r["oracle"]), tex(r["tol"]),
                                 r["tests"]]) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table*}"]
    return lines


# --------------------------------------------------------------------------- identifier tables
def va4_rel_tol(sremu_root: Path) -> str:
    """The relative tolerance of VA-4, read from its test (the agreement contract of DESIGN
    4.11), as LaTeX."""
    src = (sremu_root / "tests" / "runtime" / "test_va4_batch.py").read_text(encoding="utf-8")
    m = re.findall(r"abs\(iq\[r\] - b\) <= ([0-9.]+e-\d+) \* b", src)
    if len(m) != 1:
        raise SystemExit("vv_ids: cannot read the VA-4 relative tolerance from its test")
    mant, exp = m[0].split("e")
    mant = mant.rstrip("0").rstrip(".") if "." in mant else mant
    return (r"\ensuremath{10^{" + str(int(exp)) + "}}" if mant == "1"
            else r"\ensuremath{" + mant + r"\times10^{" + str(int(exp)) + "}}")


def ids_tables(paper_root: Path, sremu_root: Path, cases: list[Any], commit: str
               ) -> tuple[dict[str, str], dict[str, str]]:
    spec = read_yaml(paper_root / IDS_FILE) or {}
    rows_in = [{k: (v if k in ("tests", "refs") else str(v)) for k, v in r.items()}
               for r in spec.get("ids") or []]
    by_id = {r["id"]: r for r in rows_in}
    if len(by_id) != len(rows_in):
        raise SystemExit("vv_ids: duplicate identifier in verification_ids.yaml")
    design, oracle, by_token = source_texts(sremu_root)
    errors: list[str] = []
    cited = cited_ids(paper_root)
    for ident, where in sorted(cited.items()):
        if ident not in by_id:
            errors.append(f"{ident} is cited ({', '.join(where)}) but has no row in {IDS_FILE}")
    out_rows: list[dict[str, str]] = []
    for r in rows_in:
        ident = r["id"]
        text = " ".join(r.get(k, "") for k in ("property", "oracle", "tol"))
        if _BANNED.search(text):
            errors.append(f"{ident}: legacy/v1/v2 wording (framing D24)")
        src = _id_source(ident, r, design, oracle, by_token, sremu_root)
        for ref in r.get("refs") or []:
            src += "\n" + _id_source(ref, by_id.get(ref, {}), design, oracle, by_token,
                                     sremu_root)
        if not src.strip():
            errors.append(f"{ident}: no source in DESIGN.md, the oracle table or the tests")
        allowed = numbers(src)
        bad = [x for x in numbers(text) if not _close(x, allowed)]
        if bad:
            errors.append(f"{ident}: numbers {bad} appear in none of its sources")
        record = r.get("record")
        pat = spec_id_pattern(ident)
        n = sum(1 for c in cases if is_implementation_test(c.nodeid) and pat.search(c.nodeid))
        status = spec_id_status(ident, cases)
        if record == "experimental":
            tests = "exp."
        elif record == "study":
            tests = "study"
        elif status != "passed":
            errors.append(f"{ident}: status {status} in the test record")
            tests = status
        else:
            tests = str(n)
        out_rows.append({"id": ident, "property": r["property"], "oracle": r["oracle"],
                         "tol": r["tol"], "tests": tests})
    if errors:
        raise SystemExit("vv_ids: " + "; ".join(errors))
    order = {t: i for i, t in enumerate(TIERS_A + TIERS_B)}
    out_rows.sort(key=lambda r: order[_tier(r["id"])])
    a = [r for r in out_rows if _tier(r["id"]) in TIERS_A]
    b = [r for r in out_rows if _tier(r["id"]) in TIERS_B]
    common = (r" Tolerances in dB, degrees, Hz or samples are absolute; rel.: relative to the"
              r" reference magnitude; bitwise: identical samples. Tests: implementation tests"
              r" in the test record of commit " + commit[:7] + r" that carry the"
              r" identifier.")
    cap_a = (r"Physics identifiers (PH) and MATLAB code-to-code references (G): the property"
             r" checked, its oracle or reference, and the acceptance tolerance, transcribed from"
             r" the emulator's specification and test modules (the pipeline requires each number"
             r" to appear in them)." + common + r" exp.: experimental tier, outside the record.")
    cap_b = (r"Runtime (RT), interface (IO), statistical (ST), engine-path consistency (VA) and"
             r" frequency-offset (V-CFO) identifiers, as in Table~\ref{tab:ids-ph}." + common
             + r" study: accepted in the study record (Appendix~\ref{app:provenance}), not in"
             r" the test record.")
    lines = ["% GENERATED by paper/tools/vv_ids.py (via gen_results.py) from",
             "% verification_ids.yaml, checked against sremu docs/DESIGN.md,",
             "% tests/oracles/tables.py, the test modules' docstrings and the test record.",
             "% Do not edit."]
    lines += _table("tab:ids-ph", cap_a, a) + _table("tab:ids-rt", cap_b, b)
    values = {"numVaFourRelTol": va4_rel_tol(sremu_root)}
    return values, {IDS_TABLE: "\n".join(lines) + "\n"}


# --------------------------------------------------------------------------- provenance summary
def _short(result: str) -> str:
    """A result name of the provenance table as it reads inside a sentence."""
    name = re.sub(r" \((aggregate|rows)\)$", "", result)
    name = re.sub(r"^([^,(]+), ([^,()]+)$", r"\1 (\2)", name)  # "E-B, arc geometry"
    if re.match(r"[A-Z][a-z]", name):  # a word ("Test record"), not a label ("V-LAB", "BER")
        name = name[0].lower() + name[1:]
    return "the test record" if name == "test record" else name


def _join(items: list[str], sep: str = ", ") -> str:
    return items[0] if len(items) == 1 else sep.join(items[:-1]) + " and " + items[-1]


def provenance_values(paper_root: Path, rows: list[dict[str, str]],
                      junit_meta: dict[str, Any]) -> dict[str, str]:
    """Commit summary of Appendix H from the provenance rows (Table tab:provenance)."""
    regen = str(junit_meta.get("sremu_commit") or "")[:7]
    if not regen or junit_meta.get("sremu_dirty") is not False:
        raise SystemExit("vv_ids: the test record has no clean commit; Appendix H names it")
    at = [_short(r["result"]) for r in rows if r["commit"] == regen and r["tree"] == "clean"]
    groups: dict[tuple[str, str], list[str]] = {}
    for r in rows:
        if r["commit"] == regen and r["tree"] == "clean":
            continue
        groups.setdefault((r["commit"], r["tree"]), []).append(_short(r["result"]))
    tree_word = {"clean": "clean", "dirty": "uncommitted changes recorded",
                 "flagged": "dirty flag recorded, file tree equal to the commit's",
                 "n/r": "tree state not recorded"}
    other = []
    for (commit, tree), names in groups.items():
        if commit == "n/r":
            other.append(f"{_join(names)} (commit not recorded)")
        else:
            other.append(f"{_join(names)} ({commit}, {tree_word.get(tree, tree)})")
    # The regeneration job compared the V-LAB files with the locked analysis commit.
    meta = json.loads((paper_root / VLAB_META).read_text(encoding="utf-8"))
    entry = next(f for f in meta["files"] if f["path"] == Path(VLAB_CHECK).name)
    raw = (paper_root / VLAB_CHECK).read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
        raise SystemExit("vv_ids: the V-LAB check record differs from its recorded hash")
    chk = json.loads(raw)
    if not (chk.get("ok") and chk.get("digest_equals_lock") and chk.get("lock_equals_run")
            and chk.get("files_differing_from_7a63287") == []
            and chk.get("files_not_at_7a63287") == [] and chk.get("run_git_dirty") is False):
        raise SystemExit("vv_ids: Appendix H says the V-LAB files at the regeneration commit "
                         "equal the locked analysis commit; the job's check does not show it")
    if entry["checked_commit"][:7] != regen:
        raise SystemExit("vv_ids: the V-LAB check was made at another commit than the test record")
    lab_code = {m.macro: m.value for m in load_numbers_spec(paper_root / "numbers.yaml")
                }.get("txtLabCodeCommit")
    if not lab_code or not str(chk["run_git_commit"]).startswith(str(lab_code)):
        raise SystemExit("vv_ids: the V-LAB check names another run commit than txtLabCodeCommit")
    return {
        "txtProvRegenCommit": regen,
        "txtProvRegenHost": str(junit_meta.get("host") or "host not recorded"),
        "txtProvRegenResults": _join(at),
        "txtProvOtherCommits": "; ".join(other),
    }
