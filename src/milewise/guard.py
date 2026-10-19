"""Protection for the public, token-free endpoint (design.md §4.8).

Every question to POST /ask passes, in order:

  1. kill switch      `make public-off` stops public answers at once (owner token still works)
  2. bot check        Cloudflare Turnstile, when a site key is configured
  3. per-IP limits    N per 10 minutes and M per day; IPv6 grouped by /64
  4. answer cache     the same question (same day, same data and model) is answered from
                      cache: free, and it does not count against the site budget
  5. site budget      requests/day and model USD/day for all public users together; the
                      first to be reached closes the public endpoint until midnight JST,
                      and the owner gets one email the first time it happens that day

A request with the private owner token (you, and CI evals) skips 1-5 and uses its own
daily budget, so a drained public budget never locks the owner out.

State lives in one DynamoDB table (pk + TTL); without QUOTA_TABLE an in-process store is
used, which is enough for tests and a single local uvicorn.
"""

from __future__ import annotations

import hashlib
import ipaddress
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

DAY_TTL = 2 * 86400
WINDOW_S = 600


class Store(Protocol):
    def add(self, pk: str, counters: dict[str, float], ttl_s: int) -> dict[str, Any]: ...

    def get(self, pk: str) -> dict[str, Any] | None: ...

    def put(self, pk: str, item: dict[str, Any], ttl_s: int, if_absent: bool = False) -> bool: ...

    def delete(self, pk: str) -> None: ...


class MemoryStore:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def add(self, pk: str, counters: dict[str, float], ttl_s: int) -> dict[str, Any]:
        with self._lock:
            item = self.items.setdefault(pk, {"expires_at": time.time() + ttl_s})
            for k, v in counters.items():
                item[k] = item.get(k, 0) + v
            return dict(item)

    def get(self, pk: str) -> dict[str, Any] | None:
        item = self.items.get(pk)
        return dict(item) if item and item.get("expires_at", 1e12) > time.time() else None

    def put(self, pk: str, item: dict[str, Any], ttl_s: int, if_absent: bool = False) -> bool:
        with self._lock:
            if if_absent and self.get(pk) is not None:
                return False
            self.items[pk] = {**item, "expires_at": time.time() + ttl_s}
            return True

    def delete(self, pk: str) -> None:
        self.items.pop(pk, None)


def _ddb_value(v: Any) -> dict[str, Any]:
    if isinstance(v, bool):
        return {"BOOL": v}
    if isinstance(v, int | float | Decimal):
        return {"N": str(Decimal(str(v)))}
    return {"S": str(v)}


def _py(v: Any) -> Any:
    if "N" in v:
        n = Decimal(v["N"])
        return int(n) if n == n.to_integral_value() else float(n)
    if "BOOL" in v:
        return v["BOOL"]
    return v.get("S")


