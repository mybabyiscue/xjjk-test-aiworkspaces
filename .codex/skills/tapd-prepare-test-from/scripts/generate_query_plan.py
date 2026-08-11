"""Generate output/test_preparation/query_plan.json from code-review evidence.

Mapping rules:
- connection is copied from the user-confirmed --connection-name argument.
- queries[] is generated from raw/table_evidence.json, whose hash is verified
  through evidence_index.json before use.
- qualified_name is split into database and table.
- fields[].name becomes the explicit SELECT column list.
- case_ids becomes the human-readable purpose and coverage metadata.
- source_evidence points back to table_information.md and the raw table entity
  anchor; no table, column, route, module, or tenant value is known by this
  script unless it appears in the evidence inputs.
- WHERE conditions are not invented. When evidence has no executable condition,
  condition_status is marked as missing while the read-only LIMIT query remains
  explicit and auditable.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from evidence_transformer import (
    JsonObject,
    build_select_sql,
    evidence_reference,
    field_names,
    load_generation_sources,
    read_schema_config,
    require_text_field,
    split_qualified_name,
    stable_reference,
    write_validated_artifact,
)
from preparation_contract import PreparationError


def parse_arguments() -> argparse.Namespace:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Generate a read-only query plan from code-review table evidence.")
    parser.add_argument("--evidence-index", required=True)
    parser.add_argument("--unit-interface-evidence", required=True)
    parser.add_argument("--core-interface-evidence", required=True)
    parser.add_argument("--table-evidence", required=True)
    parser.add_argument("--connection-name", required=True)
    parser.add_argument("--max-rows", required=True, type=int)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def build_query_plan(
    evidence_index_path: Path,
    unit_path: Path,
    core_path: Path,
    table_path: Path,
    connection_name: str,
    max_rows: int,
    skill_dir: Path,
) -> JsonObject:
    if max_rows < 1 or max_rows > 100:
        raise PreparationError("--max-rows must be between 1 and 100.")
    sources: JsonObject = load_generation_sources(evidence_index_path, unit_path, core_path, table_path)
    raw_tables: object = sources.get("tables")
    if not isinstance(raw_tables, list):
        raise PreparationError("Loaded table evidence must be an array.")
    missing_items: list[JsonObject] = []
    queries: list[JsonObject] = []
    for index, raw_table in enumerate(raw_tables, start=1):
        if not isinstance(raw_table, dict):
            missing_items.append({"item": f"tables[{index}]", "reason": "table evidence item is not an object"})
            continue
        qualified_name: str = require_text_field(raw_table.get("qualified_name"), f"tables[{index}].qualified_name", missing_items)
        database, table = split_qualified_name(qualified_name, missing_items)
        columns: list[str] = field_names(raw_table.get("fields"), missing_items, qualified_name)
        query_reference: str = stable_reference("QRY", qualified_name)
        cases: object = raw_table.get("case_ids")
        case_ids: list[str] = [item for item in cases if isinstance(item, str) and item.strip()] if isinstance(cases, list) else []
        if not case_ids:
            missing_items.append({"item": qualified_name, "field": "case_ids", "reason": "table evidence has no case coverage"})
        anchor: str = qualified_name if qualified_name != "missing" else f"table_row_{index}"
        sql: str = build_select_sql(database, table, columns, max_rows)
        source_evidence: JsonObject = evidence_reference(
            "table_information.md",
            table_path,
            "data",
            anchor,
            "Table and column evidence is derived from the code-review table document.",
        )
        query: JsonObject = {
            "query_reference": query_reference,
            "database": database,
            "table": table,
            "purpose": "Read verified rows for covered cases: " + ", ".join(case_ids) if case_ids else "missing",
            "max_rows": max_rows,
            "sql": sql,
            "source_evidence": source_evidence,
            "case_ids": case_ids,
            "selected_columns": columns,
            "condition_status": "missing",
            "condition_missing_reason": "No executable WHERE condition value is present in the reviewed evidence.",
        }
        if sql == "missing":
            query["generation_status"] = "missing"
            missing_items.append({"item": query_reference, "field": "sql", "reason": "database, table, or column evidence is incomplete"})
        else:
            query["generation_status"] = "ready"
        queries.append(query)
    generation_status: str = "ready" if queries and not any(item.get("field") == "sql" for item in missing_items) else "blocked"
    payload: JsonObject = {
        "connection": connection_name,
        "queries": queries,
        "generation_report": {
            "status": generation_status,
            "missing_items": missing_items,
            "source_artifacts": sources.get("source_artifacts", {}),
        },
    }
    schema: JsonObject = read_schema_config(skill_dir)
    return validate_query_plan_payload(payload, schema)


def validate_query_plan_payload(payload: JsonObject, schema: JsonObject) -> JsonObject:
    queries: object = payload.get("queries")
    if not isinstance(queries, list) or not queries:
        report: object = payload.get("generation_report")
        if isinstance(report, dict):
            missing_items: object = report.get("missing_items")
            if isinstance(missing_items, list):
                missing_items.append({"item": "queries", "reason": "no table evidence produced a query"})
        raise PreparationError("query_plan.json generation failed: no query entries were produced.")
    return payload


def main() -> int:
    arguments: argparse.Namespace = parse_arguments()
    output_path: Path = Path(arguments.output)
    skill_dir: Path = Path(__file__).resolve().parents[1]
    payload: JsonObject = build_query_plan(
        Path(arguments.evidence_index),
        Path(arguments.unit_interface_evidence),
        Path(arguments.core_interface_evidence),
        Path(arguments.table_evidence),
        arguments.connection_name,
        int(arguments.max_rows),
        skill_dir,
    )
    schema: JsonObject = read_schema_config(skill_dir)
    write_validated_artifact(output_path, payload, schema, "query_plan")
    print(f"Generated query plan: {len(payload['queries'])} queries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
