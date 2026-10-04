import json
from typing import Any

from skool_data.capture import CaptureRunResult
from skool_data.daily_service import DailyRunResult
from skool_data.discovery import DiscoveryRunResult
from skool_data.redaction import redact


def _document(payload: dict[str, Any]) -> str:
    return json.dumps(redact(payload), indent=2, sort_keys=True) + "\n"


def render_discovery_json(result: DiscoveryRunResult) -> str:
    page = result.page
    return _document(
        {
            "kind": "discovery",
            "capture_id": result.run_id,
            "captured_at": result.captured_at.isoformat(),
            "status": result.status.value,
            "error": result.error,
            "result_count": page.result_count if page else 0,
            "total_available": page.total_available if page else None,
            "has_more": page.has_more if page else False,
            "listings": [listing.model_dump() for listing in page.listings]
            if page
            else [],
        }
    )


def render_daily_json(result: DailyRunResult) -> str:
    return _document(
        {
            "schema_version": 1,
            "kind": "daily",
            "capture_id": result.run_id,
            "target_date": result.target_date.isoformat(),
            "status": result.status.value,
            "metrics": [
                {
                    "name": value.metric,
                    "label": value.label,
                    "status": value.status.value,
                    "value": value.value,
                    "reason": value.reason,
                    "source_range": list(value.source_range)
                    if value.source_range
                    else None,
                }
                for value in result.values
            ],
            "failures": result.failures,
        }
    )


def render_capture_json(
    result: CaptureRunResult, rows: list[dict[str, Any]]
) -> str:
    return _document(
        {
            "schema_version": 1,
            "kind": "full_capture",
            "capture_id": result.run_id,
            "status": result.status.value,
            "sources": {
                "succeeded": result.successes,
                "failed": result.failures,
            },
            "observations": rows,
        }
    )
