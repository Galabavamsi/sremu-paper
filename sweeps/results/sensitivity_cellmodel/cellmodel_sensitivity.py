"""Cell-model sensitivity: ``point`` versus ``po_patch`` in the geometries of the paper.

Paper revision (mock review must-fix 9). The laboratory tiles are 42.8 mm at 3.5 GHz, just
below the ``auto`` threshold of half a wavelength, so ``auto`` resolves to ``point`` there.
This script evaluates, with the installed sremu (no IQ runs, coefficient level only):

1. ``cell_factor``: one half-wavelength cell at 3.5 GHz in the far field (50 m / 60 m),
   power of the RIS coefficient with ``po_patch`` relative to ``point`` (dB) for
   retro-reflection and specular reflection at several in-plane angles;
2. ``eb``: the E-B design quantities (experiments/eb.py ``design``: exact optima for RX1 and
   RX2, RIS term of RX1, change of RX1's channel by the switch) for the arc and the lab-like
   geometry, LOS blocked and present, laboratory alphabet (|Gamma| = 1, ASSUMED), with the
   panels' ``cell_model`` set to ``point`` and to ``po_patch``; plus the loss of deploying the
   ``point`` optimum when ``po_patch`` is the model;
3. ``ec``: the E-C laboratory geometry (experiments/ec.py ``lab_geometry_scenario``, 30
   receivers) under both cell models: losses of the ideal 1-bit and laboratory alphabets
   relative to ideal continuous phase (RIS term, power-averaged, as Table VIII) and the change
   of the continuous-optimum RIS power;
4. ``threshold``: the lab pitch against half a wavelength at 3.5 GHz.

Writes ``results.json`` and ``meta.json`` (and a copy of this script) to the output directory.

Usage: python cellmodel_sensitivity.py <out_dir>
"""

from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
import math
import platform
import shutil
import sys
import warnings
from fractions import Fraction
from pathlib import Path
from typing import Any

SREMU = Path(r"D:\projects\ris\sremu")
sys.path.insert(0, str(SREMU))

import numpy as np  # noqa: E402
import scipy  # noqa: E402
import yaml  # noqa: E402

from experiments import ec  # noqa: E402
from experiments import eb  # noqa: E402
from sremu.control.controller import csi_from_snapshot  # noqa: E402
from sremu.errors import RefinementNotConverged  # noqa: E402
from sremu.physics.propagation import from_scenario  # noqa: E402
from sremu.units import C0  # noqa: E402

FC = 3.5e9
CELL_MODELS = ("point", "po_patch")


