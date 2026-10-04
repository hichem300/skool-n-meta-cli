#!/usr/bin/env python3
"""Build a read-only report for today's Meta campaign performance."""

from __future__ import annotations

import datetime
import json
import math
import os
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import TextIO


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
META_PATH = PROJECT_ROOT / ".venv" / "bin" / "meta"


PURCHASE_ACTIONS = (
    "offsite_conversion.fb_pixel_purchase",
    "omni_purchase",
    "purchase",
)
LEAD_ACTIONS = ("lead", "onsite_conversion.lead_grouped")
READ_ONLY_BLOCKLIST = frozenset({"create", "update", "delete"})


class ReportError(Exception):
    """A concise, user-facing report failure."""


@dataclass(frozen=True)
class QueryPeriod:
    since: str | None
    until: str | None
    display: str
    preset: str | None = None

    @property
    def is_range(self) -> bool:
        return self.since is not None or self.preset is not None


def today_period() -> QueryPeriod:
    return QueryPeriod(since=None, until=None, display="Today")


def max_period() -> QueryPeriod:
    return QueryPeriod(
        since=None,
        until=None,
        display="Maximum available range",
        preset="maximum",
    )


def utc_today() -> datetime.date:
    """Return today's date in UTC, used to validate and bound max ranges."""
    return datetime.datetime.now(datetime.timezone.utc).date()


def resolve_max_period(campaign: dict) -> QueryPeriod:
    """Turn a campaign's created_time into an explicit maximum query period."""
    raw = campaign.get("created_time")
    match = re.match(r"(\d{4}-\d{2}-\d{2})", raw) if isinstance(raw, str) else None
    created = None
    if match is not None:
        try:
            created = datetime.datetime.strptime(match.group(1), "%Y-%m-%d").date()
        except ValueError:
            created = None
    today = utc_today()
    if created is None or created > today:
        raise ReportError(
            "Campaign is missing a valid created_time; cannot resolve maximum range."
        )
    return QueryPeriod(
        since=created.isoformat(),
        until=today.isoformat(),
        display="Maximum available range",
    )


def parse_range(value: str) -> QueryPeriod:
    match = re.fullmatch(
        r"(\d{2}-\d{2}-\d{4})\s+to\s+(\d{2}-\d{2}-\d{4})", value
    )
    if match is None:
        raise ReportError(
            'Date range must use the format "DD-MM-YYYY to DD-MM-YYYY".'
        )
    try:
        start = datetime.datetime.strptime(match.group(1), "%d-%m-%Y").date()
        end = datetime.datetime.strptime(match.group(2), "%d-%m-%Y").date()
    except ValueError as error:
        raise ReportError("Date range contains an invalid calendar date.") from error
    if start > end:
        raise ReportError("Date range start must not be after its end.")
    return QueryPeriod(
        since=start.isoformat(),
        until=end.isoformat(),
        display=f"{match.group(1)} to {match.group(2)}",
    )


AD_LEVELS = ("campaign", "ad")


