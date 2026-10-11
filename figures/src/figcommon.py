"""figcommon.py -- shared helpers of the paper figure scripts (DESIGN 13.4, 13.5; WP12).

Style (DESIGN 13.4): vector PDF, width 3.5 in (single column) or 7.16 in (double), every
text >= 8 pt, TrueType (Type 42) fonts, Okabe-Ito palette, no titles inside the axes; the
caption in the .tex names the data classes.

Script contract (figures/registry.yaml): ``python <script> --profile fast|full --figure <Fid>
--content-key <key>`` run from ``paper/`` with the sremu venv. Each script writes
``figures/out/<Fid>.pdf``, ``figures/data/<Fid>.csv`` (long form: one row per plotted value)
and ``figures/data/<Fid>.meta.json`` with at least ``content_key``, ``sremu_commit``,
``data_classes``, ``inputs: [{path, sha256, data_class}]`` and ``assumptions``.

Without ``--content-key`` the key is computed exactly as ``tools/build_paper.py`` does
(sha256 of the sremu.lock commit, sweep, input hashes and script), so a manual run leaves the
figure current for that lock. ``sremu_commit`` is the HEAD of the sremu checkout, read from
``.git`` files (no git process is started); a final build reruns every figure at the pinned
commit, where the two agree. Without a readable .git the commit is recorded as "unknown".
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import sys
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("pdf")
import matplotlib.pyplot as plt
import yaml

PAPER_ROOT = Path(__file__).resolve().parents[2]
TOOLS = PAPER_ROOT / "tools"

SINGLE_IN = 3.5
DOUBLE_IN = 7.16
FONT_PT = 8

# Okabe & Ito (2008) colour-blind-safe palette.
OI = {
    "black": "#000000",
    "orange": "#E69F00",
    "sky": "#56B4E9",
    "green": "#009E73",
    "yellow": "#F0E442",
    "blue": "#0072B2",
    "vermillion": "#D55E00",
    "purple": "#CC79A7",
    "grey": "#999999",
}
CYCLE = [OI["blue"], OI["vermillion"], OI["green"], OI["orange"], OI["purple"], OI["sky"],
         OI["black"], OI["yellow"]]

CSV_FIELDS = ["panel", "series", "data_class", "x_name", "x", "y_name", "y", "y_lo", "y_hi",
              "note"]


# --------------------------------------------------------------------------- style
def setup_style() -> None:
    plt.rcParams.update({
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "font.family": "STIXGeneral",
        "mathtext.fontset": "stix",
        "font.size": FONT_PT,
        "axes.labelsize": FONT_PT,
        "axes.titlesize": FONT_PT,
        "xtick.labelsize": FONT_PT,
        "ytick.labelsize": FONT_PT,
        "legend.fontsize": FONT_PT,
        "legend.frameon": False,
        "legend.handlelength": 1.6,
        "legend.borderaxespad": 0.3,
        "axes.linewidth": 0.6,
        "lines.linewidth": 1.0,
        "lines.markersize": 3.5,
        "xtick.major.width": 0.6,
        "ytick.major.width": 0.6,
        "xtick.direction": "in",
        "ytick.direction": "in",
        "axes.grid": True,
        "grid.linewidth": 0.3,
        "grid.alpha": 0.5,
        "axes.prop_cycle": matplotlib.cycler(color=CYCLE),
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.02,
        "figure.dpi": 300,
    })


def panel_label(ax: Any, text: str, *, x: float = 0.02, y: float = 0.97,
                polar: bool = False) -> None:
    """(a), (b), ... in the corner of a panel (a label, not a title)."""
    if polar:
        ax.text(x, y, text, transform=ax.transAxes, ha="left", va="top", fontsize=FONT_PT)
    else:
        ax.text(x, y, text, transform=ax.transAxes, ha="left", va="top", fontsize=FONT_PT,
                bbox={"facecolor": "white", "edgecolor": "none", "pad": 0.5, "alpha": 0.8})


# --------------------------------------------------------------------------- repos
def read_lock() -> dict[str, str]:
    lock = yaml.safe_load((PAPER_ROOT / "sremu.lock").read_text(encoding="utf-8")) or {}
    return {"repo": str(lock.get("repo", "../sremu")), "commit": str(lock.get("commit", ""))}


def sremu_root() -> Path:
    return (PAPER_ROOT / read_lock()["repo"]).resolve()


def git_head(repo: Path) -> str | None:
    """HEAD commit of ``repo`` read from its .git files (no git process)."""
    git = repo / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not head.startswith("ref:"):
        return head or None
    ref = head.split(":", 1)[1].strip()
    path = git / ref
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    packed = git / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref:
                return parts[0]
    return None


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def registry_entry(fid: str) -> dict[str, Any]:
    reg = yaml.safe_load((PAPER_ROOT / "figures" / "registry.yaml").read_text(encoding="utf-8"))
    return reg["figures"][fid]


def input_path(rel: str) -> Path:
    """A registry input path (relative to paper/) as an absolute path."""
    return (PAPER_ROOT / rel).resolve()


def default_content_key(fid: str) -> str | None:
    """The key tools/build_paper.py computes for this entry and the sremu.lock commit."""
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    import build_paper

    return build_paper.content_key(PAPER_ROOT, registry_entry(fid), read_lock()["commit"])


# --------------------------------------------------------------------------- CLI
def parse_args(fid: str, argv: Sequence[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=f"Figure {fid} of the sremu paper.")
    ap.add_argument("--profile", choices=("fast", "full"), default="full")
    ap.add_argument("--figure", default=fid)
    ap.add_argument("--content-key", default=None)
    args = ap.parse_args(argv)
    if args.figure != fid:
        ap.error(f"this script draws {fid}, not {args.figure}")
    return args


# --------------------------------------------------------------------------- outputs
def fit_width(fig: Any, target_in: float, tol_in: float = 0.004, iters: int = 8) -> float:
    """Resize the figure so that its tight bounding box (plus the save padding) is exactly
    ``target_in`` wide. LaTeX then includes the PDF at scale 1, so every font prints at the
    size it was set (>= 8 pt, DESIGN 13.4). Returns the resulting PDF width (in)."""
    pad = float(plt.rcParams["savefig.pad_inches"])

    def measure() -> float:
        renderer = fig._get_renderer()  # the renderer savefig itself uses for bbox="tight"
        fig.draw(renderer)
        return float(fig.get_tightbbox(renderer).width) + 2 * pad

    best: tuple[float, float] | None = None  # (pdf width <= target, figure width)
    h = fig.get_size_inches()[1]
    aim = target_in - tol_in / 2
    slope, prev = 1.0, None
    for _ in range(max(iters, 16)):
        w = float(fig.get_size_inches()[0])
        width = measure()
        if width <= target_in + 1e-9 and (best is None or width > best[0]):
            best = (width, w)
        if target_in - tol_in <= width <= target_in:
            break
        if prev is not None and abs(w - prev[0]) > 1e-6:  # secant estimate of d(pdf)/d(fig)
            slope = min(1.5, max(0.2, (width - prev[1]) / (w - prev[0])))
        prev = (w, width)
        # tick labels may toggle as the width changes: step a little short of the secant step
        fig.set_size_inches(w + 0.95 * (aim - width) / slope, h)
    if best is None:
        raise SystemExit(f"cannot fit the figure within {target_in} in")
    fig.set_size_inches(best[1], h)
    width = measure()
    # a PDF slightly narrower than the column is scaled UP by LaTeX: fonts stay >= 8 pt
    if width > target_in + 1e-6 or width < target_in - 0.05:
        raise SystemExit(f"cannot fit the figure to {target_in} in (got {width:.3f} in)")
    return width


class Rows:
    """Long-form CSV rows: one per plotted value."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add(self, panel: str, series: str, data_class: str, x_name: str, x: float | str,
            y_name: str, y: float, y_lo: float | None = None, y_hi: float | None = None,
            note: str = "") -> None:
        self.rows.append({"panel": panel, "series": series, "data_class": data_class,
                          "x_name": x_name, "x": _fmt(x), "y_name": y_name, "y": _fmt(y),
                          "y_lo": _fmt(y_lo), "y_hi": _fmt(y_hi), "note": note})

    def extend(self, panel: str, series: str, data_class: str, x_name: str, xs: Iterable[Any],
               y_name: str, ys: Iterable[float], note: str = "") -> None:
        for x, y in zip(xs, ys, strict=True):
            self.add(panel, series, data_class, x_name, x, y_name, y, note=note)


