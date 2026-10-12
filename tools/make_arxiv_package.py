"""make_arxiv_package.py -- the arXiv source package of a completed build (WP12). Offline.

Input is a completed build in the paper root (tools/build_paper.py: main.pdf, main.bbl,
generated/*, main.fls and main.fdb_latexmk from latexmk).

Steps
  1. mode     --mode final (default) refuses unless generated/buildmode.tex sets
              \\sremudraftfalse (written by build_paper.py --mode final). --mode draft-test
              accepts a draft build, writes into a scratch directory and labels the directory,
              the archive and metadata.txt DRAFT-NOT-FOR-SUBMISSION.
  2. files    main.tex and every file it reaches: \\input, \\include, \\InputIfFileExists
              (recursively, generated files included), graphics (\\includegraphics and macros
              that wrap it, such as \\sremufigure; with or without extension), local .cls/.sty
              files, and main.bbl. refs.bib is omitted: arXiv does not run BibTeX reliably and
              uses the .bbl. The list must equal the paper files that the build read (main.fls).
  3. current  every packaged file has the checksum that latexmk recorded for the build of
              main.pdf (main.fdb_latexmk; file times without it), and refs.bib has not changed
              since main.bbl was made.
  4. strip    .tex files only (.bbl, .cls and .sty are copied as they are): comments (a
              whole-line comment is dropped; `code % text` becomes `code %`, so the % that
              suppresses the end-of-line space stays; \\%, \\verb, verbatim-like environments
              and \\url/\\href/\\path arguments are kept), the calls of macros defined to print
              nothing (\\claim{ID}), and the definitions of internal macros (\\claim, \\sremustub,
              \\sremuPlaceholder, \\sremuUnreleased) that nothing references any more. A call is
              replaced by `{}` instead of nothing where removing it would change TeX's spaces
              (a space on both sides, a line of its own) or join a preceding control word.
              main.tex gets \\pdfoutput=1 as its first line (arXiv's cue for pdfLaTeX; a no-op
              under pdflatex).
  5. verify   pdflatex of the package in a temporary directory (two runs, more while LaTeX
              asks for a rerun, at most five; no BibTeX: the .bbl is there): exit 0, no
              undefined reference or citation, the page count of main.pdf, the same text
              (pdftotext, whitespace-normalized; without pdftotext the PDF size within 1 %) and
              the same rendered content (every page, font, figure and image stream of the two
              PDFs, decompressed, as a multiset). No packaged file may contain sremustub, TODO,
              \\claim{, Planned] or a local absolute path (D:/..., C:\\Users, /home/...); the
              package must stay below arXiv's 50 MB.
  6. output   <out>/source/ (the tree), <out>/sremu-arxiv.tar.gz (files at the archive root,
              sorted, fixed owner and time: the same build gives the same bytes),
              <out>/abstract.txt (tools/check_arxiv.py --emit-plain; not in the archive),
              <out>/metadata.txt (title, authors, categories, comments line) and
              <out>/verification.txt. The archive is written only when every check passes.
Default <out>: <paper>/arxiv (final, gitignored) or <temp>/sremu-arxiv-DRAFT-NOT-FOR-SUBMISSION
(draft-test). An existing <out> is replaced only if this tool wrote it (marker file).

Usage: python tools/make_arxiv_package.py [--mode final|draft-test] [--out DIR]
                                          [--paper-root DIR] [--keep-build]
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zlib
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_common import PAPER_ROOT, Report, read_text
from paper_common import strip_tex_comments as strip_comments_for_scan

TOOLS = Path(__file__).resolve().parent
MODES = ("final", "draft-test")
DRAFT_LABEL = "DRAFT-NOT-FOR-SUBMISSION"
ARCHIVE_STEM = "sremu-arxiv"
SOURCE_DIR = "source"
MARKER = ".arxiv-package"
MAIN = "main.tex"
JOBNAME = "main"
PRIMARY_CATEGORY = "eess.SP"
CROSS_LISTS = ("cs.NI",)
ARXIV_MAX_BYTES = 50_000_000  # arXiv's 50 MB per submission (decimal: the stricter reading)
SIZE_TOLERANCE = 0.01  # PDF size agreement when pdftotext is not available
MIN_RUNS, MAX_RUNS = 2, 5

FORBIDDEN = ("sremustub", "TODO", "\\claim{", "Planned]")
DRAFT_EXPECTED = ("sremustub", "Planned]")  # a draft build keeps its stubs (final refuses them)
INTERNAL_MACROS = ("claim", "sremustub", "sremuPlaceholder", "sremuUnreleased")
LOCAL_PATH_RES = (
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:/"),  # D:/projects/..., C:/Users/...
    re.compile(r"(?i)(?<![A-Za-z0-9])[A-Za-z]:\\+(?:users|projects|documents|program files|"
               r"windows|temp|tmp)\b"),  # C:\Users, D:\projects
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:\\+[\w .~$-]+\\"),  # D:\any\...
    re.compile(r"(?<![\w.~-])/(?:home|Users)/[^/\s{}]+/"),  # POSIX home directories
    re.compile(r"(?i)(?<![\w.~-])/[a-z]/(?:users|projects)/"),  # Git Bash (MSYS) paths
)

VERBATIM_ENVS = frozenset({
    "verbatim", "verbatim*", "Verbatim", "Verbatim*", "BVerbatim", "LVerbatim", "lstlisting",
    "minted", "alltt", "comment", "filecontents", "filecontents*",
})
URL_MACROS = frozenset({"url", "href", "path", "nolinkurl"})
GRAPHICS_EXT = (".pdf", ".png", ".jpg", ".jpeg", ".jbig2", ".jb2", ".mps",
                ".PDF", ".PNG", ".JPG", ".JPEG")
BUILD_PRODUCTS = frozenset({".aux", ".out", ".toc", ".lof", ".lot", ".nav", ".snm", ".vrb",
                            ".bcf", ".log", ".fls", ".fdb_latexmk", ".blg", ".gz"})

_CS_RE = re.compile(r"\\(?:[A-Za-z@]+|.)", re.S)
_INPUT_RE = re.compile(r"\\(input|include|InputIfFileExists)\s*\{([^{}]*)\}")
_GRAPHICS_RE = re.compile(r"\\includegraphics\s*\*?\s*(?:\[[^\]]*\]\s*)*\{([^{}]*)\}")
_GRAPHICSPATH_RE = re.compile(r"\\graphicspath\s*\{((?:\s*\{[^{}]*\})*)\s*\}")
_DOCCLASS_RE = re.compile(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}")
_PACKAGE_RE = re.compile(r"\\(?:usepackage|RequirePackage|LoadClass)\s*(?:\[[^\]]*\])?\s*"
                         r"\{([^{}]*)\}")
_BIB_RE = re.compile(r"\\bibliography\s*\{([^{}]*)\}")
_DEF_HEAD = re.compile(
    r"\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand)\*?\s*"
    r"(?:\{\s*\\([A-Za-z@]+)\s*\}|\\([A-Za-z@]+))\s*(?:\[\s*(\d)\s*\])?\s*(\[)?")
_DEF_TEX = re.compile(r"\\[gex]?def\s*\\([A-Za-z@]+)((?:#\d)*)\s*\{")
_DEF_CONTEXT = re.compile(
    r"(?:\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand)\*?\s*\{?\s*"
    r"|\\[gex]?def\s*|\\let\s*(?:\\[A-Za-z@]+\s*=?\s*)?|\\(?:show|meaning)\s*)$")
_RERUN_RE = re.compile(r"Rerun to get|Label\(s\) may have changed|Please rerun")
_UNDEFINED_RE = re.compile(r"(Citation|Reference) `([^']+)' (?:on page \d+ )?undefined")


# --------------------------------------------------------------------------- TeX helpers
def _group_end(text: str, k: int) -> int:
    """Index after the brace group that opens at text[k] ('{'); \\{, \\} and comments skip."""
    depth = 0
    i, n = k, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "%":
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError(f"unbalanced braces from offset {k}")


def _skip_verbatim_arg(s: str, i: int) -> int:
    """Index after a verbatim-like argument at s[i:] (\\url{...} or \\url|...|), in which % is
    a character; len(s) when it does not end on this line (the rest of the line is kept)."""
    while i < len(s) and s[i] in " \t":
        i += 1
    if i >= len(s):
        return i
    if s[i] == "{":
        depth = 0
        for k in range(i, len(s)):
            if s[k] == "{":
                depth += 1
            elif s[k] == "}":
                depth -= 1
                if depth == 0:
                    return k + 1
        return len(s)
    close = s.find(s[i], i + 1)
    return len(s) if close < 0 else close + 1


def _find_comment(s: str) -> tuple[int | None, str | None]:
    """(index of the % that starts a comment in line s, verbatim environment left open)."""
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "%":
            return i, None
        if c != "\\":
            i += 1
            continue
        m = _CS_RE.match(s, i)
        if m is None:  # a backslash at the end of the line
            return None, None
        name, i = m.group(0)[1:], m.end()
        if name == "verb":
            if i < n and s[i] == "*":
                i += 1
            close = s.find(s[i], i + 1) if i < n else -1
            if close < 0:
                return None, None  # keep the rest of the line
            i = close + 1
        elif name in URL_MACROS:
            i = _skip_verbatim_arg(s, i)
        elif name == "begin":
            env = re.match(r"\s*\{([^{}]*)\}", s[i:])
            if env and env.group(1) in VERBATIM_ENVS:
                end_tag = f"\\end{{{env.group(1)}}}"
                end = s.find(end_tag, i + env.end())
                if end < 0:
                    return None, env.group(1)
                i = end + len(end_tag)
    return None, None


def strip_comments(text: str, stats: Counter[str] | None = None) -> str:
    """Remove TeX comments without changing the tokens TeX reads (line endings become LF).

    A line that holds only a comment yields no token and is dropped; `code % text` becomes
    `code %` (the % still suppresses the end-of-line space). \\%, \\\\%, \\verb, verbatim-like
    environments and URL arguments are respected. Refuses text that changes the catcode of %.
    """
    if re.search(r"\\catcode\s*`\\?%|\\catcode\s*37\b", text):
        raise ValueError("the catcode of % is changed; comments cannot be stripped safely")
    stats = Counter() if stats is None else stats
    out: list[str] = []
    verbatim: str | None = None
    for line in text.replace("\r\n", "\n").split("\n"):
        head, rest = "", line
        if verbatim is not None:
            end_tag = f"\\end{{{verbatim}}}"
            end = line.find(end_tag)
            if end < 0:
                out.append(line)
                continue
            verbatim = None
            head, rest = line[: end + len(end_tag)], line[end + len(end_tag):]
        cut, verbatim = _find_comment(rest)
        if cut is None:
            out.append(line)
        elif (head + rest[:cut]).strip() == "":
            stats["comment lines dropped"] += 1
        elif cut + 1 < len(rest):
            out.append(head + rest[: cut + 1])
            stats["end-of-line comments cut to %"] += 1
        else:
            out.append(line)
    return "\n".join(out)


def _escaped(text: str, pos: int) -> bool:
    """True if the backslash at text[pos] is the second half of a \\\\ pair."""
    k = pos
    while k > 0 and text[k - 1] == "\\":
        k -= 1
    return (pos - k) % 2 == 1


def _cs_positions(text: str, name: str) -> Iterator[re.Match[str]]:
    """Occurrences of the control word \\name (not a longer name, not after an escaping \\)."""
    for m in re.finditer(r"\\" + re.escape(name) + r"(?![A-Za-z@])", text):
        if not _escaped(text, m.start()):
            yield m


def _ends_with_control_sequence(pre: str) -> bool:
    m = re.search(r"(\\*)([A-Za-z@]+)$", pre)
    if m:  # letters: a control word if an odd number of backslashes precedes them
        return len(m.group(1)) % 2 == 1
    if pre.endswith("\\"):  # (an even number: the call is not escaped) ends with \\
        return True
    if len(pre) >= 2:  # a control symbol such as \, or \%
        k = len(pre) - 1 - len(pre[:-1].rstrip("\\"))
        return k % 2 == 1
    return False


def _brace_args(text: str, j: int, n: int) -> tuple[list[str], int] | None:
    """n undelimited brace arguments starting at text[j] (blanks and one line end may
    precede each, as TeX skips them); None if an argument is not a brace group."""
    args: list[str] = []
    for _ in range(n):
        k = j
        while k < len(text) and text[k] in " \t":
            k += 1
        if k < len(text) and text[k] == "\n":
            k += 1
            while k < len(text) and text[k] in " \t":
                k += 1
        if k >= len(text) or text[k] != "{":
            return None
        j = _group_end(text, k)
        args.append(text[k + 1 : j - 1])
    return args, j


@dataclass(frozen=True)
class MacroDef:
    name: str
    nargs: int
    optional: bool  # the first argument is optional (\newcommand{\x}[2][default]{...})
    body: str
    start: int
    end: int


def macro_definitions(text: str) -> list[MacroDef]:
    """\\newcommand-family and \\def definitions of a (comment-stripped) text."""
    out: list[MacroDef] = []
    for m in _DEF_HEAD.finditer(text):
        if _escaped(text, m.start()):
            continue
        j = m.end()
        if m.group(4):  # optional-argument default
            close = text.find("]", j)
            if close < 0:
                continue
            j = close + 1
        while j < len(text) and text[j] in " \t\n":
            j += 1
        if j >= len(text) or text[j] != "{":
            continue
        try:
            end = _group_end(text, j)
        except ValueError:
            continue
        out.append(MacroDef(m.group(1) or m.group(2), int(m.group(3) or 0), bool(m.group(4)),
                            text[j + 1 : end - 1], m.start(), end))
    for m in _DEF_TEX.finditer(text):
        try:
            end = _group_end(text, m.end() - 1)
        except ValueError:
            continue
        out.append(MacroDef(m.group(1), len(m.group(2)) // 2, False, text[m.end() : end - 1],
                            m.start(), end))
    return out


def null_macros(texts: dict[str, str]) -> dict[str, int]:
    """Macros defined to print nothing (empty body, no optional argument): {name: #args}."""
    out: dict[str, int] = {}
    for text in texts.values():
        for d in macro_definitions(text):
            if not d.optional and not re.sub(r"%[^\n]*", "", d.body).strip():
                out[d.name] = d.nargs
    return out


def strip_null_calls(text: str, macros: dict[str, int], where: str = "") -> tuple[str, int]:
    """Remove the calls \\name{arg}... of macros that print nothing, keeping TeX's tokens.

    The call becomes nothing, or `{}` where removing it would merge a preceding control word
    with what follows, turn two space tokens into one (a blank on both sides) or leave an empty
    line (a paragraph break). Definitions are left alone; a call in math mode or with an
    argument that is not a brace group is refused (ValueError)."""
    if not macros:
        return text, 0
    names = "|".join(re.escape(n) for n in sorted(macros, key=len, reverse=True))
    pat = re.compile(r"\\(" + names + r")(?![A-Za-z@])")
    out: list[str] = []
    pos = count = 0
    scan = 0
    while True:
        m = pat.search(text, scan)
        if m is None:
            break
        scan = m.end()
        if _escaped(text, m.start()):
            continue
        line_start = text.rfind("\n", 0, m.start()) + 1
        pre = text[line_start : m.start()]
        line_no = text.count("\n", 0, m.start()) + 1
        if _DEF_CONTEXT.search(pre):
            continue
        parsed = _brace_args(text, m.end(), macros[m.group(1)])
        if parsed is None:
            raise ValueError(f"{where}:{line_no}: \\{m.group(1)} without a brace argument")
        if len(re.findall(r"(?<!\\)\$", pre)) % 2:
            raise ValueError(f"{where}:{line_no}: \\{m.group(1)} in math mode")
        end = parsed[1]
        post = text[end : end + 1]
        blank_both_sides = post in ("", "\n", " ", "\t") and (pre == "" or pre[-1] in " \t")
        repl = "{}" if _ends_with_control_sequence(pre) or blank_both_sides else ""
        out += [text[pos : m.start()], repl]
        pos = scan = end
        count += 1
    out.append(text[pos:])
    return "".join(out), count


def drop_definition(text: str, name: str) -> tuple[str, bool]:
    """Remove the definition of \\name when it occupies whole lines (and one blank line left
    behind between two blank lines); (text, False) if there is none or it shares a line."""
    for d in macro_definitions(text):
        if d.name != name:
            continue
        start = text.rfind("\n", 0, d.start) + 1
        eol = text.find("\n", d.end)
        stop = len(text) if eol < 0 else eol + 1
        if text[start : d.start].strip() or text[d.end : stop].strip() not in ("", "%"):
            return text, False
        before, after = text[:start], text[stop:]
        if (before.endswith("\n\n") or before == "") and after.startswith("\n"):
            after = after[1:]
        return before + after, True
    return text, False


def references(text: str, name: str) -> int:
    return sum(1 for _ in _cs_positions(text, name))


# --------------------------------------------------------------------------- file list
@dataclass
class Plan:
    """The files of the package: {path relative to the paper root: kind}."""

    files: dict[str, str] = field(default_factory=dict)  # tex | bbl | graphics | class | style
    preamble: set[str] = field(default_factory=set)  # .tex files read before \begin{document}
    distribution: list[str] = field(default_factory=list)  # classes/packages of the TeX system
    omitted: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _safe_rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        raise ValueError(f"{path} is outside the paper root") from None


def _resolve_tex(root: Path, target: str, cmd: str) -> str | None:
    if cmd == "include":
        cands = [target + ".tex"]
    else:
        cands = [target] if Path(target).suffix else [target + ".tex", target]
    for c in cands:
        if Path(c).is_absolute() or re.match(r"^[A-Za-z]:", c):
            raise ValueError(f"absolute path {c!r} (arXiv needs relative paths)")
        if (root / c).is_file():
            return _safe_rel(root, root / c)
    return None


def _resolve_graphic(root: Path, name: str, gpaths: list[str]) -> str | None:
    if Path(name).is_absolute() or re.match(r"^[A-Za-z]:", name):
        raise ValueError(f"absolute graphics path {name!r} (arXiv needs relative paths)")
    for prefix in ["", *gpaths]:
        base = prefix + name
        if Path(base).suffix and (root / base).is_file():
            return _safe_rel(root, root / base)
        for ext in GRAPHICS_EXT:
            if (root / (base + ext)).is_file():
                return _safe_rel(root, root / (base + ext))
    return None


def _reach(edges: dict[str, list[str]], starts: list[str]) -> set[str]:
    seen: set[str] = set()
    stack = list(starts)
    while stack:
        f = stack.pop()
        if f not in seen:
            seen.add(f)
            stack += edges.get(f, [])
    return seen


def resolve(root: Path) -> Plan:
    """Every file main.tex needs (see the module docstring); ValueError if one is missing."""
    if not (root / MAIN).is_file():
        raise ValueError(f"{MAIN} not found in {root}")
    plan = Plan()
    plan.files[MAIN] = "tex"
    texts: dict[str, str] = {}
    edges: dict[str, list[str]] = {}
    main_pre: list[str] = []
    main_body: list[str] = []
    queue = [MAIN]
    template_calls_done: set[tuple[str, int]] = set()
    while queue:
        while queue:
            rel = queue.pop(0)
            kind = plan.files[rel]
            raw = read_text(root / rel)
            text = strip_comments(raw) if kind == "tex" else strip_comments_for_scan(raw)
            texts[rel] = text
            begin_doc = text.find("\\begin{document}") if rel == MAIN else -1
            kids: list[str] = []
            for m in _INPUT_RE.finditer(text):
                target = m.group(2).strip()
                if "#" in target or _escaped(text, m.start()):
                    continue  # a macro parameter: resolved at the calls of the macro
                found = _resolve_tex(root, target, m.group(1))
                if found is None:
                    if m.group(1) == "InputIfFileExists":
                        plan.notes.append(f"optional input {target} is absent")
                        continue
                    raise ValueError(f"{rel}: \\{m.group(1)}{{{target}}} not found")
                kids.append(found)
                if rel == MAIN:
                    (main_pre if m.start() < begin_doc else main_body).append(found)
                if found not in plan.files:
                    plan.files[found] = "tex"
                    queue.append(found)
            edges[rel] = kids
            for m in _DOCCLASS_RE.finditer(text):
                _add_local(root, plan, queue, m.group(1).strip(), ".cls", "class")
            for m in _PACKAGE_RE.finditer(text):
                for name in m.group(1).split(","):
                    ext = ".cls" if "LoadClass" in m.group(0) else ".sty"
                    _add_local(root, plan, queue, name.strip(), ext,
                               "class" if ext == ".cls" else "style")
        gpaths = [g for t in texts.values() for m in _GRAPHICSPATH_RE.finditer(t)
                  for g in re.findall(r"\{([^{}]*)\}", m.group(1))]
        for rel, text in texts.items():
            for m in _GRAPHICS_RE.finditer(text):
                name = m.group(1).strip()
                if "#" in name:
                    continue
                found = _resolve_graphic(root, name, gpaths)
                if found is None:
                    raise ValueError(f"{rel}: graphics file {name!r} not found")
                plan.files.setdefault(found, "graphics")
        # macros whose body includes a file named by a parameter (\sremufigure{F3}...)
        templates = {}
        for text in texts.values():
            for d in macro_definitions(text):
                paths = [("graphics", g.group(1)) for g in _GRAPHICS_RE.finditer(d.body)
                         if "#" in g.group(1)]
                paths += [(i.group(1), i.group(2)) for i in _INPUT_RE.finditer(d.body)
                          if "#" in i.group(2)]
                if paths:
                    templates[d.name] = (d.nargs, paths)
        for rel, text in list(texts.items()):
            for name, (nargs, paths) in templates.items():
                for m in _cs_positions(text, name):
                    pre = text[text.rfind("\n", 0, m.start()) + 1 : m.start()]
                    if _DEF_CONTEXT.search(pre) or (rel, m.start()) in template_calls_done:
                        continue
                    template_calls_done.add((rel, m.start()))
                    parsed = _brace_args(text, m.end(), nargs)
                    if parsed is None:
                        raise ValueError(f"{rel}: \\{name} call without brace arguments")
                    for kind, tmpl in paths:
                        path = tmpl
                        for k in range(nargs, 0, -1):
                            path = path.replace(f"#{k}", parsed[0][k - 1].strip())
                        if kind == "graphics":
                            found = _resolve_graphic(root, path, gpaths)
                            if found is None:
                                plan.notes.append(f"\\{name}: {path} absent (the macro decides)")
                            else:
                                plan.files.setdefault(found, "graphics")
                            continue
                        found = _resolve_tex(root, path, kind)
                        if found is None:
                            plan.notes.append(f"\\{name}: {path} absent (the macro decides)")
                        elif found not in plan.files:
                            plan.files[found] = "tex"
                            queue.append(found)
    main_text = texts[MAIN]
    for m in _BIB_RE.finditer(main_text):
        bbl = f"{JOBNAME}.bbl"
        if not (root / bbl).is_file():
            raise ValueError(f"{bbl} missing: run tools/build_paper.py (BibTeX makes it)")
        plan.files[bbl] = "bbl"
        plan.omitted += [f"{b.strip()}.bib (arXiv uses {bbl})" for b in m.group(1).split(",")]
    edges[MAIN] = []
    plan.preamble = _reach(edges, main_pre) - _reach(edges, main_body)
    return plan


def _add_local(root: Path, plan: Plan, queue: list[str], name: str, ext: str, kind: str) -> None:
    if not name or "#" in name:
        return
    path = root / f"{name}{ext}"
    if path.is_file():
        rel = _safe_rel(root, path)
        if rel not in plan.files:
            plan.files[rel] = kind
            queue.append(rel)
    elif f"{name}{ext}" not in plan.distribution:
        plan.distribution.append(f"{name}{ext}")


# --------------------------------------------------------------------------- build state
def build_mode(root: Path) -> str | None:
    """'final' or 'draft' as generated/buildmode.tex (build_paper.py) sets it; None if absent."""
    path = root / "generated" / "buildmode.tex"
    if not path.is_file():
        return None
    body = strip_comments(read_text(path))
    if "\\sremudraftfalse" in body:
        return "final"
    if "\\sremudrafttrue" in body:
        return "draft"
    return None


def _norm_build_path(root: Path, base: Path, raw: str) -> str | None:
    """A path recorded by the build as a path relative to the paper root (None: outside)."""
    raw = raw.strip().replace("\\", "/")
    p = Path(raw)
    if p.is_absolute() or re.match(r"^[A-Za-z]:", raw):
        for b in (base, root):
            try:
                return Path(raw).resolve().relative_to(b.resolve()).as_posix()
            except ValueError:
                continue
        return None
    rel = Path(os.path.normpath(raw)).as_posix()
    return None if rel.startswith("..") else rel


def fls_inputs(root: Path) -> set[str] | None:
    """Paper files the last LaTeX run read (main.fls), build products excluded."""
    fls = root / f"{JOBNAME}.fls"
    if not fls.is_file():
        return None
    base = root
    out: set[str] = set()
    for line in fls.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("PWD "):
            base = Path(line[4:].strip())
        elif line.startswith("INPUT "):
            rel = _norm_build_path(root, base, line[6:])
            if rel is None or Path(rel).suffix in BUILD_PRODUCTS:
                continue
            out.add(rel)
    return out


def distribution_versions(root: Path, names: list[str]) -> dict[str, str]:
    """Release line (\\ProvidesClass[...]) of each distribution class the build read."""
    fls = root / f"{JOBNAME}.fls"
    wanted = {n for n in names if n.endswith(".cls")}
    out: dict[str, str] = {}
    if not fls.is_file() or not wanted:
        return out
    for line in fls.read_text(encoding="utf-8", errors="replace").splitlines():
        path = Path(line[6:].strip()) if line.startswith("INPUT ") else None
        if path is None or path.name not in wanted or path.name in out or not path.is_file():
            continue
        m = re.search(r"\\Provides(?:Class|Package)\s*\{[^}]*\}\s*\[([^\]]*)\]",
                      path.read_text(encoding="latin-1"))
        if m:
            out[path.name] = " ".join(m.group(1).split())
    return out


def parse_fdb(path: Path) -> dict[str, dict[str, str]]:
    """latexmk's main.fdb_latexmk: {rule: {file as written: md5}}."""
    rules: dict[str, dict[str, str]] = {}
    cur: dict[str, str] | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        head = re.match(r'\["([^"]+)"\]', line)
        if head:
            cur = rules.setdefault(head.group(1), {})
            continue
        m = re.match(r'\s+"([^"]+)"\s+\S+\s+-?\d+\s+(\S+)\s+"', line)
        if m and cur is not None:
            cur[m.group(1)] = m.group(2)
    return rules


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()


def check_current(root: Path, plan: Plan, rep: Report) -> None:
    """main.pdf (and main.bbl) were built from the files that are packaged now."""
    pdf = root / f"{JOBNAME}.pdf"
    if not pdf.is_file():
        rep.error(f"{pdf.name} missing: run tools/build_paper.py first")
        return
    fdb_path = root / f"{JOBNAME}.fdb_latexmk"
    if not fdb_path.is_file():
        newest = max(plan.files, key=lambda r: (root / r).stat().st_mtime)
        if (root / newest).stat().st_mtime > pdf.stat().st_mtime:
            rep.error(f"{newest} is newer than {pdf.name}: rebuild with tools/build_paper.py")
        rep.note("current: file times (no main.fdb_latexmk)")
        return
    rules = parse_fdb(fdb_path)

    def recorded(rule_prefix: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, files in rules.items():
            if name.startswith(rule_prefix):
                for raw, md5 in files.items():
                    rel = _norm_build_path(root, root, raw)
                    if rel is not None:
                        out[rel] = md5
        return out

    latex = recorded("pdflatex")
    stale = 0
    for rel in sorted(plan.files):
        md5 = latex.get(rel)
        if md5 is None:
            rep.error(f"{rel}: not recorded in {fdb_path.name} (rebuild with tools/build_paper.py)")
            stale += 1
        elif md5 != _md5(root / rel):
            rep.error(f"{rel}: changed since {pdf.name} was built (rebuild with "
                      "tools/build_paper.py)")
            stale += 1
    for rel, md5 in sorted(recorded("bibtex").items()):
        if rel.endswith(".bib") and (root / rel).is_file() and md5 != _md5(root / rel):
            rep.error(f"{rel}: changed since {JOBNAME}.bbl was made (rebuild)")
            stale += 1
    if not stale:
        rep.note(f"current: all {len(plan.files)} files have the checksums latexmk recorded "
                 f"for {pdf.name}")


def cross_check_build(root: Path, plan: Plan, rep: Report) -> None:
    read = fls_inputs(root)
    if read is None:
        rep.warn(f"{JOBNAME}.fls not found: file list not cross-checked with the build")
        return
    pkg = set(plan.files)
    for rel in sorted(read - pkg):
        rep.error(f"the build read {rel}, which the resolver did not find")
    for rel in sorted(pkg - read):
        rep.error(f"the resolver found {rel}, which the build did not read")
    if read == pkg:
        rep.note(f"file list = the {len(pkg)} paper files the build read ({JOBNAME}.fls)")


# --------------------------------------------------------------------------- transform
def transform(root: Path, plan: Plan, rep: Report) -> dict[str, bytes]:
    """Contents of every packaged file (comments and internal markers stripped)."""
    stats: Counter[str] = Counter()
    texts = {rel: strip_comments(read_text(root / rel), stats)
             for rel, kind in plan.files.items() if kind == "tex"}
    bbl = {rel: (root / rel).read_bytes().replace(b"\r\n", b"\n")
           for rel, kind in plan.files.items() if kind == "bbl"}
    # internal markers live in the hand-written macro files of the preamble; a generated
    # value macro that happens to be empty is data, not a marker
    nulls = null_macros({rel: t for rel, t in texts.items()
                         if rel in plan.preamble and not rel.startswith("generated/")})
    if nulls:
        rep.note("macros that print nothing: " + ", ".join(f"\\{n}" for n in sorted(nulls)))
    for rel in sorted(texts):
        texts[rel], n = strip_null_calls(texts[rel], nulls, rel)
        texts[rel] = strip_comments(texts[rel])  # a line left as a lone % is dropped
        stats["calls of empty macros removed"] += n
    bbl_text = "".join(b.decode("utf-8", "replace") for b in bbl.values())
    internal = set(INTERNAL_MACROS) | set(nulls)
    changed = True
    while changed:
        changed = False
        for name in sorted(internal):
            for rel in sorted(plan.preamble & set(texts)):
                new, ok = drop_definition(texts[rel], name)
                if not ok:
                    continue
                others = [t for r, t in texts.items() if r != rel] + [new, bbl_text]
                if not any(references(t, name) for t in others):
                    texts[rel] = new
                    changed = True
                    stats["unreferenced internal definitions dropped"] += 1
                    rep.note(f"dropped the unreferenced definition of \\{name} ({rel})")
    main = texts[MAIN]
    if not re.search(r"\\pdfoutput\s*=\s*1", "\n".join(main.split("\n")[:5])):
        texts[MAIN] = "\\pdfoutput=1\n" + main
    rep.note("stripped: " + ", ".join(f"{v} {k}" for k, v in sorted(stats.items())))
    out: dict[str, bytes] = {rel: t.encode("utf-8") for rel, t in texts.items()}
    out.update(bbl)
    for rel, kind in plan.files.items():
        if kind not in ("tex", "bbl"):
            out[rel] = (root / rel).read_bytes()
    return dict(sorted(out.items()))


# --------------------------------------------------------------------------- PDF helpers
@dataclass(frozen=True)
class PdfStream:
    head: bytes  # the stream dictionary
    start: int  # payload offsets in the file
    end: int
    data: bytes  # decoded payload (FlateDecode inflated) or the raw payload
    decoded: bool


_PDF_OBJ_RE = re.compile(rb"(\d+)\s+(\d+)\s+obj\b")
_PDF_LENGTH_RE = re.compile(rb"/Length\s+(\d+)(?!\s+\d+\s+R)")
_PDF_STREAM_KW = re.compile(rb"\s*stream(?:\r\n|\n|\r)")
_PDF_SKIP = re.compile(rb"/Type\s*/(?:XRef|ObjStm|Metadata)\b")  # dates, offsets, IDs
_PDF_BINARY = re.compile(rb"/Length[123]\b|/Subtype\s*/(?:Image|Type1C|CIDFontType0C|OpenType)\b")


def _pdf_string_end(data: bytes, i: int) -> int:
    depth = 0
    while i < len(data):
        ch = data[i]
        if ch == 0x5C:  # backslash escape
            i += 2
            continue
        if ch == 0x28:
            depth += 1
        elif ch == 0x29:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(data)


def _pdf_dict_end(data: bytes, i: int) -> int:
    depth = 0
    while i < len(data):
        two = data[i : i + 2]
        if two == b"<<":
            depth += 1
            i += 2
            continue
        if two == b">>":
            depth -= 1
            i += 2
            if depth == 0:
                return i
            continue
        if data[i] == 0x28:  # (literal string)
            i = _pdf_string_end(data, i)
            continue
        if data[i] == 0x3C:  # <hex string>
            j = data.find(b">", i)
            i = len(data) if j < 0 else j + 1
            continue
        i += 1
    raise ValueError("unterminated PDF dictionary")


def pdf_streams(data: bytes) -> list[PdfStream]:
    """Every stream object of a PDF (stdlib only; enough for pdfTeX and matplotlib output)."""
    out: list[PdfStream] = []
    pos = 0
    while True:
        m = _PDF_OBJ_RE.search(data, pos)
        if m is None:
            break
        i = m.end()
        while i < len(data) and data[i] in b" \t\r\n":
            i += 1
        if data[i : i + 2] != b"<<":
            pos = i
            continue
        try:
            dend = _pdf_dict_end(data, i)
        except ValueError:
            break
        head = data[i:dend]
        kw = _PDF_STREAM_KW.match(data, dend)
        if kw is None:
            pos = dend
            continue
        start = kw.end()
        length = _PDF_LENGTH_RE.search(head)
        end = start + int(length.group(1)) if length else -1
        if end < 0 or not data[end : end + 12].lstrip().startswith(b"endstream"):
            end = data.find(b"endstream", start)  # no usable /Length: the keyword ends it
            if end < 0:
                break
            if data[end - 2 : end] == b"\r\n":
                end -= 2
            elif data[end - 1 : end] in (b"\n", b"\r"):
                end -= 1
        payload, decoded = data[start:end], b"/Filter" not in head
        if b"/FlateDecode" in head:
            with contextlib.suppress(zlib.error):
                payload, decoded = zlib.decompress(payload), True
        out.append(PdfStream(head, start, end, payload, decoded))
        after = data.find(b"endstream", end)
        if after < 0:
            break
        pos = after + len(b"endstream")
    return out


def pdf_page_count(data: bytes) -> int:
    """Page count: the largest /Count of a /Type /Pages node (also inside object streams)."""
    texts = [data] + [s.data for s in pdf_streams(data) if b"/ObjStm" in s.head]
    counts: list[int] = []
    for t in texts:
        for m in re.finditer(rb"/Type\s*/Pages\b", t):
            start = t.rfind(b"<<", 0, m.start())
            while start >= 0:
                try:
                    end = _pdf_dict_end(t, start)
                except ValueError:
                    end = -1
                if end > m.start():
                    count = re.search(rb"/Count\s+(\d+)", t[start:end])
                    if count:
                        counts.append(int(count.group(1)))
                    break
                start = t.rfind(b"<<", 0, start)
    if counts:
        return max(counts)
    return sum(len(re.findall(rb"/Type\s*/Page(?![A-Za-z])", t)) for t in texts)


def rendered_fingerprint(data: bytes) -> Counter[str]:
    """Multiset of the decoded page, font, figure and image streams (what is drawn), without
    the cross-reference, object and metadata streams (offsets, dates, IDs)."""
    return Counter(hashlib.sha256(s.data).hexdigest() for s in pdf_streams(data)
                   if not _PDF_SKIP.search(s.head))


def pdf_text_view(data: bytes) -> str:
    """A PDF's uncompressed syntax plus its decoded non-binary streams, for string scans."""
    parts: list[bytes] = []
    last = 0
    for s in pdf_streams(data):
        parts.append(data[last : s.start])
        last = s.end
        if s.decoded and not _PDF_BINARY.search(s.head):
            parts.append(s.data)
    parts.append(data[last:])
    return b"\n".join(parts).decode("latin-1")


# --------------------------------------------------------------------------- checks
def _where(rel: str, text: str, pos: int, is_pdf: bool) -> str:
    return rel if is_pdf else f"{rel}:{text.count(chr(10), 0, pos) + 1}"


def scan_package(files: dict[str, bytes], mode: str, root: Path, rep: Report) -> None:
    """No packaged file contains an internal marker or a local absolute path."""
    homes = (root, root.as_posix(), Path.home(), Path.home().as_posix())
    literals = {str(p).lower() for p in homes}
    found: set[str] = set()
    scanned = 0
    for rel, data in files.items():
        is_pdf = Path(rel).suffix.lower() == ".pdf"
        if Path(rel).suffix.lower() in (".png", ".jpg", ".jpeg", ".jbig2", ".jb2"):
            rep.note(f"{rel}: raster image, not scanned for strings")
            continue
        scanned += 1
        text = pdf_text_view(data) if is_pdf else data.decode("utf-8", "replace")
        for s in FORBIDDEN:
            pos = text.find(s)
            if pos < 0:
                continue
            found.add(s)
            msg = f"{_where(rel, text, pos, is_pdf)}: contains '{s}' ({text.count(s)}x)"
            if mode == "draft-test" and s in DRAFT_EXPECTED:
                rep.warn(msg + " -- the draft keeps its stubs; a final build has none")
            else:
                rep.error(msg)
        low = text.lower()
        hits = sorted([(m.start(), m.group(0)) for rx in LOCAL_PATH_RES
                       for m in [rx.search(text)] if m]
                      + [(low.find(lit), lit) for lit in literals if lit in low])
        if hits:
            found.add("local absolute path")
            pos, snippet = hits[0]
            rep.error(f"{_where(rel, text, pos, is_pdf)}: local absolute path '{snippet}'")
    absent = [f"'{s}'" for s in FORBIDDEN if s not in found]
    absent += [] if "local absolute path" in found else ["local absolute path"]
    if absent:
        rep.note(f"scan: no {', '.join(absent)} in the {scanned} scanned files "
                 "(PDFs: syntax and decoded text streams)")


def _pdflatex_cmd(exe: str) -> list[str]:
    cmd = [exe, "-interaction=nonstopmode", "-halt-on-error"]
    version = subprocess.run([exe, "--version"], capture_output=True, text=True,
                             errors="replace", check=False).stdout
    if "MiKTeX" in version:
        cmd.append("--disable-installer")  # no on-the-fly package downloads
    return [*cmd, MAIN]


def run_pdflatex(work: Path) -> tuple[int, str, int]:
    """pdflatex in `work` (two runs, more while LaTeX asks for one): (exit, log, runs)."""
    exe = shutil.which("pdflatex")
    if exe is None:
        return -1, "pdflatex not found on PATH", 0
    cmd = _pdflatex_cmd(exe)
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in ("TEXINPUTS", "BIBINPUTS", "BSTINPUTS")}
    rc, log, runs = 0, "", 0
    for runs in range(1, MAX_RUNS + 1):
        try:
            proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True,
                                  errors="replace", env=env, check=False, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return -2, f"! pdflatex did not finish: {exc}", runs
        rc = proc.returncode
        log_path = work / f"{JOBNAME}.log"
        log = log_path.read_text(encoding="latin-1") if log_path.is_file() else proc.stdout
        if rc != 0 or (runs >= MIN_RUNS and not _RERUN_RE.search(log)):
            break
    return rc, log, runs


def pdf_text(pdf: Path, exe: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "text.txt"
        subprocess.run([exe, "-enc", "UTF-8", str(pdf), str(out)], capture_output=True,
                       check=True)
        return out.read_text(encoding="utf-8", errors="replace")


def _first_text_difference(a: str, b: str) -> str:
    pages_a, pages_b = a.split("\f"), b.split("\f")
    for k, (pa, pb) in enumerate(zip(pages_a, pages_b, strict=False), start=1):
        wa, wb = pa.split(), pb.split()
        if wa != wb:
            i = next((j for j, (x, y) in enumerate(zip(wa, wb, strict=False)) if x != y),
                     min(len(wa), len(wb)))
            return (f"page {k}: main.pdf {' '.join(wa[i:i + 8])!r} vs package "
                    f"{' '.join(wb[i:i + 8])!r}")
    return f"page counts in the text differ ({len(pages_a)} vs {len(pages_b)})"


def verify_build(root: Path, src: Path, rep: Report, keep: bool = False) -> None:
    """Compile the package alone and compare the result with the build's main.pdf."""
    parent = Path(tempfile.mkdtemp(prefix="sremu-arxiv-verify-"))
    work = parent / "build"
    try:
        shutil.copytree(src, work)
        rc, log, runs = run_pdflatex(work)
        if runs == 0:
            rep.error("verify: pdflatex not found on PATH; the package was not compiled")
            return
        if rc != 0:
            first = re.search(r"^!.*$", log, re.M)
            rep.error(f"verify: pdflatex failed (exit {rc}, run {runs})"
                      + (f": {first.group(0)}" if first else ""))
            return
        undefined = sorted(set(_UNDEFINED_RE.findall(log)))
        for kind, key in undefined[:10]:
            rep.error(f"verify: {kind.lower()} {key} undefined")
        if "There were undefined" in log and not undefined:
            rep.error("verify: LaTeX reports undefined references")
        if _RERUN_RE.search(log):
            rep.warn(f"verify: LaTeX still asks for a rerun after {runs} runs")
        src_pdf, pkg_pdf = root / f"{JOBNAME}.pdf", work / f"{JOBNAME}.pdf"
        a, b = src_pdf.read_bytes(), pkg_pdf.read_bytes()
        pa, pb = pdf_page_count(a), pdf_page_count(b)
        written = re.search(r"Output written on \S+ \((\d+) pages?", log)
        if written and int(written.group(1)) != pb:
            rep.warn(f"verify: log says {written.group(1)} pages, the PDF has {pb}")
        if not undefined:
            rep.note(f"verify: pdflatex x{runs} (no BibTeX) exit 0, no undefined reference "
                     "or citation")
        (rep.note if pa == pb else rep.error)(
            f"verify: pages {pb} (package) {'=' if pa == pb else '!='} {pa} ({src_pdf.name})")
        exe = shutil.which("pdftotext")
        try:
            ta, tb = (pdf_text(src_pdf, exe), pdf_text(pkg_pdf, exe)) if exe else ("", "")
        except (OSError, subprocess.CalledProcessError) as exc:
            rep.warn(f"verify: pdftotext failed ({exc}); comparing PDF sizes instead")
            exe = None
        if exe:
            if " ".join(ta.split()) == " ".join(tb.split()):
                rep.note(f"verify: text identical (pdftotext, {len(' '.join(ta.split()))} "
                         "characters, whitespace-normalized)")
            else:
                rep.error("verify: text differs: " + _first_text_difference(ta, tb))
        else:
            rel = abs(len(a) - len(b)) / max(len(a), 1)
            (rep.note if rel <= SIZE_TOLERANCE else rep.error)(
                f"verify: without pdftotext: PDF sizes {len(b)} vs {len(a)} bytes "
                f"({100 * rel:.2f} % apart, tolerance {100 * SIZE_TOLERANCE:.0f} %)")
        fa, fb = rendered_fingerprint(a), rendered_fingerprint(b)
        if fa == fb:
            rep.note(f"verify: rendered content identical ({sum(fa.values())} page, font, "
                     "figure and image streams)")
        else:
            rep.error(f"verify: rendered content differs ({sum((fa - fb).values())} streams "
                      f"only in {src_pdf.name}, {sum((fb - fa).values())} only in the package)")
    finally:
        if keep:
            rep.note(f"verify: build kept in {work}")
        else:
            shutil.rmtree(parent, ignore_errors=True)


# --------------------------------------------------------------------------- outputs
def _tex_plain(s: str) -> str:
    s = re.sub(r"%\n\s*", "", s).replace("%", "")
    s = s.replace("\\\\", " ").replace("~", " ")
    s = re.sub(r"\\[A-Za-z]+\*?\s*", "", s)
    return " ".join(s.replace("{", "").replace("}", "").split())


def _macro_arg(text: str, macro: str) -> str | None:
    m = re.search(r"\\" + macro + r"\s*(?:\[[^\]]*\])?\s*\{", text)
    if m is None:
        return None
    return text[m.end() : _group_end(text, m.end() - 1) - 1]


def _drop_calls(text: str, macro: str) -> str:
    while True:
        m = re.search(r"\\" + macro + r"\s*\{", text)
        if m is None:
            return text
        text = text[: m.start()] + text[_group_end(text, m.end() - 1) :]


def paper_title(main_tex: str) -> str:
    arg = _macro_arg(strip_comments(main_tex), "title")
    return _tex_plain(arg) if arg else ""


def paper_authors(main_tex: str) -> list[str]:
    arg = _macro_arg(strip_comments(main_tex), "author")
    if not arg:
        return []
    body = _drop_calls(_drop_calls(arg, "thanks"), "IEEEauthorrefmark")
    body = _tex_plain(body)
    return [a.strip() for a in re.split(r",|\band\b", body) if a.strip()]


def float_counts(root: Path, files: dict[str, bytes]) -> tuple[int, int]:
    """(figures, tables) with a caption: the build's list-of entries (main.aux), else the
    figure/table environments of the package."""
    aux = root / f"{JOBNAME}.aux"
    if aux.is_file():
        text = aux.read_text(encoding="latin-1")
        figs = len(re.findall(r"\\@writefile\{lof\}\{\\contentsline \{figure\}", text))
        tabs = len(re.findall(r"\\@writefile\{lot\}\{\\contentsline \{table\}", text))
        if figs or tabs:
            return figs, tabs
    tex = "".join(d.decode("utf-8", "replace") for r, d in files.items() if r.endswith(".tex"))
    return (len(re.findall(r"\\begin\{figure\*?\}", tex)),
            len(re.findall(r"\\begin\{table\*?\}", tex)))


def write_metadata(root: Path, out: Path, files: dict[str, bytes], mode: str,
                   archive: str) -> str:
    main = read_text(root / MAIN)
    pages = pdf_page_count((root / f"{JOBNAME}.pdf").read_bytes())
    figs, tabs = float_counts(root, files)
    comments = ", ".join(f"{n} {word}{'' if n == 1 else 's'}"
                         for n, word in ((pages, "page"), (figs, "figure"), (tabs, "table")))
    lines = []
    if mode == "draft-test":
        lines += [f"{DRAFT_LABEL}: a draft build packaged for testing; do not submit.", ""]
    lines += [
        f"Title: {paper_title(main)}",
        f"Authors: {', '.join(paper_authors(main))}",
        f"Primary category: {PRIMARY_CATEGORY}",
        f"Cross-list: {', '.join(CROSS_LISTS)}",
        f"Comments: {comments}",
        "Abstract: abstract.txt (tools/check_arxiv.py --emit-plain)",
        f"Source archive: {archive} (main file main.tex, pdfLaTeX; {JOBNAME}.bbl included, "
        "no .bib)",
        "",
        "Notes (README, arXiv metadata):",
        "- No repository URL in the comments field until it resolves publicly.",
        "- The arXiv paper licence is a separate choice from the code licence (human gate H-2).",
    ]
    text = "\n".join(lines) + "\n"
    (out / "metadata.txt").write_text(text, encoding="utf-8", newline="\n")
    return comments


def emit_abstract(root: Path, out: Path, mode: str, rep: Report) -> None:
    dest = out / "abstract.txt"
    proc = subprocess.run(
        [sys.executable, str(TOOLS / "check_arxiv.py"), "--paper-root", str(root),
         "--emit-plain", str(dest)],
        capture_output=True, text=True, errors="replace", check=False)
    for line in proc.stdout.splitlines():
        if "plain abstract" in line:
            kind, _, msg = line.strip().partition(": ")
            (rep.warn if kind == "warning" else rep.note)(
                "abstract.txt: " + msg.split(" -> ")[0])
    if proc.returncode != 0 or not dest.is_file():
        errors = [ln.strip() for ln in proc.stdout.splitlines() if "ERROR" in ln][:5]
        (rep.error if mode == "final" else rep.warn)(
            f"check_arxiv --emit-plain exit {proc.returncode}: " + "; ".join(errors))


def write_tarball(files: dict[str, bytes], dest: Path, mtime: int) -> None:
    """Reproducible .tar.gz: sorted members at the archive root, fixed owner, mode and time."""
    with dest.open("wb") as raw, \
            gzip.GzipFile(filename=dest.name[: -len(".gz")], mode="wb", fileobj=raw,
                          mtime=mtime) as gz, \
            tarfile.open(fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for rel in sorted(files):
            info = tarfile.TarInfo(rel)
            info.size = len(files[rel])
            info.mtime = mtime
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(files[rel]))


def default_out(root: Path, mode: str) -> Path:
    if mode == "final":
        return root / "arxiv"
    return Path(tempfile.gettempdir()) / f"{ARCHIVE_STEM}-{DRAFT_LABEL}"


def prepare_out(out: Path, root: Path) -> None:
    out = out.resolve()
    if out == root.resolve() or root.resolve().is_relative_to(out):
        raise ValueError(f"--out {out} would overwrite the paper")
    if out.exists():
        if any(out.iterdir()) and not (out / MARKER).is_file():
            raise ValueError(f"{out} exists and was not written by this tool; choose --out")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / MARKER).write_text("written by tools/make_arxiv_package.py; replaced on each run\n",
                              encoding="utf-8", newline="\n")


def _file_table(files: dict[str, bytes]) -> list[str]:
    return [f"  {len(d):>9}  {rel}" for rel, d in files.items()]


# --------------------------------------------------------------------------- driver
def make_package(paper_root: Path = PAPER_ROOT, *, mode: str = "final",
                 out: Path | None = None, keep_build: bool = False,
                 verify: bool = True) -> Report:
    rep = Report(f"make_arxiv_package ({mode})")
    if mode not in MODES:
        rep.error(f"bad mode {mode!r}")
        return rep
    root = paper_root.resolve()
    built = build_mode(root)
    if mode == "final" and built != "final":
        rep.error(f"generated/buildmode.tex says {built or 'nothing (missing)'}: --mode final "
                  "needs a final build (tools/build_paper.py --mode final); --mode draft-test "
                  "packages a draft for testing")
        return rep
    if mode == "draft-test":
        rep.note(f"draft-test of a {built or 'unknown-mode'} build: output labelled {DRAFT_LABEL}")
    try:
        plan = resolve(root)
    except (OSError, ValueError) as exc:
        rep.error(f"files: {exc}")
        return rep
    cross_check_build(root, plan, rep)
    check_current(root, plan, rep)
    if not rep.ok:
        rep.error("nothing written: rebuild or fix the file list first")
        return rep
    for note in plan.notes:
        rep.note(f"files: {note}")
    if plan.distribution:
        rep.note("from the TeX distribution (not shipped): " + ", ".join(plan.distribution))
    for name, version in distribution_versions(root, plan.distribution).items():
        rep.note(f"{name} read by the build: [{version}] from the TeX distribution; the "
                 "paper ships no modified copy, so arXiv uses its own TeX Live copy")
    for omitted in plan.omitted:
        rep.note(f"omitted: {omitted}")
    try:
        files = transform(root, plan, rep)
    except (OSError, ValueError) as exc:
        rep.error(f"strip: {exc}")
        return rep
    out = (out or default_out(root, mode)).resolve()
    try:
        prepare_out(out, root)
    except (OSError, ValueError) as exc:
        rep.error(f"output: {exc}")
        return rep
    src = out / SOURCE_DIR
    for rel, data in files.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_bytes(data)
    by_kind = Counter(plan.files[r] for r in files)
    total = sum(len(d) for d in files.values())
    rep.note(f"package: {len(files)} files (" + ", ".join(f"{v} {k}" for k, v in
             sorted(by_kind.items())) + f"), {total / 1e6:.2f} MB uncompressed")
    if total > ARXIV_MAX_BYTES:
        rep.error(f"package is {total / 1e6:.1f} MB > arXiv's {ARXIV_MAX_BYTES / 1e6:.0f} MB")
    scan_package(files, mode, root, rep)
    if verify:
        verify_build(root, src, rep, keep=keep_build)
    else:
        rep.warn("verify: skipped (the package was not compiled)")
    emit_abstract(root, out, mode, rep)
    stem = ARCHIVE_STEM if mode == "final" else f"{DRAFT_LABEL}-{ARCHIVE_STEM}"
    archive = out / f"{stem}.tar.gz"
    comments = write_metadata(root, out, files, mode, archive.name)
    rep.note(f"metadata.txt: comments '{comments}', {PRIMARY_CATEGORY} "
             f"(cross-list {', '.join(CROSS_LISTS)})")
    if rep.ok:
        write_tarball(files, archive, int((root / f"{JOBNAME}.pdf").stat().st_mtime))
        size = archive.stat().st_size
        rep.note(f"archive: {archive} ({size / 1e6:.2f} MB; limit {ARXIV_MAX_BYTES / 1e6:.0f} "
                 f"MB; sha256 {hashlib.sha256(archive.read_bytes()).hexdigest()[:16]}...)")
    else:
        rep.error(f"archive not written: fix the errors above (sources left in {src})")
    (out / "verification.txt").write_text(
        rep.render() + "\n\nfiles (bytes, path):\n" + "\n".join(_file_table(files)) + "\n",
        encoding="utf-8", newline="\n")
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="arXiv source package of a completed build "
                                             "(offline; see the module docstring).")
    ap.add_argument("--mode", choices=MODES, default="final")
    ap.add_argument("--out", type=Path, default=None,
                    help="output directory (default: <paper>/arxiv, or a temp directory for "
                         "draft-test)")
    ap.add_argument("--paper-root", type=Path, default=PAPER_ROOT)
    ap.add_argument("--keep-build", action="store_true",
                    help="keep the temporary verification build")
    args = ap.parse_args(argv)
    rep = make_package(args.paper_root, mode=args.mode, out=args.out, keep_build=args.keep_build)
    return rep.emit()


if __name__ == "__main__":
    raise SystemExit(main())