class DynamoStore:
    def __init__(self, table: str, client: Any = None) -> None:
        import boto3

        self.table = table
        self.client = client or boto3.client("dynamodb")

    def add(self, pk: str, counters: dict[str, float], ttl_s: int) -> dict[str, Any]:
        names = {f"#c{i}": k for i, k in enumerate(counters)}
        values = {f":v{i}": _ddb_value(round(v, 6)) for i, v in enumerate(counters.values())}
        values[":t"] = {"N": str(int(time.time()) + ttl_s)}
        r = self.client.update_item(
            TableName=self.table,
            Key={"pk": {"S": pk}},
            UpdateExpression="ADD "
            + ", ".join(f"#c{i} :v{i}" for i in range(len(counters)))
            + " SET expires_at = if_not_exists(expires_at, :t)",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
        return {k: _py(v) for k, v in r["Attributes"].items()}

    def get(self, pk: str) -> dict[str, Any] | None:
        r = self.client.get_item(TableName=self.table, Key={"pk": {"S": pk}})
        item = {k: _py(v) for k, v in r.get("Item", {}).items()}
        # TTL deletion is lazy (up to ~48 h), so expired items are filtered here
        if not item or item.get("expires_at", 1e12) <= time.time():
            return None
        return item

    def put(self, pk: str, item: dict[str, Any], ttl_s: int, if_absent: bool = False) -> bool:
        from botocore.exceptions import ClientError

        attrs = {k: _ddb_value(v) for k, v in item.items()}
        attrs["pk"] = {"S": pk}
        attrs["expires_at"] = {"N": str(int(time.time()) + ttl_s)}
        kw: dict[str, Any] = {}
        if if_absent:
            kw = {
                "ConditionExpression": "attribute_not_exists(pk) OR expires_at < :now",
                "ExpressionAttributeValues": {":now": {"N": str(int(time.time()))}},
            }
        try:
            self.client.put_item(TableName=self.table, Item=attrs, **kw)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def delete(self, pk: str) -> None:
        self.client.delete_item(TableName=self.table, Key={"pk": {"S": pk}})


def client_key(ip: str) -> str:
    """IPv4 as is; IPv6 by /64, the block a single subscriber usually gets."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "unknown"
    if addr.version == 6:
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


def question_key(question: str, *parts: str) -> str:
    norm = " ".join(question.split()).lower()
    return hashlib.sha256("\x1f".join([norm, *parts]).encode()).hexdigest()


@dataclass(frozen=True)
class Limits:
    public_requests: int = 1000
    public_cost_usd: float = 2.0
    ip_window: int = 5  # per WINDOW_S
    ip_daily: int = 20
    owner_requests: int = 300
    owner_cost_usd: float = 3.0
    cache_ttl_s: int = 86400


class Denied(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class Guard:
    def __init__(self, store: Store, limits: Limits, alert: Any = None) -> None:
        self.store, self.limits = store, limits
        self.alert = alert  # callable(subject, message) or None
        self._switch: tuple[float, bool] = (0.0, True)

    # -- 1. kill switch ---------------------------------------------------------------
    def public_enabled(self) -> bool:
        checked, enabled = self._switch
        if time.time() - checked > 30:  # one read per 30 s per process
            item = self.store.get("config#public")
            enabled = not (item and item.get("enabled") is False)
            self._switch = (time.time(), enabled)
        return enabled

    def set_public(self, enabled: bool) -> None:
        self.store.put("config#public", {"enabled": enabled}, ttl_s=10 * 365 * 86400)
        self._switch = (0.0, enabled)

    # -- 3. per IP ------------------------------------------------------------------
    def check_ip(self, ip: str, day: str) -> None:
        key = client_key(ip)
        window = int(time.time() // WINDOW_S)
        w = self.store.add(f"ip#{key}#w#{window}", {"requests": 1}, WINDOW_S + 60)
        d = self.store.add(f"ip#{key}#d#{day}", {"requests": 1}, DAY_TTL)
        if w["requests"] > self.limits.ip_window:
            raise Denied(429, "ip_window", "too many questions; try again in a few minutes")
        if d["requests"] > self.limits.ip_daily:
            raise Denied(429, "ip_daily", "daily limit for your network reached; try tomorrow")

    # -- 4. answer cache -----------------------------------------------------------------
    def cached(self, key: str) -> str | None:
        item = self.store.get(f"cache#{key}")
        return str(item["payload"]) if item and item.get("payload") else None

    def remember(self, key: str, payload_json: str) -> None:
        self.store.put(f"cache#{key}", {"payload": payload_json}, self.limits.cache_ttl_s)

    # -- 5. budgets ------------------------------------------------------------------
    def reserve(self, pool: str, day: str) -> None:
        """Count one request against the pool's day; raise when the pool is closed."""
        req_limit, cost_limit = self._pool_limits(pool)
        u = self.store.add(f"{pool}#{day}", {"requests": 1}, DAY_TTL)
        over = []
        if u["requests"] > req_limit:
            over.append(f"requests {u['requests'] - 1}/{req_limit}")
        if float(u.get("cost_usd", 0)) >= cost_limit:
            over.append(f"model cost ${float(u.get('cost_usd', 0)):.2f}/${cost_limit:.2f}")
        if over:
            if pool == "public":
                self._alert_once(day, over)
            raise Denied(
                429, f"{pool}_budget", "today's free quota is used up; back at midnight JST"
            )

    def record_cost(self, pool: str, day: str, cost_usd: float) -> None:
        if cost_usd > 0:
            self.store.add(f"{pool}#{day}", {"cost_usd": cost_usd}, DAY_TTL)

    def usage(self, pool: str, day: str) -> dict[str, Any]:
        return self.store.get(f"{pool}#{day}") or {"requests": 0, "cost_usd": 0}

    def _pool_limits(self, pool: str) -> tuple[int, float]:
        lim = self.limits
        if pool == "public":
            return lim.public_requests, lim.public_cost_usd
        return lim.owner_requests, lim.owner_cost_usd

    def _alert_once(self, day: str, reasons: list[str]) -> None:
        if self.alert and self.store.put(
            f"alert#public#{day}", {"sent": True}, DAY_TTL, if_absent=True
        ):
            self.alert(
                f"Milewise: public daily limit reached ({day})",
                "The public endpoint stopped answering for today (JST): "
                + "; ".join(reasons)
                + ".\nIt reopens at midnight JST. The owner token keeps working.\n"
                "If this looks like abuse: `make public-off` closes the public endpoint.",
            )
