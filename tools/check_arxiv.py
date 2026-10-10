"""check_arxiv.py -- arXiv/IEEE submission hygiene (DESIGN 13.1, 13.5 step 6; WP12). Offline.

Checks
  * abstract <= 1920 characters and <= 200 words. While pipeline numbers are placeholders,
    each one is counted at its max_chars budget from numbers.yaml, so the bound holds for
    any value the pipeline may later insert within budget;
  * no \\today, no TODO/FIXME/XXX markers and no line numbering (lineno) in the sources;
  * submission file names use arXiv-safe characters only;
  * raster images are below 34 megapixels;
  * --post-build: main.bbl and main.pdf exist (the .bbl is uploaded to arXiv).
No generative-AI disclosure section is required (lead amendment A1).

Usage: python tools/check_arxiv.py [--paper-root DIR] [--post-build]
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_common import (
    ABSTRACT_MAX_CHARS,
    ABSTRACT_MAX_WORDS,
    IMAGE_MAX_PIXELS,
    PAPER_ROOT,
    NumberMacro,
    Report,
    generated_tex_files,
    load_numbers_spec,
    read_text,
    strip_tex_comments,
    tex_source_files,
)

SAFE_NAME = re.compile(r"^[A-Za-z0-9_+\-.,=]+$")
MARKER_RE = re.compile(r"\b(TODO|FIXME|XXX)\b")
RASTER_EXT = (".png", ".jpg", ".jpeg")
# JPEG start-of-frame markers (baseline, progressive, lossless, arithmetic variants)
SOF_MARKERS = frozenset(
    (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF)
)


# --------------------------------------------------------------------------- abstract
def abstract_source(paper_root: Path) -> str:
    text = strip_tex_comments(read_text(paper_root / "sections" / "abstract.tex"))
    m = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", text, re.S)
    if not m:
        raise ValueError("no abstract environment in sections/abstract.tex")
    return m.group(1)


def _value_plain(value: str) -> str:
    return tex_to_plain(value, {})


def tex_to_plain(tex: str, macros: dict[str, NumberMacro]) -> str:
    """Approximate the plain text arXiv shows for an abstract (placeholders at budget)."""
    s = tex

    def expand(m: re.Match[str]) -> str:
        name = m.group(1)
        mac = macros.get(name)
        if mac is None:
            return m.group(0)
        if mac.value is not None:
            return _value_plain(mac.value)
        if mac.kind == "text":  # budget stand-in: words of six characters incl. the space
            return ("##### " * (mac.max_chars // 6 + 1))[: mac.max_chars].strip()
        return "#" * mac.max_chars  # budget stand-in for a number: one word

    s = re.sub(r"\\([A-Za-z]+)(?:\{\})?", expand, s)
    s = re.sub(r"\\cite[a-zA-Z]*(?:\[[^\]]*\])*\{[^}]*\}", "", s)
    # formatting commands: keep their argument (braces are dropped below; nesting-safe)
    s = re.sub(r"\\(?:emph|textbf|textit|texttt|ensuremath|mathrm|text)(?![A-Za-z])", "", s)
    s = s.replace("\\%", "%").replace("~", " ").replace("---", "-").replace("--", "-")
    s = s.replace("$", "")
    s = re.sub(r"\\[A-Za-z]+", "x", s)  # remaining commands, e.g. \lambda -> one character
    s = s.replace("{", "").replace("}", "").replace("\\", "")
    return " ".join(s.split())


def check_abstract(paper_root: Path, rep: Report) -> None:
    try:
        spec = {m.macro: m for m in load_numbers_spec(paper_root / "numbers.yaml")}
        plain = tex_to_plain(abstract_source(paper_root), spec)
    except (OSError, ValueError, KeyError) as exc:
        rep.error(f"abstract: {exc}")
        return
    n_chars, n_words = len(plain), len(plain.split())
    pending = plain.count("#") > 0
    tag = " (placeholders counted at their max_chars budget)" if pending else ""
    rep.note(f"abstract: {n_chars} characters, {n_words} words{tag}")
    if n_chars > ABSTRACT_MAX_CHARS:
        rep.error(f"abstract has {n_chars} > {ABSTRACT_MAX_CHARS} characters{tag}")
    if n_words > ABSTRACT_MAX_WORDS:
        rep.error(f"abstract has {n_words} > {ABSTRACT_MAX_WORDS} words{tag}")


# --------------------------------------------------------------------------- sources
def submission_files(paper_root: Path) -> list[Path]:
    files = tex_source_files(paper_root) + generated_tex_files(paper_root)
    files.append(paper_root / "refs.bib")
    out_dir = paper_root / "figures" / "out"
    if out_dir.is_dir():
        files += sorted(p for p in out_dir.rglob("*") if p.is_file())
    for extra in ("main.bbl",):
        if (paper_root / extra).is_file():
            files.append(paper_root / extra)
    return [f for f in files if f.is_file()]


def check_sources(paper_root: Path, rep: Report) -> None:
    for f in tex_source_files(paper_root) + generated_tex_files(paper_root):
        raw = read_text(f)
        rel = f.relative_to(paper_root)
        if re.search(r"\\today\b", raw):
            rep.error(f"{rel}: \\today found (dates must be fixed text)")
        for m in MARKER_RE.finditer(raw):
            line = raw.count("\n", 0, m.start()) + 1
            rep.error(f"{rel}:{line}: '{m.group(1)}' marker")
        body = strip_tex_comments(raw)
        lineno_pkg = re.search(r"\\usepackage(\[[^\]]*\])?\{[^}]*\blineno\b", body)
        if lineno_pkg or "\\linenumbers" in body:
            rep.error(f"{rel}: line numbering enabled")
    for f in submission_files(paper_root):
        for part in f.relative_to(paper_root).parts:
            if not SAFE_NAME.match(part):
                rep.error(f"unsafe file name for arXiv: {f.relative_to(paper_root)}")
                break


# --------------------------------------------------------------------------- images
def raster_size(path: Path) -> tuple[int, int] | None:
    data = path.read_bytes()[:65536]
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        w, h = struct.unpack(">II", data[16:24])
        return w, h
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            seg_len = struct.unpack(">H", data[i + 2 : i + 4])[0]
            if marker in SOF_MARKERS:
                h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                return w, h
            i += 2 + seg_len
    return None


def check_images(paper_root: Path, rep: Report) -> None:
    out_dir = paper_root / "figures" / "out"
    if not out_dir.is_dir():
        return
    for f in sorted(out_dir.rglob("*")):
        if f.suffix.lower() not in RASTER_EXT:
            continue
        size = raster_size(f)
        if size is None:
            rep.error(f"{f.relative_to(paper_root)}: cannot read raster header")
        elif size[0] * size[1] > IMAGE_MAX_PIXELS:
            rep.error(f"{f.relative_to(paper_root)}: {size[0]}x{size[1]} exceeds 34 MP")


def check_post_build(paper_root: Path, rep: Report) -> None:
    bbl = paper_root / "main.bbl"
    if not bbl.is_file() or bbl.stat().st_size == 0:
        rep.error("main.bbl missing or empty (arXiv needs the .bbl)")
    if not (paper_root / "main.pdf").is_file():
        rep.error("main.pdf missing")


def check_arxiv(paper_root: Path = PAPER_ROOT, post_build: bool = False) -> Report:
    rep = Report("check_arxiv")
    check_abstract(paper_root, rep)
    check_sources(paper_root, rep)
    check_images(paper_root, rep)
    if post_build:
        check_post_build(paper_root, rep)
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="arXiv/IEEE submission checks (offline).")
    ap.add_argument("--paper-root", type=Path, default=PAPER_ROOT)
    ap.add_argument("--post-build", action="store_true", help="also require main.bbl/main.pdf")
    args = ap.parse_args(argv)
    return check_arxiv(args.paper_root, args.post_build).emit()


if __name__ == "__main__":
    raise SystemExit(main())
