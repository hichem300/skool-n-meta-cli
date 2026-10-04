import hashlib
import json
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from skool_data.auth import without_waf
from skool_data.classroom import (
    ClassroomModule,
    parse_classroom_html,
    validate_module_url,
)
from skool_data.errors import (
    AuthenticationError,
    ConfigurationError,
    SchemaError,
    TransportError,
)
from skool_data.models import Credentials


class ClassroomHttpClient:
    """Constrained transport for one stable Skool classroom module page."""

    def __init__(
        self,
        credentials: Credentials,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            headers=without_waf(credentials.headers),
            follow_redirects=False,
            timeout=20.0,
            transport=transport,
        )

    def __enter__(self) -> "ClassroomHttpClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def fetch_module(self, url: str) -> ClassroomModule:
        trusted_url = validate_module_url(url)
        try:
            response = self._client.get(trusted_url)
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise TransportError("Skool classroom request failed") from exc
        try:
            if response.status_code in {401, 403}:
                raise AuthenticationError("Skool rejected classroom credentials")
            if 300 <= response.status_code < 400:
                location = response.headers.get("location", "")
                if urlsplit(urljoin(trusted_url, location)).path.startswith("/login"):
                    raise AuthenticationError("Skool rejected classroom credentials")
                raise TransportError("Skool redirected the classroom request")
            if response.status_code >= 400:
                raise TransportError(
                    f"Skool returned HTTP {response.status_code} for classroom export"
                )
            if "html" not in response.headers.get("content-type", "").lower():
                raise SchemaError("Skool returned non-HTML classroom data")
            return parse_classroom_html(response.text)
        finally:
            response.close()


class ResourceHttpClient:
    """Stream module resources without leaking Skool credentials across hosts."""

    def __init__(
        self,
        credentials: Credentials,
        *,
        trusted_transport: httpx.BaseTransport | None = None,
        external_transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._trusted = httpx.Client(
            headers=without_waf(credentials.headers),
            follow_redirects=False,
            timeout=30.0,
            transport=trusted_transport,
        )
        self._external = httpx.Client(
            follow_redirects=True,
            timeout=30.0,
            transport=external_transport,
        )

    def __enter__(self) -> "ResourceHttpClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._trusted.close()
        self._external.close()

    def inspect(self, url: str) -> str:
        _validate_resource_url(url)
        parsed = urlsplit(url)
        normalized = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        client = self._trusted if parsed.hostname == "www.skool.com" else self._external
        validators: dict[str, str] = {}
        try:
            response = client.head(url)
        except (httpx.TimeoutException, httpx.RequestError):
            response = None
        if response is not None:
            try:
                if 200 <= response.status_code < 300:
                    validators = {
                        key: value
                        for key in ("etag", "last-modified", "content-length")
                        if (value := response.headers.get(key))
                    }
            finally:
                response.close()
        encoded = json.dumps(
            {"url": normalized, "validators": validators},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode()).hexdigest()

    def download(self, url: str, destination: Path) -> None:
        _validate_resource_url(url)
        try:
            if urlsplit(url).hostname == "www.skool.com":
                self._download_trusted(url, destination)
            else:
                self._download_external(url, destination)
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise TransportError("Classroom resource download failed") from exc

    def _download_trusted(
        self, url: str, destination: Path, redirects: int = 0
    ) -> None:
        if redirects > 5:
            raise TransportError("Skool resource redirected too many times")
        with self._trusted.stream("GET", url) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise TransportError("Skool resource redirect is incomplete")
                redirected = urljoin(url, location)
                _validate_resource_url(redirected)
                if urlsplit(redirected).hostname == "www.skool.com":
                    self._download_trusted(redirected, destination, redirects + 1)
                else:
                    self._download_external(redirected, destination)
                return
            self._write_response(response, destination)

    def _download_external(self, url: str, destination: Path) -> None:
        with self._external.stream("GET", url) as response:
            self._write_response(response, destination)

    @staticmethod
    def _write_response(response: httpx.Response, destination: Path) -> None:
        if response.status_code >= 400:
            raise TransportError(
                f"Resource server returned HTTP {response.status_code}"
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise TransportError("Resource server returned an unexpected response")
        with destination.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)


def _validate_resource_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError("Skool module resource URL is invalid")
