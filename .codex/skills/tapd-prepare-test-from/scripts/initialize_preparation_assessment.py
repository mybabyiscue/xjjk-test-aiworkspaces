"""Initialize a business-neutral assessment shell from confirmed inputs."""

from __future__ import annotations

import argparse
from pathlib import Path

from evidence_transformer import read_schema_config, validate_generated_artifact_schema
from preparation_contract import PreparationError, case_catalog, file_sha256, load_cases, read_json_object, require_object, write_json_object


def parse_arguments() -> argparse.Namespace:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="初始化接口测试准备评估包。")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--tapd-cases", required=True)
    parser.add_argument("--query-plan", required=True)
    parser.add_argument("--model-mapping", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    arguments: argparse.Namespace = parse_arguments()
    snapshot: dict[str, object] = read_json_object(Path(arguments.snapshot))
    query_plan_path: Path = Path(arguments.query_plan)
    model_mapping_path: Path = Path(arguments.model_mapping)
    query_plan: dict[str, object] = read_json_object(query_plan_path)
    model_mapping: dict[str, object] = read_json_object(model_mapping_path)
    skill_dir: Path = Path(__file__).resolve().parents[1]
    schema: dict[str, object] = read_schema_config(skill_dir)
    query_errors: list[str] = validate_generated_artifact_schema(query_plan, schema, "query_plan")
    model_errors: list[str] = validate_generated_artifact_schema(model_mapping, schema, "model_mapping")
    if query_errors or model_errors:
        raise PreparationError("Generated stage-4 artifacts do not match schema: " + "; ".join(query_errors + model_errors))
    require_ready_report(query_plan, "query_plan")
    require_ready_report(model_mapping, "model_mapping")
    confirmation: dict[str, object] = require_object(snapshot.get("testcase_confirmation"), "snapshot.testcase_confirmation")
    cases: list[dict[str, object]] = load_cases(Path(arguments.tapd_cases))
    assessment: dict[str, object] = {
        "source": {
            "testcase_hash": confirmation.get("testcase_hash"),
            "code_review_run_id": confirmation.get("code_review_run_id"),
            "input_hashes": snapshot.get("input_hashes", {}),
            "generated_artifact_hashes": {
                "query_plan.json": file_sha256(query_plan_path),
                "model_mapping.json": file_sha256(model_mapping_path),
            },
        },
        "environment": snapshot.get("environment", {}),
        "case_catalog": case_catalog(cases),
        "interface_cases": [],
        "non_interface_cases": [],
        "core_flows": [],
        "core_flow_blocker_reason": "",
        "real_data_records": [],
    }
    write_json_object(Path(arguments.output), assessment)
    print(f"已初始化 {len(cases)} 条用例的准备评估包。")
    return 0


def require_ready_report(artifact: dict[str, object], artifact_name: str) -> None:
    report: object = artifact.get("generation_report")
    if not isinstance(report, dict):
        raise PreparationError(f"{artifact_name}.generation_report must be an object.")
    status: object = report.get("status")
    if status != "ready":
        missing_items: object = report.get("missing_items")
        raise PreparationError(f"{artifact_name} generation is not ready. Missing or blocked items: {missing_items}")


if __name__ == "__main__":
    raise SystemExit(main())
