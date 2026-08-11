"""Build the canonical execution plan from a validated preparation assessment."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from preparation_contract import (
    BLOCKING_SOURCE_KINDS,
    PreparationError,
    file_sha256,
    read_json_object,
    require_list,
    require_object,
    require_string,
    write_json_object,
)

VALID_VARIANT_TYPES: frozenset[str] = frozenset({"positive", "negative"})

EXECUTABLE_AUDIT_STATUSES: frozenset[str] = frozenset({"可审核", "已通过"})


def parse_arguments() -> argparse.Namespace:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="生成唯一的接口测试执行计划。")
    parser.add_argument("--assessment", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--report", required=True)
    return parser.parse_args()


def machine_assertion(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    return isinstance(value.get("assertion_type", value.get("path")), str) and isinstance(value.get("operator"), str) and (
        value.get("operator") in {"exists", "not_exists"} or "value" in value
    )


def require_auth_header_name(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise PreparationError(f"{field_name} 必须是字符串。")
    return value.strip()


def parameter_is_unresolved(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    raw_source: object = value.get("source")
    source_kind: object = raw_source.get("kind") if isinstance(raw_source, dict) else value.get("source_type")
    return isinstance(source_kind, str) and source_kind in BLOCKING_SOURCE_KINDS


def require_variant_type(value: object, field_name: str) -> str:
    normalized_value: str = require_string(value, field_name)
    if normalized_value not in VALID_VARIANT_TYPES:
        raise PreparationError(f"{field_name} 仅允许 positive/negative，当前值：{normalized_value}")
    return normalized_value


def real_data_lookup(value: object) -> dict[str, dict[str, object]]:
    lookup: dict[str, dict[str, object]] = {}
    for index, raw_record in enumerate(require_list(value, "real_data_records"), start=1):
        record_group: dict[str, object] = require_object(raw_record, f"real_data_records[{index}]")
        query_reference: str = require_string(record_group.get("query_reference"), f"real_data_records[{index}].query_reference")
        records: list[object] = require_list(record_group.get("records"), f"real_data_records[{index}].records")
        if not records:
            raise PreparationError(f"{query_reference} 缺少可用于参数解析的真实数据记录。")
        lookup[query_reference] = require_object(records[0], f"real_data_records[{index}].records[0]")
    return lookup


def parameter_source_kind(parameter: dict[str, object], field_name: str) -> str:
    source: dict[str, object] = require_object(parameter.get("source"), f"{field_name}.source")
    return require_string(source.get("kind"), f"{field_name}.source.kind")


def parameter_value(parameter: dict[str, object], records: dict[str, dict[str, object]], field_name: str) -> object:
    if "value" in parameter:
        return deepcopy(parameter.get("value"))
    source_kind: str = parameter_source_kind(parameter, field_name)
    if source_kind == "protocol_constant":
        return None
    if source_kind != "database":
        return None
    source: dict[str, object] = require_object(parameter.get("source"), f"{field_name}.source")
    reference: str = require_string(source.get("reference"), f"{field_name}.source.reference")
    query_reference: str = require_string(parameter.get("query_reference"), f"{field_name}.query_reference")
    prefix: str = f"{query_reference}."
    if not reference.startswith(prefix):
        raise PreparationError(f"{field_name}.source.reference 必须引用 {query_reference} 的字段。")
    field_name_in_record: str = reference[len(prefix) :]
    if not field_name_in_record:
        raise PreparationError(f"{field_name}.source.reference 缺少字段名。")
    if query_reference not in records:
        raise PreparationError(f"{field_name}.query_reference 未在 real_data_records 中找到：{query_reference}")
    record: dict[str, object] = records[query_reference]
    if field_name_in_record not in record:
        raise PreparationError(f"{field_name}.source.reference 字段不存在：{query_reference}.{field_name_in_record}")
    value: object = record[field_name_in_record]
    if value is None:
        if parameter.get("required") is True:
            raise PreparationError(f"{field_name}.source.reference 解析结果为空：{query_reference}.{field_name_in_record}")
        return None
    return deepcopy(value)


def apply_parameter(
    path: str,
    query: dict[str, object],
    parameter: dict[str, object],
    value: object,
    field_name: str,
) -> tuple[str, dict[str, object]]:
    name: str = require_string(parameter.get("name"), f"{field_name}.name")
    location: str = require_string(parameter.get("location"), f"{field_name}.location")
    placeholder: str = "{" + name + "}"
    if placeholder in path:
        return path.replace(placeholder, str(value)), query
    if location == "query":
        resolved_query: dict[str, object] = deepcopy(query)
        resolved_query[name] = value
        return path, resolved_query
    if location == "path":
        raise PreparationError(f"{field_name} 标记为 path 参数，但接口路径中缺少占位符：{placeholder}")
    return path, query


def resolve_request_shape(
    path: str,
    request_data: dict[str, object],
    records: dict[str, dict[str, object]],
    field_name: str,
) -> tuple[str, dict[str, object]]:
    query: dict[str, object] = deepcopy(require_object(request_data.get("query"), f"{field_name}.query"))
    for index, raw_parameter in enumerate(require_list(request_data.get("parameters"), f"{field_name}.parameters"), start=1):
        parameter: dict[str, object] = require_object(raw_parameter, f"{field_name}.parameters[{index}]")
        if parameter_source_kind(parameter, f"{field_name}.parameters[{index}]") == "protocol_constant":
            continue
        value: object = parameter_value(parameter, records, f"{field_name}.parameters[{index}]")
        if value is None:
            continue
        path, query = apply_parameter(path, query, parameter, value, f"{field_name}.parameters[{index}]")
    if "{" in path or "}" in path:
        raise PreparationError(f"{field_name}.path 存在未解析路径参数：{path}")
    return path, query


def resolve_http_status(expected: dict[str, object], field_name: str) -> int:
    raw_http_status: object = expected.get("http_status")
    if raw_http_status is None:
        return 200
    if not isinstance(raw_http_status, int):
        raise PreparationError(f"{field_name}.http_status 必须是整数，当前值：{raw_http_status}")
    return raw_http_status


def build_request(
    request_id: str,
    case_ids: object,
    variant_type: object,
    interface_evidence: object,
    request_data: dict[str, object],
    dependencies: object,
    records: dict[str, dict[str, object]],
) -> dict[str, object]:
    evidence: dict[str, object] = require_object(interface_evidence, f"{request_id}.interface_evidence")
    normalized_variant_type: str = require_variant_type(variant_type, f"{request_id}.variant_type")
    expected: dict[str, object] = require_object(request_data.get("expected"), f"{request_id}.expected")
    assertions: list[object] = require_list(expected.get("response_assertions"), f"{request_id}.response_assertions")
    if not assertions or not all(machine_assertion(item) for item in assertions):
        raise PreparationError(f"{request_id} 缺少机器可判定响应断言。")
    scenario_category: str = require_string(request_data.get("scenario_category"), f"{request_id}.scenario_category")
    normalized_expected: dict[str, object] = deepcopy(expected)
    normalized_expected["http_status"] = resolve_http_status(expected, f"{request_id}.expected")
    path, query = resolve_request_shape(
        require_string(evidence.get("path"), f"{request_id}.path"),
        request_data,
        records,
        request_id,
    )
    return {
        "id": request_id,
        "case_ids": deepcopy(require_list(case_ids, f"{request_id}.case_ids")),
        "variant_type": normalized_variant_type,
        "scenario_category": scenario_category,
        "scenario_tags": deepcopy(require_list(request_data.get("scenario_tags", []), f"{request_id}.scenario_tags")),
        "variant_type_decision": deepcopy(request_data.get("variant_type_decision", {})),
        "method": require_string(evidence.get("method", evidence.get("http_method")), f"{request_id}.method"),
        "path": path,
        "headers": deepcopy(require_object(request_data.get("headers"), f"{request_id}.headers")),
        "authorization_header": require_auth_header_name(
            request_data.get("auth_header_name", request_data.get("authorization_header", "")),
            f"{request_id}.authorization_header",
        ),
        "query": query,
        "body": deepcopy(request_data.get("request_body")),
        "expected": normalized_expected,
        "dependencies": deepcopy(require_list(dependencies, f"{request_id}.dependencies")),
    }


def build_execution_plan(
    assessment: dict[str, object],
    preparation_assessment_sha256: str,
) -> tuple[dict[str, object], dict[str, object]]:
    source: dict[str, object] = require_object(assessment.get("source"), "assessment.source")
    testcase_hash: str = require_string(source.get("testcase_hash"), "assessment.source.testcase_hash")
    code_review_run_id: str = require_string(source.get("code_review_run_id"), "assessment.source.code_review_run_id")
    records: dict[str, dict[str, object]] = real_data_lookup(assessment.get("real_data_records", []))
    requests: list[dict[str, object]] = []
    flows: list[dict[str, object]] = []
    blockers: list[str] = []
    data_setup: list[dict[str, object]] = []
    data_cleanup: list[dict[str, object]] = []

    data_preparation: dict[str, object] = require_object(assessment.get("data_preparation"), "data_preparation")
    for entry_index, raw_entry in enumerate(require_list(data_preparation.get("entries"), "data_preparation.entries"), start=1):
        entry: dict[str, object] = require_object(raw_entry, f"data_preparation.entries[{entry_index}]")
        entry_id: str = require_string(entry.get("id"), f"data_preparation.entries[{entry_index}].id")
        strategy: str = require_string(entry.get("strategy"), f"{entry_id}.strategy")
        if strategy not in {"reuse", "api_create", "sql_insert", "manual_create"}:
            blockers.append(f"{entry_id} 使用了不允许的数据策略；禁止 Mock、Fake、Stub 和 Mock seed。")
            continue
        if strategy in {"api_create", "sql_insert"}:
            setup: dict[str, object] = deepcopy(require_object(entry.get("setup"), f"{entry_id}.setup"))
            cleanup: dict[str, object] = deepcopy(require_object(entry.get("cleanup"), f"{entry_id}.cleanup"))
            setup["entry_id"] = entry_id
            cleanup["entry_id"] = entry_id
            data_setup.append(setup)
            data_cleanup.append(cleanup)

    for interface_index, raw_interface in enumerate(
        require_list(assessment.get("interface_cases"), "interface_cases"),
        start=1,
    ):
        interface: dict[str, object] = require_object(raw_interface, f"interface_cases[{interface_index}]")
        interface_key: str = require_string(interface.get("interface_key"), f"interface_cases[{interface_index}].interface_key")
        audit: dict[str, object] = require_object(interface.get("audit"), f"{interface_key}.audit")
        if audit.get("status") not in EXECUTABLE_AUDIT_STATUSES:
            blockers.append(f"{interface_key} 审核状态不允许自动化执行。")
            continue
        evidence: object = interface.get("interface_evidence")
        for variant_index, raw_variant in enumerate(
            require_list(interface.get("request_variants"), f"{interface_key}.request_variants"),
            start=1,
        ):
            variant: dict[str, object] = require_object(raw_variant, f"{interface_key}.request_variants[{variant_index}]")
            parameters: list[object] = require_list(variant.get("parameters"), f"{interface_key}.parameters")
            if any(parameter_is_unresolved(parameter) for parameter in parameters):
                blockers.append(f"{interface_key}/{variant.get('name', '')} 存在 unresolved 参数。")
                continue
            try:
                requests.append(
                    build_request(
                        f"{interface_key}__{variant_index}",
                        variant.get("case_keys"),
                        variant.get("variant_type"),
                        evidence,
                        variant,
                        [],
                        records,
                    )
                )
            except PreparationError as error:
                blockers.append(str(error))

    for flow_index, raw_flow in enumerate(require_list(assessment.get("core_flows"), "core_flows"), start=1):
        flow: dict[str, object] = require_object(raw_flow, f"core_flows[{flow_index}]")
        flow_key: str = require_string(flow.get("flow_key"), f"core_flows[{flow_index}].flow_key")
        steps: list[dict[str, object]] = []
        try:
            for step_index, raw_step in enumerate(require_list(flow.get("steps"), f"{flow_key}.steps"), start=1):
                step: dict[str, object] = require_object(raw_step, f"{flow_key}.steps[{step_index}]")
                step_key: str = require_string(step.get("step_key"), f"{flow_key}.steps[{step_index}].step_key")
                steps.append(
                    build_request(
                        step_key,
                        step.get("case_keys"),
                        step.get("variant_type"),
                        step.get("interface_evidence"),
                        step,
                        step.get("parameter_dependencies"),
                        records,
                    )
                )
            flows.append({"id": flow_key, "name": require_string(flow.get("name"), f"{flow_key}.name"), "steps": steps})
        except PreparationError as error:
            blockers.append(str(error))

    for blocked_index, raw_case in enumerate(require_list(assessment.get("non_interface_cases", []), "non_interface_cases"), start=1):
        blocked_case: dict[str, object] = require_object(raw_case, f"non_interface_cases[{blocked_index}]")
        if blocked_case.get("classification") == "blocked":
            blockers.append(
                f"{blocked_case.get('case_key', f'non_interface_cases[{blocked_index}]')} 当前为 blocked，禁止生成可执行接口计划。"
            )

    plan: dict[str, object] = {
        "version": 2,
        "ready": not blockers,
        "source": {
            "preparation_assessment_sha256": preparation_assessment_sha256,
            "testcase_hash": testcase_hash,
            "code_review_run_id": code_review_run_id,
        },
        "token_error_codes": token_error_codes_from_environment(assessment.get("environment")),
        "data_setup": data_setup,
        "data_cleanup": cleanup_order(data_cleanup),
        "requests": requests,
        "flows": flows,
    }
    report: dict[str, object] = {
        "request_count": len(requests),
        "flow_count": len(flows),
        "data_setup_count": len(data_setup),
        "ready": not blockers,
        "blocker_count": len(blockers),
        "blockers": blockers,
    }
    return plan, report


def token_error_codes_from_environment(value: object) -> list[object]:
    if not isinstance(value, dict):
        return []
    raw_codes: object = value.get("token_error_codes", value.get("healthcheck_unauthorized_codes", []))
    if not isinstance(raw_codes, list):
        raise PreparationError("environment.token_error_codes 必须是数组。")
    return deepcopy(raw_codes)


def cleanup_order(actions: list[dict[str, object]]) -> list[dict[str, object]]:
    remaining: dict[str, dict[str, object]] = {
        require_string(action.get("id"), "cleanup.id"): action
        for action in actions
    }
    ordered: list[dict[str, object]] = []
    while remaining:
        ready_ids: list[str] = []
        for action_id, action in remaining.items():
            dependencies: list[object] = require_list(action.get("depends_on", []), f"{action_id}.depends_on")
            if all(not isinstance(dependency, str) or dependency not in remaining for dependency in dependencies):
                ready_ids.append(action_id)
        if not ready_ids:
            raise PreparationError("cleanup 依赖关系存在环或引用无法排序。")
        for action_id in sorted(ready_ids):
            ordered.append(remaining.pop(action_id))
    return ordered


def main() -> int:
    arguments: argparse.Namespace = parse_arguments()
    assessment_path: Path = Path(arguments.assessment)
    assessment: dict[str, object] = read_json_object(assessment_path)
    plan, report = build_execution_plan(assessment, file_sha256(assessment_path))
    write_json_object(Path(arguments.plan), plan)
    write_json_object(Path(arguments.report), report)
    blockers: list[object] = require_list(report.get("blockers"), "report.blockers")
    if blockers:
        for blocker in blockers:
            print(str(blocker))
        raise PreparationError("执行计划存在阻断项，已终止生成：" + "；".join(str(blocker) for blocker in blockers))
    print(f"执行计划已就绪：{report['request_count']} 个单接口请求，{report['flow_count']} 个核心流程。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
