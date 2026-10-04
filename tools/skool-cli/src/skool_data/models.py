from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class Credentials(BaseModel):
    api_base_url: HttpUrl
    community_id: str = Field(min_length=1)
    headers: dict[str, str]
    imported_at: datetime
    profile_data_url: HttpUrl | None = None


class MetricObservation(BaseModel):
    name: str
    value: int | float
    observed_at: datetime
    unit: str
    dimensions: dict[str, Any] = Field(default_factory=dict)


class EndpointCapture(BaseModel):
    endpoint: str
    payload: dict[str, Any]
    observations: list[MetricObservation]


class RunStatus(StrEnum):
    pending = "pending"
    complete = "complete"
    partial = "partial"
    failed = "failed"


class RuntimePaths(BaseModel):
    root: Path

    @property
    def credentials(self) -> Path:
        return self.root / "credentials.json"

    @property
    def database(self) -> Path:
        return self.root / "data" / "skool.sqlite3"

    @property
    def raw(self) -> Path:
        return self.root / "data" / "raw"

    @property
    def exports(self) -> Path:
        return self.root / "exports"
