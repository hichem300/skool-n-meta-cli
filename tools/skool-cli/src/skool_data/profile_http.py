import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlsplit, urlunsplit

import httpx

from skool_data.auth import without_waf
from skool_data.errors import (
    AuthenticationError,
    ConfigurationError,
    SchemaError,
    TransportError,
)
from skool_data.models import Credentials

_PROFILE_PATH = re.compile(r"^/_next/data/[^/]+/(%40[^/]+)\.json$")


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


class ProfileHttpClient:
    """Constrained transport for profile activity embedded in Skool HTML."""

    def __init__(
        self,
        credentials: Credentials,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if credentials.profile_data_url is None:
            raise ConfigurationError(
                "Credentials contain no profile route; import a fresh profile HAR"
            )
        configured_url = str(credentials.profile_data_url)
        parsed = urlsplit(configured_url)
        match = _PROFILE_PATH.fullmatch(parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "www.skool.com"
            or match is None
        ):
            raise ConfigurationError("Credentials contain an untrusted profile route")
        group = parse_qs(parsed.query).get("g", [""])[0]
        if not group:
            raise ConfigurationError("Credentials contain an incomplete profile route")
        profile_slug = unquote(match.group(1))
        self._url = urlunsplit(
            ("https", "www.skool.com", f"/{profile_slug}", urlencode({"g": group}), "")
        )
        self._client = httpx.Client(
            headers=without_waf(credentials.headers),
            timeout=15.0,
            transport=transport,
        )

    def __enter__(self) -> "ProfileHttpClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get_json(self) -> dict[str, Any]:
        try:
            response = self._client.get(self._url)
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise TransportError("Profile analytics request failed") from exc
        try:
            if response.status_code in {401, 403}:
                raise AuthenticationError("Skool rejected profile credentials")
            if response.status_code >= 400:
                raise TransportError(
                    f"Skool returned HTTP {response.status_code} for profile analytics"
                )
            if "html" not in response.headers.get("content-type", "").lower():
                raise SchemaError("Skool returned non-HTML profile analytics")
            parser = _NextDataParser()
            parser.feed(response.text)
            if not parser.content:
                raise SchemaError("Skool profile HTML contains no Next.js data")
            try:
                document = json.loads(parser.content)
                page_props = document["props"]["pageProps"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise SchemaError("Skool profile Next.js data schema changed") from exc
            if not isinstance(page_props, dict):
                raise SchemaError("Skool profile page properties are not an object")
            return {"pageProps": page_props}
        finally:
            response.close()
