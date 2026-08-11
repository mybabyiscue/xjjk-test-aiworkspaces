"""Tests for token-gate secret redaction."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_PATH: Path = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_PATH))

from validate_environment_token import redact_text, sanitize_url


class SecretRedactionTests(unittest.TestCase):
    def test_redacts_known_values_and_sensitive_json_fields(self) -> None:
        account: str = "synthetic-account-secret"
        password: str = "synthetic-password-secret"
        authorization: str = "synthetic-authorization-secret"
        raw_text: str = (
            f'{{"account":"{account}","password":"{password}",'
            f'"authorization":"{authorization}","token":"unregistered-secret"}}'
        )

        redacted: str = redact_text(raw_text, (account, password, authorization))

        self.assertNotIn(account, redacted)
        self.assertNotIn(password, redacted)
        self.assertNotIn(authorization, redacted)
        self.assertNotIn("unregistered-secret", redacted)

    def test_redacts_query_values_from_logged_urls(self) -> None:
        sanitized: str = sanitize_url("https://api.example.invalid/probe?tenant=secret-value&size=10")

        self.assertEqual(sanitized, "https://api.example.invalid/probe?tenant=%2A%2A%2A&size=%2A%2A%2A")


if __name__ == "__main__":
    unittest.main()
