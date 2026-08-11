"""Integration tests for capability-driven token validation and refresh."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import threading
import unittest
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TypeAlias, cast

SCRIPTS_PATH: Path = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_PATH))

from validate_environment_token import GateResult, TokenGateError, run_token_gate

ResponseMap: TypeAlias = dict[str, tuple[int, dict[str, object]]]


def create_handler(
    response_map: ResponseMap,
    refreshed_authorization: str,
    expected_account: str,
    expected_password: str,
) -> type[BaseHTTPRequestHandler]:
    class LocalServiceHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/login":
                body: str = f"""<!doctype html>
<html><body>
<input id="account-control" type="text">
<input data-testid="password-control" type="password">
<button aria-label="submit-login" type="submit">Submit</button>
<script>
document.querySelector('[aria-label="submit-login"]').addEventListener('click', async () => {{
  const account = document.querySelector('#account-control').value;
  const password = document.querySelector('[data-testid="password-control"]').value;
  if (account === {json.dumps(expected_account)} && password === {json.dumps(expected_password)}) {{
    await fetch('/session', {{headers: {{Authorization: {json.dumps(refreshed_authorization)}}}}});
  }}
}});
</script>
</body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))
                return
            if self.path == "/session":
                self.send_response(204)
                self.end_headers()
                return
            if self.path == "/probe":
                authorization: str = self.headers.get("Authorization", "")
                response: tuple[int, dict[str, object]] = response_map.get(
                    authorization,
                    (500, {"result": {"code": "SYNTHETIC_UNMAPPED"}}),
                )
                status: int = response[0]
                payload: dict[str, object] = response[1]
                body = json.dumps(payload)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    return LocalServiceHandler


@contextlib.contextmanager
def local_service(
    response_map: ResponseMap,
    refreshed_authorization: str,
    expected_account: str,
    expected_password: str,
) -> Iterator[str]:
    handler: type[BaseHTTPRequestHandler] = create_handler(
        response_map,
        refreshed_authorization,
        expected_account,
        expected_password,
    )
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        address: tuple[str, int] = cast(tuple[str, int], server.server_address)
        host: str = address[0]
        port: int = address[1]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def write_config(directory: Path, environment: dict[str, object]) -> Path:
    config_path: Path = directory / "environments_config.json"
    config_path.write_text(
        json.dumps({"environments": [environment]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return config_path


def probe_rule(base_url: str) -> dict[str, object]:
    return {
        "url": f"{base_url}/probe",
        "headers": {},
        "response_code_path": "$.result.code",
        "success_codes": ["SYNTHETIC_ACCEPTED"],
        "unauthorized_codes": ["SYNTHETIC_REJECTED"],
    }


class TokenGateIntegrationTests(unittest.TestCase):
    def test_probe_only_environment_accepts_configured_success_code(self) -> None:
        authorization: str = "synthetic-current-authorization"
        responses: ResponseMap = {
            authorization: (200, {"result": {"code": "SYNTHETIC_ACCEPTED"}}),
        }
        with local_service(responses, "unused", "unused", "unused") as base_url:
            with tempfile.TemporaryDirectory() as raw_directory:
                config_path: Path = write_config(
                    Path(raw_directory),
                    {
                        "name": "synthetic-probe-only",
                        "api_domain": base_url,
                        "authorization": authorization,
                        "token_probe": probe_rule(base_url),
                    },
                )

                result: GateResult = run_token_gate(config_path, "synthetic-probe-only", 2, 3, 5000)

        self.assertEqual(result["status"], "valid")
        self.assertFalse(result["authorization_updated"])

    def test_login_capable_environment_refreshes_without_name_specific_logic(self) -> None:
        old_authorization: str = "synthetic-old-authorization"
        new_authorization: str = "synthetic-new-authorization"
        account: str = "synthetic-account"
        password: str = "synthetic-password"
        with local_service({}, new_authorization, account, password) as base_url:
            with tempfile.TemporaryDirectory() as raw_directory:
                config_path: Path = write_config(
                    Path(raw_directory),
                    {
                        "name": "synthetic-login-capable",
                        "api_domain": base_url,
                        "login_url": f"{base_url}/login",
                        "account": account,
                        "password": password,
                        "authorization": old_authorization,
                    },
                )

                result: GateResult = run_token_gate(config_path, "synthetic-login-capable", 2, 3, 10000)
                updated_payload: dict[str, object] = json.loads(config_path.read_text(encoding="utf-8"))

        environments: object = updated_payload["environments"]
        self.assertIsInstance(environments, list)
        typed_environments: list[dict[str, object]] = cast(list[dict[str, object]], environments)
        self.assertEqual(typed_environments[0]["authorization"], new_authorization)
        self.assertEqual(result["status"], "refreshed")

    def test_expired_probe_without_login_capability_blocks_and_redacts(self) -> None:
        authorization: str = "synthetic-sensitive-authorization"
        responses: ResponseMap = {
            authorization: (
                200,
                {"result": {"code": "SYNTHETIC_REJECTED"}, "authorization": authorization},
            ),
        }
        with local_service(responses, "unused", "unused", "unused") as base_url:
            with tempfile.TemporaryDirectory() as raw_directory:
                config_path: Path = write_config(
                    Path(raw_directory),
                    {
                        "name": "synthetic-nonrenewable",
                        "api_domain": base_url,
                        "authorization": authorization,
                        "token_probe": probe_rule(base_url),
                    },
                )
                warnings: io.StringIO = io.StringIO()
                with contextlib.redirect_stderr(warnings):
                    with self.assertRaisesRegex(TokenGateError, "没有完整登录能力"):
                        run_token_gate(config_path, "synthetic-nonrenewable", 2, 3, 5000)

        warning_text: str = warnings.getvalue()
        self.assertNotIn(authorization, warning_text)
        self.assertEqual(warning_text.count('"event": "token_probe_failed"'), 3)


if __name__ == "__main__":
    unittest.main()
