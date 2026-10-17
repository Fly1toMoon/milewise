"""Spike for design.md R1: Aurora Serverless v2 (min 0 ACU) + RDS Data API + pgvector.

Runs from your laptop (outside the VPC), exactly like the Lambda will.

    uv run python scripts/spike_data_api.py --cluster-arn ... --secret-arn ... --database milewise
    uv run python scripts/spike_data_api.py --measure-resume ...   # waits for auto-pause first
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from botocore.exceptions import ClientError

RESUMING_CODES = {"DatabaseResumingException", "DatabaseNotFoundException"}


class DataApi:
    def __init__(self, cluster_arn: str, secret_arn: str, database: str, region: str) -> None:
        self.client = boto3.client("rds-data", region_name=region)
        self.base = {"resourceArn": cluster_arn, "secretArn": secret_arn, "database": database}

    def execute(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        transaction_id: str | None = None,
        max_wait_s: float = 120,
    ) -> dict[str, Any]:
        """ExecuteStatement with retry while the cluster resumes from 0 ACU."""
        kwargs: dict[str, Any] = {**self.base, "sql": sql, "includeResultMetadata": True}
        if params:
            kwargs["parameters"] = [to_param(k, v) for k, v in params.items()]
        if transaction_id:
            kwargs["transactionId"] = transaction_id
        deadline = time.monotonic() + max_wait_s
        delay = 1.0
        while True:
            try:
                return self.client.execute_statement(**kwargs)
            except ClientError as e:
                code = e.response["Error"]["Code"]
                msg = e.response["Error"].get("Message", "")
                resuming = code in RESUMING_CODES or "resum" in msg.lower()
                if not resuming or time.monotonic() > deadline:
                    raise
                print(f"  .. {code}: {msg[:80]} (retry in {delay:.0f}s)")
                time.sleep(delay)
                delay = min(delay * 2, 10)


def to_param(name: str, value: Any) -> dict[str, Any]:
    if value is None:
        return {"name": name, "value": {"isNull": True}}
    if isinstance(value, bool):
        return {"name": name, "value": {"booleanValue": value}}
    if isinstance(value, int):
        return {"name": name, "value": {"longValue": value}}
    if isinstance(value, float):
        return {"name": name, "value": {"doubleValue": value}}
    return {"name": name, "value": {"stringValue": str(value)}}


def vec(xs: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in xs) + "]"


def cell(field: dict[str, Any]) -> Any:
    return next(iter(field.values())) if field else None


def timed(fn: Any) -> tuple[Any, float]:
    t0 = time.perf_counter()
    out = fn()
    return out, (time.perf_counter() - t0) * 1000


def run_checks(db: DataApi) -> dict[str, Any]:
    report: dict[str, Any] = {}

    r, ms = timed(lambda: db.execute("SELECT version()"))
    report["first_query_ms"] = round(ms)
    report["pg_version"] = cell(r["records"][0][0])
    print(f"[ok] connected in {ms:.0f} ms: {report['pg_version'][:60]}")

    db.execute("CREATE EXTENSION IF NOT EXISTS vector")
    r = db.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    report["pgvector_version"] = cell(r["records"][0][0])
    print(f"[ok] pgvector {report['pgvector_version']}")

    db.execute("DROP TABLE IF EXISTS spike_chunks")
    db.execute(
        "CREATE TABLE spike_chunks (id int PRIMARY KEY, body text, meta jsonb, emb vector(1024))"
    )
    db.execute("CREATE INDEX ON spike_chunks USING hnsw (emb vector_cosine_ops)")

    rng = random.Random(42)
    rows = [[rng.uniform(-1, 1) for _ in range(1024)] for _ in range(50)]
    t0 = time.perf_counter()
    for i, v in enumerate(rows):
        db.execute(
            "INSERT INTO spike_chunks "
            "VALUES (:id, :body, CAST(:meta AS jsonb), CAST(:emb AS vector))",
            {"id": i, "body": f"特典航空券 chunk {i}", "meta": json.dumps({"i": i}), "emb": vec(v)},
        )
    report["insert_1024d_avg_ms"] = round((time.perf_counter() - t0) * 1000 / len(rows))
    print(f"[ok] inserted {len(rows)} x 1024-d vectors, avg {report['insert_1024d_avg_ms']} ms")

    r, ms = timed(
        lambda: db.execute(
            "SELECT id, body, meta->>'i', 1 - (emb <=> CAST(:q AS vector)) AS score "
            "FROM spike_chunks ORDER BY emb <=> CAST(:q AS vector) LIMIT 5",
            {"q": vec(rows[7])},
        )
    )
    top = [cell(f) for f in r["records"][0]]
    assert top[0] == 7, f"nearest neighbour should be row 7, got {top}"
    assert abs(float(top[3]) - 1.0) < 1e-4, f"self-similarity should be ~1, got {top[3]}"
    assert top[1] == "特典航空券 chunk 7", "non-ASCII text must round-trip"
    report["knn_query_ms"] = round(ms)
    print(f"[ok] kNN top-1 = row 7, score {float(top[3]):.4f}, {ms:.0f} ms, UTF-8 round-trip ok")

    tx = db.client.begin_transaction(**db.base)["transactionId"]
    db.execute("DELETE FROM spike_chunks", transaction_id=tx)
    db.client.rollback_transaction(
        resourceArn=db.base["resourceArn"], secretArn=db.base["secretArn"], transactionId=tx
    )
    n = cell(db.execute("SELECT count(*) FROM spike_chunks")["records"][0][0])
    assert n == len(rows), f"rollback failed, count={n}"
    print("[ok] transaction rollback")

    lat = []
    for _ in range(10):
        _, ms = timed(lambda: db.execute("SELECT 1"))
        lat.append(ms)
    report["select1_p50_ms"] = round(statistics.median(lat))
    print(f"[ok] SELECT 1 p50 {report['select1_p50_ms']} ms (Data API round-trip overhead)")

    db.execute("DROP TABLE spike_chunks")
    return report


def wait_for_pause(cluster_id: str, region: str, timeout_s: int = 1200) -> None:
    cw = boto3.client("cloudwatch", region_name=region)
    print(f"Waiting for {cluster_id} to reach 0 ACU (auto-pause after 300 s idle)...")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        now = datetime.now(UTC)
        points = cw.get_metric_statistics(
            Namespace="AWS/RDS",
            MetricName="ServerlessDatabaseCapacity",
            Dimensions=[{"Name": "DBClusterIdentifier", "Value": cluster_id}],
            StartTime=now - timedelta(minutes=5),
            EndTime=now,
            Period=60,
            Statistics=["Maximum"],
        )["Datapoints"]
        latest = max(points, key=lambda p: p["Timestamp"]) if points else None
        cap = latest["Maximum"] if latest else None
        print(f"  {now:%H:%M:%S} capacity={cap}")
        if cap == 0:
            return
        time.sleep(60)
    raise TimeoutError("cluster did not auto-pause; is min_capacity 0?")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cluster-arn", required=True)
    p.add_argument("--secret-arn", required=True)
    p.add_argument("--database", default="milewise")
    p.add_argument("--region", default="ap-northeast-1")
    p.add_argument("--measure-resume", action="store_true")
    a = p.parse_args()

    db = DataApi(a.cluster_arn, a.secret_arn, a.database, a.region)
    cluster_id = a.cluster_arn.rsplit(":", 1)[-1]

    rds = boto3.client("rds", region_name=a.region)
    scaling = rds.describe_db_clusters(DBClusterIdentifier=cluster_id)["DBClusters"][0].get(
        "ServerlessV2ScalingConfiguration", {}
    )
    print(f"Scaling config: {scaling}")

    if a.measure_resume:
        wait_for_pause(cluster_id, a.region)
        _, ms = timed(lambda: db.execute("SELECT 1", max_wait_s=300))
        print(f"[ok] first query after auto-pause took {ms / 1000:.1f} s (incl. retries)")
        print(json.dumps({"resume_first_query_s": round(ms / 1000, 1)}))
        return 0

    try:
        report = run_checks(db)
    except (AssertionError, ClientError) as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return 1
    print("\nSUMMARY " + json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
