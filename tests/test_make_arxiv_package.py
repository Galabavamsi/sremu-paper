"""WP12: make_arxiv_package.py -- the arXiv source package keeps every token TeX reads, carries
exactly the files the paper needs, and compiles alone to the same PDF as the build."""

from __future__ import annotations

import re
import shutil
import subprocess
import tarfile
import zlib
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import PAPER_ROOT
from make_arxiv_package import (
    DRAFT_LABEL,
    MARKER,
    Plan,
    build_mode,
    drop_definition,
    fls_inputs,
    make_package,
    null_macros,
    paper_authors,
    paper_title,
    pdf_page_count,
    pdf_text_view,
    prepare_out,
    rendered_fingerprint,
    resolve,
    scan_package,
    strip_comments,
    strip_null_calls,
    transform,
    write_tarball,
)
from paper_common import Report

unit = pytest.mark.unit
needs_build = pytest.mark.skipif(not (PAPER_ROOT / "main.bbl").is_file(),
                                 reason="no build in the paper root (main.bbl)")


# --------------------------------------------------------------------------- a TeX tokenizer
def tex_tokens(text: str) -> list[str]:
    """The tokens TeX's input processor makes under standard catcodes (TeXbook, ch. 8):
    control sequences, characters, ' ' for a space token and '\\par' for an empty line."""
    toks: list[str] = []
    for raw in text.split("\n"):
        line = raw.rstrip(" ")
        state, i = "N", 0
        while i < len(line):
            c = line[i]
            if c == "%":
                break
            if c == "\\":
                m = re.match(r"\\(?:([A-Za-z@]+)|(.))", line[i:])
                if m is None:  # a backslash ends the line: the control symbol \^^M
                    toks.append("\\^^M")
                    break
                toks.append(m.group(0))
                state = "S" if m.group(1) or m.group(2) == " " else "M"
                i += m.end()
                continue
            if c in " \t":
                if state == "M":
                    toks.append(" ")
                if state != "N":
                    state = "S"
            else:
                toks.append(c)
                state = "M"
            i += 1
        else:
            if state == "N":
                toks.append("\\par")
            elif state == "M":
                toks.append(" ")
    return toks


def expand_empty(toks: list[str], names: tuple[str, ...] = ("\\claim",)) -> list[str]:
    """Expand the calls of one-argument macros that print nothing (TeX skips spaces before an
    undelimited argument)."""
    out: list[str] = []
    i = 0
    while i < len(toks):
        if toks[i] not in names:
            out.append(toks[i])
            i += 1
            continue
        j = i + 1
        while toks[j] == " ":
            j += 1
        assert toks[j] == "{", toks[i : j + 3]
        depth = 0
        for k in range(j, len(toks)):
            depth += {"{": 1, "}": -1}.get(toks[k], 0)
            if depth == 0:
                i = k + 1
                break
    return out


def no_empty_groups(toks: list[str]) -> list[str]:
    """An empty group {} is a no-op outside math mode: drop it for the comparison."""
    out: list[str] = []
    for t in toks:
        if t == "}" and out and out[-1] == "{":
            out.pop()
        else:
            out.append(t)
    return out


def same_tokens(before: str, after: str) -> bool:
    return no_empty_groups(expand_empty(tex_tokens(before))) == no_empty_groups(tex_tokens(after))


