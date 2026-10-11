"""build_paper.py -- one-command paper build (DESIGN 13.5; WP12). Python, not make.

Steps
  1. pin:     sremu.lock equals the sremu HEAD, both trees are clean, and the interpreter
              imports sremu from that checkout                               (final only)
  2. gates:   GATE-PHY and GATE-RT green in the pinned junit (gates.yaml); the junit must
              record the pinned commit and a clean tree; PH-17..22 pass on both backends
                                                                             (final refuses)
  3. figures: registry entries; each placed once with its registered width, label and data
              classes in the caption; content key = sha256(commit, sweep, inputs, script);
              stale entries rerun only in final mode after the gates are green; a figure
              is current only if its meta carries the registry data classes and the commit.
              TikZ diagrams (F1, F2: no data, no data classes) are drawn with latexmk in
              both modes whenever LaTeX runs; their key is sha256(source, style inputs)
  4./5. generated/numbers.tex from numbers.yaml (+ generated/results.json, + `code` values
              read from the installed sremu by tools/resolve_code_numbers.py), Table II
              from sremu docs/model_card.yaml (sremu's own generator) and Table III from
              sremu.api.metadata / sremu.compat (tools/gen_tables.py)
  6. checks:  check_claims, check_refs, check_arxiv; final refuses while any placeholder,
              stub, missing figure, pending claim or open human gate remains
  7. latexmk, then check_arxiv --post-build (.bbl present) and the LaTeX log

Modes
  draft  never runs git, never runs figure scripts (TikZ diagrams are drawn), compiles with
         ?? placeholders and grey stubs, and reports everything outstanding as notes. Exit 0
         unless a check fails. Code values and Table III need an importable sremu; without
         one they stay placeholders / the committed table (warnings).
  final  refuses (non-zero exit, no PDF) on any outstanding item.

Usage: python tools/build_paper.py [--mode draft|final] [--profile fast|full]
                                   [--junit FILE ...] [--sremu-root DIR] [--no-latex]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_arxiv
import check_claims
import check_refs
from paper_common import (
    FIGURE_ID_RE,
    MODES,
    PAPER_ROOT,
    SPEC_ID_RE,
    CaseResult,
    Report,
    code_number_requests,
    junit_provenance_errors,
    load_junit,
    load_numbers_spec,
    load_results,
    placeholder_count,
    read_text,
    read_yaml,
    render_numbers_tex,
    spec_id_status,
    strip_tex_comments,
    stub_count,
    tex_source_files,
)

DATA_CLASSES = {
    "MEAS", "DIG", "REPORTED", "ASSUMED", "ENG", "SIM-sremu", "ANALYTIC", "MATLAB",
}
REGISTRY_FIELDS = ("title", "width", "source", "script", "sweep", "inputs", "evidence",
                   "data_classes", "outputs", "owner", "status")
EXPECTED_FIGURES = [f"F{i}" for i in range(1, 13)]
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PROFILES = ("fast", "full")

Runner = Callable[[list[str], Path], tuple[int, str]]


def default_runner(cmd: list[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    return proc.returncode, proc.stdout


@dataclass
class GitProbe:
    """All git access of the pipeline goes through here (final mode only)."""

    runner: Runner = default_runner

    def head(self, repo: Path) -> str | None:
        rc, out = self.runner(["git", "rev-parse", "HEAD"], repo)
        return out.strip() if rc == 0 else None

    def is_clean(self, repo: Path) -> bool | None:
        rc, out = self.runner(["git", "status", "--porcelain"], repo)
        return None if rc != 0 else out.strip() == ""


# --------------------------------------------------------------------------- step 1
def read_lock(paper_root: Path) -> dict:
    lock = read_yaml(paper_root / "sremu.lock") or {}
    return {"repo": str(lock.get("repo", "../sremu")), "commit": str(lock.get("commit", ""))}


INSTALLED_SREMU_PROBE = "import sremu, sys; sys.stdout.write(sremu.__file__)"


def check_installed_sremu(sremu_root: Path, runner: Runner, rep: Report) -> None:
    """DESIGN 13.5 step 1: the sremu that figure scripts import (this interpreter) must be
    the pinned checkout, not another installed copy."""
    rc, out = runner([sys.executable, "-c", INSTALLED_SREMU_PROBE], sremu_root)
    if rc != 0 or not out.strip():
        rep.error(f"pin: `import sremu` fails in {sys.executable}")
        return
    installed = Path(out.strip()).resolve()
    if not installed.is_relative_to(sremu_root.resolve()):
        rep.error(f"pin: installed sremu ({installed}) is not the pinned checkout {sremu_root}")


def step_pin(paper_root: Path, sremu_root: Path, mode: str, git: GitProbe, rep: Report,
             runner: Runner = default_runner) -> str:
    lock = read_lock(paper_root)
    commit = lock["commit"]
    if mode == "draft":
        rep.note(f"pin: skipped in draft mode (no git calls); sremu.lock commit = {commit}")
        return commit
    if not SHA_RE.match(commit):
        rep.error(f"pin: sremu.lock commit {commit!r} is not a pinned 40-hex sha")
        return commit
    head = git.head(sremu_root)
    if head != commit:
        rep.error(f"pin: sremu HEAD {head} != sremu.lock {commit}")
    for name, repo in (("sremu", sremu_root), ("paper", paper_root)):
        clean = git.is_clean(repo)
        if clean is not True:
            rep.error(f"pin: {name} working tree is {'dirty' if clean is False else 'unreadable'}")
    check_installed_sremu(sremu_root, runner, rep)
    return commit


# --------------------------------------------------------------------------- step 2
def load_gates(paper_root: Path) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """(gate -> ids, id -> backends that must each pass) from gates.yaml."""
    doc = read_yaml(paper_root / "gates.yaml") or {}
    gates = {str(g): [str(i) for i in ids] for g, ids in (doc.get("gates") or {}).items()}
    rule = doc.get("backend_rule") or {}
    backends = [str(b) for b in rule.get("backends") or []]
    per_id = {str(i): backends for i in rule.get("ids") or []}
    return gates, per_id


def load_reference_rule(paper_root: Path) -> dict[str, tuple[str, str, list[str]]]:
    """gate -> (id prefix, reference backend, other backends) from gates.yaml's
    ``reference_rule`` (DESIGN 10.13: GATE-PHY's PH ids are evaluated "on numpy_ref")."""
    doc = read_yaml(paper_root / "gates.yaml") or {}
    out: dict[str, tuple[str, str, list[str]]] = {}
    for rule in doc.get("reference_rule") or []:
        others = [str(b) for b in rule.get("others") or []]
        out[str(rule["gate"])] = (str(rule["prefix"]), str(rule["backend"]), others)
    return out


def gate_status(paper_root: Path, cases: list[CaseResult] | None) -> dict[str, dict[str, str]]:
    gates, per_id = load_gates(paper_root)
    ref_rules = load_reference_rule(paper_root)
    out: dict[str, dict[str, str]] = {}
    for gate, ids in gates.items():
        rule = ref_rules.get(gate)
        statuses: dict[str, str] = {}
        for i in ids:
            if cases is None:
                statuses[i] = "no junit"
                continue
            ref = (rule[1], rule[2]) if rule is not None and i.startswith(rule[0]) else None
            statuses[i] = spec_id_status(i, cases, per_id.get(i, ()), reference=ref)
        out[gate] = statuses
    return out


def step_gates(paper_root: Path, junit: list[Path], mode: str, rep: Report,
               commit: str = "") -> bool:
    cases = load_junit(junit) if junit else None
    green = True
    gates, _ = load_gates(paper_root)
    if sorted(gates) != ["GATE-PHY", "GATE-RT"] or not all(gates.values()):
        rep.error(f"gates.yaml must define non-empty GATE-PHY and GATE-RT, got {sorted(gates)}")
        green = False  # fail closed: a malformed gate file never counts as green
    if mode == "final":
        for msg in junit_provenance_errors(junit, commit):
            green = False
            rep.error(f"gates: {msg}")
    for gate, statuses in gate_status(paper_root, cases).items():
        bad = {i: s for i, s in statuses.items() if s != "passed"}
        rep.note(f"{gate}: {len(statuses) - len(bad)}/{len(statuses)} ids passed")
        if bad:
            green = False
            msg = f"{gate} not green: " + ", ".join(f"{i} {s}" for i, s in list(bad.items())[:8])
            msg += " ..." if len(bad) > 8 else ""
            (rep.error if mode == "final" else rep.note)(msg)
    return green


# --------------------------------------------------------------------------- step 3
def load_registry(paper_root: Path) -> dict:
    return (read_yaml(paper_root / "figures" / "registry.yaml") or {}).get("figures") or {}


def validate_registry(reg: dict, rep: Report) -> None:
    if sorted(reg) != sorted(EXPECTED_FIGURES):
        rep.error(f"registry must define exactly F1..F12, got {sorted(reg)}")
    for fid, e in reg.items():
        if not FIGURE_ID_RE.match(fid):
            rep.error(f"registry: bad figure id {fid}")
            continue
        missing = [k for k in REGISTRY_FIELDS if k not in e]
        if missing:
            rep.error(f"registry {fid}: missing fields {missing}")
            continue
        if e["width"] not in ("single", "double"):
            rep.error(f"registry {fid}: width must be single or double")
        if e["source"] not in ("tikz", "script"):
            rep.error(f"registry {fid}: source must be tikz or script")
        if e["status"] not in ("pending", "ready"):
            rep.error(f"registry {fid}: status must be pending or ready")
        if e["status"] == "ready" and e["source"] == "script" and not e["script"]:
            rep.error(f"registry {fid}: ready but no script")
        if e["source"] == "tikz" and e["script"] and not str(e["script"]).endswith(".tex"):
            rep.error(f"registry {fid}: a TikZ figure's script must be its .tex source")
        if e["source"] == "tikz" and e["data_classes"]:
            rep.error(f"registry {fid}: a TikZ diagram carries no data classes")
        unknown = set(e["data_classes"]) - DATA_CLASSES
        if unknown:
            rep.error(f"registry {fid}: unknown data classes {sorted(unknown)}")
        if e["source"] == "script" and not e["data_classes"]:
            rep.error(f"registry {fid}: a data figure needs data classes")
        for ev in e["evidence"]:
            if not SPEC_ID_RE.match(str(ev)):
                rep.error(f"registry {fid}: evidence {ev!r} is not a spec id")
        if f"figures/out/{fid}.pdf" not in e["outputs"]:
            rep.error(f"registry {fid}: outputs must include figures/out/{fid}.pdf")


_FIGURE_CALL = re.compile(r"\\sremufigure\{(F\d+)\}\{[0-9.]*\\(columnwidth|textwidth)\}\{")


def _brace_arg(text: str, open_idx: int) -> tuple[str, int]:
    """Contents of the brace group opening at text[open_idx] and the index after it."""
    depth = 0
    for k in range(open_idx, len(text)):
        if text[k] == "{" and text[k - 1] != "\\":
            depth += 1
        elif text[k] == "}" and text[k - 1] != "\\":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1 : k], k + 1
    raise ValueError(f"unbalanced braces at offset {open_idx}")


