"""Smoke tests for the generic TAPD execution runner."""

from __future__ import annotations

import json
import copy
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType


class LocalHttpHandler(BaseHTTPRequestHandler):
    """Serve deterministic HTTP responses for integration smoke tests."""

    resources: set[str] = set()
    events: list[str] = []
    auth_headers: list[str | None] = []
    content_types: list[str | None] = []

    def read_json_body(self) -> dict[str, object]:
        self.content_types.append(self.headers.get("Content-Type"))
        content_length: int = int(self.headers.get("Content-Length", "0"))
        value: object = json.loads(self.rfile.read(content_length).decode("utf-8"))
        if not isinstance(value, dict):
            raise TypeError("Expected request JSON object.")
        return value

    def send_json(self, status: int, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        self.auth_headers.append(self.headers.get("authorization"))
        if self.path == "/forbidden":
            status: int = 403
            payload: bytes = b'{"code":"TOKEN_EXPIRED"}'
        elif self.path == "/business-failure":
            status = 200
            payload = b'{"code":"A00001","message":"service not found"}'
        elif self.path == "/assertion-failure":
            status = 200
            payload = b'{"code":1}'
        else:
            status = 200
            payload = b'{"code":0,"data":{"id":17}}'
        self.send_json(status, payload)

    def do_POST(self) -> None:
        body: dict[str, object] = self.read_json_body()
        resource_name: str = str(body["name"])
        if self.path == "/setup-failure":
            self.events.append(f"setup_failed:{resource_name}")
            self.send_json(422, b'{"code":1}')
            return
        self.resources.add(resource_name)
        self.events.append(f"setup:{resource_name}")
        self.send_json(200, b'{"code":0}')

    def do_DELETE(self) -> None:
        body: dict[str, object] = self.read_json_body()
        resource_name: str = str(body["name"])
        if self.path == "/cleanup-failure":
            self.events.append(f"cleanup_failed:{resource_name}")
            self.send_json(409, b'{"code":1}')
            return
        self.resources.discard(resource_name)
        self.events.append(f"cleanup:{resource_name}")
        self.send_json(200, b'{"code":0}')

    def log_message(self, format: str, *args: object) -> None:
        return


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def read_json(path: Path) -> dict[str, object]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected JSON object: {path}")
    return value


def assessment_payload(path: str) -> dict[str, object]:
    return {
        "source": {
            "testcase_hash": "approved-hash",
            "code_review_run_id": "review-1",
            "input_hashes": {"code_evidence": "evidence-hash"},
        },
        "data_preparation": {"entries": []},
        "interface_cases": [
            {
                "interface_key": "case_001",
                "interface_evidence": {
                    "service": "service",
                    "controller_file": "Controller.java",
                    "controller_method": "query",
                    "http_method": "GET",
                    "path": path,
                },
                "request_variants": [
                    {
                        "name": "positive",
                        "variant_type": "positive",
                        "case_keys": ["case_001"],
                        "scenario_category": "功能测试",
                        "scenario_tags": ["smoke"],
                        "headers": {"Accept": "application/json"},
                        "authorization_header": "",
                        "query": {},
                        "parameters": [],
                        "request_body": None,
                        "expected": {
                            "http_status": 200,
                            "response_assertions": [
                                {"path": "$.code", "operator": "equals", "value": 0}
                            ],
                            "database_assertions": [],
                        },
                    }
                ],
                "audit": {"status": "已通过"},
            }
        ],
        "core_flows": [],
    }


def run_builder(workspace: Path) -> subprocess.CompletedProcess[str]:
    builder_path: Path = (
        Path(__file__).resolve().parents[2]
        / "tapd-prepare-test-from"
        / "scripts"
        / "build_api_execution_plan.py"
    )
    return subprocess.run(
        [
            sys.executable,
            str(builder_path),
            "--assessment",
            str(workspace / "preparation_assessment.json"),
            "--plan",
            str(workspace / "execution_plan.json"),
            "--report",
            str(workspace / "plan_report.json"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
        check=False,
    )


def run_runner(workspace: Path, environment_name: str, expected_business_code: str) -> subprocess.CompletedProcess[str]:
    script_path: Path = Path(__file__).resolve().parents[1] / "scripts" / "run_test_execution.py"
    return subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--workspace",
            str(workspace),
            "--plan",
            "execution_plan.json",
            "--assessment",
            "preparation_assessment.json",
            "--confirmation",
            "confirmation.json",
            "--environment-config",
            "environments_config.json",
            "--environment-name",
            environment_name,
            "--expected-business-code",
            expected_business_code,
            "--output-dir",
            "output",
            "--manifest",
            "output/test_data_manifest.md",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
        check=False,
    )


def load_runner_module() -> ModuleType:
    script_path: Path = Path(__file__).resolve().parents[1] / "scripts" / "run_test_execution.py"
    spec = spec_from_file_location("run_test_execution", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load runner module: {script_path}")
    module: ModuleType = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def first_request(plan: dict[str, object]) -> dict[str, object]:
    requests: object = plan.get("requests")
    if not isinstance(requests, list) or not requests or not isinstance(requests[0], dict):
        raise TypeError("Plan must contain one request object.")
    return requests[0]


def api_data_action(action_id: str, action_type: str, method: str, path: str) -> dict[str, object]:
    return {
        "id": action_id,
        "type": action_type,
        "evidence_reference": "unit_test_interfaces.md#test-data",
        "method": method,
        "path": path,
        "headers": {"Content-Type": "application/json"},
        "authorization_header": "",
        "query": {},
        "body": {"name": "TEST_REQ_RESOURCE"},
        "expected": {
            "http_status": 200,
            "response_assertions": [{"path": "$.code", "operator": "equals", "value": 0}],
        },
        "manifest": {
            "database": "test_db",
            "table": "resource",
            "record": {"name": "TEST_REQ_RESOURCE"},
        },
    }


def with_api_data_lifecycle(assessment: dict[str, object], cleanup_path: str) -> dict[str, object]:
    prepared: dict[str, object] = copy.deepcopy(assessment)
    prepared["data_preparation"] = {
        "entries": [
            {
                "id": "resource_fixture",
                "case_keys": ["case_001"],
                "strategy": "api_create",
                "evidence_references": ["unit_test_interfaces.md#test-data"],
                "verification_query_reference": "QRY_RESOURCE",
                "isolation_prefix": "TEST_REQ_",
                "setup": api_data_action("create_resource", "http", "POST", "/setup"),
                "cleanup": api_data_action("delete_resource", "http", "DELETE", cleanup_path),
            }
        ]
    }
    return prepared


class RunnerSmokeTest(unittest.TestCase):
    """Exercise canonical plan verification and HTTP execution."""

    def setUp(self) -> None:
        LocalHttpHandler.resources = set()
        LocalHttpHandler.events = []
        LocalHttpHandler.auth_headers = []
        LocalHttpHandler.content_types = []
        self.server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), LocalHttpHandler)
        self.thread: threading.Thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def prepare_workspace(self, root: Path, path: str) -> None:
        domain: str = f"http://127.0.0.1:{self.server.server_port}"
        write_json(root / "preparation_assessment.json", assessment_payload(path))
        built: subprocess.CompletedProcess[str] = run_builder(root)
        self.assertEqual(built.returncode, 0, built.stderr)
        write_json(
            root / "confirmation.json",
            {
                "approved": True,
                "testcase_hash": "approved-hash",
                "code_review_run_id": "review-1",
            },
        )
        write_json(
            root / "environments_config.json",
            {"environments": [{"name": "local", "api_domain": domain}]},
        )

    def test_consumes_unchanged_plan_generated_by_preparation_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root: Path = Path(temporary_directory)
            self.prepare_workspace(root, "/ok")

            result: subprocess.CompletedProcess[str] = run_runner(root, "local", "0")

            self.assertEqual(result.returncode, 0, result.stderr)
            report: str = (root / "output" / "interface_test_execution_report.md").read_text(encoding="utf-8")
            self.assertIn("| case_001__1 | case_001 | positive | PASS | 200 |", report)

    def test_business_code_mismatch_fails_even_when_http_and_plan_assertions_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root: Path = Path(temporary_directory)
            payload: dict[str, object] = assessment_payload("/business-failure")
            request_variant: object = payload["interface_cases"][0]["request_variants"][0]
            if not isinstance(request_variant, dict):
                raise TypeError("Expected one request variant.")
            expected: object = request_variant.get("expected")
            if not isinstance(expected, dict):
                raise TypeError("Expected response contract.")
            expected["response_assertions"] = [{"path": "$", "operator": "exists"}]
            domain: str = f"http://127.0.0.1:{self.server.server_port}"
            write_json(root / "preparation_assessment.json", payload)
            built: subprocess.CompletedProcess[str] = run_builder(root)
            self.assertEqual(built.returncode, 0, built.stderr)
            write_json(root / "confirmation.json", {"approved": True, "testcase_hash": "approved-hash", "code_review_run_id": "review-1"})
            write_json(root / "environments_config.json", {"environments": [{"name": "local", "api_domain": domain}]})

            result: subprocess.CompletedProcess[str] = run_runner(root, "local", "00000")

            self.assertEqual(result.returncode, 1, result.stderr)
            report: str = (root / "output" / "interface_test_execution_report.md").read_text(encoding="utf-8")
            self.assertIn("| case_001__1 | case_001 | positive | FAIL | 200 |", report)
            self.assertIn('"expected_business_code": "00000"', report)
            self.assertIn('"actual_business_code": "A00001"', report)

    def test_resolves_authorization_from_local_credentials_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root: Path = Path(temporary_directory)
            domain: str = f"http://127.0.0.1:{self.server.server_port}"
            payload: dict[str, object] = assessment_payload("/ok")
            request_variant: object = payload["interface_cases"][0]["request_variants"][0]
            if not isinstance(request_variant, dict):
                raise TypeError("Expected one request variant.")
            request_variant["auth_header_name"] = "authorization"
            write_json(root / "preparation_assessment.json", payload)
            built: subprocess.CompletedProcess[str] = run_builder(root)
            self.assertEqual(built.returncode, 0, built.stderr)
            write_json(root / "confirmation.json", {"approved": True, "testcase_hash": "approved-hash", "code_review_run_id": "review-1"})
            write_json(
                root / "environments_config.json",
                {
                    "environments": [
                        {
                            "name": "local",
                            "api_domain": domain,
                            "environment_type": "test",
                            "allow_test_data_mutation": True,
                            "credentials_ref": "environments.local",
                        }
                    ]
                },
            )
            write_json(
                root / "config" / "credentials.local.json",
                {"environments": {"local": {"authorization": "Bearer local-token"}}},
            )

            result: subprocess.CompletedProcess[str] = run_runner(root, "local", "0")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(LocalHttpHandler.auth_headers, ["Bearer local-token"])

    def test_rejects_modified_assessment_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root: Path = Path(temporary_directory)
            self.prepare_workspace(root, "/ok")
            assessment: dict[str, object] = read_json(root / "preparation_assessment.json")
            assessment["tampered"] = True
            write_json(root / "preparation_assessment.json", assessment)

            result: subprocess.CompletedProcess[str] = run_runner(root, "local", "0")

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("哈希与执行计划记录不一致", result.stderr)

    def test_rejects_modified_http_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root: Path = Path(temporary_directory)
            self.prepare_workspace(root, "/ok")
            plan: dict[str, object] = read_json(root / "execution_plan.json")
            first_request(plan)["path"] = "/tampered"
            write_json(root / "execution_plan.json", plan)

            result: subprocess.CompletedProcess[str] = run_runner(root, "local", "0")

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("字段=plan.requests[0].path", result.stderr)

    def test_rejects_modified_http_method(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root: Path = Path(temporary_directory)
            self.prepare_workspace(root, "/ok")
            plan: dict[str, object] = read_json(root / "execution_plan.json")
            first_request(plan)["method"] = "POST"
            write_json(root / "execution_plan.json", plan)

            result: subprocess.CompletedProcess[str] = run_runner(root, "local", "0")

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("字段=plan.requests[0].method", result.stderr)

    def test_rejects_modified_response_assertion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root: Path = Path(temporary_directory)
            self.prepare_workspace(root, "/ok")
            plan: dict[str, object] = read_json(root / "execution_plan.json")
            request: dict[str, object] = first_request(plan)
            expected: object = request.get("expected")
            if not isinstance(expected, dict):
                raise TypeError("Request must contain expected assertions.")
            assertions: object = expected.get("response_assertions")
            if not isinstance(assertions, list) or not assertions or not isinstance(assertions[0], dict):
                raise TypeError("Expected must contain one response assertion.")
            assertions[0]["value"] = 1
            write_json(root / "execution_plan.json", plan)

            result: subprocess.CompletedProcess[str] = run_runner(root, "local", "0")

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("plan.requests[0].expected.response_assertions[0].value", result.stderr)

    def test_rejects_code_review_run_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root: Path = Path(temporary_directory)
            self.prepare_workspace(root, "/ok")
            confirmation: dict[str, object] = read_json(root / "confirmation.json")
            confirmation["code_review_run_id"] = "review-2"
            write_json(root / "confirmation.json", confirmation)

            result: subprocess.CompletedProcess[str] = run_runner(root, "local", "0")

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("code_review_run_id 不一致", result.stderr)

    def test_rejects_partial_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root: Path = Path(temporary_directory)
            self.prepare_workspace(root, "/ok")
            plan: dict[str, object] = read_json(root / "execution_plan.json")
            plan["ready"] = False
            write_json(root / "execution_plan.json", plan)

            result: subprocess.CompletedProcess[str] = run_runner(root, "local", "0")

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("plan.ready", result.stderr)

    def test_returns_token_exit_code_for_403(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root: Path = Path(temporary_directory)
            self.prepare_workspace(root, "/forbidden")
            result: subprocess.CompletedProcess[str] = run_runner(root, "local", "0")
            self.assertEqual(result.returncode, 10, result.stderr)
            self.assertIn("[TOKEN_EXPIRED_ERROR] local", result.stderr)

    def test_executes_real_http_setup_and_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root: Path = Path(temporary_directory)
            domain: str = f"http://127.0.0.1:{self.server.server_port}"
            assessment: dict[str, object] = with_api_data_lifecycle(assessment_payload("/ok"), "/cleanup")
            write_json(root / "preparation_assessment.json", assessment)
            built: subprocess.CompletedProcess[str] = run_builder(root)
            self.assertEqual(built.returncode, 0, built.stderr)
            write_json(root / "confirmation.json", {"approved": True, "testcase_hash": "approved-hash", "code_review_run_id": "review-1"})
            write_json(root / "environments_config.json", {"environments": [{"name": "local", "api_domain": domain, "environment_type": "test", "allow_test_data_mutation": True}]})

            result: subprocess.CompletedProcess[str] = run_runner(root, "local", "0")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(LocalHttpHandler.events, ["setup:TEST_REQ_RESOURCE", "cleanup:TEST_REQ_RESOURCE"])
            self.assertEqual(LocalHttpHandler.content_types, ["application/json", "application/json"])
            self.assertNotIn("TEST_REQ_RESOURCE", LocalHttpHandler.resources)
            manifest: str = (root / "output" / "test_data_manifest.md").read_text(encoding="utf-8")
            self.assertIn('"_lifecycle":"created"', manifest)
            self.assertIn('"_lifecycle":"cleaned"', manifest)
            report: str = (root / "output" / "interface_test_execution_report.md").read_text(encoding="utf-8")
            self.assertIn("## Data lifecycle actions", report)
            self.assertIn("| setup | create_resource | resource_fixture | http | PASS | 200 | 0 | 0 |", report)
            self.assertIn("| cleanup | delete_resource | resource_fixture | http | PASS | 200 | 0 | 0 |", report)
            self.assertIn("### cleanup: delete_resource", report)

    def test_cleanup_runs_after_test_assertion_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root: Path = Path(temporary_directory)
            domain: str = f"http://127.0.0.1:{self.server.server_port}"
            assessment: dict[str, object] = with_api_data_lifecycle(assessment_payload("/assertion-failure"), "/cleanup")
            write_json(root / "preparation_assessment.json", assessment)
            built: subprocess.CompletedProcess[str] = run_builder(root)
            self.assertEqual(built.returncode, 0, built.stderr)
            write_json(root / "confirmation.json", {"approved": True, "testcase_hash": "approved-hash", "code_review_run_id": "review-1"})
            write_json(root / "environments_config.json", {"environments": [{"name": "local", "api_domain": domain, "environment_type": "test", "allow_test_data_mutation": True}]})

            result: subprocess.CompletedProcess[str] = run_runner(root, "local", "0")

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(LocalHttpHandler.events[-1], "cleanup:TEST_REQ_RESOURCE")
            self.assertNotIn("TEST_REQ_RESOURCE", LocalHttpHandler.resources)

    def test_setup_failure_still_writes_lifecycle_report_and_runs_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root: Path = Path(temporary_directory)
            domain: str = f"http://127.0.0.1:{self.server.server_port}"
            assessment: dict[str, object] = with_api_data_lifecycle(assessment_payload("/ok"), "/cleanup")
            data_preparation: object = assessment.get("data_preparation")
            if not isinstance(data_preparation, dict):
                raise TypeError("Expected data preparation object.")
            entries: object = data_preparation.get("entries")
            if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
                raise TypeError("Expected one data preparation entry.")
            setup: object = entries[0].get("setup")
            if not isinstance(setup, dict):
                raise TypeError("Expected setup action.")
            setup["path"] = "/setup-failure"
            write_json(root / "preparation_assessment.json", assessment)
            built: subprocess.CompletedProcess[str] = run_builder(root)
            self.assertEqual(built.returncode, 0, built.stderr)
            write_json(root / "confirmation.json", {"approved": True, "testcase_hash": "approved-hash", "code_review_run_id": "review-1"})
            write_json(root / "environments_config.json", {"environments": [{"name": "local", "api_domain": domain, "environment_type": "test", "allow_test_data_mutation": True}]})

            result: subprocess.CompletedProcess[str] = run_runner(root, "local", "0")

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(LocalHttpHandler.events, ["setup_failed:TEST_REQ_RESOURCE", "cleanup:TEST_REQ_RESOURCE"])
            report: str = (root / "output" / "interface_test_execution_report.md").read_text(encoding="utf-8")
            self.assertIn("| setup | create_resource | resource_fixture | http | FAIL | 422 | 0 | 1 |", report)
            self.assertIn("| cleanup | delete_resource | resource_fixture | http | PASS | 200 | 0 | 0 |", report)

    def test_rejects_mutation_disabled_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root: Path = Path(temporary_directory)
            domain: str = f"http://127.0.0.1:{self.server.server_port}"
            assessment: dict[str, object] = with_api_data_lifecycle(assessment_payload("/ok"), "/cleanup")
            write_json(root / "preparation_assessment.json", assessment)
            built: subprocess.CompletedProcess[str] = run_builder(root)
            self.assertEqual(built.returncode, 0, built.stderr)
            write_json(root / "confirmation.json", {"approved": True, "testcase_hash": "approved-hash", "code_review_run_id": "review-1"})
            write_json(root / "environments_config.json", {"environments": [{"name": "local", "api_domain": domain, "environment_type": "test", "allow_test_data_mutation": False}]})

            result: subprocess.CompletedProcess[str] = run_runner(root, "local", "0")

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("allow_test_data_mutation=true", result.stderr)
            self.assertEqual(LocalHttpHandler.events, [])

    def test_rejects_tampered_setup_action(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root: Path = Path(temporary_directory)
            domain: str = f"http://127.0.0.1:{self.server.server_port}"
            assessment: dict[str, object] = with_api_data_lifecycle(assessment_payload("/ok"), "/cleanup")
            write_json(root / "preparation_assessment.json", assessment)
            built: subprocess.CompletedProcess[str] = run_builder(root)
            self.assertEqual(built.returncode, 0, built.stderr)
            plan: dict[str, object] = read_json(root / "execution_plan.json")
            data_setup: object = plan.get("data_setup")
            if not isinstance(data_setup, list) or not data_setup or not isinstance(data_setup[0], dict):
                raise TypeError("Expected one setup action.")
            data_setup[0]["method"] = "PUT"
            write_json(root / "execution_plan.json", plan)
            write_json(root / "confirmation.json", {"approved": True, "testcase_hash": "approved-hash", "code_review_run_id": "review-1"})
            write_json(root / "environments_config.json", {"environments": [{"name": "local", "api_domain": domain, "environment_type": "test", "allow_test_data_mutation": True}]})

            result: subprocess.CompletedProcess[str] = run_runner(root, "local", "0")

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("plan.data_setup[0].method", result.stderr)

    def test_cleanup_failure_records_residual_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root: Path = Path(temporary_directory)
            domain: str = f"http://127.0.0.1:{self.server.server_port}"
            assessment: dict[str, object] = with_api_data_lifecycle(assessment_payload("/ok"), "/cleanup-failure")
            write_json(root / "preparation_assessment.json", assessment)
            built: subprocess.CompletedProcess[str] = run_builder(root)
            self.assertEqual(built.returncode, 0, built.stderr)
            write_json(root / "confirmation.json", {"approved": True, "testcase_hash": "approved-hash", "code_review_run_id": "review-1"})
            write_json(root / "environments_config.json", {"environments": [{"name": "local", "api_domain": domain, "environment_type": "test", "allow_test_data_mutation": True}]})

            result: subprocess.CompletedProcess[str] = run_runner(root, "local", "0")

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("TEST_REQ_RESOURCE", LocalHttpHandler.resources)
            manifest: str = (root / "output" / "test_data_manifest.md").read_text(encoding="utf-8")
            self.assertIn('"_lifecycle":"residual"', manifest)
            report: str = (root / "output" / "interface_test_execution_report.md").read_text(encoding="utf-8")
            self.assertIn("| cleanup | delete_resource | resource_fixture | http | FAIL | 409 | 0 | 1 |", report)
            self.assertIn('"actual_business_code": 1', report)

    def test_rejects_database_write_sql(self) -> None:
        runner: ModuleType = load_runner_module()
        with self.assertRaisesRegex(ValueError, "只能包含 SELECT"):
            runner.validate_read_only_sql("UPDATE account SET status = 1", "sql")

    def test_rejects_select_with_embedded_delete(self) -> None:
        runner: ModuleType = load_runner_module()
        with self.assertRaisesRegex(ValueError, "包含禁止"):
            runner.validate_read_only_sql("SELECT 1 FROM account WHERE id IN (DELETE FROM account)", "sql")

    def test_accepts_parameterized_controlled_insert_and_exact_cleanup(self) -> None:
        runner: ModuleType = load_runner_module()
        insert_action: dict[str, object] = {
            "database": "test_db",
            "table": "resource",
            "sql": "INSERT INTO test_db.resource (test_code, name) VALUES (%s, %s)",
            "parameters": ["TEST_REQ_001", "real fixture"],
        }
        cleanup_action: dict[str, object] = {
            "database": "test_db",
            "table": "resource",
            "sql": "DELETE FROM test_db.resource WHERE test_code = %s",
            "parameters": ["TEST_REQ_001"],
        }

        runner.validate_sql_insert(insert_action, "insert_action")
        runner.validate_sql_delete(cleanup_action, "cleanup_action")

    def test_rejects_unbounded_or_non_test_cleanup(self) -> None:
        runner: ModuleType = load_runner_module()
        unbounded: dict[str, object] = {
            "database": "test_db",
            "table": "resource",
            "sql": "DELETE FROM test_db.resource",
            "parameters": [],
        }
        non_test_identifier: dict[str, object] = {
            "database": "test_db",
            "table": "resource",
            "sql": "DELETE FROM test_db.resource WHERE test_code = %s",
            "parameters": ["REAL_001"],
        }

        with self.assertRaisesRegex(ValueError, "单一测试标识"):
            runner.validate_sql_delete(unbounded, "cleanup_action")
        with self.assertRaisesRegex(ValueError, "TEST_"):
            runner.validate_sql_delete(non_test_identifier, "cleanup_action")

    def test_rejects_unconfirmed_write_connection(self) -> None:
        runner: ModuleType = load_runner_module()
        connection: dict[str, object] = {
            "access_mode": "read-only",
            "environment_name": "local",
            "allowed_databases": ["test_db"],
            "allowed_tables": ["resource"],
        }

        with self.assertRaisesRegex(PermissionError, "controlled-write"):
            runner.validate_write_connection(connection, "local", [])

    def test_rejects_production_and_mutation_disabled_environments(self) -> None:
        runner: ModuleType = load_runner_module()
        with self.assertRaisesRegex(PermissionError, "environment_type=test"):
            runner.validate_mutation_environment(
                {"environment_type": "production", "allow_test_data_mutation": True},
                "production",
            )
        with self.assertRaisesRegex(PermissionError, "allow_test_data_mutation=true"):
            runner.validate_mutation_environment(
                {"environment_type": "test", "allow_test_data_mutation": False},
                "test",
            )


if __name__ == "__main__":
    unittest.main()
