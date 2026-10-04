import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from skool_data.daily import DailyStatus
from skool_data.daily_service import DailyRunResult
from skool_data.discovery import DiscoveryRunResult
from skool_data.models import RunStatus


class RichCaptureReporter:
    """Render capture lifecycle events with one live display in interactive terminals.

    Interactive mode requires a real terminal with color support. Everything
    else falls back to the original plain lifecycle lines.
    """

    def __init__(self, console: Console) -> None:
        self.console = console
        self._total = 0
        self._succeeded = 0
        self._interactive = (
            console.is_terminal
            and not console.no_color
            and console.color_system is not None
        )
        self._live: Live | None = None
        self._current: str | None = None
        self._failed: list[tuple[str, str]] = []

    def run_started(self, total: int) -> None:
        self._total = total
        self._succeeded = 0
        self._failed = []
        self._current = None
        self.console.print()
        self.console.rule("[bold cyan]Skool Analytics Capture[/bold cyan]")
        self.console.print(
            f"[cyan]●[/cyan] Starting [bold]{total}[/bold] analytics requests\n"
        )
        if self._interactive:
            self._start_live()

    def endpoint_started(self, name: str, index: int, total: int) -> None:
        if self._interactive:
            self._current = name
            self._refresh_live()
            return
        self.console.print(
            f"[dim]{_clock()}[/dim] [cyan]●[/cyan] "
            f"[{index}/{total}] Fetching [bold]{_label(name)}[/bold]"
        )

    def endpoint_succeeded(
        self, name: str, elapsed: float, observation_count: int
    ) -> None:
        self._succeeded += 1
        if self._interactive:
            self._current = None
            self._refresh_live()
            return
        noun = "metric" if observation_count == 1 else "metrics"
        self.console.print(
            f"[dim]{_clock()}[/dim] [bold green]✓[/bold green] "
            f"{_label(name)} [green]succeeded[/green]  "
            f"[dim]{elapsed:.2f}s · {observation_count} {noun}[/dim]"
        )

    def endpoint_failed(self, name: str, elapsed: float, reason: str) -> None:
        if self._interactive:
            self._current = None
            self._failed.append((name, reason))
            self._refresh_live()
            return
        self.console.print(
            f"[dim]{_clock()}[/dim] [bold red]✗[/bold red] "
            f"{_label(name)} [red]failed[/red]  "
            f"[dim]{elapsed:.2f}s ·[/dim] [red]{reason}[/red]"
        )

    def run_finished(self, status: RunStatus, elapsed: float) -> None:
        self._stop_live()
        color = {
            RunStatus.complete: "green",
            RunStatus.partial: "yellow",
            RunStatus.failed: "red",
        }[status]
        self.console.print(
            f"\n[bold {color}]● {status.value.upper()}[/bold {color}] "
            f"[bold]{self._succeeded}/{self._total} endpoints[/bold] "
            f"[dim]in {elapsed:.2f}s[/dim]"
        )

    def _start_live(self) -> None:
        self._live = Live(
            self._renderable(),
            console=self.console,
            refresh_per_second=10,
            transient=True,
        )
        self._live.start()

    def _refresh_live(self) -> None:
        if self._live is not None:
            self._live.update(self._renderable())

    def _stop_live(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    def _renderable(self) -> RenderableType:
        done = self._succeeded + len(self._failed)
        parts: list[RenderableType] = [
            Text(
                f"[{done}/{self._total}] endpoints · "
                f"{self._succeeded} ok · {len(self._failed)} failed",
                style="bold",
            )
        ]
        if self._current is not None:
            parts.append(
                Spinner(
                    "dots",
                    text=Text(f"Fetching {_label(self._current)}", style="cyan"),
                )
            )
        for name, reason in self._failed:
            parts.append(Text(f"✗ {_label(name)} failed: {reason}", style="red"))
        return Group(*parts)


def render_discovery_report(
    console: Console, result: DiscoveryRunResult
) -> None:
    """Render one public Skool Discovery ranking capture."""
    if result.page is None:
        console.print(
            Panel(
                result.error or "Discovery capture failed",
                title="Skool Discovery",
                border_style="red",
            )
        )
        return

    page = result.page
    table = Table(title="Skool Discovery", show_lines=False)
    table.add_column("Rank", justify="right", style="bold cyan")
    table.add_column("Community", style="bold")
    table.add_column("Members", justify="right")
    table.add_column("Price")
    table.add_column("URL", overflow="fold")
    for listing in sorted(page.listings, key=lambda item: item.rank):
        table.add_row(
            str(listing.rank),
            listing.display_name,
            f"{listing.total_members:,}",
            listing.display_price or "Free",
            listing.url,
        )
    console.print()
    console.print(
        f"[cyan]✓[/cyan] Captured [bold]{page.result_count}[/bold] communities "
        f"in [dim]{result.elapsed_seconds:.2f}s[/dim]"
    )
    console.print(table)
    console.print(
        f"[dim]Capture ID: {result.run_id} · "
        f"Captured at: {result.captured_at.isoformat()}[/dim]"
    )


def render_business_compact(
    console: Console,
    rows: list[dict[str, Any]],
    *,
    status: RunStatus,
    capture_id: str,
    raw_path: Path,
) -> None:
    """Render one-screen summary of a capture run."""
    status_color = {
        RunStatus.complete: "green",
        RunStatus.partial: "yellow",
        RunStatus.failed: "red",
    }[status]
    console.print(
        f"[bold {status_color}]● {status.value.upper()}[/bold {status_color}]"
        "  Capture complete."
    )
    overview = _overview_panel(rows)
    overview.title = "[bold cyan]Business Overview[/bold cyan]"
    console.print()
    console.print(overview)
    latest_week = _latest_weekly_total(rows)
    if latest_week is None:
        traffic_line = "[dim]Weekly visitors: Unavailable[/dim]"
    else:
        key, total = latest_week
        traffic_line = (
            f"[bold blue]Weekly visitors[/bold blue] {key}: "
            f"[bold]{_count(total)}[/bold]"
        )
    console.print(traffic_line)
    console.print(
        f"[bold cyan]Capture ID[/bold cyan] {capture_id}  "
        f"[bold cyan]Raw data[/bold cyan] {raw_path}"
    )


def _latest_weekly_total(
    rows: list[dict[str, Any]],
) -> tuple[str, int] | None:
    """Return the most recent ISO week key and its visitor total."""
    daily = [row for row in rows if row["name"] == "growth.daily_visitors"]
    weeks: dict[str, int] = {}
    for row in daily:
        day = _as_utc_date(row.get("dimensions", {}).get("date"))
        if day is None:
            continue
        key = f"{day.isocalendar().year}-W{day.isocalendar().week:02d}"
        weeks[key] = weeks.get(key, 0) + int(row["value"])
    if not weeks:
        return None
    key = max(weeks)
    return key, weeks[key]


def render_business_report(
    console: Console,
    rows: list[dict[str, Any]],
    *,
    capture_id: str,
    raw_path: Path,
) -> None:
    """Render normalized observations belonging to one capture run."""
    console.print()
    console.print(_overview_panel(rows))
    console.print()
    console.print(_mrr_movement_table(rows))
    console.print()
    console.print(_cashflow_panel(rows))
    console.print()
    console.print(_source_table(rows))
    console.print()
    console.print(_daily_table(rows))
    console.print()
    console.print(_conversion_panel(rows))
    console.print()
    console.print(_retention_cohorts_table(rows))
    console.print()
    console.print(_member_retention_table(rows))
    console.print()
    console.print(_activity_panel(rows))
    console.print()
    console.print(_weekly_traffic_panel(rows))
    console.print()
    console.print(_traffic_source_table(rows))
    console.print()
    footer = Text()
    footer.append("Capture ID  ", style="bold cyan")
    footer.append(capture_id)
    footer.append("\nRaw data    ", style="bold cyan")
    footer.append(str(raw_path))
    console.print(Panel(footer, border_style="cyan", title="Saved Snapshot"))


def render_daily_report(
    console: Console,
    result: DailyRunResult,
    *,
    raw_path: Path,
) -> None:
    """Render four date-specific Skool metrics and their availability."""
    table = Table.grid(padding=(0, 2), expand=True)
    table.add_column(style="bold")
    table.add_column(justify="right")
    table.add_column(ratio=1)
    for item in result.values:
        if item.status is DailyStatus.available:
            display = Text(f"{item.value:,}", style="bold green")
            detail = Text("Available", style="green")
        elif item.status is DailyStatus.unavailable:
            display = Text("Unavailable", style="bold yellow")
            detail = Text(item.reason or "Outside retained range", style="yellow")
        else:
            display = Text("Failed", style="bold red")
            detail = Text(item.reason or "Request failed", style="red")
        table.add_row(item.label, display, detail)

    status_color = {
        RunStatus.complete: "green",
        RunStatus.partial: "yellow",
        RunStatus.failed: "red",
    }[result.status]
    console.print()
    console.print(
        Panel(
            table,
            title=(
                "[bold cyan]Daily Skool Analytics: "
                f"{result.target_date.isoformat()} UTC[/bold cyan]"
            ),
            border_style="cyan",
        )
    )
    console.print(
        f"[bold {status_color}]● {result.status.value.upper()}[/bold {status_color}] "
        f"[dim]Capture {result.run_id}[/dim]"
    )
    console.print(f"[dim]Raw data: {raw_path}[/dim]")


def _overview_panel(rows: list[dict[str, Any]]) -> Panel:
    table = Table.grid(padding=(0, 3))
    table.add_column(style="bold")
    table.add_column(justify="right", style="bold white")
    fields = [
        ("Current group members", "members.total", _count),
        ("MRR", "revenue.mrr", _money),
        ("Skool retention (definition supplied by Skool)", "retention.percent", _percent),
        ("Visitors", "growth.visitors", _count),
        ("Signups", "growth.signups", _count),
        ("New MRR", "growth.new_mrr", _money),
    ]
    for label, metric, formatter in fields:
        row = _latest(rows, metric)
        table.add_row(label, formatter(row["value"]) if row else "[dim]Unavailable[/dim]")
    paying = _estimated_paying_members(rows)
    if paying is None:
        table.add_row("Estimated paying members", "[dim]Unavailable[/dim]")
    else:
        month, count = paying
        table.add_row("Estimated paying members", f"{count:,} [dim]{month}[/dim]")
    return Panel(table, title="[bold cyan]Business Overview[/bold cyan]", border_style="cyan")


def _estimated_paying_members(
    rows: list[dict[str, Any]],
) -> tuple[str, int] | None:
    """Estimate paying members from the latest month shared by mrr.net and unit.arpu.

    Never mixes months: MRR and ARPU must come from the same reporting month.
    """
    by_month: dict[str, dict[str, Any]] = {}
    for metric in ("mrr.net", "unit.arpu"):
        matches = [row for row in rows if row["name"] == metric]
        if not matches:
            return None
        for row in matches:
            month = str(row.get("dimensions", {}).get("date", ""))
            if not month:
                continue
            cell = by_month.setdefault(month, {})
            current = cell.get(metric)
            if current is None or row["observed_at"] >= current["observed_at"]:
                cell[metric] = row
    common = sorted(
        month for month, cell in by_month.items() if "mrr.net" in cell and "unit.arpu" in cell
    )
    if not common:
        return None
    month = common[-1]
    mrr = by_month[month]["mrr.net"]["value"]
    arpu = by_month[month]["unit.arpu"]["value"]
    if arpu is None or arpu <= 0 or mrr is None:
        return None
    # Small tolerance absorbs float noise such as 40.58000183 / 8.116000175 -> 5.0
    return month, math.floor(mrr / arpu + 1e-6)


def _source_table(rows: list[dict[str, Any]]) -> Table | Panel:
    sources = [row for row in rows if row["name"] == "signups.by_source"]
    if not sources:
        return Panel("[dim]Unavailable[/dim]", title="Signup Sources", border_style="blue")
    table = Table(title="Signup Sources", border_style="blue", header_style="bold blue")
    table.add_column("Source")
    table.add_column("Signups", justify="right")
    table.add_column("Share", justify="right")
    for row in sorted(sources, key=lambda item: item["value"], reverse=True):
        dimensions = row.get("dimensions", {})
        source = next(
            (
                str(dimensions[key])
                for key in ("attribution", "label", "source", "name")
                if dimensions.get(key) is not None
            ),
            "Unknown",
        )
        share = dimensions.get("percent")
        table.add_row(source, _count(row["value"]), _percent(share) if share is not None else "—")
    return table


def _traffic_source_table(rows: list[dict[str, Any]]) -> Table | Panel:
    """Render the current visits-by-source snapshot for this capture run.

    Skool returns no source window for this chart, so the report says so
    instead of inventing a week label. Shares are computed from the grouped
    visitor totals of this run, not from the stored API percent.
    """
    sources = [row for row in rows if row["name"] == "traffic.by_source"]
    table = Table(
        title="Current Traffic Sources",
        border_style="blue",
        header_style="bold blue",
    )
    table.add_column("Source")
    table.add_column("Visitors", justify="right")
    table.add_column("Share", justify="right")
    if not sources:
        table.add_row("[dim]Unavailable[/dim]", "", "")
        table.caption = "Skool window unspecified"
        table.caption_style = "dim"
        return table
    grouped: dict[str, int] = {}
    for row in sources:
        dimensions = row.get("dimensions", {})
        attribution = str(
            dimensions.get("attribution") or "Unknown"
        )
        grouped[attribution] = grouped.get(attribution, 0) + int(row["value"])
    total = sum(grouped.values())
    for attribution, visitors in sorted(grouped.items(), key=lambda kv: kv[1], reverse=True):
        share = visitors / total if total else 0
        table.add_row(attribution, _count(visitors), _percent(share))
    table.caption = "Skool window unspecified"
    table.caption_style = "dim"
    return table


def _daily_table(rows: list[dict[str, Any]]) -> Table | Panel:
    daily = [row for row in rows if row["name"] == "signups.by_day"]
    if not daily:
        return Panel("[dim]Unavailable[/dim]", title="Daily Signups", border_style="blue")
    table = Table(title="Daily Signups", border_style="blue", header_style="bold blue")
    table.add_column("Date")
    table.add_column("Signups", justify="right")
    for row in sorted(daily, key=lambda item: str(item.get("dimensions", {}).get("date", ""))):
        date = str(row.get("dimensions", {}).get("date", row["observed_at"][:10]))
        table.add_row(date, _count(row["value"]))
    return table


def _weekly_traffic_panel(rows: list[dict[str, Any]]) -> Panel:
    """Group daily visitor observations into UTC ISO weeks with totals."""
    daily = [row for row in rows if row["name"] == "growth.daily_visitors"]
    if not daily:
        return Panel(
            "[dim]Unavailable[/dim]", title="Weekly Traffic", border_style="blue"
        )
    weeks: dict[str, list[date]] = {}
    counts: dict[str, int] = {}
    for row in daily:
        day = _as_utc_date(row.get("dimensions", {}).get("date"))
        if day is None:
            continue
        key = f"{day.isocalendar().year}-W{day.isocalendar().week:02d}"
        weeks.setdefault(key, []).append(day)
        counts[key] = counts.get(key, 0) + int(row["value"])
    table = Table(title="Weekly Traffic", border_style="blue", header_style="bold blue")
    table.add_column("Week (UTC)")
    table.add_column("Date Range")
    table.add_column("Visitors", justify="right")
    for key in sorted(weeks):
        days = sorted(weeks[key])
        start, end = days[0], days[-1]
        table.add_row(key, f"{start.strftime('%b %d')} – {end.strftime('%b %d')}", _count(counts[key]))
    return Panel(table, border_style="blue")


def _as_utc_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _activity_panel(rows: list[dict[str, Any]]) -> Panel:
    active = _latest(rows, "members.active")
    activity = _latest(rows, "activity.daily")
    activity_rows = [row for row in rows if row["name"] == "activity.daily"]
    table = Table.grid(padding=(0, 3))
    table.add_column(style="bold")
    table.add_column(justify="right")
    table.add_row(
        "Recent active people (may include departed)",
        _count(active["value"]) if active else "[dim]Unavailable[/dim]",
    )
    table.add_row("Latest daily activity", _count(activity["value"]) if activity else "[dim]Unavailable[/dim]")
    table.add_row(
        "Activity in captured range",
        _count(sum(row["value"] for row in activity_rows)) if activity_rows else "[dim]Unavailable[/dim]",
    )
    table.add_row(
        "Basis",
        Text(
            "Admin activity series. Not a subset of current membership: "
            "may include recently departed people, so it can exceed current group members.",
            style="dim",
        ),
    )
    return Panel(table, title="[bold blue]Member Activity[/bold blue]", border_style="blue")


def _latest(rows: list[dict[str, Any]], metric: str) -> dict[str, Any] | None:
    matches = [row for row in rows if row["name"] == metric]
    return max(matches, key=lambda row: row["observed_at"]) if matches else None


def _monthly_by_metric(
    rows: list[dict[str, Any]], metrics: list[str]
) -> tuple[list[str], dict[str, dict[str, dict[str, Any]]]]:
    """Group dated rows per metric, keeping the latest capture per metric and month."""
    months: set[str] = set()
    by_month: dict[str, dict[str, dict[str, Any]]] = {}
    for metric in metrics:
        for row in rows:
            if row["name"] != metric:
                continue
            month = str(row.get("dimensions", {}).get("date", ""))
            months.add(month)
            cell = by_month.setdefault(month, {})
            current = cell.get(metric)
            if current is None or row["observed_at"] >= current["observed_at"]:
                cell[metric] = row
    return sorted(months), by_month


def _monthly_table(
    title: str,
    columns: list[tuple[str, str, Any]],
    rows: list[dict[str, Any]],
    *,
    border_style: str = "blue",
    header_style: str = "bold blue",
) -> Table | Panel:
    """Render one row per month with the given (label, metric, formatter) columns."""
    months, by_month = _monthly_by_metric(rows, [metric for _, metric, _ in columns])
    if not months:
        return Panel("[dim]Unavailable[/dim]", title=title, border_style=border_style)
    table = Table(title=title, border_style=border_style, header_style=header_style)
    table.add_column("Month")
    for label, _, _ in columns:
        table.add_column(label, justify="right")
    for month in months:
        cells = by_month.get(month, {})
        table.add_row(
            month or "—",
            *[
                formatter(cells[metric]["value"]) if metric in cells else "—"
                for _, metric, formatter in columns
            ],
        )
    return table


def _mrr_movement_table(rows: list[dict[str, Any]]) -> Table | Panel:
    columns = [
        ("Net MRR", "mrr.net", _money),
        ("New", "mrr.new", _money),
        ("Existing", "mrr.existing", _money),
        ("Churn", "mrr.churn", _money),
        ("Upgrade", "mrr.upgrade", _money),
        ("Downgrade", "mrr.downgrade", _money),
        ("Reactivation", "mrr.reactivation", _money),
    ]
    return _monthly_table(
        "MRR Movement",
        columns,
        rows,
        border_style="cyan",
        header_style="bold cyan",
    )


def _cashflow_panel(rows: list[dict[str, Any]]) -> Panel:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold")
    table.add_column(justify="right", style="bold white")
    table.add_column(style="dim")
    entries = [
        ("Cashflow net", "cashflow.net", _money),
        ("Cashflow monthly", "cashflow.month", _money),
        ("Cashflow annual", "cashflow.year", _money),
        ("ARPU (paying members, monthly)", "unit.arpu", _money),
        ("LTV", "unit.ltv", _money),
    ]
    for label, metric, formatter in entries:
        matches = [row for row in rows if row["name"] == metric]
        if matches:
            row = max(
                matches,
                key=lambda item: str(item.get("dimensions", {}).get("date", "")),
            )
            month = str(row.get("dimensions", {}).get("date", ""))
            table.add_row(label, formatter(row["value"]), month)
        else:
            table.add_row(label, "[dim]Unavailable[/dim]", "")
    return Panel(
        table,
        title="[bold cyan]Cashflow & Unit Economics[/bold cyan]",
        border_style="cyan",
    )


def _retention_cohorts_table(rows: list[dict[str, Any]]) -> Table | Panel:
    columns = [
        ("MRR Retention", "retention.cohorts.rate", _percent),
        ("MRR", "retention.cohorts.mrr", _money),
    ]
    table = _monthly_table("Skool Completed Cohorts", columns, rows)
    if isinstance(table, Table):
        table.caption = (
            "API returns completed cohorts only. D30/D60/D90 unavailable."
        )
        table.caption_style = "dim"
    return table


def _member_retention_table(rows: list[dict[str, Any]]) -> Table | Panel:
    columns = [
        ("MRR Retention", "retention.members.rate", _percent),
        ("Upgrade", "retention.members.upgrade", _money),
        ("Downgrade", "retention.members.downgrade", _money),
        ("Churn", "retention.members.churn", _money),
        ("Reactivation", "retention.members.reactivation", _money),
    ]
    return _monthly_table("Member Retention", columns, rows)


def _conversion_panel(rows: list[dict[str, Any]]) -> Table | Panel:
    trials = [row for row in rows if row["name"] == "conversion.trials"]
    rates = [row for row in rows if row["name"] == "conversion.rate"]
    if not trials and not rates:
        return Panel(
            "[dim]Unavailable[/dim]", title="Trial Conversions", border_style="blue"
        )

    def latest_by_date(rows_: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        by_date: dict[str, dict[str, Any]] = {}
        for row in rows_:
            month = str(row.get("dimensions", {}).get("date", ""))
            current = by_date.get(month)
            if current is None or row["observed_at"] >= current["observed_at"]:
                by_date[month] = row
        return by_date

    trials_by_date = latest_by_date(trials)
    rates_by_date = latest_by_date(rates)
    months = sorted(set(trials_by_date) | set(rates_by_date))
    table = Table(title="Trial Conversions", border_style="blue", header_style="bold blue")
    table.add_column("Month")
    table.add_column("Trials", justify="right")
    table.add_column("Conversion rate (trial-based)", justify="right")
    for month in months:
        trial_row = trials_by_date.get(month)
        rate_row = rates_by_date.get(month)
        table.add_row(
            month or "—",
            _count(trial_row["value"]) if trial_row else "—",
            _percent(rate_row["value"]) if rate_row is not None else "—",
        )
    return table


def _label(name: str) -> str:
    return name.replace("-", " ")


def _clock() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _count(value: int | float) -> str:
    return f"{value:,.0f}"


def _money(value: int | float) -> str:
    return f"${value:,.0f}" if float(value).is_integer() else f"${value:,.2f}"


def _percent(value: int | float) -> str:
    percentage = value * 100
    return (
        f"{percentage:,.0f}%"
        if float(percentage).is_integer()
        else f"{percentage:,.1f}%"
    )
