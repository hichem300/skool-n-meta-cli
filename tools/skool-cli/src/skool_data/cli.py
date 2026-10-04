import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console

from skool_data import __version__
from skool_data.adapters import default_adapters
from skool_data.auth import import_har, load_credentials
from skool_data.capture import CaptureService
from skool_data.classroom_export import ClassroomExportService
from skool_data.classroom_http import ClassroomHttpClient, ResourceHttpClient
from skool_data.classroom_media import MediaDownloader
from skool_data.daily import (
    AdminDailySource,
    ProfileDailySource,
    VisitsDailySource,
    parse_query_since,
    parse_target_date,
)
from skool_data.daily_service import DailyAnalyticsService
from skool_data.diagnostics import run_checks
from skool_data.discovery import DiscoveryHttpClient, DiscoveryService
from skool_data.errors import SkoolDataError
from skool_data.export import render_csv, render_json, write_export
from skool_data.http import SkoolHttpClient
from skool_data.json_output import (
    render_capture_json,
    render_daily_json,
    render_discovery_json,
)
from skool_data.models import RunStatus, RuntimePaths
from skool_data.profile_http import ProfileHttpClient
from skool_data.rich_reporter import (
    RichCaptureReporter,
    render_business_compact,
    render_business_report,
    render_daily_report,
    render_discovery_report,
)
from skool_data.storage import Storage

app = typer.Typer(no_args_is_help=True)
auth_app = typer.Typer(no_args_is_help=True)
classroom_app = typer.Typer(no_args_is_help=True)
app.add_typer(auth_app, name="auth")
app.add_typer(classroom_app, name="classroom")


@app.callback()
def main() -> None:
    """Capture and inspect private Skool community analytics."""


@app.command()
def version() -> None:
    """Print the installed CLI version."""
    typer.echo(f"skool-data {__version__}")


def runtime_paths() -> RuntimePaths:
    return RuntimePaths(root=Path(os.environ.get("SKOOL_DATA_HOME", ".skool-data")))


def _storage(paths: RuntimePaths) -> Storage:
    storage = Storage(paths.database)
    storage.initialize()
    return storage


def _abort(exc: Exception) -> None:
    typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(1) from None


@auth_app.command("import-har")
def import_har_command(source: Path) -> None:
    """Import reusable private request configuration from a browser HAR."""
    destination = runtime_paths().credentials
    try:
        credentials = import_har(source, destination)
    except SkoolDataError as exc:
        _abort(exc)
    typer.echo(
        f"Imported community {credentials.community_id} credentials to {destination}"
    )


@classroom_app.command("export")
def classroom_export_command(
    module_url: str = typer.Argument(..., help="Full Skool classroom module URL."),
    output: Path = typer.Option(
        ..., "--output", "-o", help="Directory for Markdown and resources."
    ),
    force: bool = typer.Option(
        False, "--force", help="Redownload all managed artifacts."
    ),
) -> None:
    """Export one accessible classroom module to Markdown."""
    try:
        credentials = load_credentials(runtime_paths().credentials)
        with (
            ClassroomHttpClient(credentials) as module_client,
            ResourceHttpClient(credentials) as resource_client,
            MediaDownloader() as media_client,
        ):
            result = ClassroomExportService().run(
                module_url,
                output,
                module_client,
                resource_client,
                media_client,
                force=force,
                on_event=typer.echo,
            )
    except (SkoolDataError, OSError, ValueError) as exc:
        _abort(exc)
    typer.echo(f"Saved Markdown: {result.markdown_path}")


@app.command()
def discovery(
    page: int = typer.Option(
        1, "--page", "-p", min=1, help="Discovery results page."
    ),
    query: str | None = typer.Option(
        None, "--query", "-q", help="Search communities by keyword."
    ),
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI colors."),
    format: str = typer.Option("rich", "--format", help="Output: rich or json."),
) -> None:
    """Capture and save public Skool Discovery rankings."""
    paths = runtime_paths()
    console = Console(no_color=True if no_color else None)
    try:
        if format not in {"rich", "json"}:
            raise ValueError("--format must be rich or json")
        storage = _storage(paths)
        with DiscoveryHttpClient() as client:
            result = DiscoveryService(storage, paths.raw).run(
                client, datetime.now(UTC), page=page, query=query
            )
        if format == "json":
            typer.echo(render_discovery_json(result), nl=False)
        else:
            render_discovery_report(console, result)
    except (SkoolDataError, OSError, sqlite3.Error, ValueError) as exc:
        _abort(exc)
    if result.status is RunStatus.failed:
        raise typer.Exit(1)


