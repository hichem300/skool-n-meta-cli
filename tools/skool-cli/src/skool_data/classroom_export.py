import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from skool_data.classroom import ClassroomModule, render_markdown, safe_slug
from skool_data.classroom_media import MediaDownloader, MediaInspection, vtt_to_markdown
from skool_data.errors import ExportError


class ModuleFetcher(Protocol):
    def fetch_module(self, url: str) -> ClassroomModule: ...


class ResourceFetcher(Protocol):
    def inspect(self, url: str) -> str: ...

    def download(self, url: str, destination: Path) -> None: ...


class MediaFetcher(Protocol):
    def inspect(self, video: object) -> MediaInspection: ...

    def download_video(self, video: object, destination: Path) -> None: ...

    def download_transcript(self, url: str, destination: Path) -> None: ...


@dataclass(frozen=True)
class ClassroomExportResult:
    markdown_path: Path
    asset_paths: tuple[Path, ...]
    updated: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()


class ClassroomExportService:
    def run(
        self,
        module_url: str,
        output: Path,
        module_client: ModuleFetcher,
        resource_client: ResourceFetcher,
        media_client: MediaFetcher,
        *,
        force: bool = False,
        on_event: Callable[[str], None] | None = None,
    ) -> ClassroomExportResult:
        emit = on_event or (lambda message: None)
        emit("Fetching classroom module")
        module = module_client.fetch_module(module_url)
        if output.exists() and not output.is_dir():
            raise ExportError("Classroom export output is not a directory")
        output.mkdir(parents=True, exist_ok=True)

        course_dir = output / safe_slug(module.course_title, "course")
        module_name = safe_slug(module.title)
        if module.position is not None:
            module_name = f"{module.position:02d}-{module_name}"
        module_dir = course_dir / module_name
        manifest_path = module_dir / ".skool-export.json"
        previous_manifest = _find_module_manifest(output, module.module_id)
        if previous_manifest is not None and previous_manifest != manifest_path:
            previous_dir = previous_manifest.parent
            if module_dir.exists():
                raise ExportError(
                    "Updated classroom category conflicts with an existing directory"
                )
            module_dir.parent.mkdir(parents=True, exist_ok=True)
            old_course_dir = previous_dir.parent
            try:
                os.replace(previous_dir, module_dir)
            except OSError as exc:
                raise ExportError("Could not update classroom category") from exc
            if old_course_dir != module_dir.parent:
                try:
                    old_course_dir.rmdir()
                except OSError:
                    pass
        old_manifest = _load_manifest(manifest_path, module.module_id)
        old_artifacts = old_manifest.get("artifacts", {})
        if not isinstance(old_artifacts, dict):
            old_artifacts = {}

        count = len(module.resources)
        noun = "resource" if count == 1 else "resources"
        emit(f'Found module "{module.title}" with {count} {noun}')

        resource_specs: list[tuple[str, str, str, str]] = []
        used_names: set[str] = set()
        for resource in module.resources:
            key = _resource_key(resource.url)
            old = old_artifacts.get(key, {})
            old_path = old.get("path") if isinstance(old, dict) else None
            if isinstance(old_path, str) and old_path.startswith("assets/"):
                filename = Path(old_path).name
            else:
                filename = _resource_filename(resource.url, used_names)
            if filename in used_names:
                filename = _resource_filename(resource.url, used_names)
            used_names.add(filename)
            resource_specs.append(
                (key, resource.label, resource.url, f"assets/{filename}")
            )

        media_inspection = (
            media_client.inspect(module.video) if module.video is not None else None
        )
        desired: dict[str, dict[str, str]] = {}
        changed: list[tuple[str, str, Callable[[Path], None]]] = []
        updated: list[str] = []
        skipped: list[str] = []
        local_resources: dict[str, str] = {}

        for key, label, url, relative in resource_specs:
            fingerprint = resource_client.inspect(url)
            desired[key] = {
                "kind": "asset",
                "label": label,
                "path": relative,
                "fingerprint": fingerprint,
            }
            local_resources[url] = relative
            if _artifact_current(
                module_dir, old_artifacts.get(key), relative, fingerprint, force
            ):
                skipped.append(label)
            else:
                changed.append(
                    (
                        key,
                        relative,
                        lambda path, source=url: resource_client.download(source, path),
                    )
                )
                updated.append(label)

        local_video: str | None = None
        local_transcript: str | None = None
        if module.video is not None and media_inspection is not None:
            local_video = f"video/video.{media_inspection.video_extension}"
            desired["video"] = {
                "kind": "video",
                "path": local_video,
                "fingerprint": media_inspection.video_fingerprint,
            }
            if _artifact_current(
                module_dir,
                old_artifacts.get("video"),
                local_video,
                media_inspection.video_fingerprint,
                force,
            ):
                skipped.append("video")
            else:
                changed.append(
                    (
                        "video",
                        local_video,
                        lambda path: media_client.download_video(module.video, path),
                    )
                )
                updated.append("video")

            if (
                media_inspection.transcript_url
                and media_inspection.transcript_fingerprint
            ):
                local_transcript = "transcript/transcript.md"
                vtt_path = "transcript/transcript.vtt"
                transcript_fingerprint = media_inspection.transcript_fingerprint
                desired["transcript_vtt"] = {
                    "kind": "transcript",
                    "path": vtt_path,
                    "fingerprint": transcript_fingerprint,
                    "identity": media_inspection.transcript_identity or "",
                }
                transcript_changed = not _artifact_current(
                    module_dir,
                    old_artifacts.get("transcript_vtt"),
                    vtt_path,
                    transcript_fingerprint,
                    force,
                )
                if transcript_changed:
                    changed.append(
                        (
                            "transcript_vtt",
                            vtt_path,
                            lambda path, source=media_inspection.transcript_url: (
                                media_client.download_transcript(source, path)
                            ),
                        )
                    )
                    updated.append("transcript")
                else:
                    skipped.append("transcript")
                desired["transcript_md"] = {
                    "kind": "transcript",
                    "path": local_transcript,
                    "fingerprint": transcript_fingerprint,
                }

        markdown_relative = "module.md"
        markdown_path = module_dir / markdown_relative
        markdown = render_markdown(
            module,
            local_resources,
            local_video=local_video,
            local_transcript=local_transcript,
        )
        markdown_fingerprint = hashlib.sha256(markdown.encode()).hexdigest()
        desired["markdown"] = {
            "kind": "markdown",
            "path": markdown_relative,
            "fingerprint": markdown_fingerprint,
        }

        with tempfile.TemporaryDirectory(prefix=".skool-export-", dir=output) as temp:
            staging = Path(temp)
            for key, relative, writer in changed:
                emit(f"Updating {desired[key].get('label', desired[key]['kind'])}")
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                writer(target)
                desired[key]["sha256"] = _sha256(target)

            transcript_spec = desired.get("transcript_md")
            if transcript_spec:
                transcript_relative = transcript_spec["path"]
                transcript_target = staging / transcript_relative
                existing_md = old_artifacts.get("transcript_md")
                transcript_md_current = _artifact_current(
                    module_dir,
                    existing_md,
                    transcript_relative,
                    transcript_spec["fingerprint"],
                    force,
                )
                if not transcript_md_current or "transcript" in updated:
                    staged_vtt = staging / "transcript/transcript.vtt"
                    source_vtt = (
                        staged_vtt
                        if staged_vtt.exists()
                        else module_dir / "transcript/transcript.vtt"
                    )
                    transcript_target.parent.mkdir(parents=True, exist_ok=True)
                    transcript_target.write_text(
                        vtt_to_markdown(source_vtt.read_text(encoding="utf-8")),
                        encoding="utf-8",
                    )
                    transcript_spec["sha256"] = _sha256(transcript_target)
                elif isinstance(existing_md, dict):
                    transcript_spec["sha256"] = existing_md.get("sha256", "")

            old_markdown = old_artifacts.get("markdown")
            if not _artifact_current(
                module_dir,
                old_markdown,
                markdown_relative,
                markdown_fingerprint,
                force,
            ):
                target = staging / markdown_relative
                target.write_text(markdown, encoding="utf-8")
                desired["markdown"]["sha256"] = _sha256(target)
            elif isinstance(old_markdown, dict):
                desired["markdown"]["sha256"] = old_markdown.get("sha256", "")

            for key, spec in desired.items():
                if "sha256" not in spec:
                    old = old_artifacts.get(key)
                    if isinstance(old, dict):
                        spec["sha256"] = old.get("sha256", "")

            _promote_staged(staging, module_dir)
            _remove_stale_artifacts(module_dir, old_artifacts, desired)
            manifest = {
                "version": 1,
                "module_id": module.module_id,
                "course": module.course_title,
                "module": module.title,
                "position": module.position,
                "artifacts": desired,
            }
            _write_manifest(manifest_path, manifest)

        asset_paths = tuple(module_dir / relative for _, _, _, relative in resource_specs)
        if not updated:
            emit("Everything is already up to date")
        emit(f"Export complete: {markdown_path}")
        return ClassroomExportResult(
            markdown_path=markdown_path,
            asset_paths=asset_paths,
            updated=tuple(updated),
            skipped=tuple(skipped),
        )


