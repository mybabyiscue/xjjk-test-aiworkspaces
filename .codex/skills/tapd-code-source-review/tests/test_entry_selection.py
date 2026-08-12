"""Regression tests for testcase-to-source entry selection."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "analyze_testcase_evidence.py"
SPEC = importlib.util.spec_from_file_location("analyze_testcase_evidence", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Unable to load evidence analysis module")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_route_free_cases_keep_unchanged_controller_candidates() -> None:
    entries = [
        {
            "file": "controller/UnchangedController.java",
            "line": 10,
            "end_line": 18,
            "controller_route": "/api/resource",
        },
        {
            "file": "service/ChangedService.java",
            "line": 30,
            "end_line": 40,
            "controller_route": "",
        },
    ]
    cases = [{"case_id": "TC001", "routes": []}]
    changed_ranges = {"/workspace/service/changedservice.java": [(30, 40)]}

    selected = MODULE.select_business_entries(entries, cases, changed_ranges)

    assert selected == entries


def test_route_literals_still_apply_strong_root_filter() -> None:
    entries = [
        {"controller_route": "/api/resource", "line": 1, "end_line": 2},
        {"controller_route": "/admin/other", "line": 3, "end_line": 4},
    ]
    cases = [{"case_id": "TC001", "routes": ["/gateway/api/resource/save"]}]

    selected = MODULE.select_business_entries(entries, cases, {})

    assert selected == [entries[0]]
