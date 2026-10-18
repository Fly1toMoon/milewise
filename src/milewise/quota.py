"""Per-day request and cost counters for the public endpoint (design.md R7, R20, §4.6).

In Lambda: one DynamoDB item per JST day, updated with atomic ADD, expired by TTL.
Locally (no USAGE_TABLE): an in-process counter, which is enough for one uvicorn.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

TTL_DAYS = 35


@dataclass(frozen=True)
class Usage:
    requests: int
    cost_usd: float


class UsageCounter(Protocol):
    def add(self, day: str, requests: int = 0, cost_usd: float = 0.0) -> Usage: ...


class MemoryCounter:
    def __init__(self) -> None:
        self._days: dict[str, Usage] = {}
        self._lock = threading.Lock()

    def add(self, day: str, requests: int = 0, cost_usd: float = 0.0) -> Usage:
        with self._lock:
            u = self._days.get(day, Usage(0, 0.0))
            u = Usage(u.requests + requests, u.cost_usd + cost_usd)
            self._days[day] = u
            return u


class DynamoCounter:
    def __init__(self, table: str, client: Any = None) -> None:
        import boto3

        self.table = table
        self.client = client or boto3.client("dynamodb")

    def add(self, day: str, requests: int = 0, cost_usd: float = 0.0) -> Usage:
        r = self.client.update_item(
            TableName=self.table,
            Key={"day": {"S": day}},
            UpdateExpression="ADD requests :r, cost_usd :c SET expires_at = if_not_exists(expires_at, :t)",
            ExpressionAttributeValues={
                ":r": {"N": str(requests)},
                ":c": {"N": str(Decimal(str(round(cost_usd, 6))))},
                ":t": {"N": str(int(time.time()) + TTL_DAYS * 86400)},
            },
            ReturnValues="ALL_NEW",
        )
        a = r["Attributes"]
        return Usage(int(a["requests"]["N"]), float(a["cost_usd"]["N"]))


def counter(table: str) -> UsageCounter:
    return DynamoCounter(table) if table else MemoryCounter()
