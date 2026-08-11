"""Validate a complete review run and publish it without mixing batches."""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path

from workflow_contract import (
    artifact_hashes,
    read_json_object,
    review_input_path,
    sha256_file,
    validate_prepared_review_gate,
    write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and publish a TAPD code review run.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--test-cases", required=True)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    output_root = Path(args.output_root).resolve()
    test_cases_path = Path(args.test_cases).resolve()
    if run_dir.parent != output_root / "runs":
        raise ValueError(f"Review run must be inside {output_root / 'runs'}: {run_dir}")
    context = read_json_object(run_dir / "review_context.json", "review context")
    metadata_path = review_input_path(context, "metadata_path")
    validate_prepared_review_gate(
        run_dir,
        run_dir / "source_manifest.json",
        run_dir / "code_source_confirmation.json",
        test_cases_path,
        metadata_path,
    )
    inputs = context["inputs"]
    if not isinstance(inputs, dict):
        raise TypeError("review_context.json.inputs must be an object")
    testcase_hash = sha256_file(test_cases_path)
    if inputs.get("test_cases_sha256") != testcase_hash:
        raise ValueError("test_cases.md changed after review preparation")

    hashes = artifact_hashes(run_dir)
    unresolved = read_json_object(run_dir / "raw" / "table_resolution.json", "table resolution")
    raw_tables = unresolved.get("tables")
    if not isinstance(raw_tables, list):
        raise TypeError("table_resolution.json.tables must be a list")
    unresolved_count = sum(
        1 for table in raw_tables
        if isinstance(table, dict) and table.get("status") != "confirmed"
    )
    route_consistency = read_json_object(run_dir / "raw" / "route_consistency.json", "route consistency")
    route_conflict_count = require_non_negative_count(route_consistency, "route_conflict_count")
    ambiguous_route_count = require_non_negative_count(route_consistency, "ambiguous_route_count")
    gateway_evidence_unresolved_count = require_non_negative_count(route_consistency, "gateway_evidence_unresolved_count")
    blocking_route_issue_count = route_conflict_count + ambiguous_route_count + gateway_evidence_unresolved_count
    validation: dict[str, object] = {
        "valid": blocking_route_issue_count == 0,
        "approval_ready": unresolved_count == 0 and blocking_route_issue_count == 0,
        "review_run_id": run_dir.name,
        "source_run_id": context.get("source_run_id"),
        "validated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "testcase_hash": testcase_hash,
        "unresolved_table_count": unresolved_count,
        "route_matched_count": require_non_negative_count(route_consistency, "route_matched_count"),
        "route_unverified_count": require_non_negative_count(route_consistency, "route_unverified_count"),
        "route_conflict_count": route_conflict_count,
        "ambiguous_route_count": ambiguous_route_count,
        "gateway_evidence_unresolved_count": gateway_evidence_unresolved_count,
        "blocking_route_issue_count": blocking_route_issue_count,
        "artifacts": hashes,
    }
    write_json(run_dir / "review_validation.json", validation)
    if blocking_route_issue_count > 0:
        raise ValueError(
            "Blocking route consistency issues: "
            f"conflicts={route_conflict_count}, ambiguous={ambiguous_route_count}, "
            f"gateway_evidence_unresolved={gateway_evidence_unresolved_count}; "
            f"see {run_dir / 'raw' / 'route_consistency.json'}"
        )
    update_evidence_index(run_dir / "evidence_index.json", validation)
    publish_directory(run_dir, output_root / "latest")
    print(str(output_root / "latest"))
    return 0


def update_evidence_index(path: Path, validation: dict[str, object]) -> None:
    index = read_json_object(path, "evidence index")
    index["review_run_id"] = validation["review_run_id"]
    index["source_run_id"] = validation["source_run_id"]
    index["testcase_hash"] = validation["testcase_hash"]
    index["artifacts"] = validation["artifacts"]
    write_json(path, index)


def require_non_negative_count(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"route consistency field {key} must be a non-negative integer")
    return value


def publish_directory(run_dir: Path, latest_dir: Path) -> None:
    staging_dir = latest_dir.with_name("latest.staging")
    backup_dir = latest_dir.with_name("latest.backup")
    for path in (staging_dir, backup_dir):
        if path.exists():
            shutil.rmtree(path)
    shutil.copytree(run_dir, staging_dir)
    try:
        if latest_dir.exists():
            latest_dir.rename(backup_dir)
        staging_dir.rename(latest_dir)
    except OSError:
        if not latest_dir.exists() and backup_dir.exists():
            backup_dir.rename(latest_dir)
        raise
    if backup_dir.exists():
        shutil.rmtree(backup_dir)


if __name__ == "__main__":
    raise SystemExit(main())
