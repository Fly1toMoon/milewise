"""One JSON log line per request that is also a CloudWatch EMF metric record.

Lambda ships stdout to CloudWatch Logs; the `_aws` block turns the listed fields into
custom metrics without any PutMetricData call (design.md §4.5). Kept to a handful of
metrics because each one costs $0.30/month while it is being published.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

NAMESPACE = "Milewise"
METRICS = {
    "latency_ms": "Milliseconds",
    "cost_usd": "None",
    "input_tokens": "Count",
    "output_tokens": "Count",
    "postcheck_failed": "Count",
    "error": "Count",
}


def emit(event: str, **fields: Any) -> None:
    record: dict[str, Any] = {"event": event, "ts": round(time.time(), 3), **fields}
    metrics = [{"Name": k, "Unit": u} for k, u in METRICS.items() if k in fields]
    if metrics:
        record["service"] = fields.get("service", "api")
        record["_aws"] = {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {"Namespace": NAMESPACE, "Dimensions": [["service"]], "Metrics": metrics}
            ],
        }
    sys.stdout.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()
