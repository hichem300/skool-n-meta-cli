import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, cast
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from skool_data.classroom import ClassroomVideo
from skool_data.errors import ConfigurationError, SchemaError, TransportError


@dataclass(frozen=True)
class MediaInspection:
    video_fingerprint: str
    video_extension: str
    transcript_url: str | None
    transcript_identity: str | None
    transcript_fingerprint: str | None


class MediaDownloader:
    """Inspect and download module media without using Skool credentials."""

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        info_loader: Callable[[str], dict[str, Any]] | None = None,
        video_writer: Callable[[str, Path], None] | None = None,
    ) -> None:
        self._client = httpx.Client(
            follow_redirects=True,
            timeout=30.0,
            transport=transport,
        )
        self._info_loader = info_loader or _load_media_info
        self._video_writer = video_writer or _write_video

    def __enter__(self) -> "MediaDownloader":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def inspect(self, video: ClassroomVideo) -> MediaInspection:
        try:
            info = self._info_loader(video.source_url)
        except (DownloadError, OSError, ValueError) as exc:
            raise TransportError("Could not inspect classroom video") from exc
        if not isinstance(info, dict):
            raise SchemaError("Video provider returned invalid metadata")

        formats = info.get("formats")
        available = [item for item in formats or [] if isinstance(item, dict)]
        best = max(
            available,
            key=lambda item: (
                item.get("height") or 0,
                item.get("tbr") or 0,
                item.get("filesize") or item.get("filesize_approx") or 0,
            ),
            default={},
        )
        format_url = best.get("url") if isinstance(best.get("url"), str) else None
        video_validator = self._remote_validator(format_url) if format_url else None
        extension = best.get("ext") if isinstance(best.get("ext"), str) else "mp4"
        if extension not in {"mp4", "mkv", "webm", "mov"}:
            extension = "mp4"

        transcript_url, transcript_identity = self._transcript_source(
            video.source_url, info
        )
        transcript_validator = (
            self._remote_validator(transcript_url) if transcript_url else None
        )
        video_fingerprint = _fingerprint(
            {
                "identity": video.identity,
                "provider_id": info.get("id"),
                "duration": info.get("duration") or video.duration_seconds,
                "timestamp": info.get("timestamp"),
                "release_timestamp": info.get("release_timestamp"),
                "format": {
                    "id": best.get("format_id"),
                    "height": best.get("height"),
                    "width": best.get("width"),
                    "filesize": best.get("filesize") or best.get("filesize_approx"),
                },
                "validator": video_validator,
            }
        )
        transcript_fingerprint = (
            _fingerprint(
                {
                    "identity": transcript_identity,
                    "validator": transcript_validator,
                }
            )
            if transcript_url and transcript_identity
            else None
        )
        return MediaInspection(
            video_fingerprint=video_fingerprint,
            video_extension=extension,
            transcript_url=transcript_url,
            transcript_identity=transcript_identity,
            transcript_fingerprint=transcript_fingerprint,
        )

    def download_video(self, video: ClassroomVideo, destination: Path) -> None:
        try:
            self._video_writer(video.source_url, destination)
        except (DownloadError, OSError, ValueError) as exc:
            raise TransportError("Classroom video download failed") from exc
        if not destination.is_file() or destination.stat().st_size == 0:
            raise TransportError("Classroom video download produced no file")

    def download_transcript(self, url: str, destination: Path) -> None:
        _require_http_url(url)
        try:
            if urlsplit(url).path.endswith(".m3u8"):
                self._download_hls_transcript(url, destination)
            else:
                with self._client.stream("GET", url) as response:
                    self._write_transcript_response(response, destination)
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise TransportError("Classroom transcript download failed") from exc
        if not destination.is_file() or destination.stat().st_size == 0:
            raise TransportError("Classroom transcript download produced no file")

    @staticmethod
    def _write_transcript_response(
        response: httpx.Response, destination: Path
    ) -> None:
        if response.status_code >= 400:
            raise TransportError(
                f"Transcript server returned HTTP {response.status_code}"
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise TransportError("Transcript server returned an unexpected response")
        with destination.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)

    def _download_hls_transcript(
        self, playlist_url: str, destination: Path, depth: int = 0
    ) -> None:
        if depth > 3:
            raise TransportError("Transcript playlist nested too deeply")
        response = self._client.get(playlist_url)
        try:
            if response.status_code >= 400:
                raise TransportError(
                    f"Transcript server returned HTTP {response.status_code}"
                )
            entries = [
                line.strip()
                for line in response.text.splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
        finally:
            response.close()
        if len(entries) == 1 and urlsplit(entries[0]).path.endswith(".m3u8"):
            self._download_hls_transcript(
                urljoin(playlist_url, entries[0]), destination, depth + 1
            )
            return
        if not entries:
            raise SchemaError("Transcript playlist contains no caption segments")
        cue_parts: list[str] = []
        for entry in entries:
            segment_url = urljoin(playlist_url, entry)
            segment = self._client.get(segment_url)
            try:
                if segment.status_code >= 400:
                    raise TransportError(
                        f"Transcript server returned HTTP {segment.status_code}"
                    )
                text = re.sub(r"^\ufeff?WEBVTT[^\r\n]*\r?\n", "", segment.text)
                cue_parts.append(text.strip())
            finally:
                segment.close()
        destination.write_text(
            "WEBVTT\n\n" + "\n\n".join(cue_parts).strip() + "\n",
            encoding="utf-8",
        )

    def _transcript_source(
        self, source_url: str, info: dict[str, Any]
    ) -> tuple[str | None, str | None]:
        parsed = urlsplit(source_url)
        if parsed.hostname in {"loom.com", "www.loom.com"}:
            video_id = parsed.path.rstrip("/").rsplit("/", 1)[-1]
            if video_id:
                loom_source = self._loom_transcript(video_id)
                if loom_source[0]:
                    return loom_source
        subtitles = info.get("subtitles") or info.get("automatic_captions") or {}
        if isinstance(subtitles, dict):
            preferred = list(subtitles.get("en") or [])
            if not preferred:
                for language, entries in subtitles.items():
                    if language != "live_chat" and isinstance(entries, list):
                        preferred.extend(entries)
                        if preferred:
                            break
            candidates = [
                entry
                for entry in preferred
                if isinstance(entry, dict)
                and isinstance(entry.get("url"), str)
                and entry.get("ext", "vtt") in {"vtt", "srv3", "ttml", "json3"}
            ]
            vtt = next((entry for entry in candidates if entry.get("ext") == "vtt"), None)
            selected = vtt or (candidates[0] if candidates else None)
            if selected:
                url = selected["url"]
                return url, _normalized_url(url)
        if parsed.path.endswith(".m3u8"):
            return self._hls_transcript(source_url)
        return None, None

    def _hls_transcript(self, source_url: str) -> tuple[str | None, str | None]:
        try:
            response = self._client.get(source_url)
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise TransportError("Could not inspect Skool transcript") from exc
        try:
            if response.status_code >= 400:
                return None, None
            match = re.search(
                r'#EXT-X-MEDIA:[^\r\n]*TYPE=SUBTITLES[^\r\n]*URI="([^"]+)"',
                response.text,
            )
            if not match:
                return None, None
            transcript_url = urljoin(source_url, match.group(1))
            _require_http_url(transcript_url)
            return transcript_url, _normalized_url(transcript_url)
        finally:
            response.close()

    def _loom_transcript(self, video_id: str) -> tuple[str | None, str | None]:
        embed_url = f"https://www.loom.com/embed/{video_id}"
        try:
            response = self._client.get(embed_url)
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise TransportError("Could not inspect Loom transcript") from exc
        try:
            if response.status_code >= 400:
                return None, None
            source_match = re.search(
                r'"captions_source_url"\s*:\s*("(?:\\.|[^"\\])*")',
                response.text,
            )
            if not source_match:
                return None, None
            source = json.loads(source_match.group(1))
            if not isinstance(source, str):
                return None, None
            _require_http_url(source)
            identity_match = re.search(
                r'"captions_url"\s*:\s*("(?:\\.|[^"\\])*")',
                response.text,
            )
            identity = (
                json.loads(identity_match.group(1))
                if identity_match
                else _normalized_url(source)
            )
            return source, identity if isinstance(identity, str) else _normalized_url(source)
        finally:
            response.close()

    def _remote_validator(self, url: str | None) -> dict[str, str] | None:
        if not url:
            return None
        _require_http_url(url)
        try:
            response = self._client.head(url)
        except (httpx.TimeoutException, httpx.RequestError):
            return None
        try:
            if response.status_code >= 400:
                return None
            values = {
                key: value
                for key in ("etag", "last-modified", "content-length")
                if (value := response.headers.get(key))
            }
            return values or None
        finally:
            response.close()


def _load_media_info(url: str) -> dict[str, Any]:
    options: Any = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "noplaylist": True,
    }
    with YoutubeDL(options) as downloader:
        result = downloader.extract_info(url, download=False)
    if not isinstance(result, dict):
        raise ValueError("Media provider returned no metadata")
    return cast(dict[str, Any], result)


