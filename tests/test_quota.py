from __future__ import annotations

from typing import Any

from milewise.quota import DynamoCounter, MemoryCounter


class FakeDynamo:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, float]] = {}
        self.calls: list[dict[str, Any]] = []

    def update_item(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        v = kw["ExpressionAttributeValues"]
        item = self.items.setdefault(kw["Key"]["day"]["S"], {"requests": 0, "cost_usd": 0.0})
        item["requests"] += int(v[":r"]["N"])
        item["cost_usd"] += float(v[":c"]["N"])
        return {
            "Attributes": {
                "requests": {"N": str(item["requests"])},
                "cost_usd": {"N": str(item["cost_usd"])},
            }
        }


def test_dynamo_counter_adds_atomically_per_day_with_ttl() -> None:
    fake = FakeDynamo()
    c = DynamoCounter("milewise-usage", client=fake)
    assert c.add("2026-10-18", requests=1).requests == 1
    u = c.add("2026-10-18", cost_usd=0.0021)
    assert u.requests == 1 and abs(u.cost_usd - 0.0021) < 1e-9
    assert c.add("2026-10-19", requests=1).requests == 1
    call = fake.calls[0]
    assert call["UpdateExpression"].startswith("ADD requests :r, cost_usd :c")
    assert "if_not_exists(expires_at" in call["UpdateExpression"]


def test_memory_counter() -> None:
    c = MemoryCounter()
    c.add("d", requests=1)
    assert c.add("d", requests=1, cost_usd=0.5).requests == 2