# --------------------------------------------------------------------------- comments
@unit
@pytest.mark.parametrize(("src", "want"), [
    ("a % c\nb\n", "a %\nb\n"),
    ("a% c\n", "a%\n"),
    ("text\n  % whole line\nmore\n", "text\nmore\n"),
    ("% header\n\\section{A}\n", "\\section{A}\n"),
    ("50\\% done % c\n", "50\\% done %\n"),
    ("line\\\\% c\n", "line\\\\%\n"),
    ("keep %\n", "keep %\n"),
    ("a\r\n% c\r\nb\r\n", "a\nb\n"),
    ("\\verb|a%b| c % d\n", "\\verb|a%b| c %\n"),
    ("see \\url{http://x.org/a%20b} % d\n", "see \\url{http://x.org/a%20b} %\n"),
    ("\\href{http://x.org/a%20b}{t%x} % d\n", "\\href{http://x.org/a%20b}{t%\n"),
    ("\\begin{verbatim}\nx % y\n% z\n\\end{verbatim} % w\nq\n",
     "\\begin{verbatim}\nx % y\n% z\n\\end{verbatim} %\nq\n"),
    ("\\newcommand{\\x}{%\n  a%\n}% def\n", "\\newcommand{\\x}{%\n  a%\n}%\n"),
])
def test_strip_comments(src: str, want: str) -> None:
    got = strip_comments(src)
    assert got == want
    assert strip_comments(got) == got  # idempotent
    if "verbatim" not in src and "\\verb" not in src and "url" not in src:
        assert tex_tokens(got) == tex_tokens(src.replace("\r\n", "\n"))


@unit
def test_strip_comments_refuses_catcode_changes() -> None:
    with pytest.raises(ValueError, match="catcode"):
        strip_comments("\\catcode`\\%=12\n")


# --------------------------------------------------------------------------- empty macros
@unit
@pytest.mark.parametrize(("src", "want"), [
    ("x\\claim{A}y\n", "xy\n"),
    ("x. \\claim{A}Next\n", "x. Next\n"),
    ("\\claim{C-GAP}To our\n", "To our\n"),
    ("  \\claim{A}The item\n", "  The item\n"),
    ("x \\claim{A} y\n", "x {} y\n"),  # two space tokens stay two
    ("text\n\\claim{A}\nmore\n", "text\n{}\nmore\n"),  # never an empty line (a \par)
    ("text \\claim{A}\nmore\n", "text {}\nmore\n"),
    ("\\sremu\\claim{A} is\n", "\\sremu{} is\n"),  # the space after \sremu stays
    ("\\sremu\\claim{A}is\n", "\\sremu{}is\n"),  # no \sremuis
    ("a\\\\\\claim{A}[2pt]\n", "a\\\\{}[2pt]\n"),  # \\ must not see the [
    ("50\\%\\claim{A} done\n", "50\\%{} done\n"),
    ("x\\claim{A{b}c}y\n", "xy\n"),
    ("x \\claim\n  {A}y\n", "x y\n"),
    ("a\\claim{A}%\nb\n", "a%\nb\n"),
    ("\\\\claim{A}\n", "\\\\claim{A}\n"),  # a line break, then text
    ("\\newcommand{\\claim}[1]{}\n", "\\newcommand{\\claim}[1]{}\n"),
])
def test_strip_null_calls_keeps_tokens(src: str, want: str) -> None:
    got, n = strip_null_calls(src, {"claim": 1}, "t.tex")
    assert got == want
    assert n == src.count("\\claim") - want.count("\\claim")
    if "newcommand" not in src and not src.startswith("\\\\claim"):
        assert same_tokens(src, got), (tex_tokens(src), tex_tokens(got))


@unit
def test_naive_removal_would_change_tokens() -> None:
    # the reason for {}: plain deletion merges spaces, control words and lines
    for src, naive in (("x \\claim{A} y\n", "x  y\n"), ("\\sremu\\claim{A} is\n", "\\sremu is\n"),
                       ("text\n\\claim{A}\nmore\n", "text\n\nmore\n")):
        assert not same_tokens(src, naive)


@unit
@pytest.mark.parametrize("src", ["$a\\claim{A}b$\n", "\\claim A\n"])
def test_strip_null_calls_refuses_unsafe_calls(src: str) -> None:
    with pytest.raises(ValueError, match="claim"):
        strip_null_calls(src, {"claim": 1}, "t.tex")


@unit
def test_null_macros() -> None:
    texts = {"m.tex": strip_comments(
        "\\newcommand{\\claim}[1]{}\n\\newcommand{\\quiet}{ % nothing\n}\n"
        "\\newcommand{\\opt}[1][d]{}\n\\def\\gone#1#2{}\n\\newcommand{\\loud}{x}\n")}
    assert null_macros(texts) == {"claim": 1, "quiet": 0, "gone": 2}