def _write_video(url: str, destination: Path) -> None:
    with YoutubeDL(
        {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "format": "bestvideo*+bestaudio/best",
            "merge_output_format": "mp4",
            "outtmpl": str(destination),
            "overwrites": True,
        }
    ) as downloader:
        code = downloader.download([url])
    if code:
        raise DownloadError("Video download failed")


def _fingerprint(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _normalized_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _require_http_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError("Classroom media URL is invalid")


def vtt_to_markdown(value: str) -> str:
    blocks = re.split(r"\r?\n\s*\r?\n", value.strip())
    sections = ["# Transcript"]
    timestamp = re.compile(
        r"(?:(\d{2}):)?(\d{2}):(\d{2})[.,]\d{3}\s+-->\s+.*"
    )
    previous_text = None
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines or lines[0] == "WEBVTT" or lines[0].startswith(("NOTE", "STYLE")):
            continue
        if lines[0].isdigit():
            lines = lines[1:]
        if not lines:
            continue
        match = timestamp.match(lines[0])
        if not match:
            continue
        text = " ".join(lines[1:])
        text = re.sub(r"<[^>]+>", "", text).strip()
        if not text or text == previous_text:
            continue
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2))
        seconds = int(match.group(3))
        clock = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        sections.append(f"**{clock}**\n\n{text}")
        previous_text = text
    return "\n\n".join(sections).rstrip() + "\n"