def figure_calls(paper_root: Path) -> dict[str, list[tuple[str, str, str, str]]]:
    """Fid -> [(file, width macro, caption, label)] for every \\sremufigure in the sections."""
    calls: dict[str, list[tuple[str, str, str, str]]] = {}
    for f in tex_source_files(paper_root):
        if f.name == "sremu-macros.tex":
            continue
        text = strip_tex_comments(read_text(f))
        for m in _FIGURE_CALL.finditer(text):
            caption, nxt = _brace_arg(text, m.end() - 1)
            label, _ = _brace_arg(text, nxt)
            rel = f.relative_to(paper_root).as_posix()
            calls.setdefault(m.group(1), []).append((rel, m.group(2), caption, label))
    return calls


def check_figure_sources(paper_root: Path, reg: dict, rep: Report) -> None:
    """DESIGN 13.4: each registered figure is placed once, at its registered width, with
    label fig:<Fid>, and its caption names every data class of the registry entry."""
    calls = figure_calls(paper_root)
    for fid in sorted(set(calls) - set(reg)):
        rep.error(f"figure {fid} is placed in the text but not registered")
    for fid, e in reg.items():
        placed = calls.get(fid, [])
        if len(placed) != 1:
            rep.error(f"figure {fid}: placed {len(placed)} times in the sections (expected 1)")
            continue
        rel, width, caption, label = placed[0]
        want = "columnwidth" if e.get("width") == "single" else "textwidth"
        if width != want:
            rep.error(f"figure {fid} ({rel}): width \\{width}, registry says {e.get('width')}")
        if label != f"fig:{fid}":
            rep.error(f"figure {fid} ({rel}): label {label!r}, expected 'fig:{fid}'")
        flat = " ".join(caption.split())
        for dc in e.get("data_classes") or []:
            if not re.search(r"(?<![\w-])" + re.escape(dc) + r"(?![\w-])", flat):
                rep.error(f"figure {fid} ({rel}): caption does not name data class {dc}")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def content_key(paper_root: Path, entry: dict, commit: str) -> str | None:
    """sha256 over (sremu commit, sweep spec, input hashes, script); None without a script."""
    if not entry.get("script"):
        return None
    script = paper_root / entry["script"]
    sweep = paper_root / entry["sweep"] if entry.get("sweep") else None
    doc = {
        "commit": commit,
        "script": sha256_file(script),
        "sweep": sha256_file(sweep) if sweep else None,
        "inputs": {str(p): sha256_file(paper_root / p) for p in sorted(entry.get("inputs") or [])},
    }
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()


