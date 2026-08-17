"""Render the three user-facing test-preparation documents."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from preparation_contract import read_json_object, require_list, require_object


SENSITIVE_HEADER_TOKENS: frozenset[str] = frozenset(
    {"authorization", "cookie", "password", "secret", "token", "api-key", "apikey"}
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="渲染三份接口测试准备文档。")
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


def redact_data_entry(value: object) -> dict[str, object]:
    entry = copy.deepcopy(require_object(value, "data_preparation.entry"))
    for action_name in ("setup", "cleanup"):
        action = entry.get(action_name)
        if isinstance(action, dict) and isinstance(action.get("headers"), dict):
            action["headers"] = redact_headers(action["headers"])
    return entry


def join_url(domain: object, path: object) -> str:
    return f"{str(domain).rstrip('/')}/{str(path).lstrip('/')}"


def bullet_lines(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        return ["- 无"]
    return [
        f"- {json.dumps(item, ensure_ascii=False) if isinstance(item, (dict, list)) else item}"
        for item in value
    ]


def parameter_table(value: object) -> list[str]:
    parameters = require_list(value, "parameters")
    lines = [
        "| 参数 | 位置 | 类型 | 必填 | 当前值 | 来源 | 查询记录 |",
        "|---|---|---|---:|---|---|---|",
    ]
    for raw_parameter in parameters:
        parameter = require_object(raw_parameter, "parameter")
        lines.append(
            "| "
            + " | ".join(
                [
                    str(parameter.get("name", "")),
                    str(parameter.get("location", "")),
                    str(parameter.get("type", "")),
                    str(parameter.get("required", "")),
                    json.dumps(parameter.get("value"), ensure_ascii=False),
                    json.dumps(parameter.get("source"), ensure_ascii=False),
                    str(parameter.get("query_reference", "")),
                ]
            )
            + " |"
        )
    return lines


def audit_lines(value: object) -> list[str]:
    audit = require_object(value, "audit")
    return [
        "#### 审核信息",
        f"- 审核状态：{audit.get('status', '')}",
        f"- 证据充分性：{audit.get('evidence_status', '')}",
        f"- 审核依据：{audit.get('reason', '')}",
        f"- 审核人：{audit.get('reviewer', '')}",
        f"- 审核时间：{audit.get('reviewed_at', '')}",
    ]


def render_interface_document(assessment: dict[str, object], snapshot: dict[str, object]) -> str:
    environment = require_object(assessment.get("environment"), "environment")
    confirmation = require_object(snapshot.get("testcase_confirmation"), "snapshot.testcase_confirmation")
    lines = [
        "# 接口测试准备文档",
        "",
        f"- 测试环境：{environment.get('name', '')}（{environment.get('api_domain', '')}）",
        f"- 测试用例哈希：{confirmation.get('testcase_hash', '')}",
        f"- 代码审查批次：{confirmation.get('code_review_run_id', '')}",
        "",
        "## 真实数据准备与恢复",
        "",
    ]
    data_preparation = require_object(assessment.get("data_preparation"), "data_preparation")
    entries = require_list(data_preparation.get("entries"), "data_preparation.entries")
    if not entries:
        lines.extend(["- 本批次无独立测试数据准备动作。", ""])
    for raw_entry in entries:
        entry = redact_data_entry(raw_entry)
        lines.extend(
            [
                f"### {entry.get('id', '')}",
                f"- 策略：{entry.get('strategy', '')}",
                f"- 覆盖用例：{', '.join(str(item) for item in entry.get('case_keys', []))}",
                f"- 验证查询：{entry.get('verification_query_reference', '')}",
                "- Setup/Cleanup：",
                "```json",
                markdown_json({"setup": entry.get("setup"), "cleanup": entry.get("cleanup")}),
                "```",
                "",
            ]
        )
    for raw_interface in require_list(assessment.get("interface_cases"), "interface_cases"):
        interface = require_object(raw_interface, "interface_case")
        evidence = require_object(interface.get("interface_evidence"), "interface_evidence")
        lines.extend(
            [
                f"## {interface.get('interface_key', '')}",
                f"- 覆盖用例：{', '.join(str(item) for item in interface.get('covered_case_keys', []))}",
                f"- 服务：{evidence.get('service', '')}",
                f"- 操作：{evidence.get('operation', '')}",
                f"- Method：{evidence.get('method', '')}",
                f"- 完整 URL：{join_url(environment.get('api_domain', ''), evidence.get('path', ''))}",
                "",
            ]
        )
        for index, raw_variant in enumerate(require_list(interface.get("request_variants"), "request_variants"), start=1):
            variant = require_object(raw_variant, "request_variant")
            expected = require_object(variant.get("expected"), "expected")
            headers = require_object(variant.get("headers"), "headers")
            lines.extend(
                [
                    f"### 请求变体 {index}：{variant.get('name', '')}",
                    f"- 执行分类：{variant.get('variant_type', '')}",
                    f"- 场景分类：{variant.get('scenario_category', '')}",
                    f"- 覆盖用例：{', '.join(str(item) for item in variant.get('case_keys', []))}",
                    "- Header：",
                    "```json",
                    markdown_json(redact_headers(headers)),
                    "```",
                    "- 参数：",
                    *parameter_table(variant.get("parameters")),
                    "",
                    "- 请求体：",
                    "```json",
                    markdown_json(variant.get("request_body")),
                    "```",
                    f"- 预期 HTTP 状态：{expected.get('http_status', '')}",
                    "- 响应断言：",
                    *bullet_lines(expected.get("response_assertions")),
                    "- 数据库断言：",
                    *bullet_lines(expected.get("database_assertions")),
                    "- 清理步骤：",
                    *bullet_lines(variant.get("cleanup_steps")),
                    "",
                ]
            )
        lines.extend([*audit_lines(interface.get("audit")), ""])
    return "\n".join(lines).strip() + "\n"


def render_non_interface_document(assessment: dict[str, object], snapshot: dict[str, object]) -> str:
    confirmation = require_object(snapshot.get("testcase_confirmation"), "snapshot.testcase_confirmation")
    lines = [
        "# 非自动接口测试用例",
        "",
        f"- 测试用例哈希：{confirmation.get('testcase_hash', '')}",
        f"- 代码审查批次：{confirmation.get('code_review_run_id', '')}",
        "",
    ]
    for raw_case in require_list(assessment.get("non_interface_cases"), "non_interface_cases"):
        case = require_object(raw_case, "non_interface_case")
        lines.extend(
            [
                f"## {case.get('case_key', '')} - {case.get('title', '')}",
                f"- 分类：{case.get('classification', '')}",
                f"- 原因：{case.get('reason', '')}",
                f"- 推荐测试方式：{case.get('recommended_test_type', '')}",
                "- 相关接口：",
                *bullet_lines(case.get("related_interfaces")),
                "- 缺失证据：",
                *bullet_lines(case.get("missing_evidence")),
                *audit_lines(case.get("audit")),
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def render_flow_document(assessment: dict[str, object], snapshot: dict[str, object]) -> str:
    confirmation = require_object(snapshot.get("testcase_confirmation"), "snapshot.testcase_confirmation")
    lines = [
        "# 集成测试流程与执行指导",
        "",
        f"- 测试用例哈希：{confirmation.get('testcase_hash', '')}",
        f"- 代码审查批次：{confirmation.get('code_review_run_id', '')}",
        "",
    ]
    flows = require_list(assessment.get("core_flows"), "core_flows")
    if not flows:
        lines.extend(
            [
                "## 未形成可验证集成流程",
                "",
                f"- 原因：{assessment.get('core_flow_blocker_reason', '未提供充分证据。')}",
                "",
            ]
        )
    for raw_flow in flows:
        flow = require_object(raw_flow, "core_flow")
        lines.extend(
            [
                f"## {flow.get('name', '')}",
                f"- 流程键：{flow.get('flow_key', '')}",
                f"- 覆盖用例：{', '.join(str(item) for item in flow.get('case_keys', []))}",
                "- 步骤：",
                *bullet_lines(flow.get("steps")),
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    arguments = parse_arguments()
    assessment = read_json_object(Path(arguments.assessment))
    snapshot = read_json_object(Path(arguments.snapshot))
    output_dir = Path(arguments.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "interface_test_preparation.md").write_text(
        render_interface_document(assessment, snapshot), encoding="utf-8", newline="\n"
    )
    (output_dir / "non_interface_cases.md").write_text(
        render_non_interface_document(assessment, snapshot), encoding="utf-8", newline="\n"
    )
    (output_dir / "integration_test_flow.md").write_text(
        render_flow_document(assessment, snapshot), encoding="utf-8", newline="\n"
    )
    print("已渲染三份接口测试准备文档。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
