"""Tests for the requirement implementation review Halt gate."""

from __future__ import annotations

import pytest

from scripts.review_requirement_implementation import validate_assessment


def test_requirement_assessment_requires_every_case() -> None:
    parsed = {"cases": [{"case_id": "TC001"}, {"case_id": "TC002"}]}
    interfaces = {"interfaces": [{"case_ids": ["TC001"]}]}
    assessment = {"items": [{
        "requirement_id": "REQ001",
        "acceptance_criterion": "Save succeeds",
        "case_id": "TC001",
        "status": "implemented",
        "rationale": "Controller and service implement the rule.",
        "evidence": ["ActivityController.java:10"],
    }]}

    with pytest.raises(ValueError, match="missing=.*TC002"):
        validate_assessment(assessment, parsed, interfaces)


def test_implemented_conclusion_requires_mapped_interface_evidence() -> None:
    parsed = {"cases": [{"case_id": "TC001"}]}
    assessment = {"items": [{
        "requirement_id": "REQ001",
        "acceptance_criterion": "Save succeeds",
        "case_id": "TC001",
        "status": "implemented",
        "rationale": "Implementation exists.",
        "evidence": ["ActivityController.java:10"],
    }]}

    with pytest.raises(ValueError, match="lacks testcase interface evidence"):
        validate_assessment(assessment, parsed, {"interfaces": []})


def test_non_implemented_status_is_preserved_for_halt() -> None:
    parsed = {"cases": [{"case_id": "TC001"}]}
    assessment = {"items": [{
        "requirement_id": "REQ001",
        "acceptance_criterion": "Reject invalid state",
        "case_id": "TC001",
        "status": "implementation_conflict",
        "rationale": "The source permits the invalid transition.",
        "evidence": ["ActivityService.java:42"],
    }]}

    result = validate_assessment(assessment, parsed, {"interfaces": []})

    assert result[0]["status"] == "implementation_conflict"
