from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel

from skool_data.errors import ConfigurationError, SchemaError
from skool_data.http import SkoolHttpClient


class DailyStatus(StrEnum):
    available = "available"
    unavailable = "unavailable"
    failed = "failed"


class DailyValue(BaseModel):
    metric: str
    label: str
    status: DailyStatus
    value: int | None = None
    reason: str | None = None
    source_range: tuple[str, str] | None = None


class DailySourceCapture(BaseModel):
    endpoint: str
    payload: dict[str, Any]
    values: list[DailyValue]


class ProfileDataClient(Protocol):
    def get_json(self) -> dict[str, Any]: ...


def _parse_display_date(value: str) -> date:
    """Parse strict DD-MM-YYYY user input; internal values stay ISO."""
    try:
        parsed = datetime.strptime(value, "%d-%m-%Y").date()
        if parsed.strftime("%d-%m-%Y") != value:
            raise ValueError
    except ValueError as exc:
        raise ConfigurationError(
            "Date must use DD-MM-YYYY (for example 27-09-2026)"
        ) from exc
    return parsed


def _reject_future(parsed: date, current: date) -> None:
    if parsed > current:
        raise ConfigurationError(
            "Date cannot be in the future; use a DD-MM-YYYY date on or before "
            f"{current.strftime('%d-%m-%Y')}"
        )


def parse_target_date(value: str | None, *, today: date | None = None) -> date:
    current = today or datetime.now(UTC).date()
    if value is None:
        return current
    target = _parse_display_date(value)
    _reject_future(target, current)
    return target


def parse_query_since(
    value: str | None, *, today: date | None = None
) -> datetime | None:
    """Convert strict DD-MM-YYYY query input to UTC start-of-day."""
    if value is None:
        return None
    parsed = _parse_display_date(value)
    _reject_future(parsed, today or datetime.now(UTC).date())
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC)


def _as_date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise SchemaError(f"Expected date string for {field}")
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise SchemaError(f"Invalid date string for {field}") from exc


def _unavailable(
    metric: str,
    label: str,
    start: date | None,
    end: date | None,
) -> DailyValue:
    if start is not None and end is not None:
        reason = f"Available from {start.isoformat()} through {end.isoformat()}"
        source_range = (start.isoformat(), end.isoformat())
    else:
        reason = "Source returned no retained dates"
        source_range = None
    return DailyValue(
        metric=metric,
        label=label,
        status=DailyStatus.unavailable,
        reason=reason,
        source_range=source_range,
    )


def _item_value(
    items_value: Any,
    target: date,
    *,
    date_key: str,
    value_key: str,
    metric: str,
    label: str,
) -> DailyValue:
    if not isinstance(items_value, list) or any(
        not isinstance(item, dict) for item in items_value
    ):
        raise SchemaError(f"Expected item list for {metric}")
    dated = [(_as_date(item.get(date_key), date_key), item) for item in items_value]
    start = min((item_date for item_date, _ in dated), default=None)
    end = max((item_date for item_date, _ in dated), default=None)
    for item_date, item in dated:
        if item_date == target:
            value = item.get(value_key)
            if isinstance(value, bool) or not isinstance(value, int):
                raise SchemaError(f"Expected integer value for {metric}")
            return DailyValue(
                metric=metric,
                label=label,
                status=DailyStatus.available,
                value=value,
                source_range=(start.isoformat(), end.isoformat()),
            )
    return _unavailable(metric, label, start, end)


def _count_value(
    fragment_value: Any,
    target: date,
    *,
    start_key: str,
    end_key: str,
    metric: str,
    label: str,
) -> DailyValue:
    if not isinstance(fragment_value, dict):
        raise SchemaError(f"Expected activity object for {metric}")
    start = _as_date(fragment_value.get(start_key), start_key)
    end = _as_date(fragment_value.get(end_key), end_key)
    counts = fragment_value.get("counts")
    if not isinstance(counts, list):
        raise SchemaError(f"Expected counts list for {metric}")
    if target < start or target > end:
        return _unavailable(metric, label, start, end)
    index = (target - start).days
    if index >= len(counts):
        raise SchemaError(f"Activity counts do not cover {target.isoformat()}")
    value = counts[index]
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaError(f"Expected integer activity count for {metric}")
    return DailyValue(
        metric=metric,
        label=label,
        status=DailyStatus.available,
        value=value,
        source_range=(start.isoformat(), end.isoformat()),
    )


