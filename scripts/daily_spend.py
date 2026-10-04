#!/usr/bin/env python3
"""Report daily ad spend for a date or a range, using the read-only Meta CLI."""

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
READ_ONLY_BLOCKLIST = frozenset({"create", "update", "delete"})


class ReportError(Exception):
    """A concise, user-facing report failure."""


@dataclass(frozen=True)
class QueryPeriod:
    since: str
    until: str
    display: str


def parse_period(value: str) -> QueryPeriod:
    """Accept DD-MM-YYYY or "DD-MM-YYYY to DD-MM-YYYY", nothing else."""
    single = re.fullmatch(r"(\d{2}-\d{2}-\d{4})", value)
    ranged = re.fullmatch(r"(\d{2}-\d{2}-\d{4})\s+to\s+(\d{2}-\d{2}-\d{4})", value)
    try:
        if ranged is not None:
            start = datetime.datetime.strptime(ranged.group(1), "%d-%m-%Y").date()
            end = datetime.datetime.strptime(ranged.group(2), "%d-%m-%Y").date()
            if start > end:
                raise ReportError("Date range start must not be after its end.")
            return QueryPeriod(
                since=start.isoformat(),
                until=end.isoformat(),
                display=f"{ranged.group(1)} to {ranged.group(2)}",
            )
        if single is not None:
            day = datetime.datetime.strptime(single.group(1), "%d-%m-%Y").date()
            return QueryPeriod(
                since=day.isoformat(),
                until=day.isoformat(),
                display=single.group(1),
            )
    except ValueError as error:
        raise ReportError("Period contains an invalid calendar date.") from error
    raise ReportError(
        'Supply one date "DD-MM-YYYY" or a range "DD-MM-YYYY to DD-MM-YYYY".'
    )


def number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (str, int, float)):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


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
        raise ReportError(
            f"Could not start Meta CLI: {error.strerror or 'unknown error'}."
        ) from error
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


def load_daily_spend_rows(
    meta_path: pathlib.Path, period: QueryPeriod
) -> list[dict]:
    """One account-level insights call covering paused and deleted spend too."""
    return _data_rows(
        run_meta(
            meta_path,
            [
                "--output",
                "json",
                "ads",
                "insights",
                "get",
                "--since",
                period.since,
                "--until",
                period.until,
                "--time-increment",
                "daily",
                "--fields",
                "date_start,spend",
            ],
        ),
        "insights",
    )


def colors_enabled(stream: TextIO) -> bool:
    return "NO_COLOR" not in os.environ and stream.isatty()


def _color(text: str, code: str, enabled: bool) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if enabled else text


def _format_currency(value: object) -> str:
    numeric = number(value)
    return "N/A" if numeric is None else f"${numeric:,.2f}"


def render_report(spend_by_date: dict[str, float], period: QueryPeriod, use_color: bool) -> str:
    """Render one row per date plus an overall total for multi-day ranges."""
    heading = _color("Daily Ad Spend", "1;35", use_color)
    date_line = _color(f"Period: {period.display}", "36", use_color)
    if not spend_by_date:
        return f"{heading}\n{date_line}\nNo spend recorded for this period."

    dates = sorted(spend_by_date)
    is_range = period.since != period.until
    table: list[tuple[str, str]] = [
        (date, _format_currency(spend_by_date[date])) for date in dates
    ]
    if is_range:
        total = sum(spend_by_date[date] for date in dates)
        table.append(("TOTAL", _format_currency(total)))

    headers = ("Date", "Spend")
    widths = [max(len(headers[i]), *(len(row[i]) for row in table)) for i in range(2)]

    def format_row(row: tuple[str, str]) -> str:
        return " | ".join(value.ljust(widths[i]) for i, value in enumerate(row))

    separator = "-+-".join("-" * width for width in widths)
    lines = [
        heading,
        date_line,
        _color(format_row(headers), "1;32", use_color),
        separator,
    ]
    for row in table:
        if row[0] == "TOTAL":
            lines.append(_color(format_row(row), "1;33", use_color))
        else:
            lines.append(format_row(row))
    return "\n".join(lines)


def _stderr(message: str, color_code: str = "33") -> None:
    print(_color(message, color_code, colors_enabled(sys.stderr)), file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        _stderr(
            "Error: supply exactly one argument: DD-MM-YYYY or "
            '"DD-MM-YYYY to DD-MM-YYYY".',
            "1;31",
        )
        return 2
    try:
        period = parse_period(arguments[0])
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
        rows = load_daily_spend_rows(META_PATH, period)
    except ReportError as error:
        _stderr(f"Error: {error}", "1;31")
        return 1

    spend_by_date: dict[str, float] = {}
    for row in rows:
        date = row.get("date_start")
        spend = number(row.get("spend"))
        if isinstance(date, str) and spend is not None:
            spend_by_date[date] = spend_by_date.get(date, 0.0) + spend

    print(render_report(spend_by_date, period, colors_enabled(sys.stdout)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
