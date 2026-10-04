import csv
import io
import json
from pathlib import Path
from typing import Any

from skool_data.errors import ConfigurationError

_FIELDS = ["name", "value", "observed_at", "dimensions"]


def render_json(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, indent=2, sort_keys=True) + "\n"


def render_csv(rows: list[dict[str, Any]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "name": row["name"],
                "value": row["value"],
                "observed_at": row["observed_at"],
                "dimensions": json.dumps(
                    row.get("dimensions", {}), sort_keys=True, separators=(",", ":")
                ),
            }
        )
    return output.getvalue()


def write_export(path: Path, content: str, *, force: bool = False) -> None:
    if path.exists() and not force:
        raise ConfigurationError(f"Export already exists: {path}; use --force")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