def db10(x: float) -> float:
    return 10.0 * math.log10(x)


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def git_head(repo: Path) -> str | None:
    """HEAD from the .git files (no git process)."""
    head = (repo / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    if not head.startswith("ref:"):
        return head
    ref = repo / ".git" / head.split(":", 1)[1].strip()
    return ref.read_text(encoding="utf-8").strip() if ref.is_file() else None


def tree_sha256(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*.py")):
        h.update(p.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def with_cell_model(doc: dict[str, Any], cm: str) -> dict[str, Any]:
    d = copy.deepcopy(doc)
    for p in d["ris"]:
        p["grid"]["cell_model"] = cm
    return d


def resolved_points(model: Any, snap: Any) -> dict[str, int]:
    return {k.split("/", 1)[1]: int(v) for k, v in snap.diag.items()
            if k.startswith("quadrature_points/")}


# --------------------------------------------------------------------------- 1. cell factor
FINE_SUBCELL = 0.05  # po_patch started at lambda/20 (Richardson then halves as usual)


def _cell_amp(kind: str, deg: float, cm: str, numerics: dict[str, Any] | None) -> tuple[
        float | None, int | None, str | None]:
    lam = C0 / FC
    a = math.radians(deg)
    b = a if kind == "retro" else -a
    tx = [50.0 * math.cos(a), 50.0 * math.sin(a), 0.0]
    rx = [60.0 * math.cos(b), 60.0 * math.sin(b), 0.0]
    d: dict[str, Any] = {
        "schema_version": "1.0",
        "name": f"cell_{kind}_{int(deg)}_{cm}",
        "seed": 1,
        "description": "single half-wavelength cell (cell-model sensitivity)",
        "nodes": [ec._node("ap", tx, "tx", FC), ec._node("rx", rx, "rx", FC)],
        "ris": [{
            "id": "ris0",
            "pose": {"center_m": [0, 0, 0], "normal": [1, 0, 0], "u_axis": [0, 1, 0]},
            "grid": {"n_u": 1, "n_v": 1, "pitch_u_m": lam / 2, "pitch_v_m": lam / 2,
                     "cell_model": cm},
            "states": {"library": "ideal_antipodal"},
        }],
        "run": {"duration_s": "1"},
    }
    if numerics:
        d["numerics"] = dict(numerics)
    try:
        model = from_scenario(d, backend="numpy_ref")
    except RefinementNotConverged as exc:  # recorded, not hidden
        return None, None, f"{cm}: {exc}"
    snap = model.snapshot(Fraction(0))
    z = csi_from_snapshot(model, snap, "ap.tx0->rx.rx0").cascade["ris0"]
    return abs(complex(z[0, 0])), resolved_points(model, snap)["ris0"], None


def cell_factor() -> dict[str, Any]:
    """Far-field power of one lambda/2 cell, po_patch relative to point, in dB: with the
    default sub-cell start (lambda/5) and with a finer start (lambda/20); plus the continuous
    uniform-patch limit 20 log10|sinc(p (sin th_i + sin th_s)/lambda)| (ANALYTIC; th_s = -th_i
    is specular in the signed in-plane convention, th_s = th_i retro)."""
    out: dict[str, Any] = {"pitch_over_lambda": 0.5, "distances_m": [50.0, 60.0],
                           "fine_subcell_max_wavelengths": FINE_SUBCELL, "rows": []}
    for kind in ("retro", "specular"):
        for deg in (0.0, 15.0, 30.0, 45.0, 60.0):
            a_pt, q_pt, _ = _cell_amp(kind, deg, "point", None)
            a_pa, q_pa, note = _cell_amp(kind, deg, "po_patch", None)
            a_fi, q_fi, note_fi = _cell_amp(kind, deg, "po_patch",
                                            {"subcell_max_wavelengths": FINE_SUBCELL})
            x = 0.5 * (math.sin(math.radians(deg))
                       + math.sin(math.radians(deg if kind == "retro" else -deg)))
            sinc = 1.0 if x == 0 else math.sin(math.pi * x) / (math.pi * x)
            assert a_pt is not None
            out["rows"].append({
                "kind": kind,
                "angle_deg": deg,
                "patch_minus_point_db": (None if a_pa is None
                                         else 20.0 * math.log10(a_pa / a_pt)),
                "patch_fine_minus_point_db": (None if a_fi is None
                                              else 20.0 * math.log10(a_fi / a_pt)),
                "continuous_patch_factor_db": 20.0 * math.log10(abs(sinc)),
                "note_default": note,
                "note_fine": note_fi,
                "point_quadrature_points": q_pt,
                "po_patch_quadrature_points": q_pa,
                "po_patch_fine_quadrature_points": q_fi,
            })
    return out


# --------------------------------------------------------------------------- 2. E-B
def eb_design(scenario: Path, los: str, cm: str) -> tuple[Any, dict[str, Any]]:
    orig = eb.lab_scenario

    def patched(*a: Any, **k: Any) -> dict[str, Any]:
        return with_cell_model(orig(*a, **k), cm)

    eb.lab_scenario = patched
    try:
        dsg = eb.design("lab", los, scenario=scenario)
        doc = patched("lab", los, scenario=scenario)
    finally:
        eb.lab_scenario = orig
    return dsg, doc


def eb_part() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, scn in (("arc", eb.ARC), ("lab_like", eb.LAB)):
        for los in ("blocked", "present"):
            rec: dict[str, Any] = {}
            dsg: dict[str, Any] = {}
            for cm in CELL_MODELS:
                d, doc = eb_design(scn, los, cm)
                model = from_scenario(doc, backend="numpy_ref")
                snap = model.snapshot(Fraction(0))
                dsg[cm] = (d, doc, model, snap)
                keys = ("rx1_ris_term_s1_db", "rx1_direct_db", "rx1_ris_term_change_db",
                        "rx1_ris_term_rotation_deg", "rx1_total_change_db",
                        "rx1_total_rotation_deg", "n_differing_elements")
                rec[cm] = {k: d.diag[k] for k in keys}
                rec[cm]["quadrature_points"] = resolved_points(model, snap)
                rec[cm]["direct_minus_ris_db"] = (
                    d.diag["rx1_direct_db"] - d.diag["rx1_ris_term_s1_db"]
                    if math.isfinite(d.diag["rx1_direct_db"]) else None)
            pp, pa = dsg["point"][0], dsg["po_patch"][0]
            # the point optimum deployed under the po_patch model
            model, snap = dsg["po_patch"][2], dsg["po_patch"][3]
            pids = pa.panels
            v = csi_from_snapshot(model, snap, eb.LINKS[eb.RX1])
            z = np.concatenate([v.cascade[p] for p in pids], axis=1)
            cols = np.arange(z.shape[1])
            h_pt = v.h_direct + complex(z[pp.s1.astype(int), cols].sum())
            h_pa = v.h_direct + complex(z[pa.s1.astype(int), cols].sum())
            rec["delta"] = {
                "ris_term_s1_patch_minus_point_db":
                    rec["po_patch"]["rx1_ris_term_s1_db"] - rec["point"]["rx1_ris_term_s1_db"],
                "ris_term_change_patch_minus_point_db":
                    rec["po_patch"]["rx1_ris_term_change_db"]
                    - rec["point"]["rx1_ris_term_change_db"],
                "total_change_patch_minus_point_db":
                    rec["po_patch"]["rx1_total_change_db"] - rec["point"]["rx1_total_change_db"],
                "rotation_patch_minus_point_deg":
                    rec["po_patch"]["rx1_total_rotation_deg"]
                    - rec["point"]["rx1_total_rotation_deg"],
                "rx1_optimum_cells_differing": int(np.count_nonzero(pp.s1 != pa.s1)),
                "rx2_optimum_cells_differing": int(np.count_nonzero(pp.s2 != pa.s2)),
                "n_cells": int(pp.s1.size),
                "point_optimum_under_patch_loss_db": db10(abs(h_pa) ** 2 / abs(h_pt) ** 2),
            }
            out[f"{label}_{los}"] = rec
    return out


# --------------------------------------------------------------------------- 3. E-C lab
def ec_part() -> dict[str, Any]:
    d_lab, pts = ec.lab_geometry_scenario()
    rows = {r["row"]: r for r in ec.alphabet_rows()}
    names = [f"r{i:02d}" for i in range(len(pts))]
    res: dict[str, Any] = {}
    ref_ris: dict[str, np.ndarray] = {}
    for cm in CELL_MODELS:
        ps = ec.csi_points(with_cell_model(d_lab, cm), names, "lab")
        r0 = ec.eval_row(rows["ideal_continuous"], ps)
        ref_ris[cm] = r0["ris"]
        rec: dict[str, Any] = {}
        for name in ("ideal_1bit", "ideal_2bit", "lab_mag1", "ideal_designed_on_lab"):
            vv = ec.eval_row(rows[name], ps)
            rec[f"loss_ris_db_{name}"] = db10(float(np.dot(ps.w, vv["ris"])
                                                    / np.dot(ps.w, r0["ris"])))
        rec["direct_over_ris_cont_db"] = db10(float(np.dot(ps.w, np.abs(ps.h0) ** 2)
                                                    / np.dot(ps.w, r0["ris"])))
        res[cm] = rec
    per_pt = 10 * np.log10(ref_ris["po_patch"] / ref_ris["point"])
    res["delta"] = {
        "cont_ris_power_patch_minus_point_db_mean": float(np.mean(per_pt)),
        "cont_ris_power_patch_minus_point_db_min": float(np.min(per_pt)),
        "cont_ris_power_patch_minus_point_db_max": float(np.max(per_pt)),
        "loss_ris_db_ideal_1bit_patch_minus_point":
            res["po_patch"]["loss_ris_db_ideal_1bit"] - res["point"]["loss_ris_db_ideal_1bit"],
        "loss_ris_db_lab_mag1_patch_minus_point":
            res["po_patch"]["loss_ris_db_lab_mag1"] - res["point"]["loss_ris_db_lab_mag1"],
    }
    return res


# --------------------------------------------------------------------------- 4. threshold
def threshold() -> dict[str, Any]:
    arc = yaml.safe_load((SREMU / "scenarios" / "studies" / "lab_arc.yaml").read_text())
    pitch = {float(p["grid"]["pitch_u_m"]) for p in arc["ris"]} | {
        float(p["grid"]["pitch_v_m"]) for p in arc["ris"]}
    assert len(pitch) == 1
    p = pitch.pop()
    half = C0 / FC / 2
    return {"pitch_m": p, "fc_hz": FC, "half_wavelength_m": half,
            "margin_m": half - p, "margin_rel": (half - p) / half,
            "auto_resolves_to": "point" if p <= half else "po_patch"}


def main(argv: list[str]) -> int:
    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    warnings.simplefilter("ignore")
    t0 = _dt.datetime.now(_dt.timezone.utc)
    results = {
        "threshold": threshold(),
        "cell_factor": cell_factor(),
        "eb": eb_part(),
        "ec_lab": ec_part(),
    }
    t1 = _dt.datetime.now(_dt.timezone.utc)
    (out / "results.json").write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8",
                                      newline="\n")
    script = Path(__file__).resolve()
    shutil.copyfile(script, out / "cellmodel_sensitivity.py")
    files = ["experiments/eb.py", "experiments/ec.py", "experiments/common.py",
             "scenarios/studies/lab_arc.yaml", "scenarios/lab_like_A.yaml",
             "data/states/lab_binary_3p5ghz.yaml", "data/states/ideal_antipodal.yaml"]
    meta = {
        "experiment": "sensitivity_cellmodel",
        "data_classes": ["SIM-sremu", "ANALYTIC", "ASSUMED"],
        "description": ("Cell-model sensitivity (point vs po_patch) at the lab pitch: far-field "
                        "factor of a half-wavelength cell; E-B design quantities on the arc and "
                        "lab-like geometries; E-C laboratory-geometry losses. Coefficient level, "
                        "no IQ run; no study is rerun."),
        "assumptions": [
            "laboratory states with |Gamma| = 1 (ASSUMED), as in E-B and E-C",
            "cell_model forced on every panel; po_patch with the default sub-cell size and "
            "Richardson check (sremu defaults)",
            "E-B quantities from experiments/eb.py design() at t = 0 (static scene)",
            "E-C quantities from experiments/ec.py lab_geometry_scenario/csi_points/eval_row",
            "cell factor: one lambda/2 cell at 3.5 GHz, Tx 50 m and Rx 60 m at the stated "
            "in-plane angles, ideal_antipodal state 0 (Gamma = 1), isotropic antennas",
        ],
        "inputs": [{"path": f, "sha256": sha256_file(SREMU / f)} for f in files],
        "script": "cellmodel_sensitivity.py",
        "script_sha256": sha256_file(script),
        "sremu_commit": git_head(SREMU),
        "sremu_dirty": None,
        "sremu_dirty_note": "not determined: the script starts no git process",
        "src_sremu_tree_sha256": tree_sha256(SREMU / "src" / "sremu"),
        "versions": {"python": platform.python_version(), "numpy": np.__version__,
                     "scipy": scipy.__version__},
        "platform": platform.platform(),
        "t_start_utc": t0.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "t_end_utc": t1.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": {"results.json": "threshold, cell_factor, eb, ec_lab (see the script docstring)",
                  "cellmodel_sensitivity.py": "the script that wrote this directory"},
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8",
                                   newline="\n")
    print(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
