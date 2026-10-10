"""Re-verify every refs.bib entry against its primary record (network; optional; WP12).

For each entry:
  * doi    -> Crossref REST record (https://api.crossref.org/works/<doi>); title similarity
              with the BibTeX title must be >= 0.90 and the year must match when present.
              arXiv DataCite DOIs (10.48550/...) are checked through the arXiv API instead.
  * eprint -> arXiv API record (https://export.arxiv.org/api/query); same title rule.
  * url    -> HTTP GET; status < 400 counts as reachable (official-host rule is offline,
              in check_refs.py).
Writes a JSON evidence file (default: refs_verification.json) and prints a summary.
Not part of build_paper.py, which must run offline.

Usage: python tools/verify_refs_online.py [--bib refs.bib] [--out refs_verification.json]
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_common import PAPER_ROOT, norm_title, official_url, parse_bib, read_text

# Generic agent string; set SREMU_REFCHECK_MAILTO to join Crossref's polite pool if wanted.
UA = "sremu-paper-refcheck/0.1" + (
    f" (mailto:{os.environ['SREMU_REFCHECK_MAILTO']})"
    if os.environ.get("SREMU_REFCHECK_MAILTO") else ""
)
ATOM = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
TITLE_MIN_RATIO = 0.90


def _get(url: str, timeout: float = 40.0) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(400_000)
    except urllib.error.HTTPError as exc:
        return exc.code, b""


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm_title(a), norm_title(b)).ratio()


def check_handle(doi: str) -> dict:
    """DOI registered with another agency (no Crossref record): ask the DOI handle API."""
    status, body = _get("https://doi.org/api/handles/" + urllib.parse.quote(doi))
    code = json.loads(body).get("responseCode") if status == 200 and body else None
    return {"method": "doi-handle", "http": status, "response_code": code}


def check_doi(doi: str) -> dict:
    status, body = _get("https://api.crossref.org/works/" + urllib.parse.quote(doi))
    if status != 200:
        return check_handle(doi)
    msg = json.loads(body)["message"]
    issued = (msg.get("issued") or {}).get("date-parts") or [[None]]
    return {
        "method": "crossref",
        "http": status,
        "title": (msg.get("title") or [None])[0],
        "year": issued[0][0],
        "container": (msg.get("container-title") or [None])[0],
    }


def check_arxiv(eprint: str) -> dict:
    status, body = _get("https://export.arxiv.org/api/query?id_list=" + eprint)
    if status != 200:
        return {"method": "arxiv", "http": status, "title": None, "year": None}
    root = ET.fromstring(body)
    e = root.find("a:entry", ATOM)
    title = None if e is None else e.findtext("a:title", namespaces=ATOM)
    if e is None or title is None:
        return {"method": "arxiv", "http": status, "title": None, "year": None}
    published = e.findtext("a:published", namespaces=ATOM) or ""
    return {
        "method": "arxiv",
        "http": status,
        "title": " ".join(title.split()),
        "year": int(published[:4]) if published[:4].isdigit() else None,
        "version_id": e.findtext("a:id", namespaces=ATOM),
        "linked_doi": e.findtext("x:doi", namespaces=ATOM),
    }


def check_url(url: str) -> dict:
    try:
        status, _ = _get(url)
    except Exception as exc:
        return {"method": "http", "http": None, "error": type(exc).__name__}
    return {"method": "http", "http": status}


def verify_entry(fields: dict[str, str]) -> dict:
    title = fields.get("title", "")
    year = fields.get("year")
    out: dict = {"checks": []}
    doi = fields.get("doi")
    eprint = fields.get("eprint")
    if doi and not doi.lower().startswith("10.48550/"):
        out["checks"].append(check_doi(doi))
    if eprint:
        out["checks"].append(check_arxiv(eprint))
    url = official_url(fields)
    if not out["checks"] and url:
        out["checks"].append(check_url(url))
    verdicts = []
    for c in out["checks"]:
        if c["method"] == "http":
            verdicts.append("reachable" if c.get("http") and c["http"] < 400 else "unreachable")
            continue
        if c["method"] == "doi-handle":
            verdicts.append("registered" if c.get("response_code") == 1 else "no-record")
            continue
        if not c.get("title"):
            verdicts.append("no-record")
            continue
        c["title_ratio"] = round(_ratio(title, c["title"]), 3)
        year_ok = True
        if c["method"] == "crossref" and year and c.get("year"):
            year_ok = int(year) == int(c["year"])
        verdicts.append("match" if c["title_ratio"] >= TITLE_MIN_RATIO and year_ok else "mismatch")
    out["verdict"] = (
        "match" if verdicts and all(v in ("match", "reachable", "registered") for v in verdicts)
        else
        ",".join(verdicts) or "no-identifier"
    )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bib", type=Path, default=PAPER_ROOT / "refs.bib")
    ap.add_argument("--out", type=Path, default=PAPER_ROOT / "refs_verification.json")
    ap.add_argument("--keys", nargs="*", help="verify only these keys")
    args = ap.parse_args(argv)
    entries = parse_bib(read_text(args.bib))
    results: dict = {}
    if args.keys and args.out.is_file():  # partial re-run: update the existing evidence file
        results = json.loads(args.out.read_text(encoding="utf-8"))["results"]
    for e in entries:
        if args.keys and e.key not in args.keys:
            continue
        results[e.key] = verify_entry(e.fields)
        print(f"{e.key:34s} {results[e.key]['verdict']}", flush=True)
        time.sleep(0.4)  # be polite to Crossref and arXiv
    doc = {
        "generated_utc": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bib": args.bib.name,
        "title_min_ratio": TITLE_MIN_RATIO,
        "results": results,
    }
    args.out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    bad = [k for k, v in results.items() if v["verdict"] != "match"]
    print(f"{len(results) - len(bad)}/{len(results)} matched; needs attention: {bad}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
