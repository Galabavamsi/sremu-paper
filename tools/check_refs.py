"""check_refs.py -- bibliography integrity (DESIGN 13.5 step 6, 13.6; WP12). Offline.

Fails when
  * an entry lacks all of doi, arXiv eprint and an official URL, or has a malformed one;
  * a URL points to a non-official host (Wikipedia, ResearchGate, marketing pages, ...);
  * a printed field (note, howpublished, title, ...) holds a bare URL or an unescaped "_";
  * a key has no row in refs_verification.md, or a row names a key that is not in refs.bib;
  * an entry marked "source" differs from refs_sources/*.bib in author, title, year, doi,
    eprint or url ("source+augmented" may add fields but not change them; "new" must not
    exist in the source);
  * a refs_sources/*.bib file is not the byte copy whose sha256 refs_verification.md records;
  * two entries share a DOI or an eprint;
  * a \\cite key in the TeX sources is not in refs.bib.
Unused entries are reported as one note (bibtex prints only cited entries).

Usage: python tools/check_refs.py [--paper-root DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_common import (
    PAPER_ROOT,
    BibEntry,
    Report,
    bib_by_key,
    cite_keys,
    official_url,
    parse_bib,
    read_text,
    tex_source_files,
)

DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
ARXIV_RE = re.compile(r"^\d{4}\.\d{4,5}$")
BANNED_HOSTS = (
    "wikipedia.org",
    "researchgate.net",
    "scholar.google.",
    "toneisland.com",
    "academia.edu",
    "semanticscholar.org",
)
# Bibliographic identity of a "source" entry; `note` may differ (the source keeps
# verification remarks there, refs.bib keeps print-safe notes only).
COMPARED_FIELDS = ("author", "title", "year", "doi", "eprint", "url", "howpublished")
PRINTED_FIELDS = ("note", "howpublished", "title", "booktitle", "journal", "institution")
ORIGINS = ("source", "source+augmented", "new")


def parse_verification_rows(md_text: str) -> dict[str, str]:
    """Key -> origin, from the 'Per-entry record' table of refs_verification.md."""
    _, _, tail = md_text.partition("## Per-entry record")
    rows: dict[str, str] = {}
    for line in tail.splitlines():
        m = re.match(r"^\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|", line)
        if not m:
            continue
        key, origin = m.group(1), m.group(2)
        if key in rows:
            raise ValueError(f"duplicate verification row for {key!r}")
        rows[key] = origin
    return rows


def _check_identifiers(e: BibEntry, rep: Report) -> None:
    doi, eprint, url = e.fields.get("doi"), e.fields.get("eprint"), official_url(e.fields)
    if not (doi or eprint or url):
        rep.error(f"{e.key}: no doi, eprint or official url")
    if doi and not DOI_RE.match(doi):
        rep.error(f"{e.key}: malformed doi {doi!r}")
    if eprint and not ARXIV_RE.match(eprint):
        rep.error(f"{e.key}: malformed arXiv eprint {eprint!r}")
    if eprint and e.fields.get("archiveprefix", "arXiv").lower() != "arxiv":
        rep.error(f"{e.key}: eprint without archivePrefix=arXiv")
    for fld in PRINTED_FIELDS:  # IEEEtran prints these: bare URLs and "_" break LaTeX
        rest = re.sub(r"\\url\{[^}]*\}", "", e.fields.get(fld, ""))
        if re.search(r"https?://", rest):
            rep.error(f"{e.key}: bare URL in printed field {fld!r} (wrap it in \\url)")
        if re.search(r"(?<!\\)_", rest):
            rep.error(f"{e.key}: unescaped '_' in printed field {fld!r}")
    for candidate in filter(None, (url, e.fields.get("url"))):
        host = urlparse(candidate).netloc.lower()
        if not urlparse(candidate).scheme.startswith("http") or not host:
            rep.error(f"{e.key}: url is not an http(s) URL: {candidate!r}")
        elif any(b in host for b in BANNED_HOSTS):
            rep.error(f"{e.key}: non-official host {host!r}")


def check_refs(paper_root: Path = PAPER_ROOT) -> Report:
    rep = Report("check_refs")
    bib_path = paper_root / "refs.bib"
    try:
        entries = parse_bib(read_text(bib_path))
        bib = bib_by_key(entries)
    except (OSError, ValueError) as exc:
        rep.error(f"cannot parse refs.bib: {exc}")
        return rep
    rep.note(f"{len(bib)} entries in refs.bib")

    src_files = sorted((paper_root / "refs_sources").glob("*.bib"))
    source: dict[str, BibEntry] = {}
    for f in src_files:
        for e in parse_bib(read_text(f)):
            source.setdefault(e.key, e)
    if not src_files:
        rep.error("refs_sources/*.bib missing (the verified source bibliography)")

    try:
        md_text = read_text(paper_root / "refs_verification.md")
        rows = parse_verification_rows(md_text)
    except (OSError, ValueError) as exc:
        rep.error(f"cannot read refs_verification.md: {exc}")
        md_text, rows = "", {}
    # The source copy must be the byte copy whose sha256 the verification record states,
    # so a "source" entry cannot be changed by editing refs_sources/.
    recorded = set(re.findall(r"sha256\s+`([0-9a-f]{64})`", md_text))
    for f in src_files:
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        if digest not in recorded:
            rep.error(f"refs_sources/{f.name}: sha256 {digest[:12]}... is not the one "
                      "recorded in refs_verification.md")

    seen_doi: dict[str, str] = {}
    seen_eprint: dict[str, str] = {}
    for key, e in bib.items():
        _check_identifiers(e, rep)
        for field, seen in (("doi", seen_doi), ("eprint", seen_eprint)):
            val = e.fields.get(field)
            if val:
                low = val.lower()
                if low in seen:
                    rep.error(f"{key}: {field} {val} also used by {seen[low]}")
                seen[low] = key
        origin = rows.get(key)
        if origin is None:
            rep.error(f"{key}: no row in refs_verification.md")
            continue
        if origin not in ORIGINS:
            rep.error(f"{key}: unknown origin {origin!r} in refs_verification.md")
            continue
        if origin == "new":
            if key in source:
                rep.error(f"{key}: marked 'new' but present in the source bibliography")
            continue
        src = source.get(key)
        if src is None:
            rep.error(f"{key}: marked {origin!r} but absent from refs_sources/")
            continue
        for fld in COMPARED_FIELDS:
            a, b = e.fields.get(fld), src.fields.get(fld)
            if b is not None and a != b:
                rep.error(f"{key}: field {fld!r} differs from the verified source")
            if b is None and a is not None and origin == "source":
                rep.error(f"{key}: field {fld!r} added to a 'source' entry (use source+augmented)")
        if src.type != e.type:
            rep.error(f"{key}: entry type {e.type} differs from source {src.type}")
    for key in rows:
        if key not in bib:
            rep.error(f"refs_verification.md row {key!r} has no entry in refs.bib")

    cited: set[str] = set()
    for f in tex_source_files(paper_root):
        for k in cite_keys(read_text(f)):
            cited.add(k)
            if k not in bib:
                rep.error(f"{f.relative_to(paper_root)}: \\cite{{{k}}} not in refs.bib")
    unused = sorted(set(bib) - cited)
    rep.note(f"{len(cited)} keys cited; {len(unused)} entries not cited yet")
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check the paper bibliography (offline).")
    ap.add_argument("--paper-root", type=Path, default=PAPER_ROOT)
    args = ap.parse_args(argv)
    return check_refs(args.paper_root).emit()


if __name__ == "__main__":
    raise SystemExit(main())
