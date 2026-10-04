from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from skool_data.errors import SchemaError
from skool_data.http import SkoolHttpClient
from skool_data.models import EndpointCapture, MetricObservation


class AnalyticsAdapter(Protocol):
    name: str

    def capture(
        self, client: SkoolHttpClient, observed_at: datetime
    ) -> EndpointCapture: ...


def _number(value: Any, field: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaError(f"Expected numeric field {field}")
    return value


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaError(f"Expected object field {field}")
    return value


def _items(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise SchemaError(f"Expected object list field {field}")
    return value


def _token_capture(
    client: SkoolHttpClient,
    path: str,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    request_params = dict(params or {})
    token_payload = client.get_json(path, request_params)
    token = token_payload.get("token")
    if not isinstance(token, str) or not token:
        raise SchemaError(f"Expected token string from {path}")
    request_params["token"] = token
    return client.get_json(path, request_params)


def _chart_payload(
    client: SkoolHttpClient,
    chart: str,
) -> dict[str, Any]:
    """Fetch an analytics-v2 chart, handling both direct-data and token responses."""
    params = {"chart": chart}
    payload = client.get_json("analytics-v2", params)
    if isinstance(payload.get("data"), dict):
        return payload
    token = payload.get("token")
    if not isinstance(token, str) or not token:
        raise SchemaError(f"Expected analytics data or token for chart {chart}")
    return client.get_json("analytics-v2", {"chart": chart, "token": token})


def _observation(
    name: str,
    value: Any,
    observed_at: datetime,
    unit: str,
    dimensions: dict[str, Any] | None = None,
) -> MetricObservation:
    return MetricObservation(
        name=name,
        value=_number(value, name),
        observed_at=observed_at,
        unit=unit,
        dimensions=dimensions or {},
    )


class AnalyticsOverviewAdapter:
    name = "analytics-overview"

    def capture(
        self, client: SkoolHttpClient, observed_at: datetime
    ) -> EndpointCapture:
        payload = _token_capture(client, "analytics-overview-v2")
        data = _object(payload.get("data"), "data")
        observations = [
            _observation("members.total", data.get("num_members"), observed_at, "members"),
            _observation(
                "revenue.mrr",
                _number(data.get("mrr"), "mrr") / 100,
                observed_at,
                "currency",
            ),
            _observation(
                "retention.percent", data.get("retention"), observed_at, "percent"
            ),
        ]
        return EndpointCapture(
            endpoint=self.name, payload=payload, observations=observations
        )


class AnalyticsGrowthOverviewAdapter:
    name = "analytics-growth-overview"

    def capture(
        self, client: SkoolHttpClient, observed_at: datetime
    ) -> EndpointCapture:
        payload = _token_capture(client, "analytics-growth-overview-v2")
        data = _object(payload.get("data"), "data")
        observations = [
            _observation(
                "growth.visitors", data.get("num_visitors"), observed_at, "visitors"
            ),
            _observation(
                "growth.signups", data.get("num_signups"), observed_at, "signups"
            ),
            _observation(
                "growth.new_mrr", data.get("new_mrr"), observed_at, "currency"
            ),
        ]
        return EndpointCapture(
            endpoint=self.name, payload=payload, observations=observations
        )


class _ChartAdapter:
    chart: str
    metric: str
    name: str
    value_key: str

    def capture(
        self, client: SkoolHttpClient, observed_at: datetime
    ) -> EndpointCapture:
        payload = _token_capture(client, "analytics-v2", {"chart": self.chart})
        data = _object(payload.get("data"), "data")
        chart_data = _object(data.get("chart_data"), "chart_data")
        items = _items(chart_data.get("items"), "items")
        observations = []
        for item in items:
            value = item.get(self.value_key)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise SchemaError(f"Expected numeric chart value for {self.chart}")
            dimensions = {
                key: item_value
                for key, item_value in item.items()
                if key != self.value_key
            }
            observations.append(
                _observation(
                    self.metric,
                    value,
                    observed_at,
                    "signups",
                    dimensions,
                )
            )
        return EndpointCapture(
            endpoint=self.name, payload=payload, observations=observations
        )


class SignupSourceAdapter(_ChartAdapter):
    chart = "signups_by_source"
    metric = "signups.by_source"
    name = "signups-by-source"
    value_key = "total"


class SignupDayAdapter(_ChartAdapter):
    chart = "signups_by_day"
    metric = "signups.by_day"
    name = "signups-by-day"
    value_key = "num_signups"


class _TimeSeriesChartAdapter:
    """Adapter for analytics-v2 charts that return multiple numeric fields per dated item.

    Each numeric field becomes one observation named ``<metric_prefix>.<suffix>``
    with the item's ``date`` carried as a dimension. The ``fields`` mapping pins
    the emitted metric suffix to the source API field name so renames stay explicit.
    ``units`` optionally maps a suffix to a unit, overriding the class ``unit``.
    """

    chart: str
    name: str
    metric_prefix: str
    fields: dict[str, str]
    unit: str = "currency"
    units: dict[str, str] = {}

    def capture(
        self, client: SkoolHttpClient, observed_at: datetime
    ) -> EndpointCapture:
        payload = _chart_payload(client, self.chart)
        data = _object(payload.get("data"), "data")
        chart_data = _object(data.get("chart_data"), "chart_data")
        items = _items(chart_data.get("items"), "items")
        observations: list[MetricObservation] = []
        for item in items:
            date_value = item.get("date")
            if not isinstance(date_value, str) or not date_value:
                raise SchemaError(f"Expected date string in {self.chart} item")
            dimensions = {"date": date_value}
            for api_field, suffix in self.fields.items():
                value = item.get(api_field)
                if value is None:
                    continue
                observations.append(
                    _observation(
                        f"{self.metric_prefix}.{suffix}",
                        value,
                        observed_at,
                        self.units.get(suffix, self.unit),
                        dimensions,
                    )
                )
        return EndpointCapture(
            endpoint=self.name, payload=payload, observations=observations
        )


class MrrMovementAdapter(_TimeSeriesChartAdapter):
    chart = "mrr"
    name = "mrr-movement"
    metric_prefix = "mrr"
    fields = {
        "churn": "churn",
        "downgrade": "downgrade",
        "existing": "existing",
        "new": "new",
        "reactivation": "reactivation",
        "upgrade": "upgrade",
        "mrr": "net",
    }


class VisitsDayAdapter(_TimeSeriesChartAdapter):
    chart = "visits_by_day"
    name = "visits-by-day"
    metric_prefix = "growth"
    unit = "visitors"
    fields = {"num_visits": "daily_visitors"}


class VisitsSourceAdapter:
    """Adapter for the current ``visits_by_source`` chart snapshot.

    Emits one ``traffic.by_source`` observation per source item. The live API
    response carries no date or window field, so no date dimension is
    synthesized; only ``attribution``, ``icon``, and the API ``percent`` are
    preserved as dimensions.
    """

    name = "visits-by-source"

    def capture(
        self, client: SkoolHttpClient, observed_at: datetime
    ) -> EndpointCapture:
        payload = _chart_payload(client, "visits_by_source")
        data = _object(payload.get("data"), "data")
        chart_data = _object(data.get("chart_data"), "chart_data")
        items = _items(chart_data.get("items"), "items")
        observations: list[MetricObservation] = []
        for item in items:
            attribution = item.get("attribution")
            if not isinstance(attribution, str):
                raise SchemaError(
                    "Expected attribution string in visits_by_source item"
                )
            icon = item.get("icon")
            if not isinstance(icon, str):
                raise SchemaError("Expected icon string in visits_by_source item")
            total = _number(item.get("total"), "visits_by_source.total")
            percent = _number(item.get("percent"), "visits_by_source.percent")
            observations.append(
                _observation(
                    "traffic.by_source",
                    total,
                    observed_at,
                    "visitors",
                    {
                        "attribution": attribution,
                        "icon": icon,
                        "percent": percent,
                    },
                )
            )
        return EndpointCapture(
            endpoint=self.name, payload=payload, observations=observations
        )


class CashflowAdapter(_TimeSeriesChartAdapter):
    chart = "mrr_cashflow"
    name = "mrr-cashflow"
    metric_prefix = "cashflow"
    fields = {
        "one_time": "one_time",
        "year": "year",
        "month": "month",
        "affiliates": "affiliates",
        "fees": "fees",
        "refunds": "refunds",
        "disputes": "disputes",
        "net": "net",
    }


class UnitEconomicsAdapter(_TimeSeriesChartAdapter):
    chart = "mrr_unit"
    name = "mrr-unit"
    metric_prefix = "unit"
    fields = {
        "arpu": "arpu",
        "ltv": "ltv",
    }


class RetentionCohortsAdapter(_TimeSeriesChartAdapter):
    chart = "retention_cohorts"
    name = "retention-cohorts"
    metric_prefix = "retention.cohorts"
    fields = {
        "retention_rate": "rate",
        "mrr": "mrr",
    }
    units = {"rate": "percent", "mrr": "currency"}


class RetentionMembersAdapter(_TimeSeriesChartAdapter):
    chart = "retention_members"
    name = "retention-members"
    metric_prefix = "retention.members"
    unit = "currency"
    fields = {
        "retention_rate": "rate",
        "upgrade": "upgrade",
        "downgrade": "downgrade",
        "churn": "churn",
        "reactivation": "reactivation",
    }
    units = {"rate": "percent"}


class ConversionTrialsAdapter(_TimeSeriesChartAdapter):
    chart = "conversion_trials"
    name = "conversion-trials"
    metric_prefix = "conversion"
    unit = "count"
    fields = {
        "num_trials": "trials",
        "conversion_rate": "rate",
    }
    units = {"trials": "trials", "rate": "percent"}


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise SchemaError(f"Expected time string for {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SchemaError(f"Invalid time string for {field}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


class AdminMetricsAdapter:
    name = "admin-metrics"

    def capture(
        self, client: SkoolHttpClient, observed_at: datetime
    ) -> EndpointCapture:
        payload = client.get_json(
            "admin-metrics", {"range": "30d", "amt": "monthly"}
        )
        observations: list[MetricObservation] = []
        for field, metric in (
            ("total_members", "members.total"),
            ("active_members", "members.active"),
        ):
            for item in _items(payload.get(field), field):
                observations.append(
                    _observation(
                        metric,
                        item.get("value"),
                        _parse_time(item.get("time"), f"{field}.time"),
                        "members",
                    )
                )
        daily = _object(payload.get("daily_activities"), "daily_activities")
        start = _parse_time(daily.get("start_date"), "daily_activities.start_date")
        counts = daily.get("counts")
        if not isinstance(counts, list):
            raise SchemaError("Expected list field daily_activities.counts")
        for offset, count in enumerate(counts):
            observations.append(
                _observation(
                    "activity.daily",
                    count,
                    start + timedelta(days=offset),
                    "activities",
                )
            )
        return EndpointCapture(
            endpoint=self.name, payload=payload, observations=observations
        )


def default_adapters() -> list[AnalyticsAdapter]:
    return [
        AnalyticsOverviewAdapter(),
        AnalyticsGrowthOverviewAdapter(),
        VisitsDayAdapter(),
        VisitsSourceAdapter(),
        SignupSourceAdapter(),
        SignupDayAdapter(),
        MrrMovementAdapter(),
        CashflowAdapter(),
        UnitEconomicsAdapter(),
        ConversionTrialsAdapter(),
        RetentionCohortsAdapter(),
        RetentionMembersAdapter(),
        AdminMetricsAdapter(),
    ]
