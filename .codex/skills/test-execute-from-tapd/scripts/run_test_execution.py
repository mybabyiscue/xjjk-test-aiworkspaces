"""Execute an approved, business-neutral HTTP and database assertion plan."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Callable, TypeAlias, cast

import pymysql
from pymysql.connections import Connection

JsonObject: TypeAlias = dict[str, object]
HttpResult: TypeAlias = tuple[int, str]
PlanBuilder: TypeAlias = Callable[[JsonObject, str], tuple[JsonObject, JsonObject]]
SUPPORTED_OPERATORS: frozenset[str] = frozenset({"equals", "not_equals", "exists", "contains", "in"})
SUPPORTED_VARIANTS: frozenset[str] = frozenset({"positive", "negative"})
RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
SENSITIVE_TOKENS: frozenset[str] = frozenset({"authorization", "cookie", "password", "secret", "token", "api-key", "apikey"})
SQL_FORBIDDEN_PATTERN: re.Pattern[str] = re.compile(
    r"\b(insert|update|delete|replace|alter|drop|create|truncate|grant|revoke|call|load|outfile|dumpfile|lock|unlock)\b",
    re.IGNORECASE,
)
PATH_TOKEN_PATTERN: re.Pattern[str] = re.compile(r"(?:^|\.)([A-Za-z_][A-Za-z0-9_-]*)|\[(\d+)\]")


def parse_arguments() -> argparse.Namespace:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="执行已审批的 TAPD 接口测试计划。")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--assessment", required=True)
    parser.add_argument("--confirmation", required=True)
    parser.add_argument("--environment-config", required=True)
    parser.add_argument("--environment-name", required=True)
    parser.add_argument("--expected-business-code", required=True)
    parser.add_argument("--connections")
    parser.add_argument("--read-connection-name")
    parser.add_argument("--write-connection-name")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--manifest", required=True)
    return parser.parse_args()


def resolve_workspace_path(workspace: Path, relative_path: str, field_name: str) -> Path:
    candidate: Path = Path(relative_path)
    if candidate.is_absolute():
        raise ValueError(f"{field_name} 必须是工作区相对路径：{relative_path}")
    resolved: Path = (workspace / candidate).resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError as error:
        raise ValueError(f"{field_name} 超出工作区边界：{relative_path}") from error
    return resolved


def read_json_object(path: Path, field_name: str) -> JsonObject:
    try:
        raw_content: str = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise FileNotFoundError(f"无法读取 {field_name}：{path}；请生成或修复该前置文件。") from error
    try:
        value: object = json.loads(raw_content)
    except json.JSONDecodeError as error:
        raise ValueError(f"{field_name} 不是合法 JSON：{path}；请修复第 {error.lineno} 行。") from error
    return require_object(value, field_name)


def file_sha256(path: Path) -> str:
    try:
        content: bytes = path.read_bytes()
    except OSError as error:
        raise FileNotFoundError(f"无法读取哈希输入文件：{path}；请重新生成准备阶段产物。") from error
    return hashlib.sha256(content).hexdigest()


def load_plan_builder() -> PlanBuilder:
    preparation_scripts: Path = Path(__file__).resolve().parents[2] / "tapd-prepare-test-from" / "scripts"
    builder_path: Path = preparation_scripts / "build_api_execution_plan.py"
    if not builder_path.is_file():
        raise FileNotFoundError(f"缺少准备阶段规范计划构建器：{builder_path}；请同步完整技能包。")
    spec = importlib.util.spec_from_file_location("canonical_api_execution_plan_builder", builder_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载准备阶段规范计划构建器：{builder_path}；请检查技能文件完整性。")
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(preparation_scripts))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(preparation_scripts))
    raw_builder: object = getattr(module, "build_execution_plan", None)
    if not callable(raw_builder):
        raise RuntimeError(f"准备阶段规范计划构建器缺少 build_execution_plan：{builder_path}")
    return cast(PlanBuilder, raw_builder)


def require_object(value: object, field_name: str) -> JsonObject:
    if not isinstance(value, dict):
        raise TypeError(f"{field_name} 必须是对象。")
    return value


def require_list(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{field_name} 必须是数组。")
    return value


def require_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{field_name} 必须是非空字符串。")
    return value.strip()


def require_integer(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{field_name} 必须是整数。")
    return value


def validate_relative_api_path(value: object, field_name: str) -> str:
    path: str = require_string(value, field_name)
    parsed: urllib.parse.SplitResult = urllib.parse.urlsplit(path)
    if not path.startswith("/") or parsed.scheme or parsed.netloc:
        raise ValueError(f"{field_name} 必须是以 / 开头的相对网关路径。")
    return path


def validate_headers(value: object, field_name: str) -> dict[str, str]:
    raw_headers: JsonObject = require_object(value, field_name)
    headers: dict[str, str] = {}
    for raw_name, raw_value in raw_headers.items():
        name: str = require_string(raw_name, f"{field_name}.name")
        header_value: str = require_string(raw_value, f"{field_name}.{name}")
        if any(token in name.lower() for token in SENSITIVE_TOKENS):
            raise ValueError(f"{field_name}.{name} 不得保存敏感凭证；请使用 authorization_header。")
        headers[name] = header_value
    return headers


def validate_assertion(value: object, field_name: str) -> JsonObject:
    assertion: JsonObject = require_object(value, field_name)
    require_string(assertion.get("path"), f"{field_name}.path")
    operator: str = require_string(assertion.get("operator"), f"{field_name}.operator")
    if operator not in SUPPORTED_OPERATORS:
        raise ValueError(f"{field_name}.operator 不支持：{operator}")
    if operator != "exists" and "value" not in assertion:
        raise ValueError(f"{field_name}.value 不得缺失。")
    return assertion


def validate_read_only_sql(sql: str, field_name: str) -> str:
    normalized: str = sql.strip()
    if not re.match(r"^select\b", normalized, re.IGNORECASE):
        raise ValueError(f"{field_name} 只能包含 SELECT。")
    if ";" in normalized or "--" in normalized or "/*" in normalized or SQL_FORBIDDEN_PATTERN.search(normalized):
        raise ValueError(f"{field_name} 包含禁止的多语句、注释或写操作。")
    return normalized


def validate_database_assertion(value: object, field_name: str) -> JsonObject:
    assertion: JsonObject = require_object(value, field_name)
    require_string(assertion.get("database"), f"{field_name}.database")
    require_string(assertion.get("table"), f"{field_name}.table")
    sql: str = require_string(assertion.get("sql"), f"{field_name}.sql")
    validate_read_only_sql(sql, f"{field_name}.sql")
    require_list(assertion.get("parameters"), f"{field_name}.parameters")
    assertions: list[object] = require_list(assertion.get("assertions"), f"{field_name}.assertions")
    if not assertions:
        raise ValueError(f"{field_name}.assertions 不得为空。")
    for index, raw_assertion in enumerate(assertions, start=1):
        validate_assertion(raw_assertion, f"{field_name}.assertions[{index}]")
    return assertion


def qualified_table_pattern(database: str, table: str) -> str:
    database_pattern: str = rf"(?:`?{re.escape(database)}`?\s*\.\s*)?"
    return database_pattern + rf"`?{re.escape(table)}`?"


def validate_sql_insert(action: JsonObject, field_name: str) -> None:
    database: str = require_string(action.get("database"), f"{field_name}.database")
    table: str = require_string(action.get("table"), f"{field_name}.table")
    sql: str = require_string(action.get("sql"), f"{field_name}.sql").strip()
    parameters: list[object] = require_list(action.get("parameters"), f"{field_name}.parameters")
    identifier: str = r"`?[A-Za-z_][A-Za-z0-9_]*`?"
    pattern: re.Pattern[str] = re.compile(
        rf"^insert\s+into\s+{qualified_table_pattern(database, table)}\s*"
        rf"\(\s*{identifier}(?:\s*,\s*{identifier})*\s*\)\s*"
        rf"values\s*\(\s*%s(?:\s*,\s*%s)*\s*\)$",
        re.IGNORECASE,
    )
    if ";" in sql or "--" in sql or "/*" in sql or not pattern.fullmatch(sql):
        raise ValueError(f"{field_name}.sql 只允许单条、显式列、参数化 INSERT。")
    if sql.lower().count("%s") != len(parameters):
        raise ValueError(f"{field_name}.parameters 数量必须与 INSERT 占位符一致。")


def validate_sql_delete(action: JsonObject, field_name: str) -> None:
    database: str = require_string(action.get("database"), f"{field_name}.database")
    table: str = require_string(action.get("table"), f"{field_name}.table")
    sql: str = require_string(action.get("sql"), f"{field_name}.sql").strip()
    parameters: list[object] = require_list(action.get("parameters"), f"{field_name}.parameters")
    identifier: str = r"`?[A-Za-z_][A-Za-z0-9_]*`?"
    pattern: re.Pattern[str] = re.compile(
        rf"^delete\s+from\s+{qualified_table_pattern(database, table)}\s+"
        rf"where\s+{identifier}\s*(?:=|like)\s*%s$",
        re.IGNORECASE,
    )
    if ";" in sql or "--" in sql or "/*" in sql or not pattern.fullmatch(sql):
        raise ValueError(f"{field_name}.sql 只允许按单一测试标识精确或前缀匹配的参数化 DELETE。")
    if len(parameters) != 1 or not isinstance(parameters[0], str) or not parameters[0].startswith("TEST_"):
        raise ValueError(f"{field_name}.parameters 必须只包含一个以 TEST_ 开头的清理标识。")


def validate_data_action(value: object, field_name: str, allowed_types: set[str]) -> JsonObject:
    action: JsonObject = require_object(value, field_name)
    require_string(action.get("id"), f"{field_name}.id")
    require_string(action.get("entry_id"), f"{field_name}.entry_id")
    action_type: str = require_string(action.get("type"), f"{field_name}.type")
    if action_type not in allowed_types:
        raise ValueError(f"{field_name}.type 不允许：{action_type}；禁止 Mock、Fake、Stub 和 Mock seed。")
    require_string(action.get("evidence_reference"), f"{field_name}.evidence_reference")
    manifest: JsonObject = require_object(action.get("manifest"), f"{field_name}.manifest")
    require_string(manifest.get("database"), f"{field_name}.manifest.database")
    require_string(manifest.get("table"), f"{field_name}.manifest.table")
    require_object(manifest.get("record"), f"{field_name}.manifest.record")
    if action_type == "http":
        require_string(action.get("method"), f"{field_name}.method")
        validate_relative_api_path(action.get("path"), f"{field_name}.path")
        validate_headers(action.get("headers"), f"{field_name}.headers")
        if not isinstance(action.get("authorization_header"), str):
            raise TypeError(f"{field_name}.authorization_header 必须是字符串。")
        require_object(action.get("query"), f"{field_name}.query")
        if "body" not in action:
            raise ValueError(f"{field_name}.body 不得缺失。")
        expected: JsonObject = require_object(action.get("expected"), f"{field_name}.expected")
        require_integer(expected.get("http_status"), f"{field_name}.expected.http_status")
        assertions: list[object] = require_list(expected.get("response_assertions"), f"{field_name}.expected.response_assertions")
        if not assertions:
            raise ValueError(f"{field_name}.expected.response_assertions 不得为空。")
        for index, assertion in enumerate(assertions, start=1):
            validate_assertion(assertion, f"{field_name}.expected.response_assertions[{index}]")
    else:
        expected_rows: int = require_integer(action.get("expected_affected_rows"), f"{field_name}.expected_affected_rows")
        if expected_rows < 1:
            raise ValueError(f"{field_name}.expected_affected_rows 必须大于 0。")
        if action_type == "sql_insert":
            validate_sql_insert(action, field_name)
        else:
            validate_sql_delete(action, field_name)
    return action


def validate_expected(value: object, field_name: str) -> JsonObject:
    expected: JsonObject = require_object(value, field_name)
    require_integer(expected.get("http_status"), f"{field_name}.http_status")
    response_assertions: list[object] = require_list(expected.get("response_assertions"), f"{field_name}.response_assertions")
    if not response_assertions:
        raise ValueError(f"{field_name}.response_assertions 不得为空。")
    for index, raw_assertion in enumerate(response_assertions, start=1):
        validate_assertion(raw_assertion, f"{field_name}.response_assertions[{index}]")
    database_assertions: list[object] = require_list(expected.get("database_assertions"), f"{field_name}.database_assertions")
    for index, raw_assertion in enumerate(database_assertions, start=1):
        validate_database_assertion(raw_assertion, f"{field_name}.database_assertions[{index}]")
    return expected


def validate_request(value: object, field_name: str) -> JsonObject:
    request: JsonObject = require_object(value, field_name)
    require_string(request.get("id"), f"{field_name}.id")
    case_ids: list[object] = require_list(request.get("case_ids"), f"{field_name}.case_ids")
    if not case_ids:
        raise ValueError(f"{field_name}.case_ids 不得为空。")
    for index, case_id in enumerate(case_ids, start=1):
        require_string(case_id, f"{field_name}.case_ids[{index}]")
    variant_type: str = require_string(request.get("variant_type"), f"{field_name}.variant_type")
    if variant_type not in SUPPORTED_VARIANTS:
        raise ValueError(f"{field_name}.variant_type 不支持：{variant_type}")
    require_string(request.get("method"), f"{field_name}.method")
    validate_relative_api_path(request.get("path"), f"{field_name}.path")
    validate_headers(request.get("headers"), f"{field_name}.headers")
    authorization_header: object = request.get("authorization_header")
    if not isinstance(authorization_header, str):
        raise TypeError(f"{field_name}.authorization_header 必须是字符串。")
    require_object(request.get("query"), f"{field_name}.query")
    if "body" not in request:
        raise ValueError(f"{field_name}.body 不得缺失。")
    validate_expected(request.get("expected"), f"{field_name}.expected")
    return request


def validate_dependency(value: object, field_name: str, prior_step_ids: set[str]) -> JsonObject:
    dependency: JsonObject = require_object(value, field_name)
    source_step: str = require_string(dependency.get("source_step"), f"{field_name}.source_step")
    if source_step not in prior_step_ids:
        raise ValueError(f"{field_name}.source_step 必须引用更早的步骤：{source_step}")
    require_string(dependency.get("source_path"), f"{field_name}.source_path")
    target: str = require_string(dependency.get("target"), f"{field_name}.target")
    if target not in {"body", "query"}:
        raise ValueError(f"{field_name}.target 只能是 body 或 query。")
    require_string(dependency.get("target_path"), f"{field_name}.target_path")
    return dependency


def first_plan_difference(expected: object, actual: object, field_name: str) -> tuple[str, object, object] | None:
    if type(expected) is not type(actual):
        return field_name, expected, actual
    if isinstance(expected, dict) and isinstance(actual, dict):
        expected_keys: set[str] = {str(key) for key in expected}
        actual_keys: set[str] = {str(key) for key in actual}
        if expected_keys != actual_keys:
            return f"{field_name}.keys", sorted(expected_keys), sorted(actual_keys)
        for key in sorted(expected_keys):
            difference = first_plan_difference(expected[key], actual[key], f"{field_name}.{key}")
            if difference is not None:
                return difference
        return None
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return f"{field_name}.length", len(expected), len(actual)
        for index, (expected_item, actual_item) in enumerate(zip(expected, actual, strict=True)):
            difference = first_plan_difference(expected_item, actual_item, f"{field_name}[{index}]")
            if difference is not None:
                return difference
        return None
    if expected != actual:
        return field_name, expected, actual
    return None


def validate_plan(
    plan: JsonObject,
    confirmation: JsonObject,
    assessment: JsonObject,
    assessment_sha256: str,
    canonical_plan: JsonObject,
) -> None:
    if require_integer(plan.get("version"), "plan.version") != 2:
        raise ValueError("plan.version 必须等于 2；请由准备阶段重新生成执行计划。")
    if plan.get("ready") is not True:
        raise PermissionError("plan.ready 必须严格等于 true；请返回准备阶段解决全部阻断项。")
    if confirmation.get("approved") is not True:
        raise PermissionError("testcase_confirmation.json.approved 必须为 true；请先完成人工审批。")
    plan_source: JsonObject = require_object(plan.get("source"), "plan.source")
    assessment_source: JsonObject = require_object(assessment.get("source"), "assessment.source")
    recorded_assessment_sha256: str = require_string(
        plan_source.get("preparation_assessment_sha256"),
        "plan.source.preparation_assessment_sha256",
    )
    if recorded_assessment_sha256 != assessment_sha256:
        raise PermissionError(
            "preparation_assessment.json 哈希与执行计划记录不一致；准备产物可能已变更，请重新校验并生成计划。"
        )
    testcase_hash: str = require_string(plan_source.get("testcase_hash"), "plan.source.testcase_hash")
    approved_hash: str = require_string(confirmation.get("testcase_hash"), "confirmation.testcase_hash")
    assessment_testcase_hash: str = require_string(
        assessment_source.get("testcase_hash"),
        "assessment.source.testcase_hash",
    )
    if len({testcase_hash, approved_hash, assessment_testcase_hash}) != 1:
        raise PermissionError("执行计划、准备评估和审批文件的 testcase_hash 不一致；请重新生成并审批计划。")
    code_review_run_id: str = require_string(plan_source.get("code_review_run_id"), "plan.source.code_review_run_id")
    approved_review_run_id: str = require_string(
        confirmation.get("code_review_run_id"),
        "confirmation.code_review_run_id",
    )
    assessment_review_run_id: str = require_string(
        assessment_source.get("code_review_run_id"),
        "assessment.source.code_review_run_id",
    )
    if len({code_review_run_id, approved_review_run_id, assessment_review_run_id}) != 1:
        raise PermissionError("执行计划、准备评估和审批文件的 code_review_run_id 不一致；请重新完成代码复审绑定。")
    setup_action_ids: set[str] = set()
    setup_entry_ids: set[str] = set()
    for index, raw_action in enumerate(require_list(plan.get("data_setup"), "plan.data_setup"), start=1):
        action: JsonObject = validate_data_action(raw_action, f"plan.data_setup[{index}]", {"http", "sql_insert"})
        action_id: str = require_string(action.get("id"), f"plan.data_setup[{index}].id")
        if action_id in setup_action_ids:
            raise ValueError(f"数据准备动作 ID 重复：{action_id}")
        setup_action_ids.add(action_id)
        setup_entry_ids.add(require_string(action.get("entry_id"), f"plan.data_setup[{index}].entry_id"))
    cleanup_action_ids: set[str] = set()
    cleanup_entry_ids: set[str] = set()
    for index, raw_action in enumerate(require_list(plan.get("data_cleanup"), "plan.data_cleanup"), start=1):
        action = validate_data_action(raw_action, f"plan.data_cleanup[{index}]", {"http", "sql_delete"})
        action_id = require_string(action.get("id"), f"plan.data_cleanup[{index}].id")
        if action_id in cleanup_action_ids:
            raise ValueError(f"数据清理动作 ID 重复：{action_id}")
        cleanup_action_ids.add(action_id)
        cleanup_entry_ids.add(require_string(action.get("entry_id"), f"plan.data_cleanup[{index}].entry_id"))
    if setup_entry_ids != cleanup_entry_ids:
        raise ValueError("plan.data_setup 与 plan.data_cleanup 必须按 entry_id 一一对应。")
    seen_ids: set[str] = set()
    for index, raw_request in enumerate(require_list(plan.get("requests"), "plan.requests"), start=1):
        request: JsonObject = validate_request(raw_request, f"plan.requests[{index}]")
        request_id: str = require_string(request.get("id"), f"plan.requests[{index}].id")
        if request_id in seen_ids:
            raise ValueError(f"请求 ID 重复：{request_id}")
        seen_ids.add(request_id)
    for flow_index, raw_flow in enumerate(require_list(plan.get("flows"), "plan.flows"), start=1):
        flow: JsonObject = require_object(raw_flow, f"plan.flows[{flow_index}]")
        require_string(flow.get("id"), f"plan.flows[{flow_index}].id")
        require_string(flow.get("name"), f"plan.flows[{flow_index}].name")
        steps: list[object] = require_list(flow.get("steps"), f"plan.flows[{flow_index}].steps")
        if len(steps) < 2:
            raise ValueError(f"plan.flows[{flow_index}].steps 至少包含两个步骤。")
        prior_step_ids: set[str] = set()
        for step_index, raw_step in enumerate(steps, start=1):
            step_name: str = f"plan.flows[{flow_index}].steps[{step_index}]"
            step: JsonObject = validate_request(raw_step, step_name)
            step_id: str = require_string(step.get("id"), f"{step_name}.id")
            if step_id in prior_step_ids:
                raise ValueError(f"流程步骤 ID 重复：{step_id}")
            for dependency_index, raw_dependency in enumerate(require_list(step.get("dependencies"), f"{step_name}.dependencies"), start=1):
                validate_dependency(raw_dependency, f"{step_name}.dependencies[{dependency_index}]", prior_step_ids)
            prior_step_ids.add(step_id)
    difference: tuple[str, object, object] | None = first_plan_difference(canonical_plan, plan, "plan")
    if difference is not None:
        field_name, expected, actual = difference
        raise PermissionError(
            f"执行计划与 preparation_assessment.json 规范重建结果不一致；字段={field_name}；"
            f"期望={json.dumps(redact(expected), ensure_ascii=False)}；"
            f"实际={json.dumps(redact(actual), ensure_ascii=False)}；请删除改写并由准备阶段重新生成计划。"
        )


def select_environment(config: JsonObject, environment_name: str) -> JsonObject:
    matches: list[JsonObject] = []
    for index, raw_environment in enumerate(require_list(config.get("environments"), "environment_config.environments"), start=1):
        environment: JsonObject = require_object(raw_environment, f"environment_config.environments[{index}]")
        if require_string(environment.get("name"), f"environment_config.environments[{index}].name") == environment_name:
            matches.append(environment)
    if not matches:
        available: list[str] = [
            str(item.get("name")) for item in require_list(config.get("environments"), "environment_config.environments") if isinstance(item, dict)
        ]
        raise LookupError(f"未找到用户确认的平台 {environment_name}；可选平台：{available}")
    if len(matches) != 1:
        raise LookupError(f"平台名称 {environment_name} 存在 {len(matches)} 个候选；请修复配置后重新确认。")
    require_string(matches[0].get("api_domain"), f"environment[{environment_name}].api_domain")
    return matches[0]


def credentials_for_reference(credentials: JsonObject, reference: str) -> JsonObject:
    current: object = credentials
    for segment in reference.split("."):
        if not isinstance(current, dict):
            raise LookupError("credentials_ref 指向的凭证对象不存在。")
        current = current.get(segment)
    if not isinstance(current, dict):
        raise LookupError("credentials_ref 指向的凭证对象不存在。")
    return current


def load_local_credentials(workspace: Path) -> JsonObject | None:
    credentials_path: Path = workspace / "config" / "credentials.local.json"
    if not credentials_path.is_file():
        return None
    return read_json_object(credentials_path, "credentials")


def resolve_environment_authentication(environment: JsonObject, credentials_store: JsonObject | None) -> JsonObject:
    resolved: JsonObject = copy.deepcopy(environment)
    authorization: object = resolved.get("authorization")
    if isinstance(authorization, str) and authorization.strip():
        return resolved
    credentials_ref: object = resolved.get("credentials_ref")
    if not isinstance(credentials_ref, str) or not credentials_ref.strip():
        return resolved
    if credentials_store is None:
        raise TypeError(f"environment[{resolved.get('name')}].authorization 必须是非空字符串。")
    local_credentials: JsonObject = credentials_for_reference(credentials_store, credentials_ref)
    resolved_authorization: object = local_credentials.get("authorization")
    if not isinstance(resolved_authorization, str) or not resolved_authorization.strip():
        raise TypeError(f"environment[{resolved.get('name')}].authorization 必须是非空字符串。")
    resolved["authorization"] = resolved_authorization
    return resolved


def select_connection(config: JsonObject, connection_name: str, field_name: str) -> JsonObject:
    matches: list[JsonObject] = []
    for index, raw_connection in enumerate(require_list(config.get("connections"), "connections.connections"), start=1):
        connection: JsonObject = require_object(raw_connection, f"connections.connections[{index}]")
        if connection.get("enabled") is True and require_string(connection.get("name"), f"connections.connections[{index}].name") == connection_name:
            matches.append(connection)
    if len(matches) != 1:
        raise LookupError(f"连接 {connection_name} 必须唯一存在且 enabled=true；当前匹配数={len(matches)}。")
    require_string(matches[0].get("host"), f"{field_name}.host")
    require_integer(matches[0].get("port"), f"{field_name}.port")
    require_string(matches[0].get("username"), f"{field_name}.username")
    require_string(matches[0].get("password"), f"{field_name}.password")
    return matches[0]


def validate_read_connection(connection: JsonObject) -> JsonObject:
    if connection.get("access_mode") != "read-only":
        raise PermissionError("数据库断言连接必须显式声明 access_mode=read-only。")
    return connection


def validate_write_connection(connection: JsonObject, environment_name: str, actions: list[object]) -> JsonObject:
    if connection.get("access_mode") != "controlled-write":
        raise PermissionError("受控写入连接必须显式声明 access_mode=controlled-write。")
    if connection.get("environment_name") != environment_name:
        raise PermissionError("受控写入连接的 environment_name 与用户确认的 API 环境不一致。")
    allowed_databases: set[str] = {
        require_string(item, "write_connection.allowed_databases[]")
        for item in require_list(connection.get("allowed_databases"), "write_connection.allowed_databases")
    }
    allowed_tables: set[str] = {
        require_string(item, "write_connection.allowed_tables[]")
        for item in require_list(connection.get("allowed_tables"), "write_connection.allowed_tables")
    }
    for raw_action in actions:
        action: JsonObject = require_object(raw_action, "write_action")
        if action.get("type") not in {"sql_insert", "sql_delete"}:
            continue
        database: str = require_string(action.get("database"), "write_action.database")
        table: str = require_string(action.get("table"), "write_action.table")
        if database not in allowed_databases or table not in allowed_tables:
            raise PermissionError(f"受控写入动作超出连接白名单：database={database}，table={table}。")
    return connection


def validate_mutation_environment(environment: JsonObject, environment_name: str) -> None:
    if environment.get("environment_type") != "test":
        raise PermissionError(f"环境 {environment_name} 未显式声明 environment_type=test；禁止数据变更。")
    if environment.get("allow_test_data_mutation") is not True:
        raise PermissionError(
            f"环境 {environment_name} 未显式允许测试数据变更；请确认测试环境并设置 allow_test_data_mutation=true。"
        )


def path_tokens(path: str) -> list[str | int]:
    if path == "$":
        return []
    if not path.startswith("$"):
        raise ValueError(f"JSON 路径必须以 $ 开头：{path}")
    suffix: str = path[1:]
    tokens: list[str | int] = []
    position: int = 0
    for match in PATH_TOKEN_PATTERN.finditer(suffix):
        if match.start() != position:
            raise ValueError(f"不支持的 JSON 路径：{path}")
        tokens.append(match.group(1) if match.group(1) is not None else int(match.group(2)))
        position = match.end()
    if position != len(suffix):
        raise ValueError(f"不支持的 JSON 路径：{path}")
    return tokens


def extract_path(value: object, path: str) -> tuple[bool, object]:
    current: object = value
    for token in path_tokens(path):
        if isinstance(token, str) and isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(token, int) and isinstance(current, list) and token < len(current):
            current = current[token]
        else:
            return False, None
    return True, current


def replace_path(value: object, path: str, replacement: object) -> object:
    cloned: object = copy.deepcopy(value)
    tokens: list[str | int] = path_tokens(path)
    if not tokens:
        return copy.deepcopy(replacement)
    current: object = cloned
    for token in tokens[:-1]:
        if isinstance(token, str) and isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(token, int) and isinstance(current, list) and token < len(current):
            current = current[token]
        else:
            raise ValueError(f"依赖目标路径不存在：{path}")
    final_token: str | int = tokens[-1]
    if isinstance(final_token, str) and isinstance(current, dict) and final_token in current:
        current[final_token] = copy.deepcopy(replacement)
    elif isinstance(final_token, int) and isinstance(current, list) and final_token < len(current):
        current[final_token] = copy.deepcopy(replacement)
    else:
        raise ValueError(f"依赖目标路径不存在：{path}")
    return cloned


def assertion_passed(actual_root: object, assertion: JsonObject) -> tuple[bool, str]:
    path: str = require_string(assertion.get("path"), "assertion.path")
    operator: str = require_string(assertion.get("operator"), "assertion.operator")
    exists, actual = extract_path(actual_root, path)
    expected: object = assertion.get("value")
    if operator == "exists":
        passed: bool = exists
    elif operator == "equals":
        passed = exists and actual == expected
    elif operator == "not_equals":
        passed = exists and actual != expected
    elif operator == "contains":
        if isinstance(actual, str):
            passed = exists and isinstance(expected, str) and expected in actual
        elif isinstance(actual, list):
            passed = exists and expected in actual
        elif isinstance(actual, dict):
            passed = exists and isinstance(expected, str) and expected in actual
        else:
            passed = False
    elif operator == "in":
        if isinstance(expected, str):
            passed = exists and isinstance(actual, str) and actual in expected
        elif isinstance(expected, list):
            passed = exists and actual in expected
        elif isinstance(expected, dict):
            passed = exists and isinstance(actual, str) and actual in expected
        else:
            passed = False
    else:
        raise ValueError(f"不支持的断言操作符：{operator}")
    detail: str = json.dumps({"path": path, "operator": operator, "expected": expected, "actual": actual, "exists": exists}, ensure_ascii=False)
    return passed, detail


def structured_warning(operation: str, attempt: int, reason: str) -> None:
    print(json.dumps({"level": "WARNING", "operation": operation, "attempt": attempt, "reason": reason}, ensure_ascii=False), file=sys.stderr)


def request_once(url: str, method: str, headers: dict[str, str], body: object) -> HttpResult:
    data: bytes | None = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request: urllib.request.Request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")


def send_request(url: str, method: str, headers: dict[str, str], body: object) -> HttpResult:
    last_error: urllib.error.URLError | None = None
    for attempt in range(1, 4):
        try:
            status, response_body = request_once(url, method, headers, body)
        except urllib.error.URLError as error:
            last_error = error
            if attempt < 3:
                structured_warning("http_request", attempt, str(error.reason))
                time.sleep(float(attempt))
                continue
            raise ConnectionError(
                f"HTTP 请求失败；method={method}；url={url}；status=不可用；response_body=不可用；"
                f"原因={error.reason}；修复建议=检查网络、域名和测试环境服务状态。"
            ) from error
        if status in RETRYABLE_HTTP_STATUSES and attempt < 3:
            structured_warning("http_request", attempt, f"HTTP {status}: {response_body}")
            time.sleep(float(attempt))
            continue
        return status, response_body
    raise ConnectionError(f"HTTP 请求重试耗尽：{last_error}")


def parse_response_body(response_body: str) -> object:
    try:
        return json.loads(response_body)
    except json.JSONDecodeError:
        return response_body


def is_token_expired(status: int, response: object, token_error_codes: set[str]) -> bool:
    if status in {401, 403}:
        return True
    if not isinstance(response, dict):
        return False
    raw_code: object = response.get("code", response.get("Code"))
    return raw_code is not None and str(raw_code) in token_error_codes


def business_code_assertion(response: object, expected_code: str) -> tuple[bool, str]:
    if not expected_code:
        raise ValueError("--expected-business-code 不得为空；必须使用用户本次明确确认的 code 值。")
    exists: bool = isinstance(response, dict) and "code" in response
    actual: object = response.get("code") if isinstance(response, dict) else None
    passed: bool = exists and isinstance(actual, (str, int)) and not isinstance(actual, bool) and str(actual) == expected_code
    detail: str = json.dumps(
        {"path": "$.code", "operator": "equals", "expected": expected_code, "actual": actual, "exists": exists},
        ensure_ascii=False,
    )
    return passed, detail


def open_database_connection(database_config: JsonObject) -> Connection:
    required_fields: tuple[str, ...] = ("host", "username", "password")
    values: dict[str, str] = {field: require_string(database_config.get(field), f"database.{field}") for field in required_fields}
    port: int = require_integer(database_config.get("port"), "database.port")
    raw_database: object = database_config.get("database")
    database: str | None = require_string(raw_database, "database.database") if raw_database is not None else None
    try:
        return pymysql.connect(
            host=values["host"],
            port=port,
            user=values["username"],
            password=values["password"],
            database=database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )
    except pymysql.MySQLError as error:
        raise ConnectionError(
            f"数据库连接失败；host={values['host']}；port={port}；database={database or '未指定'}；"
            f"原因={error}；修复建议=确认用户选择的连接配置、环境和网络权限。"
        ) from error


def execute_database_assertion(connection: Connection, assertion: JsonObject) -> tuple[list[JsonObject], list[tuple[bool, str]]]:
    sql: str = validate_read_only_sql(require_string(assertion.get("sql"), "database_assertion.sql"), "database_assertion.sql")
    parameters: list[object] = require_list(assertion.get("parameters"), "database_assertion.parameters")
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, tuple(parameters))
            raw_rows: list[JsonObject] = list(cursor.fetchall())
    except pymysql.MySQLError as error:
        raise RuntimeError(
            f"数据库断言查询失败；sql={sql}；parameters={parameters}；status=不适用；response_body=不适用；"
            f"原因={error}；修复建议=核对准备阶段登记的只读 SQL、参数和表结构。"
        ) from error
    results: list[tuple[bool, str]] = [
        assertion_passed(raw_rows, validate_assertion(item, "database_assertion.assertion"))
        for item in require_list(assertion.get("assertions"), "database_assertion.assertions")
    ]
    return raw_rows, results


def data_action_manifest_row(action: JsonObject, lifecycle: str, status: str) -> tuple[str, str, JsonObject]:
    manifest: JsonObject = require_object(action.get("manifest"), "data_action.manifest")
    database: str = require_string(manifest.get("database"), "data_action.manifest.database")
    table: str = require_string(manifest.get("table"), "data_action.manifest.table")
    record: JsonObject = copy.deepcopy(require_object(manifest.get("record"), "data_action.manifest.record"))
    record["_lifecycle"] = lifecycle
    record["_action_id"] = action.get("id")
    record["_status"] = status
    return database, table, record


def execute_http_data_action(
    action: JsonObject,
    environment: JsonObject,
    token_error_codes: set[str],
    expected_business_code: str,
) -> JsonObject:
    url: str = request_url(environment, action)
    headers: dict[str, str] = build_headers(action, environment)
    method: str = require_string(action.get("method"), "data_action.method").upper()
    status, response_body = send_request(url, method, headers, action.get("body"))
    parsed_response: object = parse_response_body(response_body)
    if is_token_expired(status, parsed_response, token_error_codes):
        print(f"[TOKEN_EXPIRED_ERROR] {environment.get('name')}", file=sys.stderr)
        raise SystemExit(10)
    expected: JsonObject = require_object(action.get("expected"), "data_action.expected")
    expected_status: int = require_integer(expected.get("http_status"), "data_action.expected.http_status")
    assertions: list[tuple[bool, str]] = [(status == expected_status, f"HTTP status expected={expected_status}, actual={status}")]
    assertions.append(business_code_assertion(parsed_response, expected_business_code))
    for raw_assertion in require_list(expected.get("response_assertions"), "data_action.expected.response_assertions"):
        assertions.append(assertion_passed(parsed_response, validate_assertion(raw_assertion, "data_action.response_assertion")))
    passed: bool = all(result for result, _ in assertions)
    return {
        "id": action.get("id"),
        "entry_id": action.get("entry_id"),
        "type": "http",
        "status": "PASS" if passed else "FAIL",
        "http_status": status,
        "expected_business_code": expected_business_code,
        "actual_business_code": parsed_response.get("code") if isinstance(parsed_response, dict) else None,
        "response_body": redact(parsed_response),
        "assertions": [{"passed": result, "detail": detail} for result, detail in assertions],
    }


def execute_sql_data_action(action: JsonObject, connection_config: JsonObject) -> JsonObject:
    sql: str = require_string(action.get("sql"), "data_action.sql")
    parameters: list[object] = require_list(action.get("parameters"), "data_action.parameters")
    expected_rows: int = require_integer(action.get("expected_affected_rows"), "data_action.expected_affected_rows")
    connection: Connection = open_database_connection(connection_config)
    try:
        with connection.cursor() as cursor:
            affected_rows: int = cursor.execute(sql, tuple(parameters))
        acceptable_rows: set[int] = {expected_rows}
        if action.get("type") == "sql_delete":
            acceptable_rows.add(0)
        if affected_rows not in acceptable_rows:
            connection.rollback()
            return {
                "id": action.get("id"),
                "entry_id": action.get("entry_id"),
                "type": action.get("type"),
                "status": "FAIL",
                "affected_rows": affected_rows,
                "expected_affected_rows": expected_rows,
            }
        connection.commit()
        return {
            "id": action.get("id"),
            "entry_id": action.get("entry_id"),
            "type": action.get("type"),
            "status": "PASS",
            "affected_rows": affected_rows,
        }
    except pymysql.MySQLError as error:
        connection.rollback()
        raise RuntimeError(
            f"受控 SQL 动作失败；action_id={action.get('id')}；sql={sql}；parameters={parameters}；"
            f"原因={error}；修复建议=核对受控写连接、表白名单、SQL 证据和测试标识。"
        ) from error
    finally:
        connection.close()


def execute_data_action(
    action: JsonObject,
    environment: JsonObject,
    write_connection: JsonObject | None,
    token_error_codes: set[str],
    expected_business_code: str,
) -> JsonObject:
    if action.get("type") == "http":
        return execute_http_data_action(action, environment, token_error_codes, expected_business_code)
    if write_connection is None:
        raise PermissionError("执行计划包含受控 SQL 动作，但未提供并确认受控写入连接。")
    return execute_sql_data_action(action, write_connection)


def execute_setup_actions(
    raw_actions: list[object],
    environment: JsonObject,
    write_connection: JsonObject | None,
    token_error_codes: set[str],
    expected_business_code: str,
) -> tuple[list[JsonObject], list[tuple[str, str, JsonObject]], set[str]]:
    results: list[JsonObject] = []
    manifest_rows: list[tuple[str, str, JsonObject]] = []
    cleanup_entry_ids: set[str] = set()
    for index, raw_action in enumerate(raw_actions, start=1):
        action: JsonObject = require_object(raw_action, f"data_actions[{index}]")
        entry_id: str = require_string(action.get("entry_id"), f"data_actions[{index}].entry_id")
        try:
            result: JsonObject = execute_data_action(action, environment, write_connection, token_error_codes, expected_business_code)
        except SystemExit as error:
            result = {
                "id": action.get("id"),
                "entry_id": entry_id,
                "type": action.get("type"),
                "status": "EXECUTION_ERROR",
                "error_type": "SystemExit",
                "exit_code": error.code,
            }
        except (ConnectionError, LookupError, PermissionError, RuntimeError, TypeError, ValueError) as error:
            result = execution_error_result(action, error)
        results.append(result)
        status: str = str(result.get("status"))
        if status == "PASS" or action.get("type") == "http":
            cleanup_entry_ids.add(entry_id)
        failed_lifecycle: str = "residual" if action.get("type") == "http" else "rolled_back"
        manifest_rows.append(data_action_manifest_row(action, "created" if status == "PASS" else failed_lifecycle, status))
        if status != "PASS":
            break
    return results, manifest_rows, cleanup_entry_ids


def execute_cleanup_actions(
    raw_actions: list[object],
    cleanup_entry_ids: set[str],
    environment: JsonObject,
    write_connection: JsonObject | None,
    token_error_codes: set[str],
    expected_business_code: str,
) -> tuple[list[JsonObject], list[tuple[str, str, JsonObject]]]:
    results: list[JsonObject] = []
    manifest_rows: list[tuple[str, str, JsonObject]] = []
    for index, raw_action in enumerate(raw_actions, start=1):
        action: JsonObject = require_object(raw_action, f"data_cleanup[{index}]")
        entry_id: str = require_string(action.get("entry_id"), f"data_cleanup[{index}].entry_id")
        if entry_id not in cleanup_entry_ids:
            continue
        try:
            result: JsonObject = execute_data_action(action, environment, write_connection, token_error_codes, expected_business_code)
        except SystemExit as error:
            result = {
                "id": action.get("id"),
                "entry_id": entry_id,
                "type": action.get("type"),
                "status": "EXECUTION_ERROR",
                "error_type": "SystemExit",
                "exit_code": error.code,
            }
        except (ConnectionError, LookupError, PermissionError, RuntimeError, TypeError, ValueError) as error:
            result = execution_error_result(action, error)
        results.append(result)
        status: str = str(result.get("status"))
        manifest_rows.append(data_action_manifest_row(action, "cleaned" if status == "PASS" else "residual", status))
    return results, manifest_rows


def redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): "***" if any(token in str(key).lower() for token in SENSITIVE_TOKENS) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def build_headers(request: JsonObject, environment: JsonObject) -> dict[str, str]:
    headers: dict[str, str] = validate_headers(request.get("headers"), "request.headers")
    authorization_header: object = request.get("authorization_header")
    if isinstance(authorization_header, str) and authorization_header:
        token: str = require_string(environment.get("authorization"), f"environment[{environment.get('name')}].authorization")
        headers[authorization_header] = token
    return headers


def request_url(environment: JsonObject, request: JsonObject) -> str:
    api_domain: str = require_string(environment.get("api_domain"), "environment.api_domain").rstrip("/")
    path: str = validate_relative_api_path(request.get("path"), "request.path")
    query: JsonObject = require_object(request.get("query"), "request.query")
    query_string: str = urllib.parse.urlencode(query, doseq=True)
    return f"{api_domain}{path}" + (f"?{query_string}" if query_string else "")


def run_request(
    request: JsonObject,
    environment: JsonObject,
    database_config: JsonObject | None,
    token_error_codes: set[str],
    expected_business_code: str,
) -> tuple[JsonObject, list[tuple[str, str, JsonObject]]]:
    url: str = request_url(environment, request)
    headers: dict[str, str] = build_headers(request, environment)
    method: str = require_string(request.get("method"), "request.method").upper()
    body: object = request.get("body")
    started_at: str = datetime.now(timezone.utc).isoformat()
    status, response_body = send_request(url, method, headers, body)
    parsed_response: object = parse_response_body(response_body)
    if is_token_expired(status, parsed_response, token_error_codes):
        print(f"[TOKEN_EXPIRED_ERROR] {environment.get('name')}", file=sys.stderr)
        raise SystemExit(10)
    expected: JsonObject = require_object(request.get("expected"), "request.expected")
    assertion_results: list[tuple[bool, str]] = []
    expected_status: int = require_integer(expected.get("http_status"), "request.expected.http_status")
    assertion_results.append((status == expected_status, f"HTTP status expected={expected_status}, actual={status}"))
    assertion_results.append(business_code_assertion(parsed_response, expected_business_code))
    for raw_assertion in require_list(expected.get("response_assertions"), "request.expected.response_assertions"):
        assertion_results.append(assertion_passed(parsed_response, validate_assertion(raw_assertion, "response_assertion")))
    manifest_rows: list[tuple[str, str, JsonObject]] = []
    database_result_details: list[JsonObject] = []
    database_assertions: list[object] = require_list(expected.get("database_assertions"), "request.expected.database_assertions")
    if database_assertions:
        if database_config is None:
            raise RuntimeError("执行计划包含数据库断言，但 environments_config.json 缺少 database 配置。")
        connection: Connection = open_database_connection(database_config)
        try:
            for raw_database_assertion in database_assertions:
                database_assertion: JsonObject = validate_database_assertion(raw_database_assertion, "database_assertion")
                rows, database_results = execute_database_assertion(connection, database_assertion)
                assertion_results.extend(database_results)
                database_name: str = require_string(database_assertion.get("database"), "database_assertion.database")
                table_name: str = require_string(database_assertion.get("table"), "database_assertion.table")
                manifest_rows.extend((database_name, table_name, row) for row in rows)
                database_result_details.append({"database": database_name, "table": table_name, "row_count": len(rows), "assertions": [detail for _, detail in database_results]})
        finally:
            connection.rollback()
            connection.close()
    passed: bool = all(result for result, _ in assertion_results)
    result: JsonObject = {
        "id": request.get("id"),
        "case_ids": request.get("case_ids"),
        "variant_type": request.get("variant_type"),
        "started_at": started_at,
        "method": method,
        "url": url,
        "headers": redact(headers),
        "body": redact(body),
        "http_status": status,
        "expected_business_code": expected_business_code,
        "actual_business_code": parsed_response.get("code") if isinstance(parsed_response, dict) else None,
        "response_body": redact(parsed_response),
        "assertions": [{"passed": item_passed, "detail": detail} for item_passed, detail in assertion_results],
        "database_results": database_result_details,
        "status": "PASS" if passed else "FAIL",
    }
    return result, manifest_rows


def apply_dependencies(step: JsonObject, prior_results: dict[str, JsonObject]) -> JsonObject:
    prepared: JsonObject = copy.deepcopy(step)
    for raw_dependency in require_list(step.get("dependencies"), "flow_step.dependencies"):
        dependency: JsonObject = require_object(raw_dependency, "flow_step.dependency")
        source_step: str = require_string(dependency.get("source_step"), "dependency.source_step")
        source_result: JsonObject = prior_results[source_step]
        exists, source_value = extract_path(source_result.get("response_body"), require_string(dependency.get("source_path"), "dependency.source_path"))
        if not exists:
            raise LookupError(f"步骤 {source_step} 的响应缺少依赖路径 {dependency.get('source_path')}。")
        target: str = require_string(dependency.get("target"), "dependency.target")
        target_path: str = require_string(dependency.get("target_path"), "dependency.target_path")
        prepared[target] = replace_path(prepared.get(target), target_path, source_value)
    return prepared


def execution_error_result(request: JsonObject, error: Exception) -> JsonObject:
    return {
        "id": request.get("id"),
        "case_ids": request.get("case_ids"),
        "variant_type": request.get("variant_type"),
        "status": "EXECUTION_ERROR",
        "error_type": type(error).__name__,
        "error": str(error),
    }


def execute_standalone_requests(
    requests: list[object],
    environment: JsonObject,
    database_config: JsonObject | None,
    token_error_codes: set[str],
    expected_business_code: str,
) -> tuple[list[JsonObject], list[tuple[str, str, JsonObject]]]:
    results: list[JsonObject] = []
    manifest_rows: list[tuple[str, str, JsonObject]] = []
    for index, raw_request in enumerate(requests, start=1):
        request: JsonObject = validate_request(raw_request, f"requests[{index}]")
        try:
            result, request_rows = run_request(request, environment, database_config, token_error_codes, expected_business_code)
            results.append(result)
            manifest_rows.extend(request_rows)
        except SystemExit:
            raise
        except (ConnectionError, LookupError, RuntimeError, TypeError, ValueError) as error:
            results.append(execution_error_result(request, error))
    return results, manifest_rows


def execute_flows(
    flows: list[object],
    environment: JsonObject,
    database_config: JsonObject | None,
    token_error_codes: set[str],
    expected_business_code: str,
) -> tuple[list[JsonObject], list[tuple[str, str, JsonObject]]]:
    flow_results: list[JsonObject] = []
    manifest_rows: list[tuple[str, str, JsonObject]] = []
    for raw_flow in flows:
        flow: JsonObject = require_object(raw_flow, "flow")
        step_results: list[JsonObject] = []
        prior_results: dict[str, JsonObject] = {}
        interrupted: bool = False
        interruption_reason: str = ""
        for raw_step in require_list(flow.get("steps"), "flow.steps"):
            step: JsonObject = require_object(raw_step, "flow.step")
            step_id: str = require_string(step.get("id"), "flow.step.id")
            if interrupted:
                step_results.append({"id": step_id, "case_ids": step.get("case_ids"), "status": "NOT_EXECUTED", "reason": interruption_reason})
                continue
            try:
                prepared_step: JsonObject = apply_dependencies(step, prior_results)
                result, step_rows = run_request(prepared_step, environment, database_config, token_error_codes, expected_business_code)
                step_results.append(result)
                prior_results[step_id] = result
                manifest_rows.extend(step_rows)
                if result.get("status") != "PASS":
                    interrupted = True
                    interruption_reason = f"步骤 {step_id} 断言失败。"
            except SystemExit:
                raise
            except (ConnectionError, LookupError, RuntimeError, TypeError, ValueError) as error:
                error_result: JsonObject = execution_error_result(step, error)
                step_results.append(error_result)
                prior_results[step_id] = error_result
                interrupted = True
                interruption_reason = f"步骤 {step_id} 执行异常：{error}"
        flow_results.append(
            {
                "id": flow.get("id"),
                "name": flow.get("name"),
                "status": "INTERRUPTED" if interrupted else "PASS",
                "interruption_reason": interruption_reason,
                "steps": step_results,
            }
        )
    return flow_results, manifest_rows


def markdown_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def render_interface_report(results: list[JsonObject], environment_name: str) -> str:
    executed: list[JsonObject] = [item for item in results if item.get("status") in {"PASS", "FAIL"}]
    passed_count: int = sum(1 for item in executed if item.get("status") == "PASS")
    pass_rate: float = (passed_count / len(executed) * 100.0) if executed else 0.0
    lines: list[str] = [
        "# 单接口测试执行报告",
        "",
        f"- 生成时间：{datetime.now(timezone.utc).isoformat()}",
        f"- 测试平台：{environment_name}",
        f"- 计划用例数：{len(results)}",
        f"- 实际断言用例数：{len(executed)}",
        f"- 通过率：{pass_rate:.2f}%",
        "",
        "| 请求 ID | Case IDs | 类型 | 状态 | HTTP 状态 |",
        "|---|---|---|---|---:|",
    ]
    for result in results:
        lines.append(f"| {result.get('id')} | {', '.join(str(item) for item in result.get('case_ids', []))} | {result.get('variant_type')} | {result.get('status')} | {result.get('http_status', '')} |")
    for result in results:
        lines.extend(["", f"## {result.get('id')}", "", "```json", markdown_json(result), "```"])
    return "\n".join(lines).strip() + "\n"


def render_flow_report(results: list[JsonObject], environment_name: str) -> str:
    lines: list[str] = [
        "# 核心流程测试执行报告",
        "",
        f"- 生成时间：{datetime.now(timezone.utc).isoformat()}",
        f"- 测试平台：{environment_name}",
    ]
    if not results:
        lines.extend(["", "- 本次计划未定义核心流程。"])
    for result in results:
        lines.extend(["", f"## {result.get('name')}", "", f"- 流程 ID：{result.get('id')}", f"- 状态：{result.get('status')}", f"- 中断原因：{result.get('interruption_reason') or '无'}", "", "```json", markdown_json(result), "```"])
    return "\n".join(lines).strip() + "\n"


def append_manifest(path: Path, rows: list[tuple[str, str, JsonObject]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: str = path.read_text(encoding="utf-8-sig") if path.exists() else "# 测试数据台账\n"
    execution_lines: list[str] = ["", f"## 执行校验 {datetime.now(timezone.utc).isoformat()}"]
    execution_lines.extend(
        f"{database}:{table}:【{json.dumps(row, ensure_ascii=False, separators=(',', ':'))}】"
        for database, table, row in rows
    )
    path.write_text(existing.rstrip() + "\n" + "\n".join(execution_lines) + "\n", encoding="utf-8", newline="\n")


def write_reports(output_dir: Path, interface_report: str, flow_report: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "interface_test_execution_report.md").write_text(interface_report, encoding="utf-8", newline="\n")
    (output_dir / "core_flow_test_execution_report.md").write_text(flow_report, encoding="utf-8", newline="\n")


def lifecycle_failed(results: list[JsonObject]) -> bool:
    return any(result.get("status") != "PASS" for result in results)


def main() -> int:
    arguments: argparse.Namespace = parse_arguments()
    expected_business_code: str = require_string(arguments.expected_business_code, "--expected-business-code")
    workspace: Path = Path(arguments.workspace).resolve()
    if not workspace.is_dir():
        raise NotADirectoryError(f"工作区不存在：{workspace}")
    plan_path: Path = resolve_workspace_path(workspace, arguments.plan, "--plan")
    assessment_path: Path = resolve_workspace_path(workspace, arguments.assessment, "--assessment")
    confirmation_path: Path = resolve_workspace_path(workspace, arguments.confirmation, "--confirmation")
    environment_path: Path = resolve_workspace_path(workspace, arguments.environment_config, "--environment-config")
    connections_path: Path | None = (
        resolve_workspace_path(workspace, arguments.connections, "--connections")
        if isinstance(arguments.connections, str)
        else None
    )
    output_dir: Path = resolve_workspace_path(workspace, arguments.output_dir, "--output-dir")
    manifest_path: Path = resolve_workspace_path(workspace, arguments.manifest, "--manifest")
    plan: JsonObject = read_json_object(plan_path, "execution_plan")
    assessment: JsonObject = read_json_object(assessment_path, "preparation_assessment")
    confirmation: JsonObject = read_json_object(confirmation_path, "testcase_confirmation")
    environment_config: JsonObject = read_json_object(environment_path, "environment_config")
    assessment_sha256: str = file_sha256(assessment_path)
    plan_builder: PlanBuilder = load_plan_builder()
    canonical_plan, canonical_report = plan_builder(assessment, assessment_sha256)
    if canonical_report.get("ready") is not True:
        raise PermissionError(
            f"准备评估无法重建可执行计划；blockers={canonical_report.get('blockers')}；请返回准备阶段处理。"
        )
    validate_plan(plan, confirmation, assessment, assessment_sha256, canonical_plan)
    environment: JsonObject = select_environment(environment_config, arguments.environment_name)
    credentials_store: JsonObject | None = load_local_credentials(workspace)
    environment = resolve_environment_authentication(environment, credentials_store)
    raw_token_error_codes: list[object] = require_list(plan.get("token_error_codes", []), "plan.token_error_codes")
    token_error_codes: set[str] = {require_string(item, "plan.token_error_codes[]") for item in raw_token_error_codes}
    setup_actions: list[object] = require_list(plan.get("data_setup"), "plan.data_setup")
    cleanup_actions: list[object] = require_list(plan.get("data_cleanup"), "plan.data_cleanup")
    all_data_actions: list[object] = setup_actions + cleanup_actions
    if all_data_actions:
        validate_mutation_environment(environment, arguments.environment_name)
    requires_write_connection: bool = any(
        isinstance(action, dict) and action.get("type") in {"sql_insert", "sql_delete"}
        for action in all_data_actions
    )
    has_database_assertions: bool = any(
        isinstance(request, dict)
        and isinstance(request.get("expected"), dict)
        and bool(request["expected"].get("database_assertions"))
        for request in require_list(plan.get("requests"), "plan.requests")
    ) or any(
        isinstance(flow, dict)
        and any(
            isinstance(step, dict)
            and isinstance(step.get("expected"), dict)
            and bool(step["expected"].get("database_assertions"))
            for step in flow.get("steps", [])
        )
        for flow in require_list(plan.get("flows"), "plan.flows")
    )
    connection_registry: JsonObject | None = (
        read_json_object(connections_path, "connections") if connections_path is not None else None
    )
    if (requires_write_connection or has_database_assertions) and connection_registry is None:
        raise PermissionError("执行计划需要数据库连接；必须提供工作区 --connections 并显式选择连接名。")
    read_connection: JsonObject | None = None
    if has_database_assertions:
        if not isinstance(arguments.read_connection_name, str):
            raise PermissionError("执行计划包含数据库断言；必须提供用户确认的 --read-connection-name。")
        read_connection = validate_read_connection(
            select_connection(require_object(connection_registry, "connections"), arguments.read_connection_name, "read_connection")
        )
    write_connection: JsonObject | None = None
    if requires_write_connection:
        if not isinstance(arguments.write_connection_name, str):
            raise PermissionError("执行计划包含受控 SQL；必须提供用户确认的 --write-connection-name。")
        write_connection = validate_write_connection(
            select_connection(require_object(connection_registry, "connections"), arguments.write_connection_name, "write_connection"),
            arguments.environment_name,
            all_data_actions,
        )
    setup_results: list[JsonObject] = []
    cleanup_results: list[JsonObject] = []
    lifecycle_rows: list[tuple[str, str, JsonObject]] = []
    cleanup_entry_ids: set[str] = set()
    interface_results: list[JsonObject] = []
    flow_results: list[JsonObject] = []
    interface_rows: list[tuple[str, str, JsonObject]] = []
    flow_rows: list[tuple[str, str, JsonObject]] = []
    pending_error: ConnectionError | LookupError | PermissionError | RuntimeError | TypeError | ValueError | SystemExit | None = None
    try:
        setup_results, setup_rows, cleanup_entry_ids = execute_setup_actions(
            setup_actions,
            environment,
            write_connection,
            token_error_codes,
            expected_business_code,
        )
        lifecycle_rows.extend(setup_rows)
        if any(result.get("error_type") == "SystemExit" for result in setup_results):
            raise SystemExit(10)
        if lifecycle_failed(setup_results):
            raise RuntimeError("真实测试数据准备失败；已停止接口执行并进入反向清理。")
        interface_results, interface_rows = execute_standalone_requests(
            require_list(plan.get("requests"), "plan.requests"), environment, read_connection, token_error_codes, expected_business_code
        )
        flow_results, flow_rows = execute_flows(
            require_list(plan.get("flows"), "plan.flows"), environment, read_connection, token_error_codes, expected_business_code
        )
    except SystemExit as error:
        pending_error = error
    except (ConnectionError, LookupError, PermissionError, RuntimeError, TypeError, ValueError) as error:
        pending_error = error
    finally:
        try:
            cleanup_results, cleanup_rows = execute_cleanup_actions(
                cleanup_actions,
                cleanup_entry_ids,
                environment,
                write_connection,
                token_error_codes,
                expected_business_code,
            )
            lifecycle_rows.extend(cleanup_rows)
            if any(result.get("error_type") == "SystemExit" for result in cleanup_results):
                pending_error = SystemExit(10)
        except SystemExit as cleanup_error:
            pending_error = cleanup_error
        append_manifest(manifest_path, lifecycle_rows + interface_rows + flow_rows)
    if pending_error is not None:
        raise pending_error
    write_reports(
        output_dir,
        render_interface_report(interface_results, arguments.environment_name),
        render_flow_report(flow_results, arguments.environment_name),
    )
    has_execution_errors: bool = any(result.get("status") == "EXECUTION_ERROR" for result in interface_results)
    has_flow_errors: bool = any(result.get("status") == "INTERRUPTED" for result in flow_results)
    has_failures: bool = any(result.get("status") == "FAIL" for result in interface_results)
    return 1 if has_execution_errors or has_flow_errors or has_failures or lifecycle_failed(cleanup_results) else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ConnectionError, FileNotFoundError, LookupError, NotADirectoryError, PermissionError, RuntimeError, TypeError, ValueError) as error:
        print(f"[EXECUTION_ERROR] {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(2) from error