def tikz_key(paper_root: Path, entry: dict) -> str | None:
    """sha256 over a TikZ diagram's source and its style inputs; None without a source.
    The sremu commit is not part of it: a diagram carries no data."""
    if not entry.get("script"):
        return None
    doc = {
        "source": sha256_file(paper_root / entry["script"]),
        "inputs": {str(p): sha256_file(paper_root / p) for p in sorted(entry.get("inputs") or [])},
    }
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()


def figure_state(paper_root: Path, fid: str, entry: dict, commit: str) -> str:
    """'pending' (no script / TikZ not drawn), 'stale', 'missing-input', 'meta-mismatch'
    (the meta of a current figure does not carry the registry's data classes or the pinned
    commit; DESIGN 11: every figure meta carries data classes) or 'current'.
    A TikZ diagram with a registered source is current when its meta records the source's
    key (:func:`tikz_key`); one without a source is current when its PDF exists."""
    pdf = paper_root / "figures" / "out" / f"{fid}.pdf"
    if entry["source"] == "tikz":
        if not pdf.is_file():
            return "pending"
        try:
            key = tikz_key(paper_root, entry)
        except OSError:
            return "missing-input"
        if key is None:
            return "current"
        meta_path = paper_root / "figures" / "data" / f"{fid}.meta.json"
        if not meta_path.is_file():
            return "stale"
        return "current" if json.loads(read_text(meta_path)).get("content_key") == key else "stale"
    if not entry.get("script"):
        return "pending"
    try:
        key = content_key(paper_root, entry, commit)
    except OSError:
        return "missing-input"
    meta_path = paper_root / "figures" / "data" / f"{fid}.meta.json"
    if not (meta_path.is_file() and pdf.is_file()):
        return "stale"
    meta = json.loads(read_text(meta_path))
    if meta.get("content_key") != key:
        return "stale"
    if sorted(meta.get("data_classes") or []) != sorted(entry.get("data_classes") or []):
        return "meta-mismatch"
    if meta.get("sremu_commit") != commit:
        return "meta-mismatch"
    return "current"


