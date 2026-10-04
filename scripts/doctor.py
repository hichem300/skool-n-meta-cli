#!/usr/bin/env python3
"""Run read-only Meta Ads and Skool connection checks."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from scripts.configure_runtime import ConfigurationError, configure


def sanitize(message: str, secrets: list[str]) -> str:
    cleaned = message
    for secret in sorted((value for value in secrets if value), key=len, reverse=True):
        cleaned = cleaned.replace(secret, "[REDACTED]")
    cleaned = re.sub(r"(?i)(bearer\s+)[^\s]+", r"\1[REDACTED]", cleaned)
    cleaned = re.sub(
        r"(?i)(auth_token(?:%3[dD]|[=:])(?:%20|\s)*)([^;&\s]+)",
        r"\1[REDACTED]",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)(access[_-]?token(?:%3[dD]|[=:])(?:%20|\s)*)([^&\s]+)",
        r"\1[REDACTED]",
        cleaned,
    )
    return cleaned.strip()


def run_check(
    name: str,
    command: list[str],
    *,
    cwd: Path,
    environ: dict[str, str],
    secrets: list[str],
) -> bool:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environ,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if completed.returncode == 0:
        print(f"PASS  {name}")
        return True
    detail = sanitize(completed.stderr or completed.stdout, secrets)
    print(f"FAIL  {name}")
    if detail:
        print(f"      {detail}")
    return False


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    environ = dict(os.environ)
    try:
        configure(root, environ)
    except ConfigurationError as error:
        print(f"FAIL  configuration\n      {error}")
        return 1

    meta = root / ".venv" / "bin" / "meta"
    skool = root / ".venv" / "bin" / "skool-data"
    missing = [str(path.relative_to(root)) for path in (meta, skool) if not path.is_file()]
    if missing:
        print("FAIL  installation")
        print("      Missing " + ", ".join(missing) + ". Run ./scripts/setup.sh first.")
        return 1
    print("PASS  installation")

    secrets = [
        environ.get("ACCESS_TOKEN", ""),
        environ.get("SKOOL_AUTH_TOKEN", ""),
    ]
    meta_ok = run_check(
        "Meta Ads read access",
        [
            str(meta),
            "--output",
            "json",
            "--no-input",
            "ads",
            "campaign",
            "list",
            "--limit",
            "1",
            "--fields",
            "id,name",
        ],
        cwd=root,
        environ=environ,
        secrets=secrets,
    )
    skool_environ = environ | {"SKOOL_DATA_HOME": str(root / ".skool-data")}
    skool_ok = run_check(
        "Skool read access",
        [str(skool), "doctor"],
        cwd=root,
        environ=skool_environ,
        secrets=secrets,
    )

    if meta_ok and skool_ok:
        print("\nConnections ready. Both checks were read-only.")
        return 0
    print("\nSetup incomplete. Correct failed connection, then rerun ./scripts/doctor.sh.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
