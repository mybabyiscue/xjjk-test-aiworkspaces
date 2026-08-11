"""Render three user-facing documents from a validated generic assessment."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from preparation_contract import read_json_object, require_list, require_object, require_string

SENSITIVE_HEADER_TOKENS: frozenset[str] = frozenset({"authorization", "cookie", "password", "secret", "token", "api-key", "apikey"})


def parse_arguments() -> argparse.Namespace:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="渲染三份接口测试准备文档。")
    parser.add_argument("--assessment", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def markdown_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def redact_headers(headers: dict[str, object]) -> dict[str, object]:
    return {
        key: "***" if any(token in key.lower() for token in SENSITIVE_HEADER_TOKENS) else value
        for key, value in headers.items()
    }


def join_url(api_domain: object, path: object) -> str:
    return f"{str(api_domain).rstrip('/')}/{str(path).lstrip('/')}"


def bullet_lines(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        return ["- 无"]
    return [f"- {json.dumps(item, ensure_ascii=False) if isinstance(item, (dict, list)) else item}" for item in value]


def audit_lines(value: object) -> list[str]:
    audit: dict[str, object] = require_object(value, "audit")
    return [
        "#### 审核信息",
        f"- 审核状态：{audit['status']}",
        f"- 证据完整性：{audit['evidence_status']}",
        f"- 审核依据：{audit['reason'] or '待填写'}",
        f"- 审核人：{audit['reviewer'] or '待填写'}",
        f"- 审核时间：{audit['reviewed_at'] or '待填写'}",
    ]


def parameter_table(parameters: object) -> list[str]:
    raw_parameters: list[object] = require_list(parameters, "parameters")
    lines: list[str] = ["| 参数 | 位置 | 类型 | 必填 | 值 | 来源 | 查询记录 |", "|---|---|---|---:|---|---|---|"]
    for raw_parameter in raw_parameters:
        parameter: dict[str, object] = require_object(raw_parameter, "parameter")
        value: object = parameter.get("value")
        raw_source: object = parameter.get("source")
        source: object = raw_source if isinstance(raw_source, dict) else {"kind": parameter.get("source_type"), "reference": parameter.get("source_reference")}
        lines.append(
            "| "
            + " | ".join(
                [
                    str(parameter.get("name", "")),
                    str(parameter.get("location", "")),
                    str(parameter.get("type", "")),
                    str(parameter.get("required", "")),
                    json.dumps(value, ensure_ascii=False),
                    json.dumps(source, ensure_ascii=False),
                    str(parameter.get("query_reference", "")),
                ]
            )
            + " |"
        )
    return lines


def redact_data_entry(value: object) -> dict[str, object]:
    entry: dict[str, object] = copy.deepcopy(require_object(value, "data_preparation.entry"))
    for action_name in ("setup", "cleanup"):
        raw_action: object = entry.get(action_name)
        if not isinstance(raw_action, dict):
            continue
        raw_headers: object = raw_action.get("headers")
        if isinstance(raw_headers, dict):
            raw_action["headers"] = redact_headers(raw_headers)
    return entry


def render_interface_document(assessment: dict[str, object], snapshot: dict[str, object]) -> str:
    environment: dict[str, object] = require_object(assessment.get("environment"), "environment")
    confirmation: dict[str, object] = require_object(snapshot.get("testcase_confirmation"), "snapshot.testcase_confirmation")
    lines: list[str] = ["# 接口测试准备文档", "", f"- 测试环境：{environment['name']}（{environment['api_domain']}）", f"- 测试用例哈希：{confirmation['testcase_hash']}", f"- 代码复审批次：{confirmation['code_review_run_id']}", ""]
    data_preparation: dict[str, object] = require_object(assessment.get("data_preparation"), "data_preparation")
    lines.extend(["## 真实测试数据生命周期", ""])
    entries: list[object] = require_list(data_preparation.get("entries"), "data_preparation.entries")
    if not entries:
        lines.extend(["- 本次无额外测试数据创建动作。", ""])
    for raw_entry in entries:
        entry: dict[str, object] = redact_data_entry(raw_entry)
        lines.extend(
            [
                f"### {entry['id']}",
                f"- 策略：{entry['strategy']}",
                f"- 覆盖用例：{', '.join(entry['case_keys'])}",
                f"- 验证查询：{entry['verification_query_reference']}",
                "- Setup/Cleanup：",
                "```json",
                markdown_json({"setup": entry.get("setup"), "cleanup": entry.get("cleanup")}),
                "```",
                "",
            ]
        )
    for raw_interface in require_list(assessment.get("interface_cases"), "interface_cases"):
        interface: dict[str, object] = require_object(raw_interface, "interface_case")
        evidence: dict[str, object] = require_object(interface.get("interface_evidence"), "interface_evidence")
        method: object = evidence.get("method", evidence.get("http_method", ""))
        operation: object = evidence.get("operation", evidence.get("controller_method", ""))
        lines.extend([f"## {interface['interface_key']}", f"- 覆盖用例：{', '.join(interface['covered_case_keys'])}", f"- 协议：{evidence.get('protocol', 'http')}", f"- 服务：{evidence['service']}", f"- 操作：{operation}", f"- Method：{method}", f"- 请求 URL：{join_url(environment['api_domain'], evidence['path'])}", ""])
        for index, raw_variant in enumerate(require_list(interface.get("request_variants"), "request_variants"), start=1):
            variant: dict[str, object] = require_object(raw_variant, "request_variant")
            headers: dict[str, object] = require_object(variant.get("headers"), "headers")
            expected: dict[str, object] = require_object(variant.get("expected"), "expected")
            validation_evidence: list[object] = require_list(variant.get("evidence_references", variant.get("validation_evidence", [])), "evidence_references")
            lines.extend([f"### 请求变体 {index}：{variant['name']}", f"- 执行语义：{variant.get('variant_type', '')}", f"- 场景分类：{variant.get('scenario_category', '')}", f"- 场景标签：{', '.join(str(item) for item in variant.get('scenario_tags', []))}", f"- 覆盖用例：{', '.join(variant['case_keys'])}", "- 校验依据：", *bullet_lines(validation_evidence), "- Header：", "```json", markdown_json(redact_headers(headers)), "```", "- 参数：", *parameter_table(variant.get("parameters")), "", "- 请求体：", "```json", markdown_json(variant.get("request_body")), "```", f"- 预期 HTTP 状态：{expected.get('http_status', '按响应类型断言')}", "- 响应断言：", *bullet_lines(expected.get("response_assertions")), "- 数据库断言：", *bullet_lines(expected.get("database_assertions")), "- 前置步骤：", *bullet_lines(variant.get("setup_steps")), "- 清理步骤：", *bullet_lines(variant.get("cleanup_steps")), ""])
        lines.extend([f"- 反向变体策略：{interface['negative_variant_policy']}", "- 无反向变体时的证据：", *bullet_lines(interface.get("negative_variant_evidence", []))])
        lines.extend([*audit_lines(interface.get("audit")), ""])
    return "\n".join(lines).strip() + "\n"


def render_non_interface_document(assessment: dict[str, object], snapshot: dict[str, object]) -> str:
    confirmation: dict[str, object] = require_object(snapshot.get("testcase_confirmation"), "snapshot.testcase_confirmation")
    lines: list[str] = ["# 不可接口测试用例文档", "", f"- 测试用例哈希：{confirmation['testcase_hash']}", f"- 代码复审批次：{confirmation['code_review_run_id']}", ""]
    for raw_case in require_list(assessment.get("non_interface_cases"), "non_interface_cases"):
        case: dict[str, object] = require_object(raw_case, "non_interface_case")
        lines.extend([f"## {case['case_key']} - {case['title']}", f"- 分类：{case['classification']}", f"- 不可接口测试原因：{case['reason']}", f"- 推荐测试方式：{case['recommended_test_type']}", "- 前置条件：", str(case.get("precondition", "无")), "- 测试步骤：", *bullet_lines(case.get("steps")), "- 预期结果：", *bullet_lines(case.get("expected_results")), ""])
        for index, raw_interface in enumerate(require_list(case.get("related_interfaces"), "related_interfaces"), start=1):
            interface: dict[str, object] = require_object(raw_interface, "related_interface")
            headers: dict[str, object] = require_object(interface.get("headers"), "headers")
            lines.extend([f"### 相关接口 {index}", f"- Method：{interface.get('method', interface.get('http_method', ''))}", f"- 请求 URL：{interface['path']}", "- Header：", "```json", markdown_json(redact_headers(headers)), "```", "- 参数：", *parameter_table(interface.get("parameters")), ""])
        lines.extend(["- 参数数据与查询记录：", "```json", markdown_json(case.get("parameter_data", [])), "```", "- 缺失证据：", *bullet_lines(case.get("missing_evidence", [])), *audit_lines(case.get("audit")), ""])
    return "\n".join(lines).strip() + "\n"


def render_flow_document(assessment: dict[str, object], snapshot: dict[str, object]) -> str:
    confirmation: dict[str, object] = require_object(snapshot.get("testcase_confirmation"), "snapshot.testcase_confirmation")
    lines: list[str] = ["# 集成测试主流程指南", "", f"- 测试用例哈希：{confirmation['testcase_hash']}", f"- 代码复审批次：{confirmation['code_review_run_id']}", ""]
    flows: list[object] = require_list(assessment.get("core_flows"), "core_flows")
    if not flows:
        lines.extend(["## 未形成可验证核心流程", "", f"- 原因：{assessment['core_flow_blocker_reason'] or '未提供依赖证据。'}", ""])
    for raw_flow in flows:
        flow: dict[str, object] = require_object(raw_flow, "core_flow")
        lines.extend([f"## {flow['name']}", f"- 流程键：{flow['flow_key']}", f"- 关联用例：{', '.join(flow['case_keys'])}", "- 证据：", *bullet_lines(flow.get("evidence_references")), ""])
        for index, step in enumerate(require_list(flow.get("steps"), "flow_steps"), start=1):
            step_object: dict[str, object] = require_object(step, "flow_step")
            evidence: dict[str, object] = require_object(step_object.get("interface_evidence"), "flow_step.interface_evidence")
            headers: dict[str, object] = require_object(step_object.get("headers"), "flow_step.headers")
            expected: dict[str, object] = require_object(step_object.get("expected"), "flow_step.expected")
            lines.extend([f"### 步骤 {index}", f"- 协议：{evidence.get('protocol', 'http')}", f"- 服务：{evidence['service']}", f"- 操作：{evidence.get('operation', evidence.get('controller_method', ''))}", f"- Method：{evidence.get('method', evidence.get('http_method', ''))}", f"- 请求 URL：{evidence['path']}", "- Header：", "```json", markdown_json(redact_headers(headers)), "```", "- 参数：", *parameter_table(step_object.get("parameters")), "- 参数依赖：", *bullet_lines(step_object.get("parameter_dependencies")), "- 请求体：", "```json", markdown_json(step_object.get("request_body")), "```", f"- 预期 HTTP 状态：{expected.get('http_status', '按响应类型断言')}", "- 响应断言：", *bullet_lines(expected.get("response_assertions")), "- 数据库断言：", *bullet_lines(expected.get("database_assertions")), f"- 中断条件：{step_object['interrupt_condition']}", "- 清理步骤：", *bullet_lines(step_object.get("cleanup_steps")), ""])
    if flows:
        lines.extend(
            [
                "## 自动化调用骨架",
                "",
                "```python",
                "for step in selected_flow[\"steps\"]:",
                "    response = send_request(step)",
                "    assert_response(response, step[\"expected\"])",
                "    assert_database(step[\"expected\"][\"database_assertions\"])",
                "```",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    arguments: argparse.Namespace = parse_arguments()
    assessment: dict[str, object] = read_json_object(Path(arguments.assessment))
    snapshot: dict[str, object] = read_json_object(Path(arguments.snapshot))
    output_dir: Path = Path(arguments.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "interface_test_preparation.md").write_text(render_interface_document(assessment, snapshot), encoding="utf-8", newline="\n")
    (output_dir / "non_interface_cases.md").write_text(render_non_interface_document(assessment, snapshot), encoding="utf-8", newline="\n")
    (output_dir / "integration_test_flow.md").write_text(render_flow_document(assessment, snapshot), encoding="utf-8", newline="\n")
    print("已渲染三份接口测试准备文档。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
