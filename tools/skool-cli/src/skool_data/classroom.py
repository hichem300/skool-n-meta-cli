import json
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlsplit

from skool_data.errors import ConfigurationError, SchemaError


@dataclass(frozen=True)
class ClassroomResource:
    label: str
    url: str


@dataclass(frozen=True)
class ClassroomVideo:
    source_url: str
    identity: str
    duration_seconds: int | None


@dataclass(frozen=True)
class ClassroomModule:
    course_title: str
    module_id: str
    title: str
    content: str | None
    video_url: str | None
    resources: tuple[ClassroomResource, ...]
    position: int | None = None
    video: ClassroomVideo | None = None


def validate_module_url(value: str) -> str:
    parsed = urlsplit(value)
    parts = parsed.path.strip("/").split("/")
    query = parse_qs(parsed.query)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.skool.com"
        or len(parts) != 3
        or parts[1] != "classroom"
        or not all(parts)
        or not query.get("md", [""])[0]
    ):
        raise ConfigurationError("Provide a valid Skool classroom module URL")
    return value


class _NextDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._capturing = False
        self.parts: list[str] = []

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
            self.parts.append(data)


def parse_classroom_html(html: str) -> ClassroomModule:
    parser = _NextDataParser()
    parser.feed(html)
    if not parser.parts:
        raise SchemaError("Skool classroom HTML contains no Next.js data")
    try:
        page_props = json.loads("".join(parser.parts))["props"]["pageProps"]
        selected = page_props["selectedModule"]
        course_data = page_props["course"]
        course = course_data["course"]
        children = course_data["children"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SchemaError("Skool classroom Next.js data schema changed") from exc
    if not isinstance(selected, str) or not selected:
        raise SchemaError("Skool classroom has no selected module")
    if not isinstance(children, list) or not children:
        raise SchemaError("Skool classroom has no selected module data")

    selected_course, position = _selected_child(children)
    metadata = selected_course.get("metadata")
    if not isinstance(metadata, dict):
        raise SchemaError("Skool selected module metadata is invalid")

    title = metadata.get("title")
    course_metadata = course.get("metadata") if isinstance(course, dict) else None
    course_title = (
        course_metadata.get("title")
        if isinstance(course_metadata, dict)
        else None
    )
    if not isinstance(title, str) or not title.strip():
        raise SchemaError("Skool selected module has no title")
    if not isinstance(course_title, str) or not course_title.strip():
        raise SchemaError("Skool classroom course has no title")

    content = next(
        (
            metadata[key].strip()
            for key in ("content", "description", "desc", "body")
            if isinstance(metadata.get(key), str) and metadata[key].strip()
        ),
        None,
    )
    video_url = metadata.get("videoLink")
    if not isinstance(video_url, str) or not _is_http_url(video_url):
        video_url = None

    return ClassroomModule(
        course_title=course_title.strip(),
        module_id=selected,
        title=title.strip(),
        content=content,
        video_url=video_url,
        resources=_parse_resources(metadata.get("resources", "[]")),
        position=position,
        video=_parse_video(page_props.get("video"), video_url),
    )


def _selected_child(children: list[Any]) -> tuple[dict[str, Any], int]:
    candidates: list[tuple[dict[str, Any], int]] = []
    for position, child in enumerate(children, start=1):
        if isinstance(child, dict) and isinstance(child.get("course"), dict):
            candidates.append((child["course"], position))
    if not candidates:
        raise SchemaError("Skool classroom has no selected module data")
    enriched = [
        candidate
        for candidate in candidates
        if isinstance(candidate[0].get("metadata"), dict)
        and set(candidate[0]["metadata"]) - {"title", "hasAccess"}
    ]
    return enriched[0] if enriched else candidates[0]


def _parse_video(value: Any, fallback_url: str | None) -> ClassroomVideo | None:
    if isinstance(value, dict):
        playback_id = value.get("playbackId")
        token = value.get("playbackToken")
        duration = value.get("duration")
        if isinstance(playback_id, str) and playback_id and isinstance(token, str) and token:
            return ClassroomVideo(
                source_url=(
                    f"https://stream.video.skool.com/{playback_id}.m3u8?token={token}"
                ),
                identity=f"skool:{playback_id}",
                duration_seconds=duration if isinstance(duration, int) else None,
            )
    if fallback_url:
        parsed = urlsplit(fallback_url)
        identity_path = parsed.path.rstrip("/") or "/"
        return ClassroomVideo(
            source_url=fallback_url,
            identity=f"external:{parsed.netloc}{identity_path}",
            duration_seconds=None,
        )
    return None


def _parse_resources(value: Any) -> tuple[ClassroomResource, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise SchemaError("Skool module resources are malformed") from exc
    if not isinstance(value, list):
        raise SchemaError("Skool module resources are malformed")
    resources: list[ClassroomResource] = []
    for item in value:
        if not isinstance(item, dict):
            raise SchemaError("Skool module resources are malformed")
        url = item.get("url") or item.get("link")
        if not isinstance(url, str) or not _is_http_url(url):
            raise SchemaError("Skool module resource URL is invalid")
        label = next(
            (
                item[key].strip()
                for key in ("label", "title", "name")
                if isinstance(item.get(key), str) and item[key].strip()
            ),
            PurePosixPath(urlsplit(url).path).name or "Resource",
        )
        resources.append(ClassroomResource(label=label, url=url))
    return tuple(resources)


def _is_http_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def safe_slug(value: str, fallback: str = "module") -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")
    return slug or fallback


class _MarkdownParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._ignored = 0
        self._links: list[str | None] = []
        self._list_depth = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag in {"script", "style"}:
            self._ignored += 1
            return
        if self._ignored:
            return
        attributes = dict(attrs)
        if tag in {"p", "div"}:
            self.parts.append("\n\n")
        elif tag == "br":
            self.parts.append("\n")
        elif tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("*")
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in {"ul", "ol"}:
            self._list_depth += 1
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n" + "  " * max(0, self._list_depth - 1) + "- ")
        elif tag == "a":
            href = attributes.get("href")
            self._links.append(href if href and _is_http_url(href) else None)
            self.parts.append("[")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._ignored:
            self._ignored -= 1
            return
        if self._ignored:
            return
        if tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("*")
        elif tag == "a":
            href = self._links.pop() if self._links else None
            self.parts.append(f"]({href})" if href else "]")
        elif tag in {"ul", "ol"}:
            self._list_depth = max(0, self._list_depth - 1)
            self.parts.append("\n")
        elif tag in {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored:
            self.parts.append(data)

    def markdown(self) -> str:
        value = "".join(self.parts)
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r" *\n *", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()


def _content_to_markdown(content: str) -> str:
    parser = _MarkdownParser()
    parser.feed(content)
    return parser.markdown()


def render_markdown(
    module: ClassroomModule,
    local_resources: dict[str, str],
    *,
    local_video: str | None = None,
    local_transcript: str | None = None,
) -> str:
    sections = [f"# {module.title}", f"Course: {module.course_title}"]
    if module.content:
        rendered = _content_to_markdown(module.content)
        if rendered:
            sections.append(rendered)
    if module.video_url or local_video:
        video_links: list[str] = []
        if local_video:
            video_links.append(f"[Downloaded video]({local_video})")
        if module.video_url:
            label = "Watch on the source site" if local_video else "Watch the module video"
            video_links.append(f"[{label}]({module.video_url})")
        sections.append("## Video\n\n" + "\n\n".join(video_links))
    if local_transcript:
        sections.append(f"## Transcript\n\n[Transcript]({local_transcript})")
    if module.resources:
        lines = ["## Resources"]
        for resource in module.resources:
            target = local_resources.get(resource.url, resource.url)
            lines.append(f"- [{resource.label}]({target})")
        sections.append("\n".join(lines))
    return "\n\n".join(sections).rstrip() + "\n"
