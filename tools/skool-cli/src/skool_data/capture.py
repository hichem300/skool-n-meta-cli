import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from skool_data.adapters import AnalyticsAdapter
from skool_data.artifacts import write_manifest, write_payload
from skool_data.errors import SchemaError, SkoolDataError, StorageError
from skool_data.models import RunStatus
from skool_data.reporting import CaptureReporter, NullCaptureReporter
from skool_data.storage import Storage


class CaptureRunResult(BaseModel):
    run_id: str
    status: RunStatus
    successes: list[str]
    failures: dict[str, str]


class CaptureService:
    """Run endpoint adapters while preserving independent successes."""

    def __init__(
        self,
        storage: Storage,
        raw_root: Path,
        adapters: list[AnalyticsAdapter],
        reporter: CaptureReporter | None = None,
    ) -> None:
        self._storage = storage
        self._raw_root = raw_root
        self._adapters = adapters
        self._reporter = reporter or NullCaptureReporter()

    def run(
        self, community_id: str, client: Any, observed_at: datetime
    ) -> CaptureRunResult:
        try:
            run_id = self._storage.begin_run(community_id, observed_at)
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise StorageError("Could not create capture run") from exc

        successes: list[str] = []
        failures: dict[str, str] = {}
        run_started_at = time.perf_counter()
        total = len(self._adapters)
        self._reporter.run_started(total)
        try:
            for index, adapter in enumerate(self._adapters, start=1):
                endpoint_started_at = time.perf_counter()
                self._reporter.endpoint_started(adapter.name, index, total)
                try:
                    capture = adapter.capture(client, observed_at)
                    if capture.endpoint != adapter.name:
                        raise SchemaError(
                            f"Adapter {adapter.name} returned a mismatched endpoint"
                        )
                    artifact = write_payload(
                        self._raw_root, run_id, adapter.name, capture.payload
                    )
                    self._storage.record_endpoint_success(
                        run_id,
                        adapter.name,
                        artifact.relative_path,
                        artifact.sha256,
                        capture.observations,
                    )
                    successes.append(adapter.name)
                    self._reporter.endpoint_succeeded(
                        adapter.name,
                        time.perf_counter() - endpoint_started_at,
                        len(capture.observations),
                    )
                except SkoolDataError as exc:
                    message = str(exc)
                    self._storage.record_endpoint_failure(run_id, adapter.name, message)
                    failures[adapter.name] = message
                    self._reporter.endpoint_failed(
                        adapter.name,
                        time.perf_counter() - endpoint_started_at,
                        message,
                    )
                except (OSError, sqlite3.Error, ValueError) as exc:
                    self._mark_storage_failure(run_id)
                    raise StorageError("Could not persist capture data") from exc
                except Exception:
                    message = "Unexpected adapter failure"
                    self._storage.record_endpoint_failure(run_id, adapter.name, message)
                    failures[adapter.name] = message
                    self._reporter.endpoint_failed(
                        adapter.name,
                        time.perf_counter() - endpoint_started_at,
                        message,
                    )

            if successes and not failures:
                status = RunStatus.complete
            elif successes:
                status = RunStatus.partial
            else:
                status = RunStatus.failed
            error_summary = "; ".join(
                f"{endpoint}: {message}" for endpoint, message in failures.items()
            ) or None
            write_manifest(
                self._raw_root,
                run_id,
                {
                    "run_id": run_id,
                    "status": status.value,
                    "successes": successes,
                    "failures": failures,
                },
            )
            self._storage.finish_run(run_id, status, error_summary)
            self._reporter.run_finished(
                status, time.perf_counter() - run_started_at
            )
        except StorageError:
            raise
        except (OSError, sqlite3.Error, ValueError) as exc:
            self._mark_storage_failure(run_id)
            raise StorageError("Could not finalize capture data") from exc

        return CaptureRunResult(
            run_id=run_id,
            status=status,
            successes=successes,
            failures=failures,
        )

    def _mark_storage_failure(self, run_id: str) -> None:
        try:
            self._storage.finish_run(
                run_id, RunStatus.failed, "Capture storage operation failed"
            )
        except (OSError, sqlite3.Error, ValueError):
            pass
