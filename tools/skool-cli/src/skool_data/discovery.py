import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from pydantic import BaseModel

from skool_data.artifacts import write_payload
from skool_data.errors import SchemaError, SkoolDataError, TransportError
from skool_data.models import RunStatus
from skool_data.storage import Storage

_DISCOVERY_URL = "https://www.skool.com/discovery"
_CURL_META = "SKOOL_DATA_META:"
_BROWSER_USER_AGENT = "Mozilla/5.0"


def _discovery_url(*, page: int = 1, query: str | None = None) -> str:
    if page < 1:
        raise ValueError("Discovery page must be 1 or greater")
    search = query.strip() if query else ""
    parameters: list[tuple[str, str | int]] = []
    if search:
        parameters.append(("q", search))
    if page > 1:
        parameters.append(("p", page))
    if parameters:
        parameters.append(("lang", "english"))
        if not search:
            parameters.append(("srt", "trending"))
    return f"{_DISCOVERY_URL}?{urlencode(parameters)}" if parameters else _DISCOVERY_URL


class _NextDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._capturing = False
        self._parts: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self._capturing = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._capturing:
            self._capturing = False

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._parts.append(data)

    @property
    def content(self) -> str:
        return "".join(self._parts)


class DiscoveryListing(BaseModel):
    group_id: str
    slug: str
    display_name: str
    url: str
    rank: int
    description: str
    total_members: int
    display_price: str
    membership_model: str
    tags: list[str]


class DiscoveryPage(BaseModel):
    listings: list[DiscoveryListing]
    result_count: int
    total_available: int | None
    has_more: bool

    def sanitized_payload(self) -> dict[str, Any]:
        groups = []
        for listing in self.listings:
            groups.append(
                {
                    "group": {
                        "id": listing.group_id,
                        "name": listing.slug,
                        "metadata": {
                            "displayName": listing.display_name,
                            "description": listing.description,
                            "totalMembers": listing.total_members,
                            "displayPrice": listing.display_price,
                            "membershipModel": listing.membership_model,
                        },
                    },
                    "rank": listing.rank,
                    "tags": listing.tags,
                }
            )
        return {
            "groups": groups,
            "numGroups": self.total_available,
            "hasMore": self.has_more,
        }