def _find_module_manifest(output: Path, module_id: str) -> Path | None:
    for candidate in output.glob("*/*/.skool-export.json"):
        try:
            value = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(value, dict)
            and value.get("version") == 1
            and value.get("module_id") == module_id
        ):
            return candidate
    return None


def _load_manifest(path: Path, module_id: str) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if (
        not isinstance(value, dict)
        or value.get("version") != 1
        or value.get("module_id") != module_id
    ):
        return {}
    return value


def _artifact_current(
    module_dir: Path,
    old: object,
    relative: str,
    fingerprint: str,
    force: bool,
) -> bool:
    if force or not isinstance(old, dict):
        return False
    if old.get("path") != relative or old.get("fingerprint") != fingerprint:
        return False
    expected_hash = old.get("sha256")
    path = module_dir / relative
    return (
        isinstance(expected_hash, str)
        and bool(expected_hash)
        and path.is_file()
        and _sha256(path) == expected_hash
    )


def _resource_key(url: str) -> str:
    parsed = urlsplit(url)
    volatile = {
        "token",
        "signature",
        "policy",
        "key-pair-id",
        "expires",
        "skid",
        "rid",
        "cdn",
    }
    stable_query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in volatile
            and not key.lower().startswith("x-amz-")
            and not key.lower().startswith("response-")
        )
    )
    identity = urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, stable_query, "")
    )
    return "asset:" + hashlib.sha256(identity.encode()).hexdigest()


