"""resolve_code_numbers.py -- read `code` numbers of numbers.yaml from the installed sremu (WP12).

The paper describes constants and defaults of the committed sremu code (frame header size,
message caps, page-size limits, interpolation error target, ...). DESIGN 1.6 rule 7 requires
every such number to come from the pipeline, so they are not typed into numbers.yaml: each
`code` macro names an sremu module and a Python expression, and this script evaluates the
expression in that module, in the interpreter that runs it (build_paper.py uses its own, which
a final build has checked to import the pinned sremu checkout).

Usage: python tools/resolve_code_numbers.py REQUEST.json
  REQUEST.json = [{"macro": ..., "module": "sremu.x", "expr": "...", "fmt": null|"..."}, ...]
Prints one JSON object: {"sremu_file": <sremu.__file__ or null>,
                         "values": {macro: LaTeX value}, "errors": {macro: message}}.

Expression namespace: the module's globals, plus `inspect`, `math` and `Fraction`. The
expression must evaluate to an int, Fraction, float, str or a tuple/list of those (a sequence
is joined as "a, b and c"). Formats (`fmt`):
  * None      int -> "64"; Fraction -> its integer or a %g float; float -> %g; str -> as is
              (TeX-escaped); bool is refused;
  * "sci"     power-of-ten LaTeX: 1e-3 -> \\ensuremath{10^{-3}},
              2e-5 -> \\ensuremath{2\\times10^{-5}};
  * any other Python format spec applied to every element (".3f", ",d", "g").
A negative number is wrapped in \\ensuremath{...} so that it prints a true minus sign.
Standard library only; never touches the network or git.
"""

from __future__ import annotations

import importlib
import inspect
import json
import math
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

_TEX_ESCAPES = {"\\": r"\textbackslash{}", "_": r"\_", "%": r"\%", "&": r"\&", "#": r"\#",
                "$": r"\$", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
                "^": r"\textasciicircum{}"}


def tex_escape(s: str) -> str:
    return "".join(_TEX_ESCAPES.get(ch, ch) for ch in s)


def _sci(x: float) -> str:
    if x == 0 or not math.isfinite(x):
        raise ValueError(f"cannot write {x!r} as a power of ten")
    e = math.floor(math.log10(abs(x)))
    m = x / 10.0**e
    if abs(round(m) - m) < 1e-9:
        m = round(m)
    if m == 1:
        return f"\\ensuremath{{10^{{{e}}}}}"
    if m == -1:
        return f"\\ensuremath{{-10^{{{e}}}}}"
    return f"\\ensuremath{{{m:g}\\times10^{{{e}}}}}"


def format_scalar(v: Any, fmt: str | None) -> str:
    """LaTeX text of one value (see the module docstring)."""
    if isinstance(v, bool):
        raise TypeError("bool is not a quotable number")
    if isinstance(v, str):
        if fmt not in (None, ""):
            raise TypeError("a format applies to numbers, not strings")
        return tex_escape(v)
    if isinstance(v, Fraction):
        v = v.numerator if v.denominator == 1 else float(v)
    if not isinstance(v, (int, float)):
        raise TypeError(f"unsupported value type {type(v).__name__}")
    if fmt == "sci":
        return _sci(float(v))
    if fmt:
        text = format(v, fmt)
    elif isinstance(v, int):
        text = str(v)
    else:
        if not math.isfinite(v):
            raise ValueError(f"non-finite value {v!r}")
        text = format(v, "g")
    if text.startswith("-"):
        return f"\\ensuremath{{-{text[1:]}}}"
    return text


def format_value(v: Any, fmt: str | None) -> str:
    if isinstance(v, (tuple, list)):
        items = [format_scalar(x, fmt) for x in v]
        if not items:
            raise ValueError("empty sequence")
        if len(items) == 1:
            return items[0]
        return ", ".join(items[:-1]) + " and " + items[-1]
    return format_scalar(v, fmt)


def resolve(requests: list[dict[str, Any]]) -> dict[str, Any]:
    values: dict[str, str] = {}
    errors: dict[str, str] = {}
    for req in requests:
        macro = str(req["macro"])
        try:
            mod = importlib.import_module(str(req["module"]))
            ns = dict(vars(mod))
            ns.update({"inspect": inspect, "math": math, "Fraction": Fraction})
            val = eval(str(req["expr"]), ns)
            values[macro] = format_value(val, req.get("fmt"))
        except Exception as exc:  # report every failure; the caller decides
            errors[macro] = f"{type(exc).__name__}: {exc}"
    try:
        sremu_file: str | None = importlib.import_module("sremu").__file__
    except Exception:
        sremu_file = None
    return {"sremu_file": sremu_file, "values": values, "errors": errors}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    requests = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    sys.stdout.write(json.dumps(resolve(requests), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
