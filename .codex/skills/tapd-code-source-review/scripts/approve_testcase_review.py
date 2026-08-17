"""Create final testcase approval after verifying the published evidence batch."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from workflow_contract import (
    artifact_hashes,
    read_json_object,
    review_input_path,
    sha256_file,
    validate_prepared_review_gate,
    validate_review_document_schemas,
    write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Approve a validated testcase evidence review.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--test-cases", required=True)
    parser.add_argument("--approver", required=True)
    parser.add_argument("--approval-note", required=True)
    parser.add_argument("--confirmation-path", required=True)
    parser.add_argument("--knowledge-root", required=True)
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    test_cases_path = Path(args.test_cases).resolve()
    validation = read_json_object(run_dir / "review_validation.json", "review validation")
    context = read_json_object(run_dir / "review_context.json", "review context")
    confirmation = read_json_object(run_dir / "code_source_confirmation.json", "code source confirmation")
    validate_prepared_review_gate(
        run_dir,
        run_dir / "source_manifest.json",
        run_dir / "code_source_confirmation.json",
        test_cases_path,
        review_input_path(context, "metadata_path"),
    )
    if validation.get("valid") is not True or validation.get("approval_ready") is not True:
        raise ValueError("Review validation is not approval-ready")
    for key in ("route_conflict_count", "ambiguous_route_count", "gateway_evidence_unresolved_count"):
        if validation.get(key) != 0:
            raise ValueError(f"Review validation contains blocking route issues: {key}={validation.get(key)}")
    if confirmation.get("approved") is not True:
        raise ValueError("Code source confirmation is not approved")
    if validation.get("review_run_id") != run_dir.name:
        raise ValueError("Review validation belongs to a different run")
    testcase_hash = sha256_file(test_cases_path)
    if validation.get("testcase_hash") != testcase_hash:
        raise ValueError("test_cases.md changed after review validation")
    validate_review_document_schemas(run_dir)
    expected_artifacts = validation.get("artifacts")
    if not isinstance(expected_artifacts, dict):
        raise TypeError("review_validation.json.artifacts must be an object")
    if artifact_hashes(run_dir) != expected_artifacts:
        raise ValueError("Review run artifacts changed after validation")
    latest_dir = run_dir.parent.parent / "latest"
    latest_context_path = latest_dir / "review_context.json"
    latest_context = read_json_object(latest_context_path, "published review context")
    if latest_context.get("review_run_id") != run_dir.name:
        raise ValueError("The approved review run is not the published latest run")
    validate_review_document_schemas(latest_dir)
    if artifact_hashes(latest_dir) != expected_artifacts:
        raise ValueError("Published latest artifacts do not match the validated review run")
    if not args.approval_note.strip():
        raise ValueError("Approval note must not be empty")

    approved_at = datetime.now().astimezone().isoformat(timespec="seconds")
    payload: dict[str, object] = {
        "approved": True,
        "approved_at": approved_at,
        "approver": args.approver,
        "approval_note": args.approval_note.strip(),
        "testcase_hash": testcase_hash,
        "code_review_run_id": run_dir.name,
        "source_run_id": context.get("source_run_id"),
    }
    write_json(Path(args.confirmation_path), payload)
    update_knowledge_index(Path(args.knowledge_root), payload)
    print(str(Path(args.confirmation_path)))
    return 0


def update_knowledge_index(knowledge_root: Path, approval: dict[str, object]) -> None:
    knowledge_root.mkdir(parents=True, exist_ok=True)
    index_path = knowledge_root / "index.json"
    if index_path.exists():
        index = read_json_object(index_path, "knowledge index")
    else:
        index = {"entries": []}
    approvals = index.get("approvals", [])
    if not isinstance(approvals, list):
        raise TypeError("knowledge/index.json.approvals must be a list")
    approvals = [
        entry for entry in approvals
        if not isinstance(entry, dict) or entry.get("code_review_run_id") != approval["code_review_run_id"]
    ]
    approvals.append({
        "type": "approved_code_review",
        "code_review_run_id": approval["code_review_run_id"],
        "source_run_id": approval["source_run_id"],
        "testcase_hash": approval["testcase_hash"],
        "approved_at": approval["approved_at"],
    })
    index["approvals"] = approvals
    index["last_updated"] = approval["approved_at"]
    write_json(index_path, index)


if __name__ == "__main__":
    raise SystemExit(main())