@app.command()
def capture(
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI colors."),
    format: str = typer.Option("rich", "--format", help="Output: rich or json."),
    verbose: bool = typer.Option(
        False, "--verbose", help="Print the full multi-panel business report."
    ),
) -> None:
    """Capture analytics and print a compact business summary."""
    paths = runtime_paths()
    console = Console(no_color=True if no_color else None)
    try:
        if format not in {"rich", "json"}:
            raise ValueError("--format must be rich or json")
        reporter = RichCaptureReporter(console) if format == "rich" else None
        credentials = load_credentials(paths.credentials)
        storage = _storage(paths)
        with SkoolHttpClient(credentials) as client:
            result = CaptureService(
                storage,
                paths.raw,
                default_adapters(),
                reporter=reporter,
            ).run(credentials.community_id, client, datetime.now(UTC))
        rows = storage.query_metrics(run_id=result.run_id)
        if format == "json":
            typer.echo(render_capture_json(result, rows), nl=False)
        elif verbose:
            render_business_report(
                console,
                rows,
                capture_id=result.run_id,
                raw_path=paths.raw / result.run_id,
            )
        else:
            render_business_compact(
                console,
                rows,
                status=result.status,
                capture_id=result.run_id,
                raw_path=paths.raw / result.run_id,
            )
    except (SkoolDataError, OSError, sqlite3.Error, ValueError) as exc:
        _abort(exc)
    if result.status is RunStatus.failed:
        raise typer.Exit(1)


@app.command()
def daily(
    target_date: str | None = typer.Argument(
        None, metavar="DATE", help="UTC date in DD-MM-YYYY format."
    ),
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI colors."),
    format: str = typer.Option("rich", "--format", help="Output: rich or json."),
) -> None:
    """Fetch and save four analytics values for one UTC date."""
    paths = runtime_paths()
    console = Console(no_color=True if no_color else None)
    try:
        if format not in {"rich", "json"}:
            raise ValueError("--format must be rich or json")
        target = parse_target_date(target_date)
        credentials = load_credentials(paths.credentials)
        storage = _storage(paths)
        sources = [VisitsDailySource(), AdminDailySource(), ProfileDailySource()]
        with SkoolHttpClient(credentials) as api_client, ProfileHttpClient(
            credentials
        ) as profile_client:
            result = DailyAnalyticsService(storage, paths.raw, sources).run(
                credentials.community_id,
                api_client,
                profile_client,
                target,
            )
        if format == "json":
            typer.echo(render_daily_json(result), nl=False)
        else:
            render_daily_report(
                console,
                result,
                raw_path=paths.raw / result.run_id,
            )
    except (SkoolDataError, OSError, sqlite3.Error, ValueError) as exc:
        _abort(exc)
    if result.status is RunStatus.failed:
        raise typer.Exit(1)


@app.command()
def snapshots() -> None:
    """List locally recorded capture runs."""
    try:
        rows = _storage(runtime_paths()).list_runs()
    except (OSError, sqlite3.Error, ValueError) as exc:
        _abort(exc)
    if not rows:
        typer.echo("No capture runs found.")
        return
    typer.echo("CAPTURE ID\tCAPTURED AT\tSTATUS\tCOMMUNITY")
    for row in rows:
        typer.echo(
            f"{row['id']}\t{row['captured_at']}\t{row['status']}\t"
            f"{row['community_id']}"
        )


@app.command("query")
def query_command(
    metric: str | None = typer.Option(None, "--metric"),
    since: str | None = typer.Option(
        None, "--since", help="UTC start date in DD-MM-YYYY format."
    ),
    format: str = typer.Option("table", "--format"),
) -> None:
    """Query normalized metric observations."""
    try:
        since_dt = parse_query_since(since)
        rows = _storage(runtime_paths()).query_metrics(metric, since_dt)
        if format == "json":
            typer.echo(render_json(rows), nl=False)
            return
        if format != "table":
            raise ValueError("--format must be table or json")
    except (SkoolDataError, OSError, sqlite3.Error, ValueError) as exc:
        _abort(exc)
    if not rows:
        typer.echo("No metric observations found.")
        return
    typer.echo("METRIC\tVALUE\tOBSERVED AT\tDIMENSIONS")
    for row in rows:
        typer.echo(
            f"{row['name']}\t{row['value']}\t{row['observed_at']}\t"
            f"{row['dimensions']}"
        )


@app.command("export")
def export_command(
    output: Path = typer.Option(..., "--output"),
    metric: str | None = typer.Option(None, "--metric"),
    format: str = typer.Option("csv", "--format"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Export normalized observations as CSV or JSON."""
    try:
        rows = _storage(runtime_paths()).query_metrics(metric)
        if format == "csv":
            content = render_csv(rows)
        elif format == "json":
            content = render_json(rows)
        else:
            raise ValueError("--format must be csv or json")
        write_export(output, content, force=force)
    except (SkoolDataError, OSError, sqlite3.Error, ValueError) as exc:
        _abort(exc)
    typer.echo(f"Exported {len(rows)} observations to {output}")


@app.command()
def doctor(offline: bool = typer.Option(False, "--offline")) -> None:
    """Check credentials, local storage, and optionally API access."""
    checks = run_checks(runtime_paths(), offline=offline)
    for check in checks:
        typer.echo(f"{check.status}\t{check.name}\t{check.detail}")
    if any(check.status == "FAIL" for check in checks):
        raise typer.Exit(1)
