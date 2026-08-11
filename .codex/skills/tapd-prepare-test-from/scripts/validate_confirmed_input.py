"""Validate the confirmed testcase and code-review input snapshot."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from evidence_transformer import load_generation_sources
from preparation_contract import PreparationError, file_sha256, load_cases, read_json_object, require_string, write_json_object


def parse_arguments() -> argparse.Namespace:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="校验已确认用例与代码复审输入一致性。")
    parser.add_argument("--confirmation", required=True)
    parser.add_argument("--test-cases", required=True)
    parser.add_argument("--tapd-cases", required=True)
    parser.add_argument("--evidence-index", required=True)
    parser.add_argument("--unit-interface-evidence", required=True)
    parser.add_argument("--core-interface-evidence", required=True)
    parser.add_argument("--table-evidence", required=True)
    parser.add_argument("--code-evidence", required=True)
    parser.add_argument("--environment-name", required=True)
    parser.add_argument("--api-domain", required=True)
    parser.add_argument("--healthcheck-url", required=False)
    parser.add_argument("--token-error-codes", nargs="*", required=False)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def existing_path(value: str, field_name: str) -> Path:
    path: Path = Path(value)
    if not path.is_file():
        raise PreparationError(f"{field_name} 不存在或不是文件：{path}")
    return path


def main() -> int:
    arguments: argparse.Namespace = parse_arguments()
    confirmation_path: Path = existing_path(arguments.confirmation, "confirmation")
    test_cases_path: Path = existing_path(arguments.test_cases, "test_cases")
    cases_path: Path = existing_path(arguments.tapd_cases, "tapd_cases")
    load_cases(cases_path)
    evidence_index_path: Path = existing_path(arguments.evidence_index, "evidence_index")
    unit_interface_evidence_path: Path = existing_path(arguments.unit_interface_evidence, "unit_interface_evidence")
    core_interface_evidence_path: Path = existing_path(arguments.core_interface_evidence, "core_interface_evidence")
    table_evidence_path: Path = existing_path(arguments.table_evidence, "table_evidence")
    code_evidence_path: Path = existing_path(arguments.code_evidence, "code_evidence")
    raw_interface_evidence_path: Path = evidence_index_path.parent / "raw" / "testcase_interface_evidence.json"
    raw_call_chain_evidence_path: Path = evidence_index_path.parent / "raw" / "call_chain_evidence.json"
    raw_table_evidence_path: Path = evidence_index_path.parent / "raw" / "table_evidence.json"
    input_paths: dict[str, Path] = {
        "test_cases": test_cases_path,
        "tapd_cases": cases_path,
        "evidence_index": evidence_index_path,
        "unit_interface_evidence": unit_interface_evidence_path,
        "core_interface_evidence": core_interface_evidence_path,
        "table_evidence": table_evidence_path,
        "code_evidence": code_evidence_path,
        "raw_interface_evidence": raw_interface_evidence_path,
        "raw_call_chain_evidence": raw_call_chain_evidence_path,
        "raw_table_evidence": raw_table_evidence_path,
    }
    confirmation: dict[str, object] = read_json_object(confirmation_path)
    evidence_index: dict[str, object] = read_json_object(evidence_index_path)
    if confirmation.get("approved") is not True:
        raise PreparationError("testcase_confirmation.json.approved 必须为 true。")
    expected_hash: str = require_string(confirmation.get("testcase_hash"), "testcase_hash")
    actual_hash: str = file_sha256(test_cases_path)
    if expected_hash != actual_hash:
        raise PreparationError("testcase_confirmation.json.testcase_hash 与当前 test_cases.md 不一致。")
    review_run_id: str = require_string(confirmation.get("code_review_run_id"), "code_review_run_id")
    if evidence_index.get("review_run_id") != review_run_id:
        raise PreparationError("代码复审批次与 testcase_confirmation.json.code_review_run_id 不一致。")
    validate_review_artifact_hashes(
        evidence_index,
        {
            "unit_test_interfaces.md": unit_interface_evidence_path,
            "core_process_interfaces.md": core_interface_evidence_path,
            "table_information.md": table_evidence_path,
            "source_manifest.json": code_evidence_path,
            "raw/testcase_interface_evidence.json": raw_interface_evidence_path,
            "raw/call_chain_evidence.json": raw_call_chain_evidence_path,
            "raw/table_evidence.json": raw_table_evidence_path,
        },
    )
    generation_sources: dict[str, object] = load_generation_sources(
        evidence_index_path,
        unit_interface_evidence_path,
        core_interface_evidence_path,
        table_evidence_path,
    )
    snapshot: dict[str, object] = {
        "testcase_confirmation": {
            "testcase_hash": expected_hash,
            "code_review_run_id": review_run_id,
            "approved_at": confirmation.get("approved_at", ""),
        },
        "input_hashes": {name: file_sha256(path) for name, path in input_paths.items()},
        "evidence_files": {
            "unit_test_interfaces.md": str(unit_interface_evidence_path),
            "core_process_interfaces.md": str(core_interface_evidence_path),
            "table_information.md": str(table_evidence_path),
            "source_manifest.json": str(code_evidence_path),
            "raw/testcase_interface_evidence.json": str(raw_interface_evidence_path),
            "raw/call_chain_evidence.json": str(raw_call_chain_evidence_path),
            "raw/table_evidence.json": str(raw_table_evidence_path),
        },
        "generation_sources": generation_sources.get("source_artifacts", {}),
        "environment": {
            "name": require_string(arguments.environment_name, "environment_name"),
            "api_domain": require_string(arguments.api_domain, "api_domain"),
            "healthcheck_url": arguments.healthcheck_url or "",
            "token_error_codes": list(arguments.token_error_codes or []),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json_object(Path(arguments.output), snapshot)
    print(f"确认输入校验通过：{review_run_id}")
    return 0


def validate_review_artifact_hashes(evidence_index: dict[str, object], artifact_paths: dict[str, Path]) -> None:
    artifacts: object = evidence_index.get("artifacts")
    if not isinstance(artifacts, dict):
        raise PreparationError("evidence_index.json.artifacts 必须是对象。")
    for artifact_name, path in artifact_paths.items():
        expected_hash: str = require_string(artifacts.get(artifact_name), f"artifacts.{artifact_name}")
        if file_sha256(path) != expected_hash:
            raise PreparationError(f"代码复审证据哈希不一致：{artifact_name}")


if __name__ == "__main__":
    raise SystemExit(main())
