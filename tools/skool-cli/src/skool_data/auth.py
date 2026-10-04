import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ValidationError

from skool_data.errors import ConfigurationError
from skool_data.models import Credentials

_PROFILE_PATH = re.compile(r"^/_next/data/[^/]+/%40[^/]+\.json$")

_ALLOWED_HEADERS = {
    "accept",
    "accept-language",
    "authorization",
    "cookie",
    "user-agent",
    "x-aws-waf-token",
}


def without_waf(headers: dict[str, str]) -> dict[str, str]:
    """Return request headers without AWS WAF credentials."""
    sanitized: dict[str, str] = {}
    for name, value in headers.items():
        lowered = name.lower()
        if lowered == "x-aws-waf-token":
            continue
        if lowered == "cookie":
            cookies = [
                part.strip()
                for part in value.split(";")
                if part.strip()
                and part.strip().split("=", 1)[0].lower() != "aws-waf-token"
            ]
            if cookies:
                sanitized[name] = "; ".join(cookies)
            continue
        sanitized[name] = value
    return sanitized


def import_har(source: Path, destination: Path) -> Credentials:
    """Extract the minimal reusable Skool request configuration from a HAR."""
    try:
        document = json.loads(source.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Cannot read HAR: {exc}") from exc

    entries = document.get("log", {}).get("entries", [])
    profile_data_url = None
    for entry in entries:
        candidate = entry.get("request", {}).get("url", "")
        parsed = urlsplit(candidate)
        if (
            parsed.scheme == "https"
            and parsed.netloc == "www.skool.com"
            and _PROFILE_PATH.fullmatch(parsed.path)
        ):
            profile_data_url = candidate
            break

    for entry in entries:
        request = entry.get("request", {})
        parsed = urlsplit(request.get("url", ""))
        parts = parsed.path.strip("/").split("/")
        if parsed.netloc != "api2.skool.com" or "analytics" not in parsed.path:
            continue
        if len(parts) < 3 or parts[0] != "groups":
            continue
        headers = {
            item["name"].lower(): item["value"]
            for item in request.get("headers", [])
            if item.get("name", "").lower() in _ALLOWED_HEADERS
            and isinstance(item.get("value"), str)
        }
        headers = without_waf(headers)
        if not (headers.get("cookie") or headers.get("authorization")):
            raise ConfigurationError(
                "HAR has no authentication header; export it with sensitive data"
            )
        credentials = Credentials(
            api_base_url="https://api2.skool.com",
            community_id=parts[1],
            headers=headers,
            imported_at=datetime.now(UTC),
            profile_data_url=profile_data_url,
        )
        _write_private(destination, credentials.model_dump_json(indent=2))
        return credentials
    raise ConfigurationError("HAR contains no usable Skool analytics request")


def load_credentials(path: Path) -> Credentials:
    """Load and validate imported credentials without legacy WAF values."""
    try:
        credentials = Credentials.model_validate_json(path.read_text())
    except (OSError, ValidationError) as exc:
        raise ConfigurationError(f"Cannot load credentials: {exc}") from exc
    return credentials.model_copy(
        update={"headers": without_waf(credentials.headers)}
    )


def _write_private(destination: Path, content: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=destination.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