def parse_arguments(
    arguments: list[str],
) -> tuple[QueryPeriod, str | None, str]:
    """Parse CLI args into (period, campaign selector, level)."""
    level = "campaign"
    remaining: list[str] = []
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--level":
            if index + 1 >= len(arguments):
                raise ReportError(
                    'Usage: ./scripts/today.sh [--level campaign|ad] [CAMPAIGN] '
                    'or ./scripts/today.sh --range "DD-MM-YYYY to DD-MM-YYYY" '
                    '[--level campaign|ad] [CAMPAIGN]'
                )
            requested = arguments[index + 1]
            if requested not in AD_LEVELS:
                raise ReportError(
                    f'--level must be one of {", ".join(AD_LEVELS)}, got {requested!r}.'
                )
            level = requested
            index += 2
            continue
        remaining.append(token)
        index += 1

    if not remaining:
        return today_period(), None, level
    if remaining[0] == "--range":
        if len(remaining) < 2:
            raise ReportError(
                'Usage: ./scripts/today.sh --range "DD-MM-YYYY to DD-MM-YYYY" '
                "[--level campaign|ad] [CAMPAIGN_ID_OR_EXACT_NAME] or "
                "./scripts/today.sh --range max [--level campaign|ad] [CAMPAIGN]"
            )
        if remaining[1].lower() == "max":
            if len(remaining) > 3:
                raise ReportError(
                    "Usage: ./scripts/today.sh --range max "
                    "[--level campaign|ad] [CAMPAIGN_ID_OR_EXACT_NAME]"
                )
            return (
                max_period(),
                remaining[2] if len(remaining) == 3 else None,
                level,
            )
        if len(remaining) not in (2, 3):
            raise ReportError(
                'Usage: ./scripts/today.sh --range "DD-MM-YYYY to DD-MM-YYYY" '
                "[--level campaign|ad] [CAMPAIGN_ID_OR_EXACT_NAME] or "
                "./scripts/today.sh --range max [--level campaign|ad] [CAMPAIGN]"
            )
        return (
            parse_range(remaining[1]),
            remaining[2] if len(remaining) == 3 else None,
            level,
        )
    if remaining[0].startswith("-") or len(remaining) != 1:
        raise ReportError(
            'Usage: ./scripts/today.sh [--level campaign|ad] [CAMPAIGN] or '
            './scripts/today.sh --range "DD-MM-YYYY to DD-MM-YYYY" '
            '[--level campaign|ad] [CAMPAIGN]'
        )
    return today_period(), remaining[0], level


def number(value: object) -> float | None:
    """Return a finite float for a numeric API value, otherwise None."""
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (str, int, float)):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def action_value(
    row: dict, field: str, action_types: tuple[str, ...]
) -> float | None:
    """Find a numeric value in a Meta action array using type priority order."""
    values = row.get(field)
    if not isinstance(values, list):
        return None
    for action_type in action_types:
        for item in values:
            if not isinstance(item, dict) or item.get("action_type") != action_type:
                continue
            result = number(item.get("value"))
            if result is not None:
                return result
    return None


