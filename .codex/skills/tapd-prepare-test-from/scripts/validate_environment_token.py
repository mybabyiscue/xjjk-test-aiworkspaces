"""Validate configured environment token metadata without leaking credentials."""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
import os
import tempfile
from pathlib import Path

from preparation_contract import PreparationError, read_json_object, require_object, require_string, write_json_object
from security_utils import redacted_json


def parse_arguments() -> argparse.Namespace:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Validate one configured API environment token.")
    parser.add_argument("--environments", required=True)
    parser.add_argument("--credentials", required=True)
    parser.add_argument("--environment-name", required=True)
    parser.add_argument("--report", required=True)
    return parser.parse_args()


def require_environment(payload: dict[str, object], name: str) -> dict[str, object]:
    raw_environments: object = payload.get("environments")
    if not isinstance(raw_environments, list):
        raise PreparationError("environments_config.json.environments 必须是数组。")
    for raw_environment in raw_environments:
        if isinstance(raw_environment, dict) and raw_environment.get("name") == name:
            return raw_environment
    raise PreparationError(f"未找到用户确认的环境：{name}")


def require_token_codes(environment: dict[str, object]) -> list[object]:
    raw_codes: object = environment.get("token_error_codes", environment.get("healthcheck_unauthorized_codes"))
    if not isinstance(raw_codes, list) or not raw_codes:
        raise PreparationError("环境必须配置非空 token_error_codes 或 healthcheck_unauthorized_codes。")
    return list(raw_codes)


def require_test_environment(environment: dict[str, object]) -> None:
    if environment.get("environment_type") != "test":
        raise PreparationError("只允许 test 环境进入准备流程。")
    if environment.get("allow_test_data_mutation") is not True:
        raise PreparationError("环境未允许测试数据变更。")
    require_string(environment.get("api_domain"), "environment.api_domain")
    require_string(environment.get("healthcheck_url"), "environment.healthcheck_url")


def credentials_for_environment(credentials: dict[str, object], reference: str) -> dict[str, object]:
    current: object = credentials
    for segment in reference.split("."):
        if not isinstance(current, dict):
            raise PreparationError("credentials_ref 指向的凭证对象不存在。")
        current = current.get(segment)
    if not isinstance(current, dict):
        raise PreparationError("credentials_ref 指向的凭证对象不存在。")
    return current


def build_request(environment: dict[str, object], credentials: dict[str, object]) -> urllib.request.Request:
    url: str = require_string(environment.get("healthcheck_url"), "environment.healthcheck_url")
    raw_headers: object = environment.get("healthcheck_headers", {})
    if not isinstance(raw_headers, dict):
        raise PreparationError("environment.healthcheck_headers 必须是对象。")
    headers: dict[str, str] = {str(key): str(value) for key, value in raw_headers.items()}
    auth_header_name: str = str(environment.get("auth_header_name", ""))
    authorization_value: object = credentials.get("authorization")
    if auth_header_name:
        if not isinstance(authorization_value, str) or not authorization_value:
            raise PreparationError("当前环境凭证缺少 authorization。")
        headers[auth_header_name] = authorization_value
    return urllib.request.Request(url, headers=headers, method="GET")


def response_code(payload: object) -> object:
    if isinstance(payload, dict):
        for key in ("code", "status_code", "error_code"):
            if key in payload:
                return payload.get(key)
    return None


def probe_environment(environment: dict[str, object], credentials: dict[str, object]) -> dict[str, object]:
    timeout: float = float(environment.get("timeout_seconds", 10))
    request: urllib.request.Request = build_request(environment, credentials)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body: str = response.read().decode("utf-8", errors="replace")
            status: int = int(response.status)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        status = int(error.code)
    parsed_body: object
    try:
        parsed_body = json.loads(body) if body else {}
    except json.JSONDecodeError:
        parsed_body = {"raw_body": body}
    success_code: object = environment.get("healthcheck_success_code")
    token_error_codes: list[object] = require_token_codes(environment)
    current_code: object = response_code(parsed_body)
    token_invalid: bool = status == 401 or current_code in token_error_codes
    return {
        "environment_name": environment.get("name", ""),
        "http_status": status,
        "application_code": current_code,
        "token_error_codes": token_error_codes,
        "success": 200 <= status < 300 and (success_code in (None, "") or current_code == success_code),
        "token_invalid": token_invalid,
        "response_body_length": len(body),
        "redacted_error_response": "" if 200 <= status < 300 else redacted_json(parsed_body),
    }


def atomic_update_authorization(credentials_path: Path, credentials_ref: str, authorization: str) -> None:
    if not authorization:
        raise PreparationError("authorization must be a non-empty string.")
    credentials: dict[str, object] = read_json_object(credentials_path)
    current: object = credentials
    segments: list[str] = credentials_ref.split(".")
    for segment in segments[:-1]:
        if not isinstance(current, dict) or not isinstance(current.get(segment), dict):
            raise PreparationError("credentials_ref 指向的凭证对象不存在。")
        current = current[segment]
    if not isinstance(current, dict) or not isinstance(current.get(segments[-1]), dict):
        raise PreparationError("credentials_ref 指向的凭证对象不存在。")
    target: object = current[segments[-1]]
    if not isinstance(target, dict):
        raise PreparationError("credentials_ref 指向的凭证对象不存在。")
    target["authorization"] = authorization
    credentials_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(credentials_path.parent), newline="\n") as file:
        json.dump(credentials, file, ensure_ascii=False, indent=2)
        file.write("\n")
        temporary_path: str = file.name
    os.replace(temporary_path, credentials_path)


def main() -> int:
    arguments: argparse.Namespace = parse_arguments()
    environments: dict[str, object] = read_json_object(Path(arguments.environments))
    credentials: dict[str, object] = read_json_object(Path(arguments.credentials))
    environment: dict[str, object] = require_environment(environments, arguments.environment_name)
    require_test_environment(environment)
    token_codes: list[object] = require_token_codes(environment)
    credentials_ref: str = require_string(environment.get("credentials_ref"), "environment.credentials_ref")
    local_credentials: dict[str, object] = credentials_for_environment(credentials, credentials_ref)
    report: dict[str, object] = probe_environment(environment, local_credentials)
    report["token_error_codes"] = token_codes
    write_json_object(Path(arguments.report), report)
    if report["success"] is True:
        print("Token gate passed.")
        return 0
    print(redacted_json(report))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
