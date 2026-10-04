import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from skool_data.models import MetricObservation, RunStatus

_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS communities (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS capture_runs (
  id TEXT PRIMARY KEY,
  community_id TEXT NOT NULL REFERENCES communities(id),
  captured_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pending','complete','partial','failed')),
  error_summary TEXT
);
CREATE TABLE IF NOT EXISTS endpoint_results (
  run_id TEXT NOT NULL REFERENCES capture_runs(id),
  endpoint TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('success','failed')),
  error TEXT,
  PRIMARY KEY(run_id, endpoint)
);
CREATE TABLE IF NOT EXISTS raw_artifacts (
  run_id TEXT NOT NULL REFERENCES capture_runs(id),
  endpoint TEXT NOT NULL,
  path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  PRIMARY KEY(run_id, endpoint)
);
CREATE TABLE IF NOT EXISTS metric_definitions (
  name TEXT PRIMARY KEY,
  unit TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metric_observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES capture_runs(id),
  name TEXT NOT NULL REFERENCES metric_definitions(name),
  value REAL NOT NULL,
  observed_at TEXT NOT NULL,
  dimensions_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS discovery_runs (
  id TEXT PRIMARY KEY,
  captured_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pending','complete','failed')),
  result_count INTEGER,
  total_available INTEGER,
  has_more INTEGER,
  error TEXT
);
CREATE TABLE IF NOT EXISTS discovery_listings (
  run_id TEXT NOT NULL REFERENCES discovery_runs(id),
  group_id TEXT NOT NULL,
  slug TEXT NOT NULL,
  display_name TEXT NOT NULL,
  url TEXT NOT NULL,
  rank INTEGER NOT NULL,
  description TEXT NOT NULL,
  total_members INTEGER NOT NULL,
  display_price TEXT NOT NULL,
  membership_model TEXT NOT NULL,
  tags_json TEXT NOT NULL,
  PRIMARY KEY(run_id, group_id)
);
CREATE TABLE IF NOT EXISTS discovery_artifacts (
  run_id TEXT PRIMARY KEY REFERENCES discovery_runs(id),
  path TEXT NOT NULL,
  sha256 TEXT NOT NULL
);
"""


class Storage:
    """SQLite persistence for capture runs and normalized observations."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    def begin_run(self, community_id: str, captured_at: datetime) -> str:
        run_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO communities(id, created_at) VALUES (?, ?)",
                (community_id, datetime.now(UTC).isoformat()),
            )
            connection.execute(
                "INSERT INTO capture_runs(id, community_id, captured_at, status) "
                "VALUES (?, ?, ?, 'pending')",
                (run_id, community_id, captured_at.isoformat()),
            )
        return run_id

    def record_endpoint_success(
        self,
        run_id: str,
        endpoint: str,
        artifact_path: str,
        sha256: str,
        observations: list[MetricObservation],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO endpoint_results(run_id, endpoint, status) "
                "VALUES (?, ?, 'success')",
                (run_id, endpoint),
            )
            connection.execute(
                "INSERT INTO raw_artifacts(run_id, endpoint, path, sha256) "
                "VALUES (?, ?, ?, ?)",
                (run_id, endpoint, artifact_path, sha256),
            )
            for observation in observations:
                connection.execute(
                    "INSERT OR IGNORE INTO metric_definitions(name, unit) VALUES (?, ?)",
                    (observation.name, observation.unit),
                )
                connection.execute(
                    "INSERT INTO metric_observations"
                    "(run_id, name, value, observed_at, dimensions_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        run_id,
                        observation.name,
                        observation.value,
                        observation.observed_at.isoformat(),
                        json.dumps(
                            observation.dimensions,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )

    def record_endpoint_failure(self, run_id: str, endpoint: str, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO endpoint_results(run_id, endpoint, status, error) "
                "VALUES (?, ?, 'failed', ?)",
                (run_id, endpoint, error),
            )

    def finish_run(
        self,
        run_id: str,
        status: RunStatus,
        error_summary: str | None = None,
    ) -> None:
        if status is RunStatus.pending:
            raise ValueError("Cannot finalize a run as pending")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM capture_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Capture run does not exist")
            if row["status"] != RunStatus.pending.value:
                raise ValueError("Capture run is already finalized")
            connection.execute(
                "UPDATE capture_runs SET status = ?, error_summary = ? WHERE id = ?",
                (status.value, error_summary, run_id),
            )

    def begin_discovery_run(self, captured_at: datetime) -> str:
        run_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO discovery_runs(id, captured_at, status) "
                "VALUES (?, ?, 'pending')",
                (run_id, captured_at.isoformat()),
            )
        return run_id

    def complete_discovery_run(
        self, run_id: str, page: Any, artifact: Any
    ) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM discovery_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None or row["status"] != "pending":
                raise ValueError("Discovery run is missing or already finalized")
            for listing in page.listings:
                connection.execute(
                    "INSERT INTO discovery_listings("
                    "run_id, group_id, slug, display_name, url, rank, description, "
                    "total_members, display_price, membership_model, tags_json"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        listing.group_id,
                        listing.slug,
                        listing.display_name,
                        listing.url,
                        listing.rank,
                        listing.description,
                        listing.total_members,
                        listing.display_price,
                        listing.membership_model,
                        json.dumps(listing.tags, separators=(",", ":")),
                    ),
                )
            connection.execute(
                "INSERT INTO discovery_artifacts(run_id, path, sha256) VALUES (?, ?, ?)",
                (run_id, artifact.relative_path, artifact.sha256),
            )
            connection.execute(
                "UPDATE discovery_runs SET status = 'complete', result_count = ?, "
                "total_available = ?, has_more = ? WHERE id = ?",
                (
                    page.result_count,
                    page.total_available,
                    int(page.has_more),
                    run_id,
                ),
            )

    def fail_discovery_run(self, run_id: str, error: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE discovery_runs SET status = 'failed', error = ? "
                "WHERE id = ? AND status = 'pending'",
                (error, run_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Discovery run is missing or already finalized")

    def get_discovery_run(self, run_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, captured_at, status, result_count, total_available, "
                "has_more, error FROM discovery_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise ValueError("Discovery run does not exist")
        result = dict(row)
        if result["has_more"] is not None:
            result["has_more"] = bool(result["has_more"])
        return result

    def get_discovery_listings(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT group_id, slug, display_name, url, rank, description, "
                "total_members, display_price, membership_model, tags_json "
                "FROM discovery_listings WHERE run_id = ? ORDER BY rank, group_id",
                (run_id,),
            ).fetchall()
        return [
            {
                **{key: row[key] for key in row.keys() if key != "tags_json"},
                "tags": json.loads(row["tags_json"]),
            }
            for row in rows
        ]

    def list_runs(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, community_id, captured_at, status, error_summary "
                "FROM capture_runs ORDER BY captured_at DESC, id DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def query_metrics(
        self,
        metric: str | None = None,
        since: datetime | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[str] = []
        if metric:
            clauses.append("name = ?")
            parameters.append(metric)
        if since:
            clauses.append("observed_at >= ?")
            parameters.append(since.isoformat())
        if run_id:
            clauses.append("run_id = ?")
            parameters.append(run_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT name, value, observed_at, dimensions_json "
                f"FROM metric_observations {where} "
                "ORDER BY observed_at, id",
                parameters,
            ).fetchall()
        return [
            {
                "name": row["name"],
                "value": row["value"],
                "observed_at": row["observed_at"],
                "dimensions": json.loads(row["dimensions_json"]),
            }
            for row in rows
        ]
