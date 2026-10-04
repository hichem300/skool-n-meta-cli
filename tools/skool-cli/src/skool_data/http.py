import re
from typing import Any

import httpx

from skool_data.auth import without_waf
from skool_data.errors import AuthenticationError, SchemaError, TransportError
from skool_data.models import Credentials

_ENDPOINT = re.compile(r"^[a-z0-9-]+$")
_RETRY_STATUSES = {429, 500, 502, 503, 504}


class SkoolHttpClient:
    """Small authenticated HTTP transport for one configured Skool group."""

    def __init__(
        self,
        credentials: Credentials,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._credentials = credentials
        self._client = httpx.Client(
            headers=without_waf(credentials.headers),
            timeout=15.0,
            transport=transport,
        )

    def __enter__(self) -> "SkoolHttpClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get_json(
        self, path: str, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        if not _ENDPOINT.fullmatch(path):
            raise ValueError("Endpoint path is not allowed")
        base = str(self._credentials.api_base_url).rstrip("/")
        url = f"{base}/groups/{self._credentials.community_id}/{path}"
        response: httpx.Response | None = None
        for attempt in range(3):
            try:
                response = self._client.get(url, params=params)
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                if attempt < 2:
                    continue
                raise TransportError("Skool request failed after retries") from exc
            if response.status_code in _RETRY_STATUSES and attempt < 2:
                response.close()
                continue
            break

        if response is None:
            raise TransportError("Skool request produced no response")
        try:
            if response.status_code in {401, 403}:
                raise AuthenticationError(
                    "Skool rejected the credentials; import a fresh HAR"
                )
            if response.status_code >= 400:
                raise TransportError(
                    f"Skool returned HTTP {response.status_code} for {path}"
                )
            content_type = response.headers.get("content-type", "").lower()
            if "json" not in content_type:
                raise SchemaError(f"Skool returned non-JSON content for {path}")
            try:
                payload = response.json()
            except ValueError as exc:
                raise SchemaError(f"Skool returned invalid JSON for {path}") from exc
            if not isinstance(payload, dict):
                raise SchemaError(f"Skool returned a non-object JSON value for {path}")
            return payload
        finally:
            response.close()
