"""WP12: wording that tools/gen_results.py writes into the paper (Table IV caption, prose counts).

The Table IV caption explains why PH-12 is absent from the test record: its tests run in the
experimental tier, which the paper-junit markers deselect. The sremu side of that sentence is
checked here, so the caption cannot outlive the fact it states.
"""

from __future__ import annotations

import importlib.util
import re

import pytest
from conftest import SREMU_ROOT
from gen_results import prose_count, render_table4

pytestmark = pytest.mark.unit

TIER_SENTENCE = ("its implementation tests run in the experimental test tier, outside the "
                 "continuous-integration and nightly runs that this record covers.")


def _table4(ph12_status: str) -> str:
    return render_table4({"meta": {}, "families": [], "ph12_status": ph12_status})


def test_table4_caption_states_where_ph12_runs() -> None:
    assert TIER_SENTENCE in _table4("missing")
    assert "no implementation test" not in _table4("missing")
    assert "its implementation tests passed." in _table4("passed")
    assert "its status is failed." in _table4("failed")


@pytest.mark.skipif(not (SREMU_ROOT / "tools" / "tasks.py").is_file(),
                    reason="sremu checkout not found next to the paper")
def test_ph12_tests_are_experimental_and_outside_the_paper_junit() -> None:
    """The fact behind the caption: sremu's PH-12 tests carry the `experimental` marker, and
    the markers of `tasks.py paper-junit` (CI + nightly tiers) do not select it."""
    tests = sorted((SREMU_ROOT / "tests" / "experimental").glob("test_ph12*.py"))
    assert tests, "no PH-12 test in sremu tests/experimental"
    assert all("pytestmark = pytest.mark.experimental" in t.read_text(encoding="utf-8")
               for t in tests)
    spec = importlib.util.spec_from_file_location("sremu_tasks", SREMU_ROOT / "tools" / "tasks.py")
    assert spec is not None
    assert spec.loader is not None
    tasks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tasks)
    names = set(re.findall(r"[A-Za-z_]+", tasks.PAPER_MARKERS)) - {"or", "and", "not"}
    assert "experimental" not in names
    assert names == set(re.findall(r"[A-Za-z_]+", f"{tasks.CI_MARKERS} {tasks.NIGHTLY_MARKERS}")
                        ) - {"or", "and", "not"}


@pytest.mark.parametrize(("n", "want"), [(0, "zero"), (1, "one"), (6, "six"), (9, "nine"),
                                         (10, "10"), (64, "64"), (12345, "12\\,345")])
def test_prose_count_spells_out_one_to_nine(n: int, want: str) -> None:
    assert prose_count(n) == want
