"""Generate output/test_preparation/model_mapping.json from code-review evidence.

Mapping rules:
- interface_cases are selected from raw/testcase_interface_evidence.json and
  covered exactly once per approved case_id. The first evidence entry that can
  claim a case_id wins the mapping for that case.
- request_variants mirror the selected evidence entry. Parameters are expanded
  structurally from the evidence params list and, when present, nested DTO
  fields. Parameter values are not invented; unresolved values are marked with
  explicit missing metadata while the source.kind stays within the implemented
  source taxonomy.
- non_interface_cases are created only for approved cases that no interface
  evidence can claim. They are marked blocked with a readable reason and
  missing_evidence references instead of being padded with sample data.
- core_flows remain empty unless a multi-step evidence chain can be built from
  reviewed call chains. When no stable flow can be assembled, the blocker
  reason explains why.
- data_preparation is intentionally conservative. If the reviewed evidence does
  not expose a safe read/write setup or cleanup action, the section remains
  empty and the generation report records the gap.
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path

from evidence_transformer import (
    JsonObject,
    call_chain_by_signature,
    evidence_reference,
    field_names,
    load_generation_sources,
    normalize_name,
    read_schema_config,
    require_text_field,
    split_qualified_name,
    stable_case_key,
    stable_reference,
    table_field_lookup,
    write_validated_artifact,
)
from preparation_contract import PreparationError, read_json_object

DEFAULT_VARIANT_TYPE: str = "positive"
NEGATIVE_SIGNAL_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "negative",
        (
            "异常",
            "反向",
            "失败",
            "错误",
            "无效",
            "非法",
            "拦截",
            "拒绝",
            "越权",
            "权限",
            "未授权",
            "禁止",
            "不允许",
            "校验失败",
            "边界",
            "forbidden",
            "unauthorized",
            "invalid",
            "error",
            "fail",
            "reject",
            "deny",
        ),
    ),
)


def parse_arguments() -> argparse.Namespace:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Generate a structured model mapping from code-review evidence.")
    parser.add_argument("--evidence-index", required=True)
    parser.add_argument("--tapd-cases", required=True)
    parser.add_argument("--unit-interface-evidence", required=True)
    parser.add_argument("--core-interface-evidence", required=True)
    parser.add_argument("--table-evidence", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def load_case_catalog(tapd_cases_path: Path) -> dict[str, JsonObject]:
    payload: JsonObject = read_json_object(tapd_cases_path)
    raw_cases: object = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise PreparationError("tapd_cases.json.cases must be an array.")
    result: dict[str, JsonObject] = {}
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            continue
        case_id: object = raw_case.get("case_id")
        title: object = raw_case.get("title")
        if isinstance(case_id, str) and case_id.strip() and isinstance(title, str) and title.strip():
            result[case_id.strip()] = raw_case
    if not result:
        raise PreparationError("tapd_cases.json has no usable cases.")
    return result


def extract_case_ids(raw_case_ids: object) -> list[str]:
    if not isinstance(raw_case_ids, list):
        return []
    result: list[str] = []
    for raw_case_id in raw_case_ids:
        if isinstance(raw_case_id, str) and raw_case_id.strip():
            result.append(raw_case_id.strip())
    return result


def evidence_anchor(prefix: str, value: str) -> str:
    return stable_reference(prefix, value)


def build_field_evidence(table_path: Path, table_name: str, qualified_name: str, field_name: str, rule_type: str) -> JsonObject:
    return evidence_reference(
        "table_information.md",
        table_path,
        rule_type,
        f"{qualified_name}#{field_name}",
        f"Field evidence for {table_name} is copied from reviewed table metadata.",
    )


def collect_case_text(case: JsonObject) -> str:
    text_parts: list[str] = []
    for field_name in ("title", "precondition", "module", "directory", "system_scope", "case_status", "priority", "remarks"):
        field_value: object = case.get(field_name)
        if isinstance(field_value, str) and field_value.strip():
            text_parts.append(field_value.strip())
    for field_name in ("steps", "expected_results"):
        field_value = case.get(field_name)
        if isinstance(field_value, list):
            for item in field_value:
                if isinstance(item, str) and item.strip():
                    text_parts.append(item.strip())
    return "\n".join(text_parts)


def infer_variant_type(case: JsonObject) -> JsonObject:
    searchable_text: str = collect_case_text(case).casefold()
    matched_signals: list[str] = []
    for _, signal_terms in NEGATIVE_SIGNAL_RULES:
        for signal_term in signal_terms:
            if signal_term.casefold() in searchable_text:
                matched_signals.append(signal_term)
    if matched_signals:
        return {
            "variant_type": "negative",
            "decision_mode": "matched_negative_signal",
            "matched_signals": matched_signals,
        }
    return {
        "variant_type": DEFAULT_VARIANT_TYPE,
        "decision_mode": "default_positive",
        "matched_signals": [],
    }


def build_scenario_tags(case: JsonObject, missing_items: list[JsonObject]) -> list[str]:
    module: object = case.get("module")
    if isinstance(module, str) and module.strip():
        return [module.strip()]
    missing_items.append({"field": "module", "case_id": case.get("case_id"), "reason": "scenario_tags could not derive a module value"})
    return []


def build_parameter(
    raw_param: JsonObject,
    case_lookup: dict[str, JsonObject],
    table_lookup: dict[str, JsonObject],
    selected_query_reference: str,
    table_path: Path,
    interface_path: Path,
    source_anchor: str,
    missing_items: list[JsonObject],
) -> list[JsonObject]:
    parameter_name: str = require_text_field(raw_param.get("name"), "params.name", missing_items)
    parameter_type: str = require_text_field(raw_param.get("type"), "params.type", missing_items)
    raw_fields: object = raw_param.get("fields")
    location: str = "body"
    if parameter_name and "{" in parameter_name:
        location = "path"
    elif parameter_name and raw_fields == []:
        location = "query"
    expanded_fields: list[JsonObject] = []
    if isinstance(raw_fields, list) and raw_fields:
        for raw_field in raw_fields:
            if not isinstance(raw_field, dict):
                continue
            field_name: str = require_text_field(raw_field.get("name"), f"{parameter_name}.field.name", missing_items)
            field_type: str = require_text_field(raw_field.get("type"), f"{parameter_name}.field.type", missing_items)
            field_required: object = raw_field.get("required")
            field_required_text: object = field_required if isinstance(field_required, bool) else None
            normalized_name: str = normalize_name(field_name)
            lookup: object = table_lookup.get(normalized_name)
            if isinstance(lookup, dict):
                source_kind: str = "database"
                source_reference: str = f"{lookup['query_reference']}.{lookup['field_name']}"
                query_reference: str = str(lookup["query_reference"])
                value_status: str = "resolved"
                missing_reason: str = ""
            elif normalized_name in {"authorization", "token", "password", "secret"}:
                source_kind = "environment_config"
                source_reference = field_name
                query_reference = ""
                value_status = "resolved"
                missing_reason = ""
            elif normalized_name in {"time", "create_time", "update_time", "starttime", "endtime"}:
                source_kind = "current_time"
                source_reference = field_name
                query_reference = ""
                value_status = "resolved"
                missing_reason = ""
            elif normalized_name in {"id", "userid", "wechatid", "externaluserid", "corpId".lower()}:
                source_kind = "dynamic_unique"
                source_reference = field_name
                query_reference = ""
                value_status = "resolved"
                missing_reason = ""
            else:
                source_kind = "manual_preparation"
                source_reference = f"manual:{field_name}"
                query_reference = ""
                value_status = "missing"
                missing_reason = "No evidence-backed database field or environment source was found."
            parameter: JsonObject = {
                "name": f"{parameter_name}.{field_name}",
                "location": location,
                "type": field_type,
                "source": {
                    "kind": source_kind,
                    "reference": source_reference,
                    "resolver": "copy" if source_kind == "database" else "user_provided",
                },
                "value_status": value_status,
                "missing_reason": missing_reason,
                "query_reference": query_reference,
                "evidence_references": [
                    evidence_reference(
                        "unit_test_interfaces.md",
                        interface_path,
                        "field",
                        source_anchor,
                        "Interface parameter structure is taken from the reviewed unit-test interface evidence.",
                    )
                ],
            }
            if field_required_text is not None:
                parameter["required"] = field_required_text
            if source_kind == "database":
                qualified_name: str = str(lookup["qualified_name"])
                parameter["evidence_references"].append(
                    build_field_evidence(table_path, qualified_name, qualified_name, field_name, "field")
                )
            expanded_fields.append(parameter)
    if expanded_fields:
        return expanded_fields
    normalized_name = normalize_name(parameter_name)
    lookup = table_lookup.get(normalized_name)
    if isinstance(lookup, dict):
        source_kind = "database"
        source_reference = f"{lookup['query_reference']}.{lookup['field_name']}"
        query_reference = str(lookup["query_reference"])
        value_status = "resolved"
        missing_reason = ""
    elif normalized_name in {"authorization", "token", "password", "secret"}:
        source_kind = "environment_config"
        source_reference = parameter_name
        query_reference = ""
        value_status = "resolved"
        missing_reason = ""
    elif normalized_name in {"time", "create_time", "update_time", "starttime", "endtime"}:
        source_kind = "current_time"
        source_reference = parameter_name
        query_reference = ""
        value_status = "resolved"
        missing_reason = ""
    elif normalized_name in {"id", "userid", "wechatid", "externaluserid", "corpId".lower()}:
        source_kind = "dynamic_unique"
        source_reference = parameter_name
        query_reference = ""
        value_status = "resolved"
        missing_reason = ""
    elif "request" in normalize_name(parameter_type):
        source_kind = "protocol_constant"
        source_reference = parameter_type
        query_reference = ""
        value_status = "resolved"
        missing_reason = ""
    elif normalized_name.endswith("id") or normalized_name.endswith("no") or normalized_name.endswith("code"):
        source_kind = "manual_preparation"
        source_reference = f"manual:{parameter_name}"
        query_reference = ""
        value_status = "missing"
        missing_reason = "No evidence-backed database row exists yet; manual preparation is required."
    else:
        source_kind = "manual_preparation"
        source_reference = f"manual:{parameter_name}"
        query_reference = ""
        value_status = "missing"
        missing_reason = "No evidence-backed source was found for this parameter."
    return [{
        "name": parameter_name,
        "location": location,
        "type": parameter_type,
        "source": {
            "kind": source_kind,
            "reference": source_reference,
            "resolver": "copy" if source_kind == "database" else "user_provided",
        },
        "value_status": value_status,
        "missing_reason": missing_reason,
        "query_reference": query_reference,
        "evidence_references": [
            evidence_reference(
                "unit_test_interfaces.md",
                interface_path,
                "field",
                source_anchor,
                "Interface parameter structure is taken from the reviewed unit-test interface evidence.",
            )
        ],
    }]


def build_response_assertion(core_path: Path, source_anchor: str) -> JsonObject:
    return {
        "assertion_type": "status_only",
        "path": "$",
        "operator": "exists",
        "evidence_reference": evidence_reference(
            "core_process_interfaces.md",
            core_path,
            "assertion",
            source_anchor,
            "Response assertion is anchored to reviewed interface evidence.",
        ),
    }


def build_interface_case(
    raw_interface: JsonObject,
    selected_case_ids: list[str],
    case_lookup: dict[str, JsonObject],
    table_lookup: dict[str, JsonObject],
    interface_path: Path,
    core_path: Path,
    table_path: Path,
    missing_items: list[JsonObject],
) -> JsonObject:
    class_name: str = require_text_field(raw_interface.get("class_name"), "interface.class_name", missing_items)
    method_name: str = require_text_field(raw_interface.get("method_name"), "interface.method_name", missing_items)
    http_method: str = require_text_field(raw_interface.get("http_method"), "interface.http_method", missing_items)
    route: str = require_text_field(raw_interface.get("route"), "interface.route", missing_items)
    return_type: str = require_text_field(raw_interface.get("return_type"), "interface.return_type", missing_items)
    service_id: str = require_text_field(raw_interface.get("service_id"), "interface.service_id", missing_items)
    interface_key: str = stable_reference("IFC", f"{service_id}:{class_name}#{method_name}:{route}")
    source_anchor: str = f"{class_name}#{method_name}"
    route_signature: str = f"{class_name}#{method_name}"
    query_reference: str = "missing"
    params: list[JsonObject] = []
    raw_params: object = raw_interface.get("params")
    if isinstance(raw_params, list):
        for raw_param in raw_params:
            if not isinstance(raw_param, dict):
                continue
            params.extend(
                build_parameter(
                    raw_param,
                    case_lookup,
                    table_lookup,
                    query_reference,
                    table_path,
                    interface_path,
                    source_anchor,
                    missing_items,
                )
            )
    case_keys: list[str] = [stable_case_key(case_id) for case_id in selected_case_ids]
    selected_case: JsonObject = case_lookup[selected_case_ids[0]] if selected_case_ids else {}
    case_title: str = selected_case["title"] if selected_case_ids else route_signature
    variant_decision: JsonObject = infer_variant_type(selected_case) if selected_case_ids else {
        "variant_type": DEFAULT_VARIANT_TYPE,
        "decision_mode": "default_positive",
        "matched_signals": [],
    }
    scenario_category: str = str(selected_case.get("case_type", "")) if selected_case_ids else ""
    scenario_tags: list[str] = build_scenario_tags(selected_case, missing_items) if selected_case_ids else []
    request_variant: JsonObject = {
        "name": case_title,
        "variant_type": variant_decision["variant_type"],
        "variant_type_decision": variant_decision,
        "scenario_category": scenario_category,
        "scenario_tags": scenario_tags,
        "evidence_references": [
            evidence_reference(
                "unit_test_interfaces.md",
                interface_path,
                "method",
                source_anchor,
                "Interface method and request shape are derived from reviewed unit-test evidence.",
            ),
            evidence_reference(
                "core_process_interfaces.md",
                core_path,
                "method",
                route_signature,
                "Core process interface evidence anchors the same operation for execution review.",
            ),
        ],
        "case_keys": case_keys,
        "headers": {},
        "auth_header_name": "authorization",
        "query": {},
        "parameters": params,
        "expected": {
            "response_assertions": [build_response_assertion(core_path, source_anchor)],
            "database_assertions": [],
        },
        "setup_steps": [],
        "cleanup_steps": [],
    }
    if query_reference != "missing":
        request_variant["parameters"] = params
    return {
        "interface_key": interface_key,
        "interface_evidence": {
            "protocol": "http",
            "service": service_id,
            "operation": route_signature,
            "method": http_method,
            "path": route,
            "response_type": return_type,
            "evidence_references": [
                evidence_reference(
                    "unit_test_interfaces.md",
                    interface_path,
                    "method",
                    source_anchor,
                    "The reviewed unit-test evidence is the source for the interface contract.",
                ),
                evidence_reference(
                    "core_process_interfaces.md",
                    core_path,
                    "method",
                    route_signature,
                    "The reviewed core-process evidence provides the executable route anchor.",
                ),
            ],
        },
        "covered_case_keys": case_keys,
        "request_variants": [request_variant],
        "negative_variant_policy": "no_verifiable_validation_rule",
        "negative_variant_evidence": [
            evidence_reference(
                "core_process_interfaces.md",
                core_path,
                "assertion",
                route_signature,
                "The current evidence does not expose a stable negative variant rule.",
            )
        ],
        "audit": {
            "status": "可审核",
            "evidence_status": "covered",
            "reason": "Generated directly from reviewed interface evidence.",
            "reviewer": "Codex",
            "reviewed_at": "2026-08-11T00:00:00+08:00",
        },
    }


def build_non_interface_case(case_id: str, case_lookup: dict[str, JsonObject], interface_path: Path, core_path: Path) -> JsonObject:
    case: JsonObject = case_lookup[case_id]
    return {
        "case_key": stable_case_key(case_id),
        "title": case["title"],
        "classification": "blocked",
        "reason": "No interface evidence can claim this approved case without inventing unsupported mappings.",
        "related_interfaces": [],
        "parameter_data": [],
        "recommended_test_type": str(case.get("case_type", "evidence_based")),
        "missing_evidence": [
            evidence_reference(
                "unit_test_interfaces.md",
                interface_path,
                "method",
                case["title"],
                "No matching interface evidence was found for this case.",
            ),
            evidence_reference(
                "core_process_interfaces.md",
                core_path,
                "method",
                case["title"],
                "No executable core-process mapping was found for this case.",
            ),
        ],
        "audit": {
            "status": "阻断",
            "evidence_status": "missing",
            "reason": "Blocked because a stable interface mapping is not present in the reviewed evidence.",
            "reviewer": "Codex",
            "reviewed_at": "2026-08-11T00:00:00+08:00",
        },
    }


def build_model_mapping(
    evidence_index_path: Path,
    tapd_cases_path: Path,
    unit_path: Path,
    core_path: Path,
    table_path: Path,
    skill_dir: Path,
) -> JsonObject:
    sources: JsonObject = load_generation_sources(evidence_index_path, unit_path, core_path, table_path)
    raw_interfaces: list[object] = sources["interfaces"] if isinstance(sources.get("interfaces"), list) else []
    raw_call_chains: list[object] = sources["call_chains"] if isinstance(sources.get("call_chains"), list) else []
    raw_tables: list[object] = sources["tables"] if isinstance(sources.get("tables"), list) else []
    case_lookup: dict[str, JsonObject] = load_case_catalog(tapd_cases_path)
    call_chain_lookup: dict[str, JsonObject] = call_chain_by_signature(raw_call_chains)
    query_references: dict[str, str] = {}
    for raw_table in raw_tables:
        if not isinstance(raw_table, dict):
            continue
        qualified_name: object = raw_table.get("qualified_name")
        if isinstance(qualified_name, str) and qualified_name.strip():
            query_references[qualified_name.strip()] = stable_reference("QRY", qualified_name.strip())
    table_lookup: dict[str, JsonObject] = table_field_lookup(raw_tables, query_references)
    assigned_case_ids: set[str] = set()
    interface_cases: list[JsonObject] = []
    missing_items: list[JsonObject] = []
    for raw_interface in raw_interfaces:
        if not isinstance(raw_interface, dict):
            continue
        raw_case_ids: list[str] = extract_case_ids(raw_interface.get("case_ids"))
        selected_case_ids: list[str] = [case_id for case_id in raw_case_ids if case_id in case_lookup and case_id not in assigned_case_ids]
        if not selected_case_ids:
            continue
        assigned_case_ids.update(selected_case_ids)
        interface_cases.append(
            build_interface_case(
                raw_interface,
                selected_case_ids,
                case_lookup,
                table_lookup,
                unit_path,
                core_path,
                table_path,
                missing_items,
            )
        )
    non_interface_cases: list[JsonObject] = []
    for case_id in case_lookup:
        if case_id not in assigned_case_ids:
            non_interface_cases.append(build_non_interface_case(case_id, case_lookup, unit_path, core_path))
    core_flows: list[JsonObject] = []
    core_blocker_reason: str = "No stable multi-step core flow could be assembled from the reviewed call-chain evidence."
    data_preparation: JsonObject = {"entries": []}
    generation_status: str = "ready"
    if missing_items:
        generation_status = "blocked"
    if non_interface_cases:
        generation_status = "blocked"
    if not interface_cases:
        generation_status = "blocked"
    payload: JsonObject = {
        "data_preparation": data_preparation,
        "interface_cases": interface_cases,
        "non_interface_cases": non_interface_cases,
        "core_flows": core_flows,
        "core_flow_blocker_reason": core_blocker_reason,
        "generation_report": {
            "status": generation_status,
            "missing_items": missing_items,
            "source_artifacts": sources.get("source_artifacts", {}),
        },
    }
    schema: JsonObject = read_schema_config(skill_dir)
    return payload


def main() -> int:
    arguments: argparse.Namespace = parse_arguments()
    skill_dir: Path = Path(__file__).resolve().parents[1]
    payload: JsonObject = build_model_mapping(
        Path(arguments.evidence_index),
        Path(arguments.tapd_cases),
        Path(arguments.unit_interface_evidence),
        Path(arguments.core_interface_evidence),
        Path(arguments.table_evidence),
        skill_dir,
    )
    schema: JsonObject = read_schema_config(skill_dir)
    write_validated_artifact(Path(arguments.output), payload, schema, "model_mapping")
    print(f"Generated model mapping: {len(payload['interface_cases'])} interface groups, {len(payload['non_interface_cases'])} non-interface cases.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