def _resource_filename(url: str, used: set[str]) -> str:
    raw_name = unquote(Path(urlsplit(url).path).name)
    if not raw_name or raw_name in {".", ".."}:
        raw_name = "resource"
    source = Path(raw_name)
    suffix = "".join(source.suffixes).lower()
    stem = raw_name[: -len(suffix)] if suffix else raw_name
    safe_stem = safe_slug(stem, "resource")
    safe_suffix = "".join(
        character
        for character in suffix
        if character.isascii() and (character.isalnum() or character == ".")
    )
    candidate = f"{safe_stem}{safe_suffix}"
    index = 2
    while candidate in used:
        candidate = f"{safe_stem}-{index}{safe_suffix}"
        index += 1
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _promote_staged(staging: Path, module_dir: Path) -> None:
    for source in sorted(staging.rglob("*")):
        if not source.is_file():
            continue
        relative = source.relative_to(staging)
        destination = module_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(source, destination)
        except OSError as exc:
            raise ExportError("Could not finalize classroom export") from exc


def _remove_stale_artifacts(
    module_dir: Path,
    old: dict[str, object],
    desired: dict[str, dict[str, str]],
) -> None:
    desired_paths = {spec["path"] for spec in desired.values()}
    for spec in old.values():
        if not isinstance(spec, dict):
            continue
        relative = spec.get("path")
        if not isinstance(relative, str) or relative in desired_paths:
            continue
        candidate = module_dir / relative
        try:
            candidate.relative_to(module_dir)
        except ValueError:
            continue
        if candidate.is_file() or candidate.is_symlink():
            candidate.unlink(missing_ok=True)


def _write_manifest(path: Path, manifest: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".manifest-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
