from __future__ import annotations

from typing import Any

from milewise.guard import DynamoStore


class FakeDynamo:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def update_item(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("update", kw))
        return {
            "Attributes": {
                "pk": {"S": kw["Key"]["pk"]["S"]},
                "requests": {"N": "3"},
                "cost_usd": {"N": "0.25"},
            }
        }

    def put_item(self, **kw: Any) -> None:
        self.calls.append(("put", kw))


def test_dynamo_add_is_one_atomic_update_with_ttl() -> None:
    fake = FakeDynamo()
    out = DynamoStore("milewise-quota", client=fake).add("public#2026-10-19", {"requests": 1}, 60)
    assert out["requests"] == 3 and out["cost_usd"] == 0.25
    _kind, kw = fake.calls[0]
    assert (
        kw["UpdateExpression"].startswith("ADD #c0 :v0")
        and "if_not_exists(expires_at" in kw["UpdateExpression"]
    )
    assert kw["ExpressionAttributeNames"] == {"#c0": "requests"}


def test_dynamo_put_if_absent_is_conditional() -> None:
    fake = FakeDynamo()
    assert DynamoStore("t", client=fake).put("alert#public#d", {"sent": True}, 60, if_absent=True)
    kw = fake.calls[0][1]
    assert "attribute_not_exists(pk)" in kw["ConditionExpression"] and kw["Item"]["sent"] == {
        "BOOL": True
    }
