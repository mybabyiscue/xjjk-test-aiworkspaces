"""Validate and publish a human-authored requirement implementation assessment."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from workflow_contract import read_json_object, write_json


ALLOWED_STATUSES: frozenset[str] = frozenset({
    "implemented",
    "partially_implemented",
    "not_implemented",
    "implementation_conflict",
    "unverifiable",
})


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record requirement-to-code implementation conclusions with source evidence."
    )
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--assessment", required=True)
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    assessment = read_json_object(Path(args.assessment).resolve(), "requirement assessment")
    parsed_cases = read_json_object(
        run_dir / "raw" / "parsed_test_cases.json", "parsed test cases"
    )
    interface_evidence = read_json_object(
        run_dir / "raw" / "testcase_interface_evidence.json", "interface evidence"
    )
    matrix = validate_assessment(assessment, parsed_cases, interface_evidence)
    blocking_items = [item for item in matrix if item["status"] != "implemented"]

    write_json(run_dir / "raw" / "requirement_code_matrix.json", {"items": matrix})
    write_review(run_dir / "requirement_implementation_review.md", matrix)
    write_findings(run_dir / "requirement_findings.md", blocking_items)
    write_json(run_dir / "requirement_review_status.json", {
        "status": "halted" if blocking_items else "completed",
        "reviewed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "blocking_count": len(blocking_items),
        "resolution": None,
    })
    print(str(run_dir / "requirement_implementation_review.md"))
    return 0


def validate_assessment(
    assessment: dict[str, object],
    parsed_cases: dict[str, object],
    interface_evidence: dict[str, object],
) -> list[dict[str, object]]:
    raw_cases = parsed_cases.get("cases")
    raw_items = assessment.get("items")
    raw_interfaces = interface_evidence.get("interfaces")
    if not isinstance(raw_cases, list) or not isinstance(raw_items, list):
        raise TypeError("Assessment items and parsed cases must be lists")
    if not isinstance(raw_interfaces, list):
        raise TypeError("Interface evidence must be a list")
    expected_case_ids = {
        str(case.get("case_id", "")) for case in raw_cases if isinstance(case, dict)
    }
    evidenced_case_ids = {
        str(case_id)
        for interface in raw_interfaces if isinstance(interface, dict)
        for case_id in interface.get("case_ids", []) if isinstance(case_id, str)
    }
    matrix: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise TypeError("Each assessment item must be an object")
        case_id = require_text(raw_item, "case_id")
        status = require_text(raw_item, "status")
        rationale = require_text(raw_item, "rationale")
        evidence = raw_item.get("evidence")
        if case_id in seen:
            raise ValueError(f"Duplicate requirement assessment: {case_id}")
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"Invalid implementation status for {case_id}: {status}")
        if not isinstance(evidence, list) or not all(
            isinstance(value, str) and value.strip() for value in evidence
        ):
            raise TypeError(f"Evidence must be a non-empty string list: {case_id}")
        if status == "implemented" and case_id not in evidenced_case_ids:
            raise ValueError(
                f"Implemented conclusion lacks testcase interface evidence: {case_id}"
            )
        seen.add(case_id)
        matrix.append({
            "case_id": case_id,
            "requirement_id": require_text(raw_item, "requirement_id"),
            "acceptance_criterion": require_text(raw_item, "acceptance_criterion"),
            "status": status,
            "rationale": rationale,
            "evidence": [str(value).strip() for value in evidence],
        })
    missing = sorted(expected_case_ids - seen)
    extra = sorted(seen - expected_case_ids)
    if missing or extra:
        raise ValueError(f"Assessment coverage mismatch; missing={missing}, extra={extra}")
    return matrix


def require_text(item: dict[str, object], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Assessment field must be non-empty: {key}")
    return value.strip()


def write_review(path: Path, matrix: list[dict[str, object]]) -> None:
    lines = [
        "# 需求实现正确性审查", "",
        "| 需求点 | 验收标准 | 用例 | 实现结论 | 代码证据 | 判断依据 |",
        "|---|---|---|---|---|---|",
    ]
    for item in matrix:
        evidence = "<br>".join(str(value) for value in item["evidence"])
        lines.append(
            f"| {item['requirement_id']} | {item['acceptance_criterion']} | "
            f"{item['case_id']} | {item['status']} | {evidence} | {item['rationale']} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def write_findings(path: Path, findings: list[dict[str, object]]) -> None:
    lines = [
        "# 需求实现审查问题", "",
        "出现以下问题时流程必须 Halt，未经用户明确处理不得发布审查批次。", "",
        "| 用例 | 状态 | 原因 |", "|---|---|---|",
    ]
    if findings:
        lines.extend(
            f"| {item['case_id']} | {item['status']} | {item['rationale']} |"
            for item in findings
        )
    else:
        lines.append("| 无 | implemented | 无阻塞项 |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    raise SystemExit(main())