TIKZ_BUILD_DIR = Path("figures") / "build"


def draw_tikz(paper_root: Path, fid: str, entry: dict, rep: Report,
              runner: Runner = default_runner) -> bool:
    """Compile a TikZ diagram (standalone class) with latexmk into figures/build, copy the PDF
    to figures/out/<Fid>.pdf and record its key in figures/data/<Fid>.meta.json."""
    exe = shutil.which("latexmk")
    if exe is None:
        rep.warn(f"figure {fid}: latexmk not found; TikZ diagram not drawn")
        return False
    src = Path(entry["script"])
    build_dir = paper_root / TIKZ_BUILD_DIR
    build_dir.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "-pdf", "-interaction=nonstopmode", "-halt-on-error",
           f"-outdir={TIKZ_BUILD_DIR.as_posix()}", src.as_posix()]
    rc, _ = runner(cmd, paper_root)
    built = build_dir / f"{src.stem}.pdf"
    if rc != 0 or not built.is_file():
        rep.error(f"figure {fid}: TikZ compilation of {src.as_posix()} failed (exit {rc}); "
                  f"see {(TIKZ_BUILD_DIR / (src.stem + '.log')).as_posix()}")
        return False
    out = paper_root / "figures" / "out" / f"{fid}.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(built, out)
    meta = {"figure": fid, "content_key": tikz_key(paper_root, entry), "source": src.as_posix(),
            "inputs": sorted(entry.get("inputs") or []), "data_classes": [],
            "tool": "latexmk (standalone TikZ)"}
    meta_path = paper_root / "figures" / "data" / f"{fid}.meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8", newline="\n")
    return True