@unit
def test_real_macros_file_defines_claim_as_empty() -> None:
    text = strip_comments((PAPER_ROOT / "sremu-macros.tex").read_text(encoding="utf-8"))
    assert null_macros({"sremu-macros.tex": text}) == {"claim": 1}


@unit
def test_drop_definition() -> None:
    text = "\\newif\\ifx\n\n\\newcommand{\\claim}[1]{}\n\n\\newcommand{\\stub}[1]{%\n  a #1%\n}\n"
    out, ok = drop_definition(text, "claim")
    assert ok
    assert out == "\\newif\\ifx\n\n\\newcommand{\\stub}[1]{%\n  a #1%\n}\n"
    out, ok = drop_definition(out, "stub")
    assert ok
    assert out == "\\newif\\ifx\n\n"
    assert drop_definition("x \\newcommand{\\claim}[1]{}\n", "claim") == (
        "x \\newcommand{\\claim}[1]{}\n", False)  # shares a line: kept


# --------------------------------------------------------------------------- the real paper
@unit
@needs_build
def test_resolver_finds_exactly_what_main_tex_needs() -> None:
    plan = resolve(PAPER_ROOT)
    files = set(plan.files)
    main = strip_comments((PAPER_ROOT / "main.tex").read_text(encoding="utf-8"))
    for target in re.findall(r"\\input\{([^}]*)\}", main):
        assert f"{target}.tex" in files
    sections = "".join(strip_comments(p.read_text(encoding="utf-8"))
                       for p in (PAPER_ROOT / "sections").rglob("*.tex"))
    for fid in re.findall(r"\\sremufigure\{(F\d+)\}", sections):
        if (PAPER_ROOT / "figures" / "out" / f"{fid}.pdf").is_file():
            assert plan.files[f"figures/out/{fid}.pdf"] == "graphics"
    assert plan.files["main.bbl"] == "bbl"
    assert not [f for f in files if Path(f).suffix in (".bib", ".yaml", ".py", ".md", ".json")]
    assert not [f for f in files if f.startswith(("tests/", "tools/", "sweeps/", "figures/src"))]
    assert "refs.bib (arXiv uses main.bbl)" in plan.omitted
    assert "IEEEtran.cls" in plan.distribution
    want = {"sremu-macros.tex", "generated/numbers.tex"}
    if (PAPER_ROOT / "generated" / "buildmode.tex").is_file():
        want.add("generated/buildmode.tex")
    assert plan.preamble == want


@unit
@needs_build
def test_stripped_sources_keep_every_token() -> None:
    """Every body file of the real paper reads as the same tokens once \\claim is expanded."""
    plan = resolve(PAPER_ROOT)
    out = transform(PAPER_ROOT, plan, Report("t"))
    checked = 0
    for rel, kind in plan.files.items():
        if kind != "tex" or rel in plan.preamble:
            continue  # the preamble loses the definitions of unused internal macros
        before = (PAPER_ROOT / rel).read_text(encoding="utf-8")
        after = out[rel].decode("utf-8")
        if rel == "main.tex":
            assert after.startswith("\\pdfoutput=1\n")
            after = after[len("\\pdfoutput=1\n"):]
        assert "\\claim" not in after, rel
        assert re.search(r"(?m)^\s*%", after) is None, rel  # no whole-line comment left
        assert re.search(r"(?<!\\)%.", after) is None, rel  # no comment text left
        assert same_tokens(before, after), rel
        checked += 1
    assert checked >= 30


@unit
def test_title_and_authors_of_main_tex() -> None:
    main = (PAPER_ROOT / "main.tex").read_text(encoding="utf-8")
    assert paper_title(main) == ("Timed RIS Control in the Loop: A Sample-Accurate IQ-Level "
                                 "Emulator with Characterized Timing")
    assert paper_authors(main) == ["Galaba Vamsi", "Shubhika Mishra", "Anurag Jaiswal",
                                   "Smriti Kumar", "Mansi Ambwani", "Arzad Alam Kherani"]