def _minimal_payload(target: date, values: list[DailyValue]) -> dict[str, Any]:
    return {
        "target_date": target.isoformat(),
        "results": [value.model_dump(mode="json") for value in values],
    }


class VisitsDailySource:
    name = "daily-visitors"

    def fetch(self, client: SkoolHttpClient, target: date) -> DailySourceCapture:
        params = {"chart": "visits_by_day"}
        payload = client.get_json("analytics-v2", params)
        if "data" not in payload:
            token = payload.get("token")
            if not isinstance(token, str) or not token:
                raise SchemaError("Expected visits analytics token")
            payload = client.get_json(
                "analytics-v2", {"chart": "visits_by_day", "token": token}
            )
        try:
            items = payload["data"]["chart_data"]["items"]
        except (KeyError, TypeError) as exc:
            raise SchemaError("Visits analytics response schema changed") from exc
        value = _item_value(
            items,
            target,
            date_key="date",
            value_key="num_visits",
            metric="daily.group_visitors",
            label="Group visitors",
        )
        return DailySourceCapture(
            endpoint=self.name,
            payload=_minimal_payload(target, [value]),
            values=[value],
        )


class AdminDailySource:
    name = "daily-admin"

    def __init__(self, *, today: date | None = None) -> None:
        self._today = today or datetime.now(UTC).date()

    def fetch(self, client: SkoolHttpClient, target: date) -> DailySourceCapture:
        payload = client.get_json(
            "admin-metrics", {"range": "30d", "amt": "monthly"}
        )
        group_activity = _count_value(
            payload.get("daily_activities"),
            target,
            start_key="start_date",
            end_key="end_date",
            metric="daily.group_activity",
            label="Group activity",
        )
        total_members = _item_value(
            payload.get("total_members"),
            target,
            date_key="time",
            value_key="value",
            metric="daily.total_members",
            label="Total members",
        )
        member_total_source = "daily-series"
        if (
            total_members.status is DailyStatus.unavailable
            and target == self._today
        ):
            total_members = self._current_members(client)
            member_total_source = "current-overview"
        values = [group_activity, total_members]
        minimal = _minimal_payload(target, values)
        minimal["member_total_source"] = member_total_source
        return DailySourceCapture(
            endpoint=self.name,
            payload=minimal,
            values=values,
        )

    def _current_members(self, client: SkoolHttpClient) -> DailyValue:
        payload = client.get_json("analytics-overview-v2")
        if "data" not in payload:
            token = payload.get("token")
            if not isinstance(token, str) or not token:
                raise SchemaError("Expected overview analytics token")
            payload = client.get_json(
                "analytics-overview-v2", {"token": token}
            )
        try:
            value = payload["data"]["num_members"]
        except (KeyError, TypeError) as exc:
            raise SchemaError("Overview member response schema changed") from exc
        if isinstance(value, bool) or not isinstance(value, int):
            raise SchemaError("Expected integer current member total")
        return DailyValue(
            metric="daily.total_members",
            label="Total members",
            status=DailyStatus.available,
            value=value,
        )


class ProfileDailySource:
    name = "daily-profile"

    def fetch(self, client: ProfileDataClient, target: date) -> DailySourceCapture:
        payload = client.get_json()
        try:
            fragment = payload["pageProps"]["currentUser"]["profileData"][
                "dailyActivities"
            ]
        except (KeyError, TypeError) as exc:
            raise SchemaError("Profile activity response schema changed") from exc
        value = _count_value(
            fragment,
            target,
            start_key="startDate",
            end_key="endDate",
            metric="daily.profile_activity",
            label="Profile activity",
        )
        return DailySourceCapture(
            endpoint=self.name,
            payload=_minimal_payload(target, [value]),
            values=[value],
        )