def step_figures(
    paper_root: Path, commit: str, mode: str, profile: str, gates_green: bool, rep: Report,
    run_script: Runner = default_runner, draw: bool = False,
) -> None:
    """Registry, placement and currency of every figure. ``draw``: compile stale TikZ
    diagrams (whenever LaTeX runs; they carry no data, so no gate applies)."""
    reg = load_registry(paper_root)
    validate_registry(reg, rep)
    if not rep.ok:
        return
    check_figure_sources(paper_root, reg, rep)
    for fid in EXPECTED_FIGURES:
        entry = reg[fid]
        state = figure_state(paper_root, fid, entry, commit)
        tikz_stale = entry["source"] == "tikz" and entry.get("script") and state != "current"
        if tikz_stale and draw and draw_tikz(paper_root, fid, entry, rep, run_script):
            state = figure_state(paper_root, fid, entry, commit)
        if state == "stale" and mode == "final" and gates_green and entry["source"] == "script":
            cmd = [sys.executable, entry["script"], "--profile", profile, "--figure", fid,
                   "--content-key", str(content_key(paper_root, entry, commit))]
            rc, _ = run_script(cmd, paper_root)
            state = figure_state(paper_root, fid, entry, commit) if rc == 0 else "failed"
        if state != "current":
            (rep.error if mode == "final" else rep.note)(f"figure {fid}: {state}")


# --------------------------------------------------------------------------- step 4/5
def step_numbers(paper_root: Path, rep: Report, mode: str = "draft", commit: str = "",
                 runner: Runner = default_runner) -> None:
    spec = load_numbers_spec(paper_root / "numbers.yaml")
    try:
        results, produced_at = load_results(paper_root)
    except ValueError as exc:
        rep.error(str(exc))
        results, produced_at = {}, None
    if results and produced_at != commit:
        # Rule 7: a quoted number must come from the pipeline run of the pinned commit.
        (rep.error if mode == "final" else rep.note)(
            f"results.json was produced at sremu {produced_at}, sremu.lock pins {commit}")
    unknown = set(results) - {m.macro for m in spec}
    if unknown:
        rep.error(f"results.json has values for undeclared macros {sorted(unknown)}")
    fixed = sorted(m.macro for m in spec if m.value is not None and m.macro in results)
    if fixed:
        rep.error(f"results.json overrides definition/data macros {fixed}")
    coded = sorted(m.macro for m in spec if m.source_type == "code" and m.macro in results)
    if coded:
        rep.error(f"results.json overrides code macros {coded} (they are read from sremu)")
    code_values = resolve_code_values(paper_root, spec, mode, rep, runner)
    out = paper_root / "generated" / "numbers.tex"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_numbers_tex(spec, results, code_values), encoding="utf-8", newline="\n")
    rep.note(f"numbers.tex: {len(spec)} macros, {placeholder_count(read_text(out))} placeholders")


