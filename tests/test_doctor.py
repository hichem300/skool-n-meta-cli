from __future__ import annotations

import unittest

from scripts.doctor import sanitize


class DoctorTests(unittest.TestCase):
    def test_sanitize_removes_known_secrets(self) -> None:
        message = "request failed token=meta-secret cookie auth_token=skool-secret"
        cleaned = sanitize(message, ["meta-secret", "skool-secret"])
        self.assertNotIn("meta-secret", cleaned)
        self.assertNotIn("skool-secret", cleaned)
        self.assertIn("[REDACTED]", cleaned)

    def test_sanitize_removes_bearer_and_encoded_access_token(self) -> None:
        message = "Bearer abc123 access_token%3Dxyz789"
        cleaned = sanitize(message, [])
        self.assertEqual(
            cleaned,
            "Bearer [REDACTED] access_token%3D[REDACTED]",
        )


if __name__ == "__main__":
    unittest.main()