@unit
@pytest.mark.skipif(not (PAPER_ROOT / "main.pdf").is_file(), reason="no main.pdf")
def test_page_count_of_the_build_matches_its_log() -> None:
    log = (PAPER_ROOT / "main.log").read_text(encoding="latin-1")
    m = re.search(r"Output written on main\.pdf \((\d+) pages", log)
    if m is None or (PAPER_ROOT / "main.log").stat().st_mtime < (
            PAPER_ROOT / "main.pdf").stat().st_mtime - 5:
        pytest.skip("main.log is not the log of main.pdf")
    assert pdf_page_count((PAPER_ROOT / "main.pdf").read_bytes()) == int(m.group(1))


# --------------------------------------------------------------------------- checks
def _tiny_pdf(text: bytes, pages: int = 2) -> bytes:
    content = zlib.compress(b"BT /F1 12 Tf 10 10 Td (" + text + b") Tj ET")
    kids = b" ".join(b"%d 0 R" % (3 + k) for k in range(pages))
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Count %d /Kids [%s] >>" % (pages, kids)]
    objs += [b"<< /Type /Page /Parent 2 0 R /Contents %d 0 R >>" % (3 + pages)] * pages
    objs.append(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(content) + content
                + b"\nendstream")
    out = b"%PDF-1.5\n"
    for i, o in enumerate(objs, 1):
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    return out + b"trailer\n<< /Root 1 0 R /Info << /CreationDate (D:2026) >> >>\n%%EOF\n"


@unit
def test_pdf_helpers() -> None:
    a, b = _tiny_pdf(b"hello", 3), _tiny_pdf(b"hellO", 3)
    assert pdf_page_count(a) == 3
    assert rendered_fingerprint(a) == rendered_fingerprint(_tiny_pdf(b"hello", 3))
    assert rendered_fingerprint(a) != rendered_fingerprint(b)
    assert "(hello) Tj" in pdf_text_view(a)


@unit
def test_scan_finds_markers_and_local_paths(tmp_path: Path) -> None:
    files = {
        "a.tex": b"x\n% TODO later\n",
        "b.tex": b"\\input{D:/projects/ris/paper/x}\n",
        "c.tex": b"see C:\\Users\\me\\x\n",
        "d.tex": b"/home/me/paper/x\n",
        "e.tex": b"\\claim{C-1}x\n",
        "f.tex": b"\\sremustub{x} [Planned] y\n",
        "g.pdf": _tiny_pdf(b"C:/Users/me/fig.py"),
        "ok.tex": b"https://example.org/a/b and Fig.~3: \\ref{x}\n",
    }
    rep = Report("t")
    scan_package(files, "final", tmp_path, rep)
    errs = "\n".join(rep.errors)
    for needle in ("a.tex:2: contains 'TODO'", "b.tex:1: local absolute path 'D:/'",
                   "c.tex:1: local absolute path", "d.tex:1: local absolute path",
                   "e.tex:1: contains '\\claim{'", "f.tex:1: contains 'sremustub'",
                   "f.tex:1: contains 'Planned]'", "g.pdf: local absolute path"):
        assert needle in errs, needle
    assert "ok.tex" not in errs
    draft = Report("t")
    scan_package({"f.tex": files["f.tex"]}, "draft-test", tmp_path, draft)
    assert draft.ok
    assert len(draft.warnings) == 2


@unit
def test_tarball_is_reproducible(tmp_path: Path) -> None:
    files = {"sections/b.tex": b"b\n", "main.tex": b"m\n", "figures/out/F1.pdf": b"%PDF\n"}
    a, b = tmp_path / "a" / "x.tar.gz", tmp_path / "b" / "x.tar.gz"
    a.parent.mkdir()
    b.parent.mkdir()
    write_tarball(files, a, 1_700_000_000)
    write_tarball(dict(reversed(files.items())), b, 1_700_000_000)
    assert a.read_bytes() == b.read_bytes()
    with tarfile.open(a) as tar:
        members = tar.getmembers()
        assert [m.name for m in members] == sorted(files)
        assert {(m.mode, m.uid, m.gid, m.mtime) for m in members} == {
            (0o644, 0, 0, 1_700_000_000)}
        assert tar.extractfile("main.tex").read() == b"m\n"