RESOLVER = Path(__file__).resolve().parent / "resolve_code_numbers.py"


def resolve_code_values(paper_root: Path, spec: list, mode: str, rep: Report,
                        runner: Runner = default_runner) -> dict[str, str]:
    """Values of the `code` macros, read from the sremu that this interpreter imports (a final
    build has checked it is the pinned checkout). Writes generated/code_values.json.
    Unresolved macros stay placeholders: a warning in draft, an error in final mode."""
    requests = code_number_requests(spec)
    if not requests:
        return {}
    with tempfile.TemporaryDirectory() as tmp:
        req_path = Path(tmp) / "code_requests.json"
        req_path.write_text(json.dumps(requests), encoding="utf-8")
        rc, out = runner([sys.executable, str(RESOLVER), str(req_path)], paper_root)
    try:
        doc = json.loads(out) if rc == 0 else {}
    except json.JSONDecodeError:
        doc = {}
    values = {str(k): str(v) for k, v in (doc.get("values") or {}).items()}
    errors = dict(doc.get("errors") or {}) if doc else {r["macro"]: f"resolver exit {rc}"
                                                         for r in requests}
    report = rep.error if mode == "final" else rep.warn
    for macro, msg in sorted(errors.items()):
        report(f"code number {macro} unresolved: {msg}")
    path = paper_root / "generated" / "code_values.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"values": dict(sorted(values.items()))}, indent=2,
                               ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
    rep.note(f"code numbers: {len(values)}/{len(requests)} read from sremu "
             f"({doc.get('sremu_file') or 'sremu not importable'})")
    return values


TABLE_GENERATOR = Path(__file__).resolve().parent / "gen_tables.py"


def step_tables(paper_root: Path, mode: str, rep: Report, runner: Runner = default_runner) -> None:
    """Table III from sremu.api.metadata and sremu.compat (tools/gen_tables.py). Without an
    importable sremu the committed table stays (warning in draft, error in final mode)."""
    rc, out = runner([sys.executable, str(TABLE_GENERATOR), "--out",
                      str(paper_root / "generated" / "tables")], paper_root)
    if rc != 0:
        (rep.error if mode == "final" else rep.warn)(
            f"Table III generation failed (exit {rc}): {out.strip()[:200]}")


def step_model_card(paper_root: Path, sremu_root: Path, mode: str, rep: Report,
                    runner: Runner = default_runner) -> None:
    gen = sremu_root / "docs" / "tools" / "gen_model_card.py"
    tex = paper_root / "generated" / "tables" / "table2_model_card.tex"
    if not gen.is_file():
        (rep.error if mode == "final" else rep.warn)(f"model card generator not found at {gen}")
        if not tex.is_file():
            tex.parent.mkdir(parents=True, exist_ok=True)
            tex.write_text("% placeholder: sremu checkout not found\n\\begin{table*}[t]\\caption"
                           "{Model card (not generated).}\\label{tab:modelcard}\\end{table*}\n",
                           encoding="utf-8")
        return
    md_copy = paper_root / "generated" / "tables" / "model_card.md"
    rc, _ = runner([sys.executable, str(gen), "--md", str(md_copy), "--tex", str(tex)], paper_root)
    if rc != 0:
        rep.error("model card generation failed")
        return
    rc, _ = runner([sys.executable, str(gen), "--check"], sremu_root)
    if rc != 0:
        (rep.error if mode == "final" else rep.warn)(
            "sremu docs/model_card.md is out of date with docs/model_card.yaml")


# --------------------------------------------------------------------------- step 6
def step_final_scan(paper_root: Path, rep: Report) -> None:
    """Final mode: no placeholder number, no stub."""
    n_ph = placeholder_count(read_text(paper_root / "generated" / "numbers.tex"))
    n_stub = sum(stub_count(read_text(f)) for f in tex_source_files(paper_root)
                 if f.name != "sremu-macros.tex")
    if n_ph:
        rep.error(f"{n_ph} pipeline numbers are still placeholders (generated/numbers.tex)")
    if n_stub:
        rep.error(f"{n_stub} section stubs (\\sremustub) remain")


