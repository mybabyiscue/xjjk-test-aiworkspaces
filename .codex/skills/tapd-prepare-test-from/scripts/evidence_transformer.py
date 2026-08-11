"""Shared evidence-to-preparation transformation helpers.

Mapping rules:
- Evidence identity comes from evidence_index.json. The transformer verifies the
  hash of every consumed artifact before deriving any output.
- Interface values are copied from raw/testcase_interface_evidence.json entries:
  service_id -> service, class_name#method_name -> operation, http_method ->
  method, route -> path, return_type -> response_type, params -> parameters.
- Table values are copied from raw/table_evidence.json entries:
  qualified_name -> database/table, fields[].name -> selectable columns,
  case_ids -> query purpose coverage, source_file/source_line -> evidence anchor.
- Markdown files are read and hash-checked as human-review evidence. Their table
  headers are validated so a changed upstream document shape blocks generation.
- Parameter data sources are resolved only by structural name matching between
  interface parameter names and table field names. Unmatched parameters are
  marked as unresolved with explicit missing evidence instead of receiving
  placeholder values.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from preparation_contract import PreparationError, file_sha256, read_json_object, stable_case_key, write_json_object

JsonObject = dict[str, object]


def read_schema_config(skill_dir: Path) -> JsonObject:
    schema_path: Path = skill_dir / "references" / "generated-artifact-schema.json"
    return read_json_object(schema_path)


def artifact_hashes(evidence_index: JsonObject) -> dict[str, str]:
    artifacts: object = evidence_index.get("artifacts")
    if not isinstance(artifacts, dict):
        raise PreparationError("evidence_index.json.artifacts must be an object.")
    result: dict[str, str] = {}
    for key, value in artifacts.items():
        if isinstance(key, str) and isinstance(value, str) and value.strip():
            result[key] = value.strip()
    return result


def require_artifact_path(evidence_index_path: Path, artifact_name: str, hashes: dict[str, str]) -> Path:
    if artifact_name not in hashes:
        raise PreparationError(f"Required code-review artifact is missing from evidence_index.json: {artifact_name}")
    artifact_path: Path = evidence_index_path.parent / artifact_name
    if not artifact_path.is_file():
        raise PreparationError(f"Required code-review artifact file is missing: {artifact_path}")
    actual_hash: str = file_sha256(artifact_path)
    if actual_hash != hashes[artifact_name]:
        raise PreparationError(f"Code-review artifact hash mismatch: {artifact_name}")
    return artifact_path


def read_raw_artifact(evidence_index_path: Path, artifact_name: str, hashes: dict[str, str], root_key: str) -> list[object]:
    path: Path = require_artifact_path(evidence_index_path, artifact_name, hashes)
    payload: JsonObject = read_json_object(path)
    value: object = payload.get(root_key)
    if not isinstance(value, list):
        raise PreparationError(f"{artifact_name}.{root_key} must be an array.")
    if not value:
        raise PreparationError(f"{artifact_name}.{root_key} has no evidence records.")
    return value


def validate_markdown_artifact(evidence_index_path: Path, artifact_name: str, hashes: dict[str, str], required_headers: list[str]) -> Path:
    path: Path = require_artifact_path(evidence_index_path, artifact_name, hashes)
    text: str = path.read_text(encoding="utf-8-sig")
    headers: list[str] = first_markdown_table_headers(text)
    missing_headers: list[str] = [header for header in required_headers if header not in headers]
    if missing_headers:
        raise PreparationError(f"{artifact_name} markdown table is missing headers: {', '.join(missing_headers)}")
    return path


def first_markdown_table_headers(text: str) -> list[str]:
    for line in text.splitlines():
        stripped: str = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells: list[str] = [cell.strip() for cell in stripped.strip("|").split("|")]
        if cells and not all(re.fullmatch(r"-+", cell) for cell in cells):
            return cells
    return []


def load_generation_sources(evidence_index_path: Path, unit_path: Path, core_path: Path, table_path: Path) -> JsonObject:
    evidence_index: JsonObject = read_json_object(evidence_index_path)
    hashes: dict[str, str] = artifact_hashes(evidence_index)
    actual_unit_path: Path = validate_markdown_artifact(
        evidence_index_path,
        "unit_test_interfaces.md",
        hashes,
        ["用例编号", "方法签名", "代码位置"],
    )
    actual_core_path: Path = validate_markdown_artifact(
        evidence_index_path,
        "core_process_interfaces.md",
        hashes,
        ["用例编号", "接口名称/描述", "接口类型与地址", "请求参数", "代码位置"],
    )
    actual_table_path: Path = validate_markdown_artifact(
        evidence_index_path,
        "table_information.md",
        hashes,
        ["用例编号", "库.表名", "关键字段", "读/写类型"],
    )
    if actual_unit_path.resolve() != unit_path.resolve():
        raise PreparationError("unit_test_interfaces.md path does not match evidence_index.json location.")
    if actual_core_path.resolve() != core_path.resolve():
        raise PreparationError("core_process_interfaces.md path does not match evidence_index.json location.")
    if actual_table_path.resolve() != table_path.resolve():
        raise PreparationError("table_information.md path does not match evidence_index.json location.")
    interfaces: list[object] = read_raw_artifact(evidence_index_path, "raw/testcase_interface_evidence.json", hashes, "interfaces")
    call_chains: list[object] = read_raw_artifact(evidence_index_path, "raw/call_chain_evidence.json", hashes, "call_chains")
    tables: list[object] = read_raw_artifact(evidence_index_path, "raw/table_evidence.json", hashes, "tables")
    return {
        "evidence_index": evidence_index,
        "hashes": hashes,
        "interfaces": interfaces,
        "call_chains": call_chains,
        "tables": tables,
        "source_artifacts": {
            "evidence_index.json": str(evidence_index_path),
            "unit_test_interfaces.md": str(unit_path),
            "core_process_interfaces.md": str(core_path),
            "table_information.md": str(table_path),
            "raw/testcase_interface_evidence.json": str(evidence_index_path.parent / "raw" / "testcase_interface_evidence.json"),
            "raw/call_chain_evidence.json": str(evidence_index_path.parent / "raw" / "call_chain_evidence.json"),
            "raw/table_evidence.json": str(evidence_index_path.parent / "raw" / "table_evidence.json"),
        },
    }


def require_text_field(value: object, field_name: str, missing_items: list[JsonObject]) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    missing_items.append({"field": field_name, "reason": "missing"})
    return "missing"


def split_qualified_name(value: str, missing_items: list[JsonObject]) -> tuple[str, str]:
    parts: list[str] = value.split(".")
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        missing_items.append({"field": "qualified_name", "value": value, "reason": "database.table format is missing"})
        return "missing", "missing"
    return parts[0].strip(), parts[1].strip()


def field_names(raw_fields: object, missing_items: list[JsonObject], qualified_name: str) -> list[str]:
    if not isinstance(raw_fields, list) or not raw_fields:
        missing_items.append({"field": "fields", "table": qualified_name, "reason": "table field evidence is missing"})
        return []
    names: list[str] = []
    for raw_field in raw_fields:
        if not isinstance(raw_field, dict):
            continue
        raw_name: object = raw_field.get("name")
        if isinstance(raw_name, str) and raw_name.strip():
            names.append(raw_name.strip())
    if not names:
        missing_items.append({"field": "fields.name", "table": qualified_name, "reason": "no selectable column names were found"})
    return names


def stable_reference(prefix: str, value: str) -> str:
    digest: str = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def quote_mysql_identifier(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_]+", value) is None:
        raise PreparationError(f"Unsafe SQL identifier in evidence: {value}")
    return f"`{value}`"


def build_select_sql(database: str, table: str, columns: list[str], max_rows: int) -> str:
    if database == "missing" or table == "missing" or not columns:
        return "missing"
    column_sql: str = ", ".join(quote_mysql_identifier(column) for column in columns)
    return f"SELECT {column_sql} FROM {quote_mysql_identifier(database)}.{quote_mysql_identifier(table)} LIMIT {max_rows}"


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def table_field_lookup(tables: list[object], query_references: dict[str, str]) -> dict[str, JsonObject]:
    lookup: dict[str, JsonObject] = {}
    for raw_table in tables:
        if not isinstance(raw_table, dict):
            continue
        qualified_name: object = raw_table.get("qualified_name")
        if not isinstance(qualified_name, str):
            continue
        query_reference: str = query_references.get(qualified_name, "")
        raw_fields: object = raw_table.get("fields")
        if not isinstance(raw_fields, list):
            continue
        for raw_field in raw_fields:
            if not isinstance(raw_field, dict):
                continue
            name: object = raw_field.get("name")
            if isinstance(name, str) and name.strip() and query_reference:
                lookup[normalize_name(name)] = {
                    "query_reference": query_reference,
                    "field_name": name.strip(),
                    "qualified_name": qualified_name,
                }
    return lookup


def evidence_reference(source_file: str, source_path: Path, rule_type: str, anchor: str, summary: str) -> JsonObject:
    return {
        "source_type": "code",
        "source_file": source_file,
        "source_location": anchor,
        "source_sha256": file_sha256(source_path),
        "rule_type": rule_type,
        "rule_summary": summary,
        "anchor": anchor,
    }


def call_chain_by_signature(call_chains: list[object]) -> dict[str, JsonObject]:
    result: dict[str, JsonObject] = {}
    for raw_chain in call_chains:
        if not isinstance(raw_chain, dict):
            continue
        signature: object = raw_chain.get("signature")
        if isinstance(signature, str) and signature.strip():
            result[signature.strip()] = raw_chain
    return result


def case_map_from_tapd_cases(tapd_cases_path: Path) -> dict[str, JsonObject]:
    payload: JsonObject = read_json_object(tapd_cases_path)
    raw_cases: object = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise PreparationError("tapd_cases.json.cases must be an array.")
    result: dict[str, JsonObject] = {}
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            continue
        case_id: object = raw_case.get("case_id")
        if isinstance(case_id, str) and case_id.strip():
            result[case_id.strip()] = raw_case
    if not result:
        raise PreparationError("tapd_cases.json has no usable case_id values.")
    return result


def validate_generated_artifact_schema(artifact: JsonObject, schema: JsonObject, schema_key: str) -> list[str]:
    errors: list[str] = []
    raw_schema: object = schema.get(schema_key)
    if not isinstance(raw_schema, dict):
        return [f"Schema config is missing section: {schema_key}"]
    required_fields: object = raw_schema.get("required_object_fields")
    if isinstance(required_fields, list):
        for field in required_fields:
            if isinstance(field, str) and field not in artifact:
                errors.append(f"{schema_key}.{field} is missing.")
    raw_report: object = artifact.get("generation_report")
    if not isinstance(raw_report, dict):
        errors.append(f"{schema_key}.generation_report must be an object.")
    else:
        report_fields: object = raw_schema.get("required_generation_report_fields")
        if isinstance(report_fields, list):
            for field in report_fields:
                if isinstance(field, str) and field not in raw_report:
                    errors.append(f"{schema_key}.generation_report.{field} is missing.")
    if schema_key == "query_plan":
        errors.extend(validate_query_items(artifact, raw_schema))
    if schema_key == "model_mapping":
        errors.extend(validate_model_mapping_items(artifact, raw_schema))
    return errors


def validate_query_items(artifact: JsonObject, raw_schema: JsonObject) -> list[str]:
    errors: list[str] = []
    queries: object = artifact.get("queries")
    if not isinstance(queries, list):
        return ["query_plan.queries must be an array."]
    required_query_fields: object = raw_schema.get("required_query_fields")
    for index, raw_query in enumerate(queries, start=1):
        if not isinstance(raw_query, dict):
            errors.append(f"query_plan.queries[{index}] must be an object.")
            continue
        if isinstance(required_query_fields, list):
            for field in required_query_fields:
                if isinstance(field, str) and field not in raw_query:
                    errors.append(f"query_plan.queries[{index}].{field} is missing.")
    return errors


def validate_model_mapping_items(artifact: JsonObject, raw_schema: JsonObject) -> list[str]:
    errors: list[str] = []
    for field in ("interface_cases", "non_interface_cases", "core_flows"):
        if not isinstance(artifact.get(field), list):
            errors.append(f"model_mapping.{field} must be an array.")
    data_preparation: object = artifact.get("data_preparation")
    if not isinstance(data_preparation, dict):
        errors.append("model_mapping.data_preparation must be an object.")
    else:
        required_fields: object = raw_schema.get("required_data_preparation_fields")
        if isinstance(required_fields, list):
            for field in required_fields:
                if isinstance(field, str) and field not in data_preparation:
                    errors.append(f"model_mapping.data_preparation.{field} is missing.")
    if not isinstance(artifact.get("core_flow_blocker_reason"), str):
        errors.append("model_mapping.core_flow_blocker_reason must be a string.")
    return errors


def write_validated_artifact(path: Path, payload: JsonObject, schema: JsonObject, schema_key: str) -> None:
    errors: list[str] = validate_generated_artifact_schema(payload, schema, schema_key)
    if errors:
        raise PreparationError("; ".join(errors))
    write_json_object(path, payload)
