import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ENDPOINT = re.compile(r"^[a-z0-9_-]+$")
_RUN_ID = re.compile(r"^[a-zA-Z0-9_-]+$")


@dataclass(frozen=True)
class Artifact:
    relative_path: str
    sha256: str


def _run_directory(raw_root: Path, run_id: str) -> Path:
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("Unsafe run ID")
    directory = raw_root / run_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_exclusive(path: Path, content: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def write_payload(
    raw_root: Path, run_id: str, endpoint: str, payload: dict[str, Any]
) -> Artifact:
    """Write one endpoint response without permitting replacement."""
    if not _ENDPOINT.fullmatch(endpoint):
        raise ValueError("Unsafe endpoint name")
    content = _json_bytes(payload)
    path = _run_directory(raw_root, run_id) / f"{endpoint}.json"
    _write_exclusive(path, content)
    return Artifact(
        relative_path=f"{run_id}/{endpoint}.json",
        sha256=hashlib.sha256(content).hexdigest(),
    )


def write_manifest(
    raw_root: Path, run_id: str, manifest: dict[str, Any]
) -> Path:
    """Write a final immutable capture manifest."""
    path = _run_directory(raw_root, run_id) / "manifest.json"
    _write_exclusive(path, _json_bytes(manifest))
    return path