@unit
def test_prepare_out_replaces_only_its_own_directory(tmp_path: Path) -> None:
    paper = tmp_path / "paper"
    paper.mkdir()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="not written by this tool"):
        prepare_out(foreign, paper)
    with pytest.raises(ValueError, match="overwrite the paper"):
        prepare_out(tmp_path, paper)
    ours = tmp_path / "ours"
    prepare_out(ours, paper)
    (ours / "old.txt").write_text("x", encoding="utf-8")
    prepare_out(ours, paper)
    assert sorted(p.name for p in ours.iterdir()) == [MARKER]


def _minimal(tmp_path: Path, mode_line: str | None) -> Path:
    paper = tmp_path / "paper"
    (paper / "generated").mkdir(parents=True)
    (paper / "main.tex").write_text("\\documentclass{article}\n\\begin{document}\nx\n"
                                    "\\end{document}\n", encoding="utf-8")
    if mode_line is not None:
        (paper / "generated" / "buildmode.tex").write_text(f"% mode\n{mode_line}\n",
                                                          encoding="utf-8")
    return paper


@unit
@pytest.mark.parametrize("mode_line", [None, "\\sremudrafttrue"])
def test_final_mode_refuses_a_draft_build(tmp_path: Path, mode_line: str | None) -> None:
    paper = _minimal(tmp_path, mode_line)
    assert build_mode(paper) == (None if mode_line is None else "draft")
    rep = make_package(paper, mode="final", out=tmp_path / "out")
    assert not rep.ok
    assert any("needs a final build" in e for e in rep.errors)
    assert not (tmp_path / "out").exists()


@unit
def test_stale_build_writes_nothing(tmp_path: Path) -> None:
    paper = _minimal(tmp_path, "\\sremudraftfalse")
    assert build_mode(paper) == "final"
    rep = make_package(paper, mode="final", out=tmp_path / "out")
    assert any("main.pdf missing" in e for e in rep.errors)
    assert not (tmp_path / "out").exists()


# --------------------------------------------------------------------------- compiled
SYN_MAIN = r"""% main.tex of a synthetic paper (header comment)
\documentclass{article}
\usepackage{graphicx}
\usepackage{url}
\usepackage[hidelinks]{hyperref}
\input{sremu-macros}
\input{generated/numbers}
\begin{document}
\title{A Small Test:\\ Of Packaging}
\author{Ann~Author,
        and~Bob~Builder%
\thanks{Somewhere.}}
\maketitle
\input{sections/abstract}
\section{One}\label{sec:one}
\claim{C-1}Text with a comment. % a comment to strip
Fifty\% done, see Section~\ref{sec:two} and~\cite{key1}.\claim{C-2}
\sremu\claim{C-3} is here.
A \claim{C-4} B.
\claim{C-5}
line after.
\begin{figure}[t]\centering
\includegraphics[width=2cm]{figs/box}% no extension
\caption{A box.}\label{fig:box}
\end{figure}
\input{sections/s2}
\bibliographystyle{plain}
\bibliography{refs}
\end{document}
"""
SYN_FILES = {
    "sremu-macros.tex": "% macros of the synthetic paper\n\\newif\\ifsremudraft\n"
                        "\\sremudrafttrue\n\\InputIfFileExists{generated/buildmode}{}{}\n\n"
                        "\\newcommand{\\claim}[1]{}\n\n"
                        "\\newcommand{\\sremustub}[1]{%\n  \\ifsremudraft [Planned] #1\\fi}\n\n"
                        "\\newcommand{\\sremu}{\\texttt{sremu}}\n",
    "generated/numbers.tex": "% generated\n\\newcommand{\\numX}{42}% definition:x\n",
    "sections/abstract.tex": "\\begin{abstract}\nWe test \\numX{} things.\n\\end{abstract}\n",
    "sections/s2.tex": "\\section{Two}\\label{sec:two}\n% whole-line comment\n"
                       "A URL \\url{http://example.org/a%20b} and 50\\,\\% of it.%\n"
                       "\\begin{verbatim}\nkeep % this\n\\end{verbatim}\nDone.\n",
    "sections/unused.tex": "Not included.\n",
    "main.bbl": "\\begin{thebibliography}{1}\n\\bibitem{key1}\nA.~Author, ``A title,'' "
                "2020.\n\\end{thebibliography}\n",
    "refs.bib": "@misc{key1, title={A title}}\n",
    "numbers.yaml": "macros:\n  - macro: numX\n    description: d\n"
                    "    source: {type: definition, id: x}\n    value: 42\n",
    "README.md": "notes\n",
    "claims.yaml": "claims: []\n",
}
SYN_PACKAGE = sorted(["figs/box.pdf", "generated/buildmode.tex", "generated/numbers.tex",
                      "main.bbl", "main.tex", "sections/abstract.tex", "sections/s2.tex",
                      "sremu-macros.tex"])


