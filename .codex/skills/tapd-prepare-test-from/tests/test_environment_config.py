"""Runtime-validation tests for generic API environment configuration."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_PATH: Path = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_PATH))

from validate_environment_token import EnvironmentConfig, TokenGateError, validate_environment


def direct_environment() -> dict[str, object]:
    return {
        "name": "synthetic-login-capable",
        "api_domain": "https://api.example.invalid",
        "login_url": "https://console.example.invalid/login",
        "account": "local-account",
        "password": "local-password",
        "authorization": "raw-authorization-value",
        "unrelated_metadata": "ignored",
    }


class EnvironmentConfigurationTests(unittest.TestCase):
    def test_accepts_direct_credentials_and_ignores_unrelated_metadata(self) -> None:
        environment: EnvironmentConfig = validate_environment(direct_environment(), "environment")

        self.assertEqual(environment["name"], "synthetic-login-capable")
        self.assertEqual(environment["authorization"], "raw-authorization-value")
        self.assertNotIn("unrelated_metadata", environment)

    def test_rejects_partial_login_capability(self) -> None:
        raw_environment: dict[str, object] = direct_environment()
        raw_environment.pop("password")

        with self.assertRaisesRegex(TokenGateError, "必须同时存在"):
            validate_environment(raw_environment, "environment")

    def test_rejects_legacy_reference_fields(self) -> None:
        raw_environment: dict[str, object] = direct_environment()
        raw_environment["credentials_ref"] = "legacy.reference"

        with self.assertRaisesRegex(TokenGateError, "不再支持的旧字段"):
            validate_environment(raw_environment, "environment")

    def test_probe_codes_are_read_from_configuration(self) -> None:
        raw_environment: dict[str, object] = {
            "name": "synthetic-probe-only",
            "api_domain": "https://api.example.invalid",
            "authorization": "raw-authorization-value",
            "token_probe": {
                "url": "https://api.example.invalid/read-only-probe",
                "headers": {},
                "response_code_path": "$.result.code",
                "success_codes": ["SYNTHETIC_ACCEPTED"],
                "unauthorized_codes": ["SYNTHETIC_REJECTED"],
            },
        }

        environment: EnvironmentConfig = validate_environment(raw_environment, "environment")

        self.assertEqual(environment["token_probe"]["success_codes"], ["SYNTHETIC_ACCEPTED"])
        self.assertEqual(environment["token_probe"]["unauthorized_codes"], ["SYNTHETIC_REJECTED"])


if __name__ == "__main__":
    unittest.main()
