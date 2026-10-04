import stat
from dataclasses import dataclass

from skool_data.auth import load_credentials
from skool_data.errors import SkoolDataError
from skool_data.http import SkoolHttpClient
from skool_data.models import RuntimePaths
from skool_data.storage import Storage


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str


def run_checks(paths: RuntimePaths, *, offline: bool) -> list[CheckResult]:
    checks: list[CheckResult] = []
    try:
        credentials = load_credentials(paths.credentials)
        checks.append(CheckResult("credentials", "PASS", "configuration is valid"))
    except SkoolDataError as exc:
        return [CheckResult("credentials", "FAIL", str(exc))]

    mode = stat.S_IMODE(paths.credentials.stat().st_mode)
    if mode & 0o077:
        checks.append(
            CheckResult("permissions", "FAIL", "credentials must use mode 0600")
        )
    else:
        checks.append(CheckResult("permissions", "PASS", "credentials are private"))

    try:
        Storage(paths.database).initialize()
        paths.raw.mkdir(parents=True, exist_ok=True)
        paths.exports.mkdir(parents=True, exist_ok=True)
        checks.append(CheckResult("storage", "PASS", "runtime paths are writable"))
    except OSError as exc:
        checks.append(CheckResult("storage", "FAIL", f"runtime is not writable: {exc}"))

    checks.append(
        CheckResult("waf-credentials", "PASS", "AWS WAF credentials are not required")
    )

    if credentials.headers.get("cookie") or credentials.headers.get("authorization"):
        checks.append(
            CheckResult("authentication", "PASS", "authentication header is present")
        )
    else:
        checks.append(
            CheckResult(
                "authentication",
                "FAIL",
                "import a HAR exported with sensitive data",
            )
        )

    if offline:
        checks.append(CheckResult("online", "SKIPPED", "offline mode"))
    else:
        try:
            with SkoolHttpClient(credentials) as client:
                payload = client.get_json("analytics-overview-v2")
            if not isinstance(payload.get("token"), str) or not payload["token"]:
                checks.append(CheckResult("online", "FAIL", "token response changed"))
            else:
                checks.append(CheckResult("online", "PASS", "Skool API accepted credentials"))
        except SkoolDataError as exc:
            checks.append(CheckResult("online", "FAIL", str(exc)))
    return checks
