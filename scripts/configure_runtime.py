#!/usr/bin/env python3
"""Create private runtime files expected by Meta and Skool CLIs."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

REQUIRED_KEYS = (
    "ACCESS_TOKEN",
    "AD_ACCOUNT_ID",
    "SKOOL_AUTH_TOKEN",
    "SKOOL_COMMUNITY_ID",
)


class ConfigurationError(ValueError):
    """Raised when cloud environment configuration is incomplete or unsafe."""


def required_values(environ: dict[str, str]) -> dict[str, str]:
    values: dict[str, str] = {}
    missing: list[str] = []
    for key in REQUIRED_KEYS:
        value = environ.get(key, "").strip()
        if not value:
            missing.append(key)
        elif "\n" in value or "\r" in value:
            raise ConfigurationError(f"{key} must be a single-line value")
        else:
            values[key] = value
    if missing:
        raise ConfigurationError(
            "Missing cloud environment variables: " + ", ".join(missing)
        )
    account_id = values["AD_ACCOUNT_ID"]
    if account_id.startswith("act_"):
        account_id = account_id[4:]
    if not account_id.isdigit():
        raise ConfigurationError("AD_ACCOUNT_ID must contain only digits")
    values["AD_ACCOUNT_ID"] = account_id
    skool_token = values["SKOOL_AUTH_TOKEN"]
    if skool_token.startswith("auth_token="):
        skool_token = skool_token.removeprefix("auth_token=")
    if not skool_token:
        raise ConfigurationError("SKOOL_AUTH_TOKEN has no token value")
    values["SKOOL_AUTH_TOKEN"] = skool_token
    return values


def _private_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _dotenv_value(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def configure(root: Path, environ: dict[str, str]) -> tuple[Path, Path]:
    values = required_values(environ)

    meta_path = root / ".env"
    meta_content = "\n".join(
        (
            f'ACCESS_TOKEN={_dotenv_value(values["ACCESS_TOKEN"])}',
            f'AD_ACCOUNT_ID={_dotenv_value(values["AD_ACCOUNT_ID"])}',
            "",
        )
    )
    _private_write(meta_path, meta_content)

    profile_url = environ.get("SKOOL_PROFILE_DATA_URL", "").strip() or None
    credentials = {
        "api_base_url": "https://api2.skool.com",
        "community_id": values["SKOOL_COMMUNITY_ID"],
        "headers": {
            "accept": "application/json",
            "cookie": f'auth_token={values["SKOOL_AUTH_TOKEN"]}',
            "user-agent": "Mozilla/5.0 Funnel-Leak-Hunt-Cloud",
        },
        "imported_at": datetime.now(UTC).isoformat(),
        "profile_data_url": profile_url,
    }
    skool_path = root / ".skool-data" / "credentials.json"
    _private_write(skool_path, json.dumps(credentials, indent=2) + "\n")
    return meta_path, skool_path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        configure(root, dict(os.environ))
    except ConfigurationError as error:
        print(f"Configuration failed: {error}")
        return 1
    print("Runtime credentials configured. Secret values were not displayed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