def _pdflatex(cwd: Path, name: str, runs: int) -> None:
    for _ in range(runs):
        subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "-recorder",
                        name], cwd=cwd, capture_output=True, check=True)


@pytest.fixture(scope="module")
def built_paper(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A small paper with the features of the real one, built like build_paper.py (final)."""
    paper = tmp_path_factory.mktemp("syn") / "paper"
    for rel, text in {**SYN_FILES, "main.tex": SYN_MAIN}.items():
        (paper / rel).parent.mkdir(parents=True, exist_ok=True)
        (paper / rel).write_text(text, encoding="utf-8", newline="\n")
    (paper / "generated" / "buildmode.tex").write_text(
        "% GENERATED by tools/build_paper.py: build mode = final\n\\sremudraftfalse\n",
        encoding="utf-8", newline="\n")
    fig = paper.parent / "fig"
    fig.mkdir()
    (fig / "box.tex").write_text("\\documentclass{article}\\pagestyle{empty}\\begin{document}"
                                 "\\rule{1cm}{1cm}\\end{document}\n", encoding="utf-8")
    _pdflatex(fig, "box.tex", 1)
    (paper / "figs").mkdir()
    shutil.copyfile(fig / "box.pdf", paper / "figs" / "box.pdf")
    (paper / "figs" / "unused.pdf").write_bytes((fig / "box.pdf").read_bytes())
    _pdflatex(paper, "main.tex", 3)
    return paper


def compiled(fn: Callable[..., None]) -> Callable[..., None]:
    """Integration: runs pdflatex (MiKTeX or TeX Live), skipped without it."""
    fn = pytest.mark.integration(fn)
    return pytest.mark.skipif(shutil.which("pdflatex") is None, reason="no pdflatex")(fn)


@compiled
def test_final_package_of_a_small_paper(built_paper: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    rep = make_package(built_paper, mode="final", out=out)
    assert rep.ok, rep.render()
    notes = "\n".join(rep.notes)
    assert "rendered content identical" in notes
    assert "no undefined reference or citation" in notes
    if shutil.which("pdftotext"):
        assert "text identical" in notes
    assert fls_inputs(built_paper) == set(SYN_PACKAGE)
    with tarfile.open(out / "sremu-arxiv.tar.gz") as tar:
        assert tar.getnames() == SYN_PACKAGE
    src = out / "source"
    main = (src / "main.tex").read_text(encoding="utf-8")
    assert main.startswith("\\pdfoutput=1\n\\documentclass{article}\n")
    assert "\\sremu{} is here." in main
    assert "A {} B." in main
    assert "\n{}\nline after." in main
    assert "Text with a comment. %\n" in main
    assert "\\claim" not in main
    assert "{figs/box}%\n" in main
    macros = (src / "sremu-macros.tex").read_text(encoding="utf-8")
    assert "\\claim" not in macros
    assert "sremustub" not in macros  # unreferenced: dropped
    assert "\\newcommand{\\sremu}" in macros
    s2 = (src / "sections" / "s2.tex").read_text(encoding="utf-8")
    assert "\\url{http://example.org/a%20b}" in s2
    assert "keep % this" in s2
    assert "whole-line" not in s2
    assert (out / "abstract.txt").read_text(encoding="utf-8").strip() == "We test 42 things."
    meta = (out / "metadata.txt").read_text(encoding="utf-8")
    assert "Title: A Small Test: Of Packaging\n" in meta
    assert "Authors: Ann Author, Bob Builder\n" in meta
    assert "Primary category: eess.SP\nCross-list: cs.NI\n" in meta
    assert re.search(r"Comments: \d+ pages?, 1 figure, 0 tables\n", meta)
    assert DRAFT_LABEL not in meta
    assert (out / "verification.txt").is_file()
    first = (out / "sremu-arxiv.tar.gz").read_bytes()
    assert make_package(built_paper, mode="final", out=out).ok
    assert (out / "sremu-arxiv.tar.gz").read_bytes() == first  # same build, same bytes


@compiled
def test_a_change_that_renders_differently_is_caught(
    built_paper: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import make_arxiv_package as mod

    real = mod.transform

    def bad(root: Path, plan: Plan, rep: Report) -> dict[str, bytes]:
        files = real(root, plan, rep)
        files["sections/s2.tex"] = files["sections/s2.tex"].replace(b"Done.", b"Done!")
        return files

    monkeypatch.setattr(mod, "transform", bad)
    rep = make_package(built_paper, mode="final", out=tmp_path / "out")
    assert any("rendered content differs" in e for e in rep.errors), rep.render()
    if shutil.which("pdftotext"):
        assert any("text differs" in e and "Done" in e for e in rep.errors)
    assert not (tmp_path / "out" / "sremu-arxiv.tar.gz").exists()


@compiled
def test_without_pdftotext_the_pdf_size_is_compared(
    built_paper: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import make_arxiv_package as mod

    which = shutil.which
    monkeypatch.setattr(mod.shutil, "which", lambda name: None if name == "pdftotext"
                        else which(name))
    rep = make_package(built_paper, mode="final", out=tmp_path / "out")
    assert rep.ok, rep.render()
    assert any("without pdftotext: PDF sizes" in n for n in rep.notes)
    assert any("rendered content identical" in n for n in rep.notes)


@compiled
def test_draft_test_label_and_stale_sources(built_paper: Path, tmp_path: Path) -> None:
    paper = tmp_path / "paper"
    shutil.copytree(built_paper, paper)
    (paper / "generated" / "buildmode.tex").write_text("\\sremudrafttrue\n", encoding="utf-8")
    assert any("needs a final build" in e
               for e in make_package(paper, mode="final", out=tmp_path / "f").errors)
    rep = make_package(paper, mode="draft-test", out=tmp_path / "d")
    # buildmode.tex is newer than main.pdf now: the PDF does not show these sources
    assert any("newer than main.pdf" in e for e in rep.errors), rep.render()
    assert not (tmp_path / "d").exists()
    _pdflatex(paper, "main.tex", 2)
    rep = make_package(paper, mode="draft-test", out=tmp_path / "d")
    assert rep.ok, rep.render()
    assert (tmp_path / "d" / f"{DRAFT_LABEL}-sremu-arxiv.tar.gz").is_file()
    meta = (tmp_path / "d" / "metadata.txt").read_text(encoding="utf-8")
    assert meta.startswith(DRAFT_LABEL)


@compiled
@pytest.mark.skipif(not (PAPER_ROOT / "main.pdf").is_file(), reason="no build of the paper")
def test_real_paper_package(tmp_path: Path) -> None:
    mode = "final" if build_mode(PAPER_ROOT) == "final" else "draft-test"
    rep = make_package(PAPER_ROOT, mode=mode, out=tmp_path / "out")
    stale = ("changed since", "not recorded", "newer than", "did not read", "resolver did not")
    if any(s in e for e in rep.errors for s in stale):
        pytest.skip("the build in the paper root is not current: " + rep.errors[0])
    assert rep.ok, rep.render()
    notes = "\n".join(rep.notes)
    assert "rendered content identical" in notes
    with tarfile.open(next((tmp_path / "out").glob("*.tar.gz"))) as tar:
        names = tar.getnames()
    assert "main.tex" in names
    assert "main.bbl" in names
    assert "refs.bib" not in names
    assert not [n for n in names if not n.endswith((".tex", ".bbl", ".pdf"))]
