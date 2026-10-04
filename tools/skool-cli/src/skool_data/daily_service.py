import sqlite3
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from skool_data.artifacts import write_manifest, write_payload
from skool_data.daily import DailySourceCapture, DailyStatus, DailyValue
from skool_data.errors import SchemaError, SkoolDataError, StorageError
from skool_data.models import MetricObservation, RunStatus
from skool_data.storage import Storage

_METRICS = [
    ("daily.group_visitors", "Group visitors"),
    ("daily.group_activity", "Group activity"),
    ("daily.profile_activity", "Profile activity"),
    ("daily.total_members", "Total members"),
]
_SOURCE_METRICS = {
    "daily-visitors": ["daily.group_visitors"],
    "daily-admin": ["daily.group_activity", "daily.total_members"],
    "daily-profile": ["daily.profile_activity"],
}
_LABELS = dict(_METRICS)


class DailySource(Protocol):
    name: str

    def fetch(self, client: Any, target: date) -> DailySourceCapture: ...


class DailyRunResult(BaseModel):
    run_id: str
    target_date: date
    status: RunStatus
    values: list[DailyValue]
    failures: dict[str, str]


class DailyAnalyticsService:
    """Fetch daily sources independently and persist minimal snapshots."""

    def __init__(
        self,
        storage: Storage,
        raw_root: Path,
        sources: list[DailySource],
    ) -> None:
        self._storage = storage
        self._raw_root = raw_root
        self._sources = sources

    def run(
        self,
        community_id: str,
        api_client: Any,
        profile_client: Any,
        target: date,
    ) -> DailyRunResult:
        try:
            run_id = self._storage.begin_run(community_id, datetime.now(UTC))
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise StorageError("Could not create daily analytics run") from exc

        values_by_metric: dict[str, DailyValue] = {}
        failures: dict[str, str] = {}
        successful_sources = 0
        try:
            for source in self._sources:
                client = profile_client if source.name == "daily-profile" else api_client
                try:
                    capture = source.fetch(client, target)
                    self._validate_capture(source.name, capture)
                    artifact = write_payload(
                        self._raw_root, run_id, source.name, capture.payload
                    )
                    observations = [
                        self._observation(value, target)
                        for value in capture.values
                        if value.status is DailyStatus.available
                    ]
                    self._storage.record_endpoint_success(
                        run_id,
                        source.name,
                        artifact.relative_path,
                        artifact.sha256,
                        observations,
                    )
                    values_by_metric.update(
                        {value.metric: value for value in capture.values}
                    )
                    successful_sources += 1
                except SkoolDataError as exc:
                    message = str(exc)
                    failures[source.name] = message
                    self._storage.record_endpoint_failure(run_id, source.name, message)
                    self._add_failed_values(values_by_metric, source.name, message)
                except (OSError, sqlite3.Error, ValueError) as exc:
                    self._mark_storage_failure(run_id)
                    raise StorageError("Could not persist daily analytics") from exc
                except Exception:
                    message = "Unexpected daily analytics failure"
                    failures[source.name] = message
                    self._storage.record_endpoint_failure(run_id, source.name, message)
                    self._add_failed_values(values_by_metric, source.name, message)

            if successful_sources == len(self._sources):
                status = RunStatus.complete
            elif successful_sources:
                status = RunStatus.partial
            else:
                status = RunStatus.failed
            ordered = [
                values_by_metric.get(metric)
                or DailyValue(
                    metric=metric,
                    label=label,
                    status=DailyStatus.failed,
                    reason="Source did not return this metric",
                )
                for metric, label in _METRICS
            ]
            write_manifest(
                self._raw_root,
                run_id,
                {
                    "run_id": run_id,
                    "kind": "daily",
                    "target_date": target.isoformat(),
                    "status": status.value,
                    "values": [value.model_dump(mode="json") for value in ordered],
                    "failures": failures,
                },
            )
            error_summary = "; ".join(
                f"{source}: {message}" for source, message in failures.items()
            ) or None
            self._storage.finish_run(run_id, status, error_summary)
        except StorageError:
            raise
        except (OSError, sqlite3.Error, ValueError) as exc:
            self._mark_storage_failure(run_id)
            raise StorageError("Could not finalize daily analytics") from exc

        return DailyRunResult(
            run_id=run_id,
            target_date=target,
            status=status,
            values=ordered,
            failures=failures,
        )

    def _validate_capture(self, source_name: str, capture: DailySourceCapture) -> None:
        if capture.endpoint != source_name:
            raise SchemaError(f"Source {source_name} returned a mismatched endpoint")
        expected = set(_SOURCE_METRICS.get(source_name, []))
        returned = {value.metric for value in capture.values}
        if expected != returned:
            raise SchemaError(f"Source {source_name} returned incomplete metrics")

    def _observation(self, value: DailyValue, target: date) -> MetricObservation:
        if value.value is None:
            raise SchemaError(f"Available metric {value.metric} has no value")
        return MetricObservation(
            name=value.metric,
            value=value.value,
            observed_at=datetime.combine(target, time.min, tzinfo=UTC),
            unit="count",
            dimensions={"target_date": target.isoformat()},
        )

    def _add_failed_values(
        self,
        values_by_metric: dict[str, DailyValue],
        source_name: str,
        message: str,
    ) -> None:
        for metric in _SOURCE_METRICS.get(source_name, []):
            values_by_metric[metric] = DailyValue(
                metric=metric,
                label=_LABELS[metric],
                status=DailyStatus.failed,
                reason=message,
            )

    def _mark_storage_failure(self, run_id: str) -> None:
        try:
            self._storage.finish_run(
                run_id, RunStatus.failed, "Daily analytics storage operation failed"
            )
        except (OSError, sqlite3.Error, ValueError):
            pass
