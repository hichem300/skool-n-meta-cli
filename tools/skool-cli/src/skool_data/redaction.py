from collections.abc import Mapping
from typing import Any

_SECRET_KEYS = {"authorization", "cookie", "token", "x-aws-waf-token"}


def redact(value: Any, secrets: set[str] | None = None) -> Any:
    """Recursively remove known secret fields and secret string values."""
    known = {secret for secret in secrets or set() if secret}
    if isinstance(value, str):
        result = value
        for secret in known:
            result = result.replace(secret, "[REDACTED]")
        return result
    if isinstance(value, Mapping):
        return {
            key: (
                "[REDACTED]"
                if key.lower() in _SECRET_KEYS
                else redact(item, known)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, known) for item in value]
    return value
