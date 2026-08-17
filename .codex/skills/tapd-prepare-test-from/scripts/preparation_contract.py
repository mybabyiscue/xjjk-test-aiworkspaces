"""Generic contracts for confirmed interface-test preparation."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TypeAlias

JsonObject: TypeAlias = dict[str, object]
AUDIT_STATUSES: frozenset[str] = frozenset({"待审核", "可审核", "阻断", "已通过", "已驳回"})
NON_INTERFACE_CLASSIFICATIONS: frozenset[str] = frozenset({"ui_only", "manual_only", "blocked"})
NEGATIVE_VARIANT_POLICIES: frozenset[str] = frozenset({"covered", "no_verifiable_validation_rule"})
VALID_VARIANT_TYPES: frozenset[str] = frozenset({"positive", "negative"})
FORBIDDEN_SCENARIO_CATEGORY_MARKERS: frozenset[str] = frozenset({"todo", "n/a", "待定", "tbd", "unknown", "placeholder"})
DATA_PREPARATION_STRATEGIES: frozenset[str] = frozenset({"reuse", "api_create", "api_snapshot_restore", "sql_insert", "manual_create"})
DATA_ACTION_TYPES: frozenset[str] = frozenset({"http", "sql_insert", "sql_delete"})
AUTOMATED_DATA_STRATEGIES: frozenset[str] = frozenset({"api_create", "sql_insert"})
MUTATING_ACTION_TYPES: frozenset[str] = frozenset({"http", "sql_insert"})
IMPLEMENTED_SOURCE_KINDS: frozenset[str] = frozenset(
    {"database", "upstream_response", "setup_response", "environment_config", "protocol_constant", "dynamic_unique", "current_time", "manual_preparation", "negative_constructed"}
)
BLOCKING_SOURCE_KINDS: frozenset[str] = frozenset({"unresolved", "file", "cache", "message", "user_confirmed_input"})
PARAMETER_LOCATIONS: frozenset[str] = frozenset({"path", "query", "header", "cookie", "body"})
PROTOCOL_KINDS: frozenset[str] = frozenset({"http"})
UNSUPPORTED_PROTOCOL_KINDS: frozenset[str] = frozenset({"rpc", "graphql", "websocket", "message_queue"})
RESPONSE_ASSERTION_TYPES: frozenset[str] = frozenset({"json_path", "status_only", "header", "body_contains", "file_metadata", "stream", "async_task"})
CLEANUP_POLICIES: frozenset[str] = frozenset({"automatic", "idempotent", "not_required_with_evidence", "manual", "blocked_unsafe"})
SENSITIVE_KEYWORDS: frozenset[str] = frozenset({"authorization", "cookie", "token", "password", "secret", "credential", "api-key", "apikey"})
FORBIDDEN_PLACEHOLDER_MARKERS: frozenset[str] = frozenset({"placeholder", "example_only", "示例", "占位"})


class PreparationError(RuntimeError):
    """Raised when a preparation artifact violates its contract."""


def read_json_object(path: Path) -> JsonObject:
    try:
        raw_content: str = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise PreparationError(f"无法读取 JSON 文件：{path}") from error
    try:
        value: object = json.loads(raw_content)
    except json.JSONDecodeError as error:
        raise PreparationError(f"JSON 格式错误：{path}") from error
    if not isinstance(value, dict):
        raise PreparationError(f"JSON 根节点必须是对象：{path}")
    return value


def write_json_object(path: Path, payload: JsonObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def file_sha256(path: Path) -> str:
    digest: hashlib._Hash = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def require_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PreparationError(f"字段 {field_name} 必须是非空字符串。")
    return value.strip()


def require_object(value: object, field_name: str) -> JsonObject:
    if not isinstance(value, dict):
        raise PreparationError(f"字段 {field_name} 必须是对象。")
    return value


def require_list(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise PreparationError(f"字段 {field_name} 必须是数组。")
    return value


def require_string_list(value: object, field_name: str) -> list[str]:
    items: list[object] = require_list(value, field_name)
    if not items:
        raise PreparationError(f"字段 {field_name} 必须是非空字符串数组。")
    result: list[str] = []
    for index, item in enumerate(items, start=1):
        result.append(require_string(item, f"{field_name}[{index}]"))
    return result


def optional_string(value: object, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PreparationError(f"字段 {field_name} 必须是字符串。")
    return value


def validate_variant_type_value(value: object, field_name: str, errors: list[str]) -> str:
    try:
        variant_type: str = require_string(value, field_name)
        if variant_type not in VALID_VARIANT_TYPES:
            errors.append(f"{field_name} 仅允许 positive/negative，当前值：{variant_type}")
        return variant_type
    except PreparationError as error:
        errors.append(str(error))
    return ""


def validate_scenario_category_value(value: object, field_name: str, errors: list[str]) -> str:
    try:
        scenario_category: str = require_string(value, field_name)
        normalized_category: str = scenario_category.casefold()
        if normalized_category in FORBIDDEN_SCENARIO_CATEGORY_MARKERS:
            errors.append(f"{field_name} 不能是占位文本：{scenario_category}")
        return scenario_category
    except PreparationError as error:
        errors.append(str(error))
    return ""


def stable_case_key(case_id: str) -> str:
    digest: str = hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:12]
    return f"case_{digest}"


def load_cases(path: Path) -> list[JsonObject]:
    payload: JsonObject = read_json_object(path)
    raw_cases: list[object] = require_list(payload.get("cases"), "cases")
    if payload.get("total_count") != len(raw_cases):
        raise PreparationError("tapd_cases.json.total_count 必须等于 cases 数量。")
    cases: list[JsonObject] = []
    case_ids: set[str] = set()
    for index, raw_case in enumerate(raw_cases, start=1):
        case: JsonObject = require_object(raw_case, f"cases[{index}]")
        case_id: str = require_string(case.get("case_id"), f"cases[{index}].case_id")
        if case_id in case_ids:
            raise PreparationError(f"cases[{index}].case_id 重复：{case_id}。")
        case_ids.add(case_id)
        require_string(case.get("title"), f"cases[{index}].title")
        require_string(case.get("directory"), f"cases[{index}].directory")
        require_string(case.get("requirement_id"), f"cases[{index}].requirement_id")
        for name in ("case_type", "case_status", "priority", "system_scope", "module"):
            require_string(case.get(name), f"cases[{index}].{name}")
        for name in ("precondition", "remarks"):
            optional_string(case.get(name), f"cases[{index}].{name}")
        require_string_list(case.get("steps"), f"cases[{index}].steps")
        require_string_list(case.get("expected_results"), f"cases[{index}].expected_results")
        require_string_list(case.get("requirement_points"), f"cases[{index}].requirement_points")
        cases.append(case)
    return cases


def case_catalog(cases: list[JsonObject]) -> list[JsonObject]:
    catalog: list[JsonObject] = []
    for index, case in enumerate(cases, start=1):
        case_id: str = require_string(case.get("case_id"), f"cases[{index}].case_id")
        catalog.append(
            {
                "case_key": stable_case_key(case_id),
                "case_index": index,
                "case_id": case_id,
                "title": require_string(case.get("title"), "title"),
                "directory": require_string(case.get("directory"), "directory"),
                "requirement_id": require_string(case.get("requirement_id"), "requirement_id"),
                "precondition": case.get("precondition", ""),
                "steps": case.get("steps", []),
                "expected_results": case.get("expected_results", []),
                "scenario_category": optional_string(case.get("scenario_category"), "scenario_category"),
                "scenario_tags": case.get("scenario_tags", []),
            }
        )
    return catalog


def audit_errors(value: object, field_name: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return [f"{field_name} 必须是对象。"]
    status: object = value.get("status")
    if status not in AUDIT_STATUSES:
        errors.append(f"{field_name}.status 不合法。")
    for name in ("evidence_status", "reason", "reviewer", "reviewed_at"):
        if not isinstance(value.get(name), str):
            errors.append(f"{field_name}.{name} 必须是字符串。")
    return errors


def validation_errors(assessment: JsonObject, cases: list[JsonObject], snapshot: JsonObject) -> list[str]:
    errors: list[str] = []
    source: object = assessment.get("source")
    if not isinstance(source, dict):
        errors.append("assessment.source 必须是对象。")
    else:
        confirmation: JsonObject = require_object(snapshot.get("testcase_confirmation"), "snapshot.testcase_confirmation")
        if source.get("testcase_hash") != confirmation.get("testcase_hash"):
            errors.append("assessment.source.testcase_hash 与确认文件不一致。")
        if source.get("code_review_run_id") != confirmation.get("code_review_run_id"):
            errors.append("assessment.source.code_review_run_id 与确认文件不一致。")
        if source.get("input_hashes") != snapshot.get("input_hashes"):
            errors.append("assessment.source.input_hashes 与确认输入快照不一致。")
    expected_keys: set[str] = {stable_case_key(require_string(case.get("case_id"), "case.case_id")) for case in cases}
    raw_catalog: object = assessment.get("case_catalog")
    if not isinstance(raw_catalog, list) or {item.get("case_key") for item in raw_catalog if isinstance(item, dict)} != expected_keys:
        errors.append("assessment.case_catalog 必须与当前测试用例逐条对应。")
    evidence_files: dict[str, Path] = snapshot_evidence_files(snapshot)
    raw_records: object = assessment.get("real_data_records")
    records: list[object] = raw_records if isinstance(raw_records, list) else []
    if not isinstance(raw_records, list):
        errors.append("assessment.real_data_records 必须是数组。")
    record_references: set[str] = set()
    nonempty_record_references: set[str] = set()
    for index, raw_record in enumerate(records, start=1):
        if not isinstance(raw_record, dict):
            errors.append(f"real_data_records[{index}] 必须是对象。")
            continue
        try:
            reference: str = require_string(raw_record.get("query_reference"), f"real_data_records[{index}].query_reference")
            record_references.add(reference)
            for name in ("connection", "database", "table", "executed_at", "purpose"):
                require_string(raw_record.get(name), f"real_data_records[{index}].{name}")
            require_list(raw_record.get("fields"), f"real_data_records[{index}].fields")
            require_object(raw_record.get("filters"), f"real_data_records[{index}].filters")
            row_count: object = raw_record.get("row_count")
            if not isinstance(row_count, int):
                errors.append(f"real_data_records[{index}].row_count 必须是整数。")
            elif row_count > 0:
                nonempty_record_references.add(reference)
            require_list(raw_record.get("records"), f"real_data_records[{index}].records")
        except PreparationError as error:
            errors.append(str(error))
    errors.extend(data_preparation_errors(assessment.get("data_preparation"), expected_keys, nonempty_record_references, evidence_files))
    coverage: dict[str, int] = {key: 0 for key in expected_keys}
    raw_interfaces: object = assessment.get("interface_cases")
    if not isinstance(raw_interfaces, list):
        errors.append("assessment.interface_cases 必须是数组。")
        raw_interfaces = []
    for index, raw_interface in enumerate(raw_interfaces, start=1):
        errors.extend(interface_case_errors(raw_interface, index, coverage, record_references, evidence_files))
    raw_non_interface_cases: object = assessment.get("non_interface_cases")
    if not isinstance(raw_non_interface_cases, list):
        errors.append("assessment.non_interface_cases 必须是数组。")
        raw_non_interface_cases = []
    for index, raw_case in enumerate(raw_non_interface_cases, start=1):
        errors.extend(non_interface_case_errors(raw_case, index, coverage, record_references, evidence_files))
    for key, count in coverage.items():
        if count != 1:
            errors.append(f"{key} 必须恰好被可接口测试或不可接口测试集合覆盖一次，当前为 {count} 次。")
    raw_flows: object = assessment.get("core_flows")
    if not isinstance(raw_flows, list):
        errors.append("assessment.core_flows 必须是数组。")
    else:
        for index, raw_flow in enumerate(raw_flows, start=1):
            errors.extend(core_flow_errors(raw_flow, index, expected_keys, record_references, evidence_files))
    reason: object = assessment.get("core_flow_blocker_reason")
    if not isinstance(reason, str):
        errors.append("assessment.core_flow_blocker_reason 必须是字符串。")
    return errors


def snapshot_evidence_files(snapshot: JsonObject) -> dict[str, Path]:
    raw_files: object = snapshot.get("evidence_files")
    if not isinstance(raw_files, dict):
        return {}
    result: dict[str, Path] = {}
    for key, value in raw_files.items():
        if isinstance(key, str) and isinstance(value, str):
            result[key] = Path(value)
    return result


def evidence_errors(value: object, field_name: str, evidence_files: dict[str, Path]) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return [f"{field_name} 必须是结构化证据对象。"]
    try:
        source_type: str = require_string(value.get("source_type"), f"{field_name}.source_type")
        source_file: str = require_string(value.get("source_file"), f"{field_name}.source_file")
        source_location: str = require_string(value.get("source_location"), f"{field_name}.source_location")
        require_string(value.get("rule_type"), f"{field_name}.rule_type")
        require_string(value.get("rule_summary"), f"{field_name}.rule_summary")
        if source_type == "example":
            errors.append(f"{field_name}.source_type 不得使用文档示例作为业务证据。")
        path: Path | None = evidence_files.get(source_file)
        if path is None:
            candidate: Path = Path(source_file)
            path = candidate if candidate.is_file() else None
        if path is None or not path.is_file():
            errors.append(f"{field_name}.source_file 引用的证据文件不存在：{source_file}。")
        else:
            content: str = path.read_text(encoding="utf-8-sig")
            expected_hash: object = value.get("source_sha256")
            if isinstance(expected_hash, str) and expected_hash and file_sha256(path) != expected_hash:
                errors.append(f"{field_name}.source_sha256 与证据文件不一致。")
            anchors: list[object] = []
            for key in ("evidence_id", "anchor", "method_name", "field_name"):
                raw_anchor: object = value.get(key)
                if isinstance(raw_anchor, str) and raw_anchor.strip():
                    anchors.append(raw_anchor.strip())
            raw_line: object = value.get("line")
            if isinstance(raw_line, int) and raw_line > 0:
                lines: list[str] = content.splitlines()
                if raw_line > len(lines):
                    errors.append(f"{field_name}.line 超出证据文件范围。")
                else:
                    anchors.append(lines[raw_line - 1].strip())
            if source_location.strip():
                anchors.append(source_location.strip())
            if anchors and not any(anchor and anchor in content for anchor in anchors):
                errors.append(f"{field_name} 的锚点、方法、字段或位置未在证据文件中找到。")
    except PreparationError as error:
        errors.append(str(error))
    return errors


def evidence_list_errors(value: object, field_name: str, evidence_files: dict[str, Path]) -> list[str]:
    errors: list[str] = []
    raw_items: list[object] = require_list(value, field_name)
    if not raw_items:
        return [f"{field_name} 必须包含至少一条结构化证据。"]
    for index, raw_item in enumerate(raw_items, start=1):
        errors.extend(evidence_errors(raw_item, f"{field_name}[{index}]", evidence_files))
    return errors


def data_preparation_errors(
    value: object,
    expected_case_keys: set[str],
    nonempty_record_references: set[str],
    evidence_files: dict[str, Path] | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["assessment.data_preparation 必须是对象。"]
    raw_entries: object = value.get("entries")
    if not isinstance(raw_entries, list):
        return ["assessment.data_preparation.entries 必须是数组。"]
    entry_ids: set[str] = set()
    action_ids: set[str] = set()
    evidence_lookup: dict[str, Path] = evidence_files if evidence_files is not None else {}
    for index, raw_entry in enumerate(raw_entries, start=1):
        field_name: str = f"data_preparation.entries[{index}]"
        if not isinstance(raw_entry, dict):
            errors.append(f"{field_name} 必须是对象。")
            continue
        try:
            entry_id: str = require_string(raw_entry.get("id"), f"{field_name}.id")
            if entry_id in entry_ids:
                errors.append(f"{field_name}.id 重复：{entry_id}。")
            entry_ids.add(entry_id)
            case_keys: list[object] = require_list(raw_entry.get("case_keys"), f"{field_name}.case_keys")
            if not case_keys or any(not isinstance(key, str) or key not in expected_case_keys for key in case_keys):
                errors.append(f"{field_name}.case_keys 必须引用已知用例。")
            strategy: str = require_string(raw_entry.get("strategy"), f"{field_name}.strategy")
            if strategy not in DATA_PREPARATION_STRATEGIES:
                errors.append(f"{field_name}.strategy 不合法；禁止 Mock、Fake 或 Stub 数据策略。")
            errors.extend(evidence_list_errors(raw_entry.get("evidence_references"), f"{field_name}.evidence_references", evidence_lookup))
            verification_reference: str = require_string(
                raw_entry.get("verification_query_reference"),
                f"{field_name}.verification_query_reference",
            )
            if strategy in {"reuse", "manual_create"} and verification_reference not in nonempty_record_references:
                errors.append(f"{field_name} 必须引用已返回真实记录的查询；手工创建后必须重新查询确认。")
            setup: object = raw_entry.get("setup")
            cleanup: object = raw_entry.get("cleanup")
            if strategy in {"reuse", "manual_create"}:
                if setup is not None or cleanup is not None:
                    errors.append(f"{field_name} 的 {strategy} 策略不得包含自动写入动作。")
                continue
            isolation_prefix: str = require_string(raw_entry.get("isolation_prefix"), f"{field_name}.isolation_prefix")
            if not isolation_prefix.startswith("TEST_"):
                errors.append(f"{field_name}.isolation_prefix 必须以 TEST_ 开头。")
            setup_type: str = "http" if strategy in {"api_create", "api_snapshot_restore"} else "sql_insert"
            errors.extend(data_action_errors(setup, f"{field_name}.setup", {setup_type}, action_ids, evidence_lookup))
            errors.extend(data_action_errors(cleanup, f"{field_name}.cleanup", {"http", "sql_delete"}, action_ids, evidence_lookup))
        except PreparationError as error:
            errors.append(str(error))
    return errors


def data_action_errors(
    value: object,
    field_name: str,
    allowed_types: set[str],
    action_ids: set[str],
    evidence_files: dict[str, Path] | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return [f"{field_name} 必须是对象。"]
    try:
        action_id: str = require_string(value.get("id"), f"{field_name}.id")
        if action_id in action_ids:
            errors.append(f"{field_name}.id 重复：{action_id}。")
        action_ids.add(action_id)
        action_type: str = require_string(value.get("type"), f"{field_name}.type")
        if action_type not in DATA_ACTION_TYPES or action_type not in allowed_types:
            errors.append(f"{field_name}.type 不合法；只允许真实 HTTP 或受控 SQL 动作，禁止 Mock、Fake、Stub 和 Mock seed。")
        errors.extend(evidence_errors(value.get("evidence_reference"), f"{field_name}.evidence_reference", evidence_files if evidence_files is not None else {}))
        cleanup_policy: str = require_string(value.get("cleanup_policy"), f"{field_name}.cleanup_policy")
        if cleanup_policy not in CLEANUP_POLICIES:
            errors.append(f"{field_name}.cleanup_policy 不合法。")
        if action_type in MUTATING_ACTION_TYPES and cleanup_policy in {"manual", "blocked_unsafe"}:
            errors.append(f"{field_name} 是自动创建动作，必须具备可追踪自动清理策略或阻断。")
        dependencies: list[object] = require_list(value.get("depends_on", []), f"{field_name}.depends_on")
        if any(not isinstance(item, str) or not item.strip() for item in dependencies):
            errors.append(f"{field_name}.depends_on 必须是字符串数组。")
        manifest: JsonObject = require_object(value.get("manifest"), f"{field_name}.manifest")
        require_string(manifest.get("database"), f"{field_name}.manifest.database")
        require_string(manifest.get("table"), f"{field_name}.manifest.table")
        require_object(manifest.get("record"), f"{field_name}.manifest.record")
        if action_type == "http":
            require_string(value.get("method"), f"{field_name}.method")
            path: str = require_string(value.get("path"), f"{field_name}.path")
            if not path.startswith("/"):
                errors.append(f"{field_name}.path 必须是完整网关相对路径。")
            require_object(value.get("headers"), f"{field_name}.headers")
            if not isinstance(value.get("auth_header_name"), str):
                errors.append(f"{field_name}.auth_header_name 必须是字符串。")
            require_object(value.get("query"), f"{field_name}.query")
            if "body" not in value:
                value["body"] = None
            expected: JsonObject = require_object(value.get("expected"), f"{field_name}.expected")
            if "http_status" in expected and not isinstance(expected.get("http_status"), int):
                errors.append(f"{field_name}.expected.http_status 必须是整数。")
            assertions: list[object] = require_list(expected.get("response_assertions"), f"{field_name}.expected.response_assertions")
            errors.extend(assertion_list_errors(assertions, f"{field_name}.expected.response_assertions", evidence_files if evidence_files is not None else {}))
        elif action_type in {"sql_insert", "sql_delete"}:
            database: str = require_string(value.get("database"), f"{field_name}.database")
            table: str = require_string(value.get("table"), f"{field_name}.table")
            sql: str = require_string(value.get("sql"), f"{field_name}.sql")
            parameters: list[object] = require_list(value.get("parameters"), f"{field_name}.parameters")
            errors.extend(sql_write_errors(sql, action_type, database, table, parameters))
            expected_rows: object = value.get("expected_affected_rows")
            if not isinstance(expected_rows, int) or isinstance(expected_rows, bool) or expected_rows < 0:
                errors.append(f"{field_name}.expected_affected_rows 必须是非负整数。")
            if action_type == "sql_delete" and cleanup_policy != "idempotent" and expected_rows == 0:
                errors.append(f"{field_name}.expected_affected_rows 为 0 时 cleanup_policy 必须是 idempotent。")
    except PreparationError as error:
        errors.append(str(error))
    return errors


def assertion_list_errors(value: object, field_name: str, evidence_files: dict[str, Path]) -> list[str]:
    errors: list[str] = []
    assertions: list[object] = require_list(value, field_name)
    if not assertions:
        return [f"{field_name} 必须包含机器可判定断言或明确状态类断言。"]
    for index, raw_assertion in enumerate(assertions, start=1):
        assertion_name: str = f"{field_name}[{index}]"
        if not isinstance(raw_assertion, dict):
            errors.append(f"{assertion_name} 必须是对象。")
            continue
        assertion_type: str = require_string(raw_assertion.get("assertion_type"), f"{assertion_name}.assertion_type")
        if assertion_type not in RESPONSE_ASSERTION_TYPES:
            errors.append(f"{assertion_name}.assertion_type 不受支持。")
        if not isinstance(raw_assertion.get("operator"), str):
            errors.append(f"{assertion_name}.operator 必须是字符串。")
        errors.extend(evidence_errors(raw_assertion.get("evidence_reference"), f"{assertion_name}.evidence_reference", evidence_files))
    return errors


def sql_write_errors(sql: str, action_type: str, database: str, table: str, parameters: list[object]) -> list[str]:
    errors: list[str] = []
    normalized: str = " ".join(sql.strip().lower().split())
    if any(marker in normalized for marker in (";", "--", "#", "/*", "*/")):
        errors.append("SQL 写入禁止多语句和注释。")
    if re.search(r"\b(update|alter|drop|truncate|create|replace|merge|call|execute)\b", normalized) is not None:
        errors.append("SQL 写入禁止 UPDATE、DDL、TRUNCATE、存储过程或替换类操作。")
    qualified_table: str = rf"`?{re.escape(database.lower())}`?\.`?{re.escape(table.lower())}`?"
    plain_table: str = rf"`?{re.escape(table.lower())}`?"
    if action_type == "sql_insert":
        if re.match(rf"^insert\s+into\s+({qualified_table}|{plain_table})\s*\([^)]+\)\s+values\s*\([%s,\s]+\)$", normalized) is None:
            errors.append("sql_insert 必须是显式列、单条参数化 INSERT。")
    elif action_type == "sql_delete":
        if re.match(rf"^delete\s+from\s+({qualified_table}|{plain_table})\s+where\s+.+$", normalized) is None:
            errors.append("sql_delete 必须是带 WHERE 的单条参数化 DELETE。")
        if re.search(r"\bwhere\s+(1\s*=\s*1|true)\b", normalized) is not None:
            errors.append("sql_delete 禁止无界或恒真条件。")
    placeholder_count: int = normalized.count("%s")
    if placeholder_count != len(parameters):
        errors.append("SQL 参数占位符数量必须与 parameters 数量一致。")
    return errors


def interface_evidence_errors(value: object, field_name: str, evidence_files: dict[str, Path]) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return [f"{field_name} 必须是对象。"]
    try:
        protocol: str = require_string(value.get("protocol"), f"{field_name}.protocol")
        if protocol in UNSUPPORTED_PROTOCOL_KINDS:
            errors.append(f"{field_name}.protocol 当前不支持：{protocol}，不得伪装为 HTTP。")
            return errors
        if protocol not in PROTOCOL_KINDS:
            errors.append(f"{field_name}.protocol 不受支持。")
            return errors
        for name in ("service", "operation", "method", "path", "response_type"):
            require_string(value.get(name), f"{field_name}.{name}")
        if not require_string(value.get("path"), f"{field_name}.path").startswith("/"):
            errors.append(f"{field_name}.path 必须是证据中的完整网关相对路径。")
        errors.extend(evidence_list_errors(value.get("evidence_references"), f"{field_name}.evidence_references", evidence_files))
    except PreparationError as error:
        errors.append(str(error))
    return errors


def interface_case_errors(
    value: object,
    index: int,
    coverage: dict[str, int],
    record_references: set[str],
    evidence_files: dict[str, Path],
) -> list[str]:
    field_name: str = f"interface_cases[{index}]"
    errors: list[str] = []
    if not isinstance(value, dict):
        return [f"{field_name} 必须是对象。"]
    try:
        require_string(value.get("interface_key"), f"{field_name}.interface_key")
        errors.extend(interface_evidence_errors(value.get("interface_evidence"), f"{field_name}.interface_evidence", evidence_files))
        case_keys: list[object] = require_list(value.get("covered_case_keys"), f"{field_name}.covered_case_keys")
        if not case_keys:
            errors.append(f"{field_name}.covered_case_keys 不得为空。")
        for raw_key in case_keys:
            if not isinstance(raw_key, str) or raw_key not in coverage:
                errors.append(f"{field_name} 引用了不存在的 case_key。")
            elif raw_key:
                coverage[raw_key] += 1
        variants: list[object] = require_list(value.get("request_variants"), f"{field_name}.request_variants")
        if not variants:
            errors.append(f"{field_name}.request_variants 不得为空。")
        scenario_categories: set[str] = set()
        for variant_index, variant in enumerate(variants, start=1):
            scenario_categories.update(request_variant_errors(variant, f"{field_name}.request_variants[{variant_index}]", record_references, evidence_files, errors))
        policy: object = value.get("negative_variant_policy")
        if policy not in NEGATIVE_VARIANT_POLICIES:
            errors.append(f"{field_name}.negative_variant_policy 不合法。")
        elif policy == "covered" and not scenario_categories.intersection({"negative", "boundary", "permission", "state", "idempotency", "validation"}):
            errors.append(f"{field_name} 已声明存在可验证反向规则，但缺少对应场景变体。")
        elif policy == "no_verifiable_validation_rule":
            errors.extend(evidence_list_errors(value.get("negative_variant_evidence"), f"{field_name}.negative_variant_evidence", evidence_files))
        errors.extend(audit_errors(value.get("audit"), f"{field_name}.audit"))
    except PreparationError as error:
        errors.append(str(error))
    return errors


def request_variant_errors(
    value: object,
    field_name: str,
    record_references: set[str],
    evidence_files: dict[str, Path],
    errors: list[str],
) -> set[str]:
    scenario_categories: set[str] = set()
    if not isinstance(value, dict):
        errors.append(f"{field_name} 必须是对象。")
        return scenario_categories
    try:
        require_string(value.get("name"), f"{field_name}.name")
        validate_variant_type_value(value.get("variant_type"), f"{field_name}.variant_type", errors)
        scenario_category: str = validate_scenario_category_value(value.get("scenario_category"), f"{field_name}.scenario_category", errors)
        if scenario_category:
            scenario_categories.add(scenario_category)
        require_list(value.get("scenario_tags", []), f"{field_name}.scenario_tags")
        errors.extend(evidence_list_errors(value.get("evidence_references", value.get("validation_evidence")), f"{field_name}.evidence_references", evidence_files))
        require_list(value.get("case_keys"), f"{field_name}.case_keys")
        require_object(value.get("headers"), f"{field_name}.headers")
        if not isinstance(value.get("auth_header_name", value.get("authorization_header", "")), str):
            errors.append(f"{field_name}.auth_header_name 必须是字符串。")
        require_object(value.get("query"), f"{field_name}.query")
        parameters: list[object] = require_list(value.get("parameters"), f"{field_name}.parameters")
        expected: JsonObject = require_object(value.get("expected"), f"{field_name}.expected")
        if "http_status" in expected and not isinstance(expected.get("http_status"), int):
            errors.append(f"{field_name}.expected.http_status 必须是整数。")
        errors.extend(assertion_list_errors(expected.get("response_assertions"), f"{field_name}.expected.response_assertions", evidence_files))
        require_list(expected.get("database_assertions"), f"{field_name}.expected.database_assertions")
        require_list(value.get("setup_steps"), f"{field_name}.setup_steps")
        require_list(value.get("cleanup_steps"), f"{field_name}.cleanup_steps")
        for parameter_index, parameter in enumerate(parameters, start=1):
            errors.extend(parameter_errors(parameter, f"{field_name}.parameters[{parameter_index}]", record_references))
    except PreparationError as error:
        errors.append(str(error))
    return scenario_categories


def parameter_errors(value: object, field_name: str, record_references: set[str]) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return [f"{field_name} 必须是对象。"]
    try:
        require_string(value.get("name"), f"{field_name}.name")
        require_string(value.get("type"), f"{field_name}.type")
        location: str = require_string(value.get("location"), f"{field_name}.location")
        if location not in PARAMETER_LOCATIONS:
            errors.append(f"{field_name}.location 不合法。")
        source: JsonObject
        raw_source: object = value.get("source")
        if isinstance(raw_source, dict):
            source = raw_source
        else:
            source = {
                "kind": value.get("source_type"),
                "reference": value.get("source_reference"),
                "resolver": value.get("source_resolver", ""),
            }
        source_kind: str = require_string(source.get("kind"), f"{field_name}.source.kind")
        require_string(source.get("reference"), f"{field_name}.source.reference")
        if not isinstance(source.get("resolver", ""), str):
            errors.append(f"{field_name}.source.resolver 必须是字符串。")
        if source_kind in BLOCKING_SOURCE_KINDS:
            errors.append(f"{field_name} 的 {source_kind} 来源当前不能进入正式执行计划。")
        elif source_kind not in IMPLEMENTED_SOURCE_KINDS:
            errors.append(f"{field_name}.source.kind 当前未实现，必须阻断或补充适配器：{source_kind}。")
        query_reference: object = value.get("query_reference")
        if source_kind == "database" and (not isinstance(query_reference, str) or query_reference not in record_references):
            errors.append(f"{field_name} 的 database 来源必须引用真实查询记录。")
        if any(
            isinstance(value.get(marker), str) and value.get(marker) in FORBIDDEN_PLACEHOLDER_MARKERS
            for marker in ("source_reference", "value_kind")
        ):
            errors.append(f"{field_name} 包含示例或占位值，不能进入执行计划。")
    except PreparationError as error:
        errors.append(str(error))
    return errors


def non_interface_case_errors(
    value: object,
    index: int,
    coverage: dict[str, int],
    record_references: set[str],
    evidence_files: dict[str, Path],
) -> list[str]:
    field_name: str = f"non_interface_cases[{index}]"
    errors: list[str] = []
    if not isinstance(value, dict):
        return [f"{field_name} 必须是对象。"]
    try:
        key: str = require_string(value.get("case_key"), f"{field_name}.case_key")
        if key not in coverage:
            errors.append(f"{field_name}.case_key 不存在。")
        else:
            coverage[key] += 1
        require_string(value.get("title"), f"{field_name}.title")
        classification: object = value.get("classification")
        if classification not in NON_INTERFACE_CLASSIFICATIONS:
            errors.append(f"{field_name}.classification 不合法。")
        require_string(value.get("reason"), f"{field_name}.reason")
        related_interfaces: list[object] = require_list(value.get("related_interfaces"), f"{field_name}.related_interfaces")
        for related_index, related in enumerate(related_interfaces, start=1):
            related_object: JsonObject = require_object(related, f"{field_name}.related_interfaces[{related_index}]")
            for name in ("method", "path"):
                require_string(related_object.get(name), f"{field_name}.related_interfaces[{related_index}].{name}")
            require_object(related_object.get("headers"), f"{field_name}.related_interfaces[{related_index}].headers")
            require_list(related_object.get("parameters"), f"{field_name}.related_interfaces[{related_index}].parameters")
            errors.extend(evidence_list_errors(related_object.get("evidence_references"), f"{field_name}.related_interfaces[{related_index}].evidence_references", evidence_files))
        parameter_data: list[object] = require_list(value.get("parameter_data"), f"{field_name}.parameter_data")
        for raw_data in parameter_data:
            if isinstance(raw_data, dict) and raw_data.get("query_reference") not in record_references:
                errors.append(f"{field_name}.parameter_data 引用了不存在的查询记录。")
        require_string(value.get("recommended_test_type"), f"{field_name}.recommended_test_type")
        if classification == "blocked":
            missing_evidence: list[object] = require_list(value.get("missing_evidence"), f"{field_name}.missing_evidence")
            if not missing_evidence:
                errors.append(f"{field_name} 为 blocked 时必须说明缺失证据。")
        errors.extend(audit_errors(value.get("audit"), f"{field_name}.audit"))
    except PreparationError as error:
        errors.append(str(error))
    return errors


def core_flow_errors(
    value: object,
    index: int,
    expected_keys: set[str],
    record_references: set[str],
    evidence_files: dict[str, Path],
) -> list[str]:
    field_name: str = f"core_flows[{index}]"
    errors: list[str] = []
    if not isinstance(value, dict):
        return [f"{field_name} 必须是对象。"]
    try:
        require_string(value.get("flow_key"), f"{field_name}.flow_key")
        require_string(value.get("name"), f"{field_name}.name")
        case_keys: list[object] = require_list(value.get("case_keys"), f"{field_name}.case_keys")
        if not case_keys or any(not isinstance(key, str) or key not in expected_keys for key in case_keys):
            errors.append(f"{field_name}.case_keys 必须引用已知用例。")
        errors.extend(evidence_list_errors(value.get("evidence_references"), f"{field_name}.evidence_references", evidence_files))
        steps: list[object] = require_list(value.get("steps"), f"{field_name}.steps")
        if len(steps) < 2:
            errors.append(f"{field_name}.steps 至少包含两个接口步骤。")
        prior_step_keys: set[str] = set()
        for step_index, step in enumerate(steps, start=1):
            step_errors, step_key = core_flow_step_errors(
                step,
                f"{field_name}.steps[{step_index}]",
                expected_keys,
                record_references,
                prior_step_keys,
                evidence_files,
            )
            errors.extend(step_errors)
            if step_key:
                if step_key in prior_step_keys:
                    errors.append(f"{field_name}.steps[{step_index}].step_key 重复。")
                prior_step_keys.add(step_key)
    except PreparationError as error:
        errors.append(str(error))
    return errors


def core_flow_step_errors(
    value: object,
    field_name: str,
    expected_keys: set[str],
    record_references: set[str],
    prior_step_keys: set[str],
    evidence_files: dict[str, Path],
) -> tuple[list[str], str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return [f"{field_name} 必须是对象。"], ""
    step_key: str = ""
    try:
        step_key = require_string(value.get("step_key"), f"{field_name}.step_key")
        validate_variant_type_value(value.get("variant_type"), f"{field_name}.variant_type", errors)
        validate_scenario_category_value(value.get("scenario_category"), f"{field_name}.scenario_category", errors)
        case_keys: list[object] = require_list(value.get("case_keys"), f"{field_name}.case_keys")
        if not case_keys or any(not isinstance(key, str) or key not in expected_keys for key in case_keys):
            errors.append(f"{field_name}.case_keys 必须引用已知用例。")
        errors.extend(interface_evidence_errors(value.get("interface_evidence"), f"{field_name}.interface_evidence", evidence_files))
        require_object(value.get("headers"), f"{field_name}.headers")
        if not isinstance(value.get("auth_header_name", value.get("authorization_header", "")), str):
            errors.append(f"{field_name}.auth_header_name 必须是字符串。")
        require_object(value.get("query"), f"{field_name}.query")
        parameters: list[object] = require_list(value.get("parameters"), f"{field_name}.parameters")
        expected: JsonObject = require_object(value.get("expected"), f"{field_name}.expected")
        if "http_status" in expected and not isinstance(expected.get("http_status"), int):
            errors.append(f"{field_name}.expected.http_status 必须是整数。")
        errors.extend(assertion_list_errors(expected.get("response_assertions"), f"{field_name}.expected.response_assertions", evidence_files))
        require_list(expected.get("database_assertions"), f"{field_name}.expected.database_assertions")
        dependencies: list[object] = require_list(value.get("parameter_dependencies"), f"{field_name}.parameter_dependencies")
        for dependency_index, raw_dependency in enumerate(dependencies, start=1):
            dependency: JsonObject = require_object(raw_dependency, f"{field_name}.parameter_dependencies[{dependency_index}]")
            source_step: str = require_string(dependency.get("source_step"), f"{field_name}.parameter_dependencies[{dependency_index}].source_step")
            if source_step not in prior_step_keys:
                errors.append(f"{field_name}.parameter_dependencies[{dependency_index}].source_step 必须引用更早的步骤。")
            for dependency_field in ("source_path", "target", "target_path"):
                require_string(dependency.get(dependency_field), f"{field_name}.parameter_dependencies[{dependency_index}].{dependency_field}")
            if dependency.get("target") not in PARAMETER_LOCATIONS:
                errors.append(f"{field_name}.parameter_dependencies[{dependency_index}].target 不合法。")
        require_string(value.get("interrupt_condition"), f"{field_name}.interrupt_condition")
        require_list(value.get("cleanup_steps"), f"{field_name}.cleanup_steps")
        for parameter_index, parameter in enumerate(parameters, start=1):
            errors.extend(parameter_errors(parameter, f"{field_name}.parameters[{parameter_index}]", record_references))
    except PreparationError as error:
        errors.append(str(error))
    return errors, step_key