def _required_string(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected non-empty string")
    return value


def _required_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("expected integer")
    return value


def _display_price(value: Any) -> str:
    structured = value
    if isinstance(value, str) and value.startswith("{"):
        try:
            structured = json.loads(value)
        except json.JSONDecodeError:
            return value
    if not isinstance(structured, dict):
        return str(value or "")
    amount = structured.get("amount")
    currency = structured.get("currency")
    interval = structured.get("recurring_interval")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        return str(value or "")
    major_amount = amount / 100
    number = f"{major_amount:,.0f}" if major_amount.is_integer() else f"{major_amount:,.2f}"
    symbols = {"usd": "$", "eur": "€", "gbp": "£"}
    currency_text = str(currency or "").lower()
    prefix = symbols.get(currency_text, "")
    suffix = "" if prefix else f" {currency_text.upper()}"
    recurring = f"/{interval}" if isinstance(interval, str) and interval else ""
    return f"{prefix}{number}{suffix}{recurring}"


def _tags(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("expected tags list")
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item:
            result.append(item)
        elif isinstance(item, dict):
            label = item.get("name") or item.get("displayName") or item.get("slug")
            if isinstance(label, str) and label:
                result.append(label)
    return result


def parse_discovery_html(html: str) -> DiscoveryPage:
    parser = _NextDataParser()
    parser.feed(html)
    if not parser.content:
        raise SchemaError("Skool Discovery HTML contains no Next.js data")
    try:
        document = json.loads(parser.content)
        page_props = document["props"]["pageProps"]
        groups = page_props["groups"]
        has_more = page_props["hasMore"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SchemaError("Skool Discovery Next.js schema changed") from exc
    if not isinstance(page_props, dict) or not isinstance(groups, list):
        raise SchemaError("Skool Discovery Next.js schema changed")
    if not isinstance(has_more, bool):
        raise SchemaError("Skool Discovery pagination schema changed")

    listings: list[DiscoveryListing] = []
    try:
        for position, item in enumerate(groups, start=1):
            if not isinstance(item, dict):
                raise ValueError("expected listing object")
            group = item["group"]
            if not isinstance(group, dict):
                raise ValueError("expected group object")
            metadata = group["metadata"]
            if not isinstance(metadata, dict):
                raise ValueError("expected metadata object")
            slug = _required_string(group["name"])
            source_rank = _required_int(item.get("rank", position))
            listings.append(
                DiscoveryListing(
                    group_id=_required_string(group["id"]),
                    slug=slug,
                    display_name=_required_string(metadata["displayName"]),
                    url=f"https://www.skool.com/{slug}",
                    rank=source_rank if source_rank > 0 else position,
                    description=str(metadata.get("description") or ""),
                    total_members=_required_int(metadata["totalMembers"]),
                    display_price=_display_price(metadata.get("displayPrice")),
                    membership_model=str(metadata.get("membershipModel") or ""),
                    tags=_tags(item.get("tags")),
                )
            )
        total_available = page_props.get("numGroups")
        if total_available is not None:
            total_available = _required_int(total_available)
    except (KeyError, TypeError, ValueError) as exc:
        raise SchemaError("Skool Discovery listing schema changed") from exc

    return DiscoveryPage(
        listings=listings,
        result_count=len(listings),
        total_available=total_available,
        has_more=has_more,
    )


class DiscoveryHttpClient:
    """Anonymous transport constrained to Skool's public Discovery page."""

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        curl_runner: Callable[..., Any] | None = None,
    ) -> None:
        self._curl_runner = curl_runner or subprocess.run
        self._client = (
            httpx.Client(
                headers={
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "user-agent": _BROWSER_USER_AGENT,
                },
                timeout=15.0,
                follow_redirects=True,
                transport=transport,
            )
            if transport is not None
            else None
        )

    def __enter__(self) -> "DiscoveryHttpClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def get_page(
        self, *, page: int = 1, query: str | None = None
    ) -> DiscoveryPage:
        url = _discovery_url(page=page, query=query)
        if self._client is None:
            return self._get_page_with_curl(url)
        try:
            response = self._client.get(url)
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise TransportError("Skool Discovery request failed") from exc
        try:
            final_url = urlsplit(str(response.url))
            if final_url.scheme != "https" or final_url.netloc != "www.skool.com":
                raise TransportError("Skool Discovery redirected to an untrusted location")
            if response.status_code >= 400:
                raise TransportError(
                    f"Skool returned HTTP {response.status_code} for Discovery"
                )
            if "html" not in response.headers.get("content-type", "").lower():
                raise SchemaError("Skool returned non-HTML Discovery content")
            return parse_discovery_html(response.text)
        finally:
            response.close()

    def _get_page_with_curl(self, url: str) -> DiscoveryPage:
        meta_format = f"\\n{_CURL_META}%{{http_code}}|%{{content_type}}|%{{url_effective}}"
        command = [
            "curl",
            "--silent",
            "--show-error",
            "--location",
            "--max-redirs",
            "3",
            "--max-time",
            "15",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--user-agent",
            _BROWSER_USER_AGENT,
            "--write-out",
            meta_format,
            url,
        ]
        try:
            completed = self._curl_runner(
                command,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TransportError("Skool Discovery request failed") from exc
        if completed.returncode != 0:
            raise TransportError("Skool Discovery request failed")
        marker = f"\n{_CURL_META}"
        if marker not in completed.stdout:
            raise TransportError("Skool Discovery response metadata is missing")
        html, metadata = completed.stdout.rsplit(marker, 1)
        try:
            status_text, content_type, final_url_text = metadata.split("|", 2)
            status = int(status_text)
        except (ValueError, TypeError) as exc:
            raise TransportError("Skool Discovery response metadata is invalid") from exc
        final_url = urlsplit(final_url_text.strip())
        if final_url.scheme != "https" or final_url.netloc != "www.skool.com":
            raise TransportError("Skool Discovery redirected to an untrusted location")
        if status >= 400:
            raise TransportError(f"Skool returned HTTP {status} for Discovery")
        if "html" not in content_type.lower():
            raise SchemaError("Skool returned non-HTML Discovery content")
        return parse_discovery_html(html)


@dataclass(frozen=True)
class DiscoveryRunResult:
    run_id: str
    captured_at: datetime
    status: RunStatus
    page: DiscoveryPage | None
    error: str | None = None
    elapsed_seconds: float = 0.0


class DiscoveryService:
    def __init__(self, storage: Storage, raw_root: Path) -> None:
        self._storage = storage
        self._raw_root = raw_root

    def run(
        self,
        client: DiscoveryHttpClient,
        captured_at: datetime,
        *,
        page: int = 1,
        query: str | None = None,
    ) -> DiscoveryRunResult:
        run_id = self._storage.begin_discovery_run(captured_at)
        started = time.monotonic()
        try:
            page_result = client.get_page(page=page, query=query)
            artifact = write_payload(
                self._raw_root,
                run_id,
                "discovery",
                page_result.sanitized_payload(),
            )
            self._storage.complete_discovery_run(run_id, page_result, artifact)
        except SkoolDataError as exc:
            message = str(exc)
            self._storage.fail_discovery_run(run_id, message)
            return DiscoveryRunResult(
                run_id=run_id,
                captured_at=captured_at,
                status=RunStatus.failed,
                page=None,
                error=message,
                elapsed_seconds=time.monotonic() - started,
            )
        except OSError:
            message = "Could not persist Discovery artifact"
            self._storage.fail_discovery_run(run_id, message)
            return DiscoveryRunResult(
                run_id=run_id,
                captured_at=captured_at,
                status=RunStatus.failed,
                page=None,
                error=message,
                elapsed_seconds=time.monotonic() - started,
            )
        return DiscoveryRunResult(
            run_id=run_id,
            captured_at=captured_at,
            status=RunStatus.complete,
            page=page_result,
            elapsed_seconds=time.monotonic() - started,
        )
