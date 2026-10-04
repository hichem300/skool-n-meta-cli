from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path

from scripts.configure_runtime import ConfigurationError, configure, required_values


class ConfigureRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environ = {
            "ACCESS_TOKEN": "meta-secret",
            "AD_ACCOUNT_ID": "act_123456789",
            "SKOOL_AUTH_TOKEN": "skool-secret",
            "SKOOL_COMMUNITY_ID": "community-123",
        }

    def test_required_values_normalizes_meta_account_id(self) -> None:
        values = required_values(self.environ)
        self.assertEqual(values["AD_ACCOUNT_ID"], "123456789")

    def test_missing_values_names_keys_without_exposing_secrets(self) -> None:
        with self.assertRaisesRegex(
            ConfigurationError, "SKOOL_AUTH_TOKEN, SKOOL_COMMUNITY_ID"
        ):
            required_values(
                {"ACCESS_TOKEN": "meta-secret", "AD_ACCOUNT_ID": "123456789"}
            )

    def test_configure_writes_private_runtime_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            meta_path, skool_path = configure(root, self.environ)

            self.assertEqual(stat.S_IMODE(meta_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(skool_path.stat().st_mode), 0o600)
            self.assertIn('ACCESS_TOKEN="meta-secret"', meta_path.read_text())
            self.assertIn('AD_ACCOUNT_ID="123456789"', meta_path.read_text())

            credentials = json.loads(skool_path.read_text())
            self.assertEqual(credentials["community_id"], "community-123")
            self.assertEqual(
                credentials["headers"]["cookie"], "auth_token=skool-secret"
            )
            self.assertNotIn("x-aws-waf-token", credentials["headers"])

    def test_strips_cookie_name_from_skool_token(self) -> None:
        environ = self.environ | {"SKOOL_AUTH_TOKEN": "auth_token=skool-secret"}
        with tempfile.TemporaryDirectory() as directory:
            _, skool_path = configure(Path(directory), environ)
            credentials = json.loads(skool_path.read_text())
        self.assertEqual(
            credentials["headers"]["cookie"], "auth_token=skool-secret"
        )

    def test_rejects_multiline_secret(self) -> None:
        environ = self.environ | {"ACCESS_TOKEN": "first\nsecond"}
        with self.assertRaisesRegex(ConfigurationError, "single-line"):
            required_values(environ)


if __name__ == "__main__":
    unittest.main()
