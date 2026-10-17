"""DataApiDb against a stubbed rds-data client (the real one is exercised after apply)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import boto3
import pytest
from botocore.stub import ANY, Stubber

from milewise.db import DataApiDb, split_statements

ARN = "arn:aws:rds:ap-northeast-1:111122223333:cluster:milewise"
SECRET = "arn:aws:secretsmanager:ap-northeast-1:111122223333:secret:db-AbCdEf"
BASE = {"resourceArn": ARN, "secretArn": SECRET, "database": "milewise"}


@pytest.fixture
def stubbed() -> tuple[DataApiDb, Stubber]:
    client = boto3.client(
        "rds-data", region_name="ap-northeast-1", aws_access_key_id="x", aws_secret_access_key="x"
    )
    return DataApiDb(ARN, SECRET, "milewise", client=client), Stubber(client)


def test_query_converts_params_and_types(stubbed: tuple[DataApiDb, Stubber]) -> None:
    db, stub = stubbed
    stub.add_response(
        "execute_statement",
        {
            "columnMetadata": [
                {"name": "chart_id", "label": "chart_id", "typeName": "text"},
                {"name": "booking_from", "label": "booking_from", "typeName": "date"},
                {"name": "fetched_at", "label": "fetched_at", "typeName": "timestamptz"},
                {"name": "miles", "label": "miles", "typeName": "int4"},
                {"name": "score", "label": "score", "typeName": "numeric"},
                {"name": "booking_to", "label": "booking_to", "typeName": "date"},
            ],
            "records": [
                [
                    {"stringValue": "NH-INTL-OWN-RT@2025-06-24"},
                    {"stringValue": "2025-06-24"},
                    {"stringValue": "2026-10-16 15:33:45"},
                    {"longValue": 110000},
                    {"stringValue": "0.701"},
                    {"isNull": True},
                ]
            ],
        },
        {
            **BASE,
            "sql": "SELECT ... WHERE b <= CAST(:b AS date) AND c = :c AND n = :n",
            "parameters": [
                {"name": "b", "value": {"stringValue": "2026-10-17"}},
                {"name": "c", "value": {"stringValue": "C"}},
                {"name": "n", "value": {"isNull": True}},
            ],
            "includeResultMetadata": True,
        },
    )
    with stub:
        rows = db.query(
            "SELECT ... WHERE b <= CAST(:b AS date) AND c = :c AND n = :n",
            {"b": date(2026, 10, 17), "c": "C", "n": None},
        )
    assert rows == [
        {
            "chart_id": "NH-INTL-OWN-RT@2025-06-24",
            "booking_from": date(2025, 6, 24),
            "fetched_at": datetime(2026, 10, 16, 15, 33, 45, tzinfo=UTC),
            "miles": 110000,
            "score": Decimal("0.701"),
            "booking_to": None,
        }
    ]


def test_transaction_threads_id_and_commits(stubbed: tuple[DataApiDb, Stubber]) -> None:
    db, stub = stubbed
    stub.add_response("begin_transaction", {"transactionId": "tx-1"}, BASE)
    stub.add_response(
        "execute_statement",
        {},
        {**BASE, "sql": "DELETE FROM t", "parameters": [], "transactionId": "tx-1"},
    )
    stub.add_response(
        "batch_execute_statement",
        {"updateResults": []},
        {**BASE, "sql": "INSERT INTO t VALUES (:a)", "parameterSets": ANY, "transactionId": "tx-1"},
    )
    stub.add_response(
        "commit_transaction",
        {"transactionStatus": "Transaction Committed"},
        {"resourceArn": ARN, "secretArn": SECRET, "transactionId": "tx-1"},
    )
    with stub, db.transaction():
        db.execute("DELETE FROM t")
        db.execute_many("INSERT INTO t VALUES (:a)", [{"a": 1}, {"a": 2}])
    stub.assert_no_pending_responses()


def test_transaction_rolls_back_on_error(stubbed: tuple[DataApiDb, Stubber]) -> None:
    db, stub = stubbed
    stub.add_response("begin_transaction", {"transactionId": "tx-2"}, BASE)
    stub.add_response(
        "rollback_transaction",
        {"transactionStatus": "Rollback Complete"},
        {"resourceArn": ARN, "secretArn": SECRET, "transactionId": "tx-2"},
    )
    with stub, pytest.raises(RuntimeError), db.transaction():
        raise RuntimeError("boom")
    stub.assert_no_pending_responses()


def test_resuming_cluster_is_retried(
    stubbed: tuple[DataApiDb, Stubber], monkeypatch: pytest.MonkeyPatch
) -> None:
    db, stub = stubbed
    monkeypatch.setattr("milewise.db.time.sleep", lambda _s: None)
    stub.add_client_error("execute_statement", "DatabaseResumingException", "resuming")
    stub.add_response("execute_statement", {"records": [], "columnMetadata": []})
    with stub:
        assert db.query("SELECT 1") == []


def test_split_statements_respects_quotes_and_comments() -> None:
    sql = """
    -- a comment; with a semicolon
    CREATE TABLE a (x text DEFAULT 'a;b');
    CREATE INDEX ON a (x);
    """
    assert split_statements(sql) == [
        "CREATE TABLE a (x text DEFAULT 'a;b')",
        "CREATE INDEX ON a (x)",
    ]


def test_every_migration_splits_cleanly() -> None:
    from milewise.db import MIGRATIONS_DIR

    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        stmts = split_statements(path.read_text(encoding="utf-8"))
        assert stmts, path
        assert all(s.split()[0].upper() in {"CREATE", "ALTER", "INSERT"} for s in stmts), path