def safe_rate(
    numerator: float | None, denominator: float | None
) -> float | None:
    """Calculate percentage points without inventing values for absent/zero data."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator * 100


def select_result(objective: str, row: dict) -> tuple[float | None, float | None]:
    """Select the primary result and cost-per-result for a supported objective."""
    if objective == "OUTCOME_SALES":
        action_types = PURCHASE_ACTIONS
    elif objective == "OUTCOME_LEADS":
        action_types = LEAD_ACTIONS
    else:
        return None, None
    return (
        action_value(row, "actions", action_types),
        action_value(row, "cost_per_action_type", action_types),
    )


def _scalar_or_action(
    row: dict, field: str, action_types: tuple[str, ...]
) -> float | None:
    scalar = number(row.get(field))
    if scalar is not None:
        return scalar
    return action_value(row, field, action_types)


def build_metrics(campaign: dict, insight: dict) -> dict[str, object]:
    """Normalize API fields and derive every value shown by the report."""
    objective = str(campaign.get("objective") or "")
    results, cost_per_result = select_result(objective, insight)

    daily_budget = number(campaign.get("daily_budget"))
    lifetime_budget = number(campaign.get("lifetime_budget"))
    if daily_budget is not None:
        budget = daily_budget / 100
        budget_period: str | None = "Daily"
    elif lifetime_budget is not None:
        budget = lifetime_budget / 100
        budget_period = "Lifetime"
    else:
        budget = None
        budget_period = None

    spend = number(insight.get("spend"))
    impressions = number(insight.get("impressions"))
    link_clicks = number(insight.get("inline_link_clicks"))
    link_cpc = number(insight.get("cost_per_inline_link_click"))
    if link_cpc is None and spend is not None and link_clicks not in (None, 0):
        link_cpc = spend / link_clicks
    date_start = insight.get("date_start")
    if not isinstance(date_start, str) or not date_start:
        date_start = None
    landing_page_views = action_value(
        insight, "actions", ("landing_page_view",)
    )
    video_views = action_value(insight, "actions", ("video_view",))
    thruplays = action_value(
        insight, "video_thruplay_watched_actions", ("video_view",)
    )

    return {
        "budget": budget,
        "budget_period": budget_period,
        "date_start": date_start,
        "spend": spend,
        "impressions": impressions,
        "reach": number(insight.get("reach")),
        "frequency": number(insight.get("frequency")),
        "link_clicks": link_clicks,
        "outbound_clicks": _scalar_or_action(
            insight, "outbound_clicks", ("outbound_click",)
        ),
        "unique_outbound_clicks": _scalar_or_action(
            insight, "unique_outbound_clicks", ("outbound_click",)
        ),
        "unique_outbound_ctr": _scalar_or_action(
            insight, "unique_outbound_clicks_ctr", ("outbound_click",)
        ),
        "ctr": number(insight.get("ctr")),
        "link_ctr": number(insight.get("inline_link_click_ctr")),
        "unique_link_ctr": _scalar_or_action(
            insight,
            "unique_inline_link_click_ctr",
            ("link_click", "inline_link_click"),
        ),
        "link_cpc": link_cpc,
        "cpm": number(insight.get("cpm")),
        "results": results,
        "cost_per_result": cost_per_result,
        "landing_page_views": landing_page_views,
        "landing_page_view_rate": safe_rate(landing_page_views, link_clicks),
        "cost_per_landing_page_view": action_value(
            insight, "cost_per_action_type", ("landing_page_view",)
        ),
        "purchase_value": action_value(
            insight, "action_values", PURCHASE_ACTIONS
        ),
        "purchase_roas": _scalar_or_action(
            insight, "purchase_roas", PURCHASE_ACTIONS
        ),
        "website_purchase_roas": _scalar_or_action(
            insight, "website_purchase_roas", PURCHASE_ACTIONS
        ),
        "hook_rate": safe_rate(video_views, impressions),
        "hold_rate": safe_rate(thruplays, video_views),
    }


def _sanitize_error(message: str) -> str:
    sanitized = re.sub(
        r"(?i)(access[_-]?token(?:%3[dD]|[=:])(?:%20|\s)*)([^&\s]+)",
        r"\1[REDACTED]",
        message,
    )
    sanitized = re.sub(r"(?i)(bearer\s+)[^\s]+", r"\1[REDACTED]", sanitized)
    return sanitized.strip()


def run_meta(meta_path: pathlib.Path, arguments: list[str]) -> object:
    """Run one read-only Meta CLI command and decode its JSON response."""
    if READ_ONLY_BLOCKLIST.intersection(argument.lower() for argument in arguments):
        raise ReportError("Refusing to run a command that can modify Meta data.")

    try:
        completed = subprocess.run(
            [str(meta_path), *arguments],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise ReportError(f"Could not start Meta CLI: {error.strerror or 'unknown error'}.") from error
    if completed.returncode != 0:
        detail = _sanitize_error(completed.stderr)
        if detail:
            raise ReportError(f"Meta CLI failed: {detail}")
        raise ReportError(f"Meta CLI failed with exit status {completed.returncode}.")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ReportError("Meta CLI returned invalid JSON.") from error


def _data_rows(payload: object, context: str) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ReportError(f"Meta CLI returned an unexpected {context} response.")
    return [row for row in payload["data"] if isinstance(row, dict)]


def load_campaigns(meta_path: pathlib.Path) -> list[dict]:
    payload = run_meta(
        meta_path,
        [
            "--output",
            "json",
            "ads",
            "campaign",
            "list",
            "--limit",
            "200",
            "--fields",
            "id,name,objective,effective_status,daily_budget,lifetime_budget,"
            "created_time",
        ],
    )
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return _data_rows(payload, "campaign list")


INSIGHT_FIELDS = (
    "date_start,date_stop,spend,impressions,reach,frequency,inline_link_clicks,"
    "outbound_clicks,unique_outbound_clicks,unique_outbound_clicks_ctr,"
    "ctr,inline_link_click_ctr,unique_inline_link_click_ctr,"
    "cost_per_inline_link_click,cpm,actions,cost_per_action_type,"
    "action_values,purchase_roas,website_purchase_roas,"
    "video_thruplay_watched_actions"
)


def _append_period_arguments(
    arguments: list[str], period: QueryPeriod, time_increment: str | None
) -> list[str]:
    """Append date-preset or since/until arguments, plus requested increment."""
    if period.preset is not None:
        arguments.extend(["--date-preset", period.preset])
        if time_increment is not None:
            arguments.extend(["--time-increment", time_increment])
    elif period.is_range:
        arguments.extend(["--since", period.since or "", "--until", period.until or ""])
        if time_increment is not None:
            arguments.extend(["--time-increment", time_increment])
    else:
        arguments.extend(["--date-preset", "today"])
    return arguments


def load_insight_rows(
    meta_path: pathlib.Path,
    campaign_id: str,
    period: QueryPeriod,
    time_increment: str | None,
) -> list[dict]:
    arguments = [
        "--output",
        "json",
        "ads",
        "insights",
        "get",
        "--campaign-id",
        campaign_id,
    ]
    _append_period_arguments(arguments, period, time_increment)
    arguments.extend(["--fields", INSIGHT_FIELDS])
    return _data_rows(run_meta(meta_path, arguments), "insights")


def load_today_insight(meta_path: pathlib.Path, campaign_id: str) -> dict:
    rows = load_insight_rows(meta_path, campaign_id, today_period(), None)
    return rows[0] if rows else {}


def _load_adset_rows(meta_path: pathlib.Path, campaign_id: str) -> list[dict]:
    payload = run_meta(
        meta_path,
        [
            "--output",
            "json",
            "ads",
            "adset",
            "list",
            campaign_id,
            "--limit",
            "200",
            "--fields",
            "id,name,effective_status",
        ],
    )
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return _data_rows(payload, "adset list")


def _load_ad_rows_for_adset(meta_path: pathlib.Path, adset_id: str) -> list[dict]:
    payload = run_meta(
        meta_path,
        [
            "--output",
            "json",
            "ads",
            "ad",
            "list",
            adset_id,
            "--limit",
            "200",
            "--fields",
            "id,name,effective_status,adset_id",
        ],
    )
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return _data_rows(payload, "ad list")


def _load_ad_insight(
    meta_path: pathlib.Path, ad_id: str, period: QueryPeriod
) -> dict:
    arguments = [
        "--output",
        "json",
        "ads",
        "insights",
        "get",
        "--ad-id",
        ad_id,
    ]
    _append_period_arguments(arguments, period, None)
    arguments.extend(["--fields", INSIGHT_FIELDS])
    rows = _data_rows(run_meta(meta_path, arguments), "ad insights")
    return rows[0] if rows else {}


def load_ad_breakdown_rows(
    meta_path: pathlib.Path,
    campaign_id: str,
    period: QueryPeriod,
) -> list[dict]:
    """Return one row per ad in the campaign, with adset name and insight."""
    adsets = _load_adset_rows(meta_path, campaign_id)
    rows: list[dict] = []
    for adset in adsets:
        adset_id = str(adset.get("id") or "")
        adset_name = str(adset.get("name") or adset_id or "Ad set")
        if not adset_id:
            continue
        ads = _load_ad_rows_for_adset(meta_path, adset_id)
        for ad in ads:
            ad_id = str(ad.get("id") or "")
            if not ad_id:
                continue
            try:
                insight = _load_ad_insight(meta_path, ad_id, period)
            except ReportError:
                insight = {}
            rows.append(
                {
                    "ad_id": ad_id,
                    "ad_name": str(ad.get("name") or ad_id),
                    "adset_name": adset_name,
                    "status": str(ad.get("effective_status") or ""),
                    "insight": insight,
                }
            )
    return rows


def select_campaigns(campaigns: list[dict], selector: str | None) -> list[dict]:
    """Select all active campaigns or one campaign by exact ID/name."""
    if selector is None:
        active = [
            campaign
            for campaign in campaigns
            if campaign.get("effective_status") == "ACTIVE"
        ]
        if not active:
            raise ReportError("No active campaigns found.")
        return active

    id_matches = [campaign for campaign in campaigns if campaign.get("id") == selector]
    if id_matches:
        return [id_matches[0]]

    name_matches = [
        campaign for campaign in campaigns if campaign.get("name") == selector
    ]
    if not name_matches:
        raise ReportError(f"No campaign matched exact ID or name: {selector}")
    if len(name_matches) > 1:
        raise ReportError(
            f"Multiple campaigns have the exact name {selector!r}; select by campaign ID."
        )
    return name_matches


def colors_enabled(stream: TextIO) -> bool:
    """Follow terminal capability and the standard NO_COLOR opt-out."""
    return "NO_COLOR" not in os.environ and stream.isatty()


def _color(text: str, code: str, enabled: bool) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if enabled else text


def _format_count(value: object) -> str:
    numeric = number(value)
    if numeric is None:
        return "N/A"
    if numeric.is_integer():
        return f"{int(numeric):,}"
    return f"{numeric:,.2f}"


def _format_decimal(value: object) -> str:
    numeric = number(value)
    return "N/A" if numeric is None else f"{numeric:,.2f}"


def _format_currency(value: object) -> str:
    numeric = number(value)
    return "N/A" if numeric is None else f"${numeric:,.2f}"


def _format_percent(value: object) -> str:
    numeric = number(value)
    return "N/A" if numeric is None else f"{numeric:,.2f}%"


def _format_roas(value: object) -> str:
    numeric = number(value)
    return "N/A" if numeric is None else f"{numeric:,.2f}x"


def render_report(
    campaign: dict,
    metrics: dict[str, object],
    use_color: bool,
    report_date: str | None = None,
    section_title: str | None = None,
) -> str:
    """Render one campaign as an aligned, two-column terminal table."""
    budget = _format_currency(metrics.get("budget"))
    budget_period = metrics.get("budget_period")
    if budget != "N/A" and isinstance(budget_period, str):
        budget = f"{budget} ({budget_period})"

    rows = [
        ("Budget", budget),
        ("Amount spent", _format_currency(metrics.get("spend"))),
        ("Impressions", _format_count(metrics.get("impressions"))),
        ("Reach", _format_count(metrics.get("reach"))),
        ("Frequency", _format_decimal(metrics.get("frequency"))),
        ("Link clicks", _format_count(metrics.get("link_clicks"))),
        ("Outbound clicks", _format_count(metrics.get("outbound_clicks"))),
        (
            "Unique outbound clicks",
            _format_count(metrics.get("unique_outbound_clicks")),
        ),
        ("Unique outbound CTR", _format_percent(metrics.get("unique_outbound_ctr"))),
        ("CTR", _format_percent(metrics.get("ctr"))),
        ("Link CTR", _format_percent(metrics.get("link_ctr"))),
        (
            "Unique CTR (link click-through rate)",
            _format_percent(metrics.get("unique_link_ctr")),
        ),
        ("CPC (cost per link click)", _format_currency(metrics.get("link_cpc"))),
        ("CPM", _format_currency(metrics.get("cpm"))),
        ("Results", _format_count(metrics.get("results"))),
        ("Cost per result", _format_currency(metrics.get("cost_per_result"))),
        ("Landing page views", _format_count(metrics.get("landing_page_views"))),
        (
            "Landing page views rate per link clicks",
            _format_percent(metrics.get("landing_page_view_rate")),
        ),
        (
            "Cost per landing page view",
            _format_currency(metrics.get("cost_per_landing_page_view")),
        ),
        ("Purchase value", _format_currency(metrics.get("purchase_value"))),
        ("Purchase ROAS", _format_roas(metrics.get("purchase_roas"))),
        (
            "Website purchase ROAS",
            _format_roas(metrics.get("website_purchase_roas")),
        ),
        ("Hook Rate", _format_percent(metrics.get("hook_rate"))),
        ("Hold Rate", _format_percent(metrics.get("hold_rate"))),
    ]

    label_width = max(len("Metric"), *(len(label) for label, _ in rows))
    value_width = max(len("Value"), *(len(value) for _, value in rows))
    campaign_name = str(campaign.get("name") or "Unnamed campaign")
    campaign_id = str(campaign.get("id") or "Unknown ID")
    heading = _color(f"Campaign: {campaign_name} ({campaign_id})", "1;36", use_color)
    displayed_date = report_date or metrics.get("date_start") or "N/A"
    date_line = _color(f"Date: {displayed_date}", "36", use_color)
    header = f"{'Metric':<{label_width}} | {'Value':<{value_width}}"
    separator = f"{'-' * label_width}-+-{'-' * value_width}"
    table_rows = [
        f"{label:<{label_width}} | {value:<{value_width}}" for label, value in rows
    ]
    lines = [heading, date_line]
    if section_title is not None:
        lines.append(_color(section_title, "1;35", use_color))
    lines.extend([_color(header, "1;32", use_color), separator, *table_rows])
    return "\n".join(lines)


def render_daily_breakdown(
    campaign: dict, insight_rows: list[dict], use_color: bool
) -> str:
    heading = _color("Daily Breakdown", "1;35", use_color)
    if not insight_rows:
        return f"{heading}\nNo daily insight rows returned by Meta."

    headers = (
        "Date",
        "Spend",
        "Impressions",
        "Reach",
        "Link clicks",
        "CTR",
        "Link CPC",
        "CPM",
        "Results",
        "Landing page views",
        "Hook Rate",
        "Hold Rate",
    )
    table: list[tuple[str, ...]] = []
    for insight in insight_rows:
        metrics = build_metrics(campaign, insight)
        table.append(
            (
                str(metrics.get("date_start") or "N/A"),
                _format_currency(metrics.get("spend")),
                _format_count(metrics.get("impressions")),
                _format_count(metrics.get("reach")),
                _format_count(metrics.get("link_clicks")),
                _format_percent(metrics.get("ctr")),
                _format_currency(metrics.get("link_cpc")),
                _format_currency(metrics.get("cpm")),
                _format_count(metrics.get("results")),
                _format_count(metrics.get("landing_page_views")),
                _format_percent(metrics.get("hook_rate")),
                _format_percent(metrics.get("hold_rate")),
            )
        )

    widths = [
        max(len(headers[index]), *(len(row[index]) for row in table))
        for index in range(len(headers))
    ]

    def format_row(row: tuple[str, ...]) -> str:
        return " | ".join(value.ljust(widths[index]) for index, value in enumerate(row))

    header = format_row(headers)
    separator = "-+-".join("-" * width for width in widths)
    return "\n".join(
        [heading, _color(header, "1;32", use_color), separator]
        + [format_row(row) for row in table]
    )


def render_ad_breakdown(
    campaign: dict, ad_rows: list[dict], use_color: bool
) -> str:
    heading = _color("Ad Breakdown", "1;35", use_color)
    if not ad_rows:
        return f"{heading}\nNo ads found for this campaign."

    headers = (
        "Ad name",
        "Ad set",
        "Status",
        "Spend",
        "Impressions",
        "Reach",
        "Link clicks",
        "CTR",
        "Link CPC",
        "CPM",
        "Results",
        "Landing page views",
        "Hook Rate",
        "Hold Rate",
    )
    table: list[tuple[str, ...]] = []
    for entry in ad_rows:
        metrics = build_metrics(campaign, entry.get("insight") or {})
        table.append(
            (
                str(entry.get("ad_name") or "N/A"),
                str(entry.get("adset_name") or "N/A"),
                str(entry.get("status") or "N/A"),
                _format_currency(metrics.get("spend")),
                _format_count(metrics.get("impressions")),
                _format_count(metrics.get("reach")),
                _format_count(metrics.get("link_clicks")),
                _format_percent(metrics.get("ctr")),
                _format_currency(metrics.get("link_cpc")),
                _format_currency(metrics.get("cpm")),
                _format_count(metrics.get("results")),
                _format_count(metrics.get("landing_page_views")),
                _format_percent(metrics.get("hook_rate")),
                _format_percent(metrics.get("hold_rate")),
            )
        )

    widths = [
        max(len(headers[index]), *(len(row[index]) for row in table))
        for index in range(len(headers))
    ]

    def format_row(row: tuple[str, ...]) -> str:
        return " | ".join(
            value.ljust(widths[index]) for index, value in enumerate(row)
        )

    header = format_row(headers)
    separator = "-+-".join("-" * width for width in widths)
    return "\n".join(
        [heading, _color(header, "1;32", use_color), separator]
        + [format_row(row) for row in table]
    )


def _stderr(message: str, color_code: str = "33") -> None:
    print(_color(message, color_code, colors_enabled(sys.stderr)), file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        period, selector, level = parse_arguments(arguments)
    except ReportError as error:
        _stderr(f"Error: {error}", "1;31")
        return 2

    if not ENV_PATH.is_file():
        _stderr("Error: project .env file is missing.", "1;31")
        return 1
    if not META_PATH.is_file():
        _stderr("Error: project Meta CLI is missing at .venv/bin/meta.", "1;31")
        return 1

    try:
        campaigns = load_campaigns(META_PATH)
        selected = select_campaigns(campaigns, selector)
    except ReportError as error:
        _stderr(f"Error: {error}", "1;31")
        return 1

    noun = "campaign" if len(selected) == 1 else "campaigns"
    _stderr(f"Selected {len(selected)} {noun}.", "1;36")
    reports: list[str] = []
    had_failure = False
    use_color = colors_enabled(sys.stdout)
    for campaign in selected:
        campaign_name = str(campaign.get("name") or campaign.get("id") or "campaign")
        campaign_id = str(campaign.get("id") or "")
        _stderr(f"Loading {campaign_name}...")
        try:
            effective_period = (
                resolve_max_period(campaign)
                if period.preset == "maximum"
                else period
            )
            if effective_period.is_range:
                aggregate_rows = load_insight_rows(
                    META_PATH, campaign_id, effective_period, "all_days"
                )
                aggregate = aggregate_rows[0] if aggregate_rows else {}
                summary = render_report(
                    campaign,
                    build_metrics(campaign, aggregate),
                    use_color=use_color,
                    report_date=effective_period.display,
                    section_title="Period Summary",
                )
                sections = [summary]
                if level == "ad":
                    ad_rows = load_ad_breakdown_rows(META_PATH, campaign_id, effective_period)
                    sections.append(render_ad_breakdown(campaign, ad_rows, use_color))
                else:
                    daily_rows = load_insight_rows(
                        META_PATH, campaign_id, effective_period, "daily"
                    )
                    sections.append(
                        render_daily_breakdown(campaign, daily_rows, use_color)
                    )
                reports.append("\n\n".join(sections))
            else:
                insight = load_today_insight(META_PATH, campaign_id)
                sections = [
                    render_report(
                        campaign,
                        build_metrics(campaign, insight),
                        use_color=use_color,
                    )
                ]
                if level == "ad":
                    ad_rows = load_ad_breakdown_rows(
                        META_PATH, campaign_id, period
                    )
                    sections.append(
                        render_ad_breakdown(campaign, ad_rows, use_color)
                    )
                reports.append("\n\n".join(sections))
        except ReportError as error:
            had_failure = True
            _stderr(f"Warning: {campaign_name}: {error}", "1;31")

    if reports:
        print("\n\n".join(reports))
    return 1 if had_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