def _fmt(v: Any) -> str:
    """CSV cell text. numpy scalars are converted first: under numpy 2 their repr is
    ``np.float64(1.0)``, which is not a number in a CSV."""
    if v is None:
        return ""
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        v = v.item()  # numpy scalar -> Python scalar
    if isinstance(v, float):
        if math.isnan(v):
            return "nan"
        return repr(float(v))
    return str(v)


def write_outputs(
    fid: str,
    fig: Any,
    rows: Rows,
    *,
    args: argparse.Namespace,
    inputs: Sequence[Mapping[str, str]],
    assumptions: Sequence[str],
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Write figures/out/<fid>.pdf, figures/data/<fid>.csv and figures/data/<fid>.meta.json.

    ``inputs``: [{path (relative to paper/), data_class, (optional) provenance...}]; the
    sha256 of every file is recomputed here."""
    entry = registry_entry(fid)
    out_pdf = PAPER_ROOT / "figures" / "out" / f"{fid}.pdf"
    data_dir = PAPER_ROOT / "figures" / "data"
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    target = SINGLE_IN if entry["width"] == "single" else DOUBLE_IN
    printed_in = fit_width(fig, target)
    fig.savefig(out_pdf, format="pdf", metadata={"CreationDate": None, "ModDate": None,
                                                  "Creator": f"paper/{entry['script']}"})
    plt.close(fig)
    with (data_dir / f"{fid}.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows.rows)
    used_classes = sorted({r["data_class"] for r in rows.rows})
    reg_classes = sorted(entry["data_classes"])
    missing = sorted(set(reg_classes) - set(used_classes) - {
        i["data_class"] for i in inputs})
    if missing:
        raise SystemExit(f"{fid}: registry data classes {missing} appear in no row or input")
    in_list = []
    for item in inputs:
        p = input_path(item["path"])
        rec = dict(item)
        rec["sha256"] = sha256_file(p)
        in_list.append(rec)
    root = sremu_root()
    import numpy
    import scipy

    meta: dict[str, Any] = {
        "figure": fid,
        "title": entry["title"],
        "content_key": args.content_key or default_content_key(fid),
        "sremu_commit": git_head(root) or "unknown",
        "sremu_dirty": None,
        "sremu_dirty_note": "not determined: figure scripts start no git process; a final "
                            "build checks clean trees before it runs any script",
        "lock_commit": read_lock()["commit"],
        "data_classes": reg_classes,
        "data_classes_in_rows": used_classes,
        "inputs": in_list,
        "assumptions": list(assumptions),
        "profile": args.profile,
        "script": entry["script"],
        "script_sha256": sha256_file(PAPER_ROOT / entry["script"]),
        "outputs": {
            f"figures/out/{fid}.pdf": sha256_file(out_pdf),
            f"figures/data/{fid}.csv": sha256_file(data_dir / f"{fid}.csv"),
        },
        "versions": {"python": platform.python_version(), "numpy": numpy.__version__,
                     "scipy": scipy.__version__, "matplotlib": matplotlib.__version__},
        "platform": platform.platform(),
        "generated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "print": {"registry_width": entry["width"], "target_width_in": target,
                  "pdf_width_in": round(printed_in, 4), "font_pt": FONT_PT,
                  "fonttype": "Type 42 (TrueType)"},
    }
    if extra:
        meta.update(extra)
    (data_dir / f"{fid}.meta.json").write_text(json.dumps(meta, indent=2) + "\n",
                                               encoding="utf-8", newline="\n")


def study_provenance(meta_path: Path) -> dict[str, Any]:
    """Commit, dirty flag and run directory of a study's meta.json (or manifest.json)."""
    try:
        doc = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    keys = ("sremu_commit", "sremu_dirty", "git_commit", "git_dirty", "run_dir", "study",
            "experiment", "dirty_patch_sha256", "platform", "cpu_model")
    return {k: doc[k] for k in keys if k in doc}
