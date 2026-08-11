"""Contract tests for the generic TAPD preparation skill."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import ast
from pathlib import Path

SCRIPTS_PATH: Path = Path(__file__).resolve().parents[1] / "scripts"
SKILL_PATH: Path = SCRIPTS_PATH.parent
sys.path.insert(0, str(SCRIPTS_PATH))

from build_api_execution_plan import EXECUTABLE_AUDIT_STATUSES, build_execution_plan
from check_no_business_hardcoding import scan_skill
from execute_read_query_plan import QueryPlanError, select_connection, validate_controlled_write_connection, validate_select
from generate_model_mapping import build_model_mapping
from generate_query_plan import build_query_plan
from preparation_contract import (
    PreparationError,
    case_catalog,
    data_preparation_errors,
    file_sha256,
    load_cases,
    stable_case_key,
    validate_scenario_category_value,
    validate_variant_type_value,
    validation_errors,
)
from security_utils import redacted_json
from validate_environment_token import atomic_update_authorization, require_token_codes


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def evidence_file(directory: Path, name: str, content: str) -> Path:
    path: Path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


def evidence(path: Path, rule_type: str) -> dict[str, object]:
    return {
        "source_type": "code",
        "source_file": path.name,
        "source_location": "operation_anchor",
        "source_sha256": file_sha256(path),
        "rule_type": rule_type,
        "rule_summary": "operation_anchor validates the rule.",
        "anchor": "operation_anchor",
    }


def valid_case(case_id: str, scenario_category: str) -> dict[str, object]:
    return {
        "case_id": case_id,
        "title": f"{scenario_category} scenario",
        "directory": "Generic",
        "requirement_id": "REQ-1",
        "case_type": "custom acceptance",
        "case_status": "ready for review",
        "priority": "P3",
        "system_scope": "generic",
        "module": "module",
        "precondition": "",
        "steps": ["Run the approved step."],
        "expected_results": ["The evidenced result is observed."],
        "requirement_points": ["requirement anchor"],
        "remarks": "",
        "scenario_category": scenario_category,
        "scenario_tags": [scenario_category],
    }


def parameter(name: str, location: str, source_kind: str, query_reference: str = "") -> dict[str, object]:
    return {
        "name": name,
        "location": location,
        "type": "string",
        "required": True,
        "value": "REAL_VALUE",
        "source": {"kind": source_kind, "reference": "operation_anchor", "resolver": "copy"},
        "query_reference": query_reference,
    }


def assertion(item: dict[str, object]) -> dict[str, object]:
    return {
        "assertion_type": "json_path",
        "path": "$.result",
        "operator": "exists",
        "evidence_reference": item,
    }


class CaseContractTests(unittest.TestCase):
    def test_accepts_non_continuous_case_ids_and_custom_values(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            path: Path = Path(raw_directory) / "tapd_cases.json"
            cases: list[dict[str, object]] = [valid_case("TAPD-777", "permission"), valid_case("CASE-X9", "state")]
            write_json(path, {"total_count": 2, "cases": cases})

            loaded: list[dict[str, object]] = load_cases(path)
            catalog: list[dict[str, object]] = case_catalog(loaded)

            self.assertEqual([item["case_id"] for item in catalog], ["TAPD-777", "CASE-X9"])
            self.assertEqual(catalog[0]["case_key"], stable_case_key("TAPD-777"))
            self.assertEqual(catalog[0]["precondition"], "")

    def test_case_key_is_stable_after_input_reorder(self) -> None:
        first: list[dict[str, object]] = case_catalog([valid_case("A-9", "boundary"), valid_case("B-2", "idempotency")])
        second: list[dict[str, object]] = case_catalog([valid_case("B-2", "idempotency"), valid_case("A-9", "boundary")])

        self.assertEqual(
            {item["case_id"]: item["case_key"] for item in first},
            {item["case_id"]: item["case_key"] for item in second},
        )

    def test_rejects_duplicate_case_id_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            path: Path = Path(raw_directory) / "tapd_cases.json"
            write_json(path, {"total_count": 2, "cases": [valid_case("DUP", "state"), valid_case("DUP", "boundary")]})
            with self.assertRaisesRegex(PreparationError, "case_id 重复"):
                load_cases(path)


class AssessmentValidationTests(unittest.TestCase):
    def test_negative_only_interface_group_does_not_require_positive_variant(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory: Path = Path(raw_directory)
            source_path: Path = evidence_file(directory, "unit.md", "operation_anchor POST /generic/order\n")
            item: dict[str, object] = evidence(source_path, "permission")
            case: dict[str, object] = valid_case("NEG-ONLY", "permission")
            case_key: str = stable_case_key("NEG-ONLY")
            assessment: dict[str, object] = {
                "source": {"testcase_hash": "hash", "code_review_run_id": "review", "input_hashes": {}},
                "case_catalog": case_catalog([case]),
                "real_data_records": [],
                "data_preparation": {"entries": []},
                "interface_cases": [
                    {
                        "interface_key": "permission_check",
                        "interface_evidence": {
                            "protocol": "http",
                            "service": "generic-service",
                            "operation": "operation_anchor",
                            "method": "POST",
                            "path": "/generic/order",
                            "response_type": "json",
                            "evidence_references": [item],
                        },
                        "covered_case_keys": [case_key],
                        "request_variants": [
                            {
                                "name": "permission denied",
                                "variant_type": "negative",
                                "scenario_category": "permission",
                                "scenario_tags": ["permission"],
                                "evidence_references": [item],
                                "case_keys": [case_key],
                                "headers": {"X-Session": "***"},
                                "auth_header_name": "X-Session",
                                "query": {},
                                "parameters": [parameter("resourceId", "path", "negative_constructed")],
                                "expected": {"http_status": 403, "response_assertions": [assertion(item)], "database_assertions": []},
                                "setup_steps": [],
                                "cleanup_steps": [],
                            }
                        ],
                        "negative_variant_policy": "covered",
                        "negative_variant_evidence": [item],
                        "audit": {"status": "可审核", "evidence_status": "verified", "reason": "linked", "reviewer": "Codex", "reviewed_at": "now"},
                    }
                ],
                "non_interface_cases": [],
                "core_flows": [],
                "core_flow_blocker_reason": "No flow dependency evidence.",
            }
            snapshot: dict[str, object] = {
                "testcase_confirmation": {"testcase_hash": "hash", "code_review_run_id": "review"},
                "input_hashes": {},
                "evidence_files": {"unit.md": str(source_path)},
            }

            self.assertEqual(validation_errors(assessment, [case], snapshot), [])

    def test_unresolved_parameter_blocks_execution_plan(self) -> None:
        item: dict[str, object] = {
            "source_type": "code",
            "source_file": "unit.md",
            "source_location": "operation_anchor",
            "rule_type": "field",
            "rule_summary": "operation_anchor",
        }
        assessment: dict[str, object] = {
            "source": {"testcase_hash": "hash", "code_review_run_id": "review"},
            "environment": {"token_error_codes": ["AUTH_EXPIRED"]},
            "data_preparation": {"entries": []},
            "interface_cases": [
                {
                    "interface_key": "blocked_request",
                    "interface_evidence": {"method": "GET", "path": "/generic/resource"},
                    "request_variants": [
                        {
                            "name": "missing data",
                            "variant_type": "positive",
                            "scenario_category": "state",
                            "scenario_tags": [],
                            "case_keys": ["case_x"],
                            "headers": {},
                            "auth_header_name": "",
                            "query": {},
                            "parameters": [parameter("id", "query", "unresolved")],
                            "expected": {"response_assertions": [assertion(item)]},
                        }
                    ],
                    "audit": {"status": "已通过"},
                }
            ],
            "core_flows": [],
        }

        plan, report = build_execution_plan(assessment, "sha")

        self.assertFalse(plan["ready"])
        self.assertIn("AUTH_EXPIRED", plan["token_error_codes"])
        self.assertEqual(report["blocker_count"], 1)

    def test_database_parameter_resolves_path_placeholder(self) -> None:
        item: dict[str, object] = {
            "source_type": "code",
            "source_file": "unit.md",
            "source_location": "operation_anchor",
            "rule_type": "assertion",
            "rule_summary": "operation_anchor",
        }
        assessment: dict[str, object] = {
            "source": {"testcase_hash": "hash", "code_review_run_id": "review"},
            "real_data_records": [
                {
                    "query_reference": "QRY_RESOURCE",
                    "connection": "鲨域测试",
                    "database": "db",
                    "table": "resource",
                    "executed_at": "2026-08-11T00:00:00+08:00",
                    "purpose": "test",
                    "fields": ["resourceId"],
                    "filters": {"sql": "SELECT resourceId FROM resource"},
                    "row_count": 1,
                    "records": [{"resourceId": "ABC123"}],
                }
            ],
            "data_preparation": {"entries": []},
            "interface_cases": [
                {
                    "interface_key": "path_parameter_request",
                    "interface_evidence": {"method": "GET", "path": "/generic/resource/{resourceId}"},
                    "request_variants": [
                        {
                            "name": "path parameter",
                            "variant_type": "positive",
                            "scenario_category": "功能测试",
                            "scenario_tags": [],
                            "case_keys": ["case_x"],
                            "headers": {},
                            "auth_header_name": "",
                            "query": {},
                            "parameters": [
                                {
                                    "name": "resourceId",
                                    "location": "path",
                                    "type": "string",
                                    "required": True,
                                    "source": {
                                        "kind": "database",
                                        "reference": "QRY_RESOURCE.resourceId",
                                        "resolver": "copy",
                                    },
                                    "query_reference": "QRY_RESOURCE",
                                }
                            ],
                            "expected": {
                                "response_assertions": [assertion(item)],
                                "database_assertions": [],
                            },
                            "setup_steps": [],
                            "cleanup_steps": [],
                        }
                    ],
                    "audit": {"status": next(iter(EXECUTABLE_AUDIT_STATUSES))},
                }
            ],
            "core_flows": [],
        }

        plan, report = build_execution_plan(assessment, "sha")

        self.assertTrue(report["ready"])
        self.assertEqual(plan["requests"][0]["path"], "/generic/resource/ABC123")
        self.assertEqual(plan["requests"][0]["query"], {})

    def test_variant_type_and_scenario_category_validate_independently(self) -> None:
        variant_errors: list[str] = []
        scenario_errors: list[str] = []
        validate_variant_type_value("功能测试", "case.variant_type", variant_errors)
        validate_scenario_category_value("TODO", "case.scenario_category", scenario_errors)

        self.assertTrue(any("positive/negative" in error for error in variant_errors))
        self.assertTrue(any("占位文本" in error for error in scenario_errors))


class DataPreparationTests(unittest.TestCase):
    def test_empty_real_query_must_use_explicit_creation_or_blocking_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            source_path: Path = evidence_file(Path(raw_directory), "table.md", "operation_anchor table column\n")
            item: dict[str, object] = evidence(source_path, "data")
            errors: list[str] = data_preparation_errors(
                {
                    "entries": [
                        {
                            "id": "manual_data",
                            "case_keys": ["case_a"],
                            "strategy": "manual_create",
                            "evidence_references": [item],
                            "verification_query_reference": "Q_EMPTY",
                            "setup": None,
                            "cleanup": None,
                        }
                    ]
                },
                {"case_a"},
                set(),
                {"table.md": source_path},
            )

            self.assertTrue(any("真实记录" in error for error in errors))

    def test_sql_delete_zero_rows_allowed_only_when_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            source_path: Path = evidence_file(Path(raw_directory), "table.md", "operation_anchor cleanup\n")
            item: dict[str, object] = evidence(source_path, "cleanup")
            errors: list[str] = data_preparation_errors(
                {
                    "entries": [
                        {
                            "id": "create_data",
                            "case_keys": ["case_a"],
                            "strategy": "sql_insert",
                            "evidence_references": [item],
                            "verification_query_reference": "Q_DATA",
                            "isolation_prefix": "TEST_DATA_",
                            "setup": {
                                "id": "insert_data",
                                "type": "sql_insert",
                                "cleanup_policy": "automatic",
                                "depends_on": [],
                                "evidence_reference": item,
                                "database": "domain_db",
                                "table": "domain_table",
                                "sql": "INSERT INTO domain_db.domain_table (code) VALUES (%s)",
                                "parameters": ["TEST_DATA_1"],
                                "expected_affected_rows": 1,
                                "manifest": {"database": "domain_db", "table": "domain_table", "record": {"code": "TEST_DATA_1"}},
                            },
                            "cleanup": {
                                "id": "delete_data",
                                "type": "sql_delete",
                                "cleanup_policy": "idempotent",
                                "depends_on": ["insert_data"],
                                "evidence_reference": item,
                                "database": "domain_db",
                                "table": "domain_table",
                                "sql": "DELETE FROM domain_db.domain_table WHERE code = %s",
                                "parameters": ["TEST_DATA_1"],
                                "expected_affected_rows": 0,
                                "manifest": {"database": "domain_db", "table": "domain_table", "record": {"code": "TEST_DATA_1"}},
                            },
                        }
                    ]
                },
                {"case_a"},
                {"Q_DATA"},
                {"table.md": source_path},
            )

            self.assertEqual(errors, [])


class EvidenceGenerationTests(unittest.TestCase):
    def write_generation_fixture(self, directory: Path, table_fields: list[dict[str, object]]) -> tuple[Path, Path, Path, Path, Path]:
        review_dir: Path = directory / "code_review"
        raw_dir: Path = review_dir / "raw"
        raw_dir.mkdir(parents=True)
        unit_path: Path = review_dir / "unit_test_interfaces.md"
        core_path: Path = review_dir / "core_process_interfaces.md"
        table_path: Path = review_dir / "table_information.md"
        unit_path.write_text(
            "\n".join(
                [
                    "# Unit Evidence",
                    "",
                    "| 用例编号 | 方法签名 | 输入边界场景 | 需隔离的外部依赖 | 当前覆盖状态 | 代码位置 |",
                    "|---|---|---|---|---|---|",
                    "| TC-A | GenericController#read(resourceId:String) | evidenced | none | covered | source.java#L1 |",
                ]
            ),
            encoding="utf-8",
        )
        core_path.write_text(
            "\n".join(
                [
                    "# Core Evidence",
                    "",
                    "| 用例编号 | 接口名称/描述 | 接口类型与地址 | 请求参数 | 返回参数 | 调用链路 | 代码位置 |",
                    "|---|---|---|---|---|---|---|",
                    "| TC-A | GenericController#read | GET /generic/resource | resourceId:String | Json | GenericController#read | source.java#L1 |",
                ]
            ),
            encoding="utf-8",
        )
        table_path.write_text(
            "\n".join(
                [
                    "# Table Evidence",
                    "",
                    "| 用例编号 | 所属平台 | 库.表名 | 物理注释 | 确认等级 | 判定依据说明 | 关键字段 | 读/写类型 | 租户隔离 |",
                    "|---|---|---|---|---|---|---|---|---|",
                    "| TC-A | generic-platform | generic_db.generic_table | generic table | B | code match | resource_id, name | SELECT | none |",
                ]
            ),
            encoding="utf-8",
        )
        write_json(
            raw_dir / "testcase_interface_evidence.json",
            {
                "interfaces": [
                    {
                        "service_id": "svc",
                        "entry_type": "HTTP",
                        "http_method": "GET",
                        "route": "/generic/resource",
                        "class_name": "GenericController",
                        "method_name": "read",
                        "return_type": "Json",
                        "params": [{"name": "resourceId", "type": "String", "fields": []}],
                        "file": "source.java",
                        "line": 1,
                        "case_ids": ["TC-A"],
                    }
                ]
            },
        )
        write_json(
            raw_dir / "call_chain_evidence.json",
            {"call_chains": [{"signature": "svc::GenericController#read", "case_ids": ["TC-A"], "chain": ["GenericController#read"], "dependencies": []}]},
        )
        write_json(
            raw_dir / "table_evidence.json",
            {
                "tables": [
                    {
                        "service_id": "svc",
                        "platform": "generic-platform",
                        "table_name": "generic_table",
                        "qualified_name": "generic_db.generic_table",
                        "grade": "B",
                        "status": "confirmed",
                        "case_ids": ["TC-A"],
                        "fields": table_fields,
                        "operations": ["SELECT"],
                    }
                ]
            },
        )
        tapd_cases_path: Path = directory / "tapd_cases.json"
        write_json(
            tapd_cases_path,
            {
                "total_count": 1,
                "cases": [
                    {
                        "case_id": "TC-A",
                        "title": "generic resource read",
                        "case_type": "功能测试",
                        "module": "generic",
                    }
                ],
            },
        )
        artifacts: dict[str, str] = {
            "unit_test_interfaces.md": file_sha256(unit_path),
            "core_process_interfaces.md": file_sha256(core_path),
            "table_information.md": file_sha256(table_path),
            "raw/testcase_interface_evidence.json": file_sha256(raw_dir / "testcase_interface_evidence.json"),
            "raw/call_chain_evidence.json": file_sha256(raw_dir / "call_chain_evidence.json"),
            "raw/table_evidence.json": file_sha256(raw_dir / "table_evidence.json"),
        }
        evidence_index_path: Path = review_dir / "evidence_index.json"
        write_json(evidence_index_path, {"review_run_id": "review", "source_run_id": "source", "testcase_hash": "hash", "artifacts": artifacts})
        return evidence_index_path, tapd_cases_path, unit_path, core_path, table_path

    def test_generates_ready_query_plan_and_model_mapping_from_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            root: Path = Path(raw_directory)
            evidence_index_path, tapd_cases_path, unit_path, core_path, table_path = self.write_generation_fixture(
                root,
                [{"name": "resource_id", "data_type": "bigint"}, {"name": "name", "data_type": "varchar"}],
            )

            query_plan: dict[str, object] = build_query_plan(evidence_index_path, unit_path, core_path, table_path, "generic-readonly", 5, SKILL_PATH)
            model_mapping: dict[str, object] = build_model_mapping(evidence_index_path, tapd_cases_path, unit_path, core_path, table_path, SKILL_PATH)

            self.assertEqual(query_plan["generation_report"]["status"], "ready")
            self.assertIn("SELECT", query_plan["queries"][0]["sql"])
            self.assertNotIn("SELECT *", query_plan["queries"][0]["sql"])
            self.assertEqual(model_mapping["generation_report"]["status"], "ready")
            self.assertEqual(len(model_mapping["interface_cases"]), 1)
            self.assertEqual(model_mapping["non_interface_cases"], [])
            variant: dict[str, object] = model_mapping["interface_cases"][0]["request_variants"][0]
            self.assertEqual(variant["variant_type"], "positive")
            self.assertEqual(variant["scenario_category"], "功能测试")
            self.assertEqual(variant["scenario_tags"], ["generic"])
            self.assertEqual(variant["variant_type_decision"]["decision_mode"], "default_positive")
            plan, _report = build_execution_plan(
                {
                    "source": {"testcase_hash": "hash", "code_review_run_id": "review"},
                    "environment": {"token_error_codes": ["AUTH_EXPIRED"]},
                    "data_preparation": {"entries": []},
                    "interface_cases": [
                        {
                            "interface_key": "generic_resource",
                            "interface_evidence": {"method": "GET", "path": "/generic/resource"},
                            "request_variants": [
                                {
                                    "name": "generic resource read",
                                    "variant_type": "positive",
                                    "scenario_category": "功能测试",
                                    "scenario_tags": ["generic"],
                                    "case_keys": ["case_a"],
                                    "headers": {},
                                    "auth_header_name": "",
                                    "query": {},
                                    "parameters": [],
                                    "expected": {"response_assertions": [assertion({"source_type": "code", "source_file": "unit.md", "source_location": "operation_anchor", "source_sha256": "sha", "rule_type": "assertion", "rule_summary": "anchor", "anchor": "operation_anchor"})], "database_assertions": []},
                                    "setup_steps": [],
                                    "cleanup_steps": [],
                                }
                            ],
                            "audit": {"status": next(iter(EXECUTABLE_AUDIT_STATUSES))},
                        }
                    ],
                    "non_interface_cases": [],
                    "core_flows": [],
                },
                "sha",
            )
            self.assertEqual(plan["requests"][0]["expected"]["http_status"], 200)

    def test_query_plan_marks_missing_table_fields_without_placeholder_sql(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            root: Path = Path(raw_directory)
            evidence_index_path, _, unit_path, core_path, table_path = self.write_generation_fixture(root, [])

            query_plan: dict[str, object] = build_query_plan(evidence_index_path, unit_path, core_path, table_path, "generic-readonly", 5, SKILL_PATH)

            self.assertEqual(query_plan["generation_report"]["status"], "blocked")
            self.assertEqual(query_plan["queries"][0]["sql"], "missing")
            self.assertTrue(query_plan["generation_report"]["missing_items"])


class QuerySafetyTests(unittest.TestCase):
    def test_validate_select_accepts_limited_projection(self) -> None:
        validate_select("SELECT id, status FROM generic_db.generic_table WHERE status = %s LIMIT 20")

    def test_validate_select_rejects_unsafe_sql(self) -> None:
        rejected: tuple[str, ...] = (
            "SELECT * FROM generic_db.generic_table LIMIT 1",
            "SELECT id FROM generic_db.generic_table",
            "SELECT id FROM generic_db.generic_table; DELETE FROM generic_db.generic_table",
            "SELECT id FROM generic_db.generic_table -- hidden LIMIT 1",
            "SELECT id FROM generic_db.generic_table INTO OUTFILE 'x' LIMIT 1",
            "SELECT id FROM generic_db.generic_table FOR UPDATE LIMIT 1",
            "SELECT SLEEP(1) LIMIT 1",
            "UPDATE generic_db.generic_table SET status = 1",
            "DROP TABLE generic_db.generic_table",
            "DELETE FROM generic_db.generic_table",
        )
        for sql in rejected:
            with self.subTest(sql=sql), self.assertRaises(QueryPlanError):
                validate_select(sql)

    def test_read_connection_requires_read_only_and_mysql_adapter(self) -> None:
        with self.assertRaisesRegex(QueryPlanError, "read-only"):
            select_connection({"connections": [{"name": "writer", "enabled": True, "access_mode": "controlled-write"}]}, "writer")
        with self.assertRaisesRegex(QueryPlanError, "Unsupported database_type"):
            select_connection({"connections": [{"name": "pg", "enabled": True, "access_mode": "read-only", "database_type": "postgres"}]}, "pg")

    def test_controlled_write_connection_requires_environment_and_allowlist(self) -> None:
        config: dict[str, object] = {
            "enabled": True,
            "access_mode": "controlled-write",
            "environment_name": "example-test",
            "allowed_databases": ["domain_db"],
            "allowed_tables": ["domain_table"],
        }

        validate_controlled_write_connection(config, "example-test", "domain_db", "domain_table")
        with self.assertRaisesRegex(QueryPlanError, "allowlist"):
            validate_controlled_write_connection(config, "example-test", "domain_db", "other_table")


class TokenAndSecurityTests(unittest.TestCase):
    def test_token_error_codes_must_come_from_config(self) -> None:
        with self.assertRaisesRegex(PreparationError, "非空"):
            require_token_codes({"name": "test"})
        self.assertEqual(require_token_codes({"token_error_codes": ["AUTH_EXPIRED"]}), ["AUTH_EXPIRED"])

    def test_sensitive_values_are_redacted(self) -> None:
        output: str = redacted_json({"account": "user-a", "nested": {"password": "pass-a"}, "plain": "ok"})

        self.assertNotIn("user-a", output)
        self.assertNotIn("pass-a", output)
        self.assertIn("ok", output)

    def test_authorization_update_is_scoped_to_selected_environment(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            path: Path = Path(raw_directory) / "credentials.local.json"
            write_json(
                path,
                {
                    "environments": {
                        "example-test": {"authorization": "old-a", "password": "keep-a"},
                        "other-test": {"authorization": "old-b"},
                    }
                },
            )

            atomic_update_authorization(path, "environments.example-test", "new-a")
            payload: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(payload["environments"]["example-test"]["authorization"], "new-a")
            self.assertEqual(payload["environments"]["example-test"]["password"], "keep-a")
            self.assertEqual(payload["environments"]["other-test"]["authorization"], "old-b")


class GeneralizationAndReleaseTests(unittest.TestCase):
    def test_three_fictional_domains_do_not_depend_on_one_business_shape(self) -> None:
        domains: tuple[tuple[str, str, str], ...] = (
            ("order", "/orders/{id}", "order_table"),
            ("lesson", "/lessons/{id}/publish-state", "lesson_audit"),
            ("device", "/devices/{serial}/commands", "device_command"),
        )
        for domain_name, route, table in domains:
            with self.subTest(domain=domain_name):
                self.assertNotEqual(route, table)
                self.assertTrue(route.startswith("/"))
                self.assertNotIn("Controller", domain_name)

    def test_hardcoding_scanner_checks_entire_skill_tree(self) -> None:
        findings: list[dict[str, object]] = scan_skill(SKILL_PATH)

        self.assertEqual(findings, [])

    def test_scanner_detects_known_retired_residue_in_actual_content(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory: Path = Path(raw_directory)
            bad: Path = directory / "bad.txt"
            bad.write_text("https://" + ".".join(("api", "test", "njxjjt", "com")), encoding="utf-8")

            findings: list[dict[str, object]] = scan_skill(directory)

            self.assertEqual(findings[0]["pattern"], "retired_domain")

    def test_no_bytecode_cache_is_packaged(self) -> None:
        caches: list[Path] = [path for path in SKILL_PATH.rglob("*") if path.suffix == "." + "pyc" or ("__" + "pycache" + "__") in path.parts]

        self.assertEqual(caches, [])


class CommandSmokeTests(unittest.TestCase):
    def test_scripts_import_without_optional_database_driver(self) -> None:
        for path in SCRIPTS_PATH.glob("*.py"):
            with self.subTest(path=path.name):
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


if __name__ == "__main__":
    unittest.main()