# --------------------------------------------------------------------------- step 7
def write_buildmode(paper_root: Path, mode: str) -> None:
    path = paper_root / "generated" / "buildmode.tex"
    path.parent.mkdir(parents=True, exist_ok=True)
    flag = "\\sremudrafttrue" if mode == "draft" else "\\sremudraftfalse"
    path.write_text(f"% GENERATED by tools/build_paper.py: build mode = {mode}\n{flag}\n",
                    encoding="utf-8", newline="\n")


def step_latex(paper_root: Path, mode: str, rep: Report, runner: Runner = default_runner) -> None:
    exe = shutil.which("latexmk")
    if exe is None:
        rep.error("latexmk not found on PATH")
        return
    cmd = [exe, "-pdf", "-interaction=nonstopmode", "-halt-on-error", "-file-line-error",
           "main.tex"]
    rc, _ = runner(cmd, paper_root)
    if rc != 0:
        rep.error(f"latexmk failed (exit {rc}); see main.log")
    log = paper_root / "main.log"
    if log.is_file():
        text = log.read_text(encoding="latin-1")
        undefined = sorted(set(re.findall(r"(Citation|Reference) `([^']+)' .*undefined", text)))
        for kind, key in undefined:
            (rep.error if mode == "final" else rep.warn)(f"LaTeX: {kind.lower()} {key} undefined")
    rep.extend(check_arxiv.check_arxiv(paper_root, post_build=True))


# --------------------------------------------------------------------------- driver
def build(
    paper_root: Path = PAPER_ROOT,
    *,
    mode: str = "draft",
    profile: str = "fast",
    junit: list[Path] | None = None,
    sremu_root: Path | None = None,
    latex: bool = True,
    git: GitProbe | None = None,
    runner: Runner = default_runner,
) -> Report:
    rep = Report(f"build_paper ({mode}, PROFILE={profile})")
    if mode not in MODES or profile not in PROFILES:
        rep.error(f"bad mode/profile {mode}/{profile}")
        return rep
    sremu_root = sremu_root or (paper_root / read_lock(paper_root)["repo"]).resolve()
    junit = check_claims.default_junit(paper_root) if junit is None else junit
    commit = step_pin(paper_root, sremu_root, mode, git or GitProbe(), rep, runner=runner)
    green = step_gates(paper_root, junit, mode, rep, commit=commit)
    step_figures(paper_root, commit, mode, profile, green, rep, run_script=runner, draw=latex)
    step_numbers(paper_root, rep, mode=mode, commit=commit, runner=runner)
    step_model_card(paper_root, sremu_root, mode, rep, runner=runner)
    step_tables(paper_root, mode, rep, runner=runner)
    rep.extend(check_claims.check_claims(paper_root, mode, junit))
    rep.extend(check_refs.check_refs(paper_root))
    rep.extend(check_arxiv.check_arxiv(paper_root, post_build=False))
    if mode == "final":
        step_final_scan(paper_root, rep)
        if not rep.ok:
            rep.error("final build refused: outstanding items above (no PDF produced)")
            return rep
    write_buildmode(paper_root, mode)
    if latex:
        step_latex(paper_root, mode, rep, runner=runner)
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the sremu paper (DESIGN 13.5).")
    ap.add_argument("--mode", choices=MODES, default="draft")
    ap.add_argument("--profile", choices=PROFILES, default=os.environ.get("PROFILE", "fast"))
    ap.add_argument("--junit", type=Path, nargs="*", default=None)
    ap.add_argument("--sremu-root", type=Path, default=None)
    ap.add_argument("--no-latex", action="store_true")
    args = ap.parse_args(argv)
    t0 = time.perf_counter()
    rep = build(PAPER_ROOT, mode=args.mode, profile=args.profile, junit=args.junit,
                sremu_root=args.sremu_root, latex=not args.no_latex)
    rep.note(f"wall time {time.perf_counter() - t0:.1f} s")
    return rep.emit()


if __name__ == "__main__":
    raise SystemExit(main())
