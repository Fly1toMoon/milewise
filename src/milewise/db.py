"""Database access with plain SQL.

SQL uses `:name` parameters everywhere so the same statements run on local
Postgres (psycopg) and, from M4, on Aurora through the RDS Data API.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row

Params = Mapping[str, Any]
_NAMED = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")


class Db(Protocol):
    def query(self, sql: str, params: Params | None = None) -> list[dict[str, Any]]: ...

    def execute(self, sql: str, params: Params | None = None) -> None: ...

    def execute_many(self, sql: str, rows: Sequence[Params]) -> None: ...

    def transaction(self) -> Any: ...


def to_psycopg(sql: str) -> str:
    """Translate `:name` to `%(name)s`, leaving `::casts` and literal `%` intact."""
    return _NAMED.sub(r"%(\1)s", sql.replace("%", "%%"))


class PsycopgDb:
    def __init__(self, url: str) -> None:
        self.conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)

    def _run(self, cur: psycopg.Cursor[dict[str, Any]], sql: str, params: Params | None) -> None:
        # Without parameters, send the text as-is (simple protocol): this allows
        # multi-statement migration files and needs no placeholder translation.
        if params:
            cur.execute(to_psycopg(sql), params)
        else:
            cur.execute(sql)

    def query(self, sql: str, params: Params | None = None) -> list[dict[str, Any]]:
        with self.conn.cursor() as cur:
            self._run(cur, sql, params)
            return list(cur.fetchall())

    def execute(self, sql: str, params: Params | None = None) -> None:
        with self.conn.cursor() as cur:
            self._run(cur, sql, params)

    def execute_many(self, sql: str, rows: Sequence[Params]) -> None:
        with self.conn.cursor() as cur:
            cur.executemany(to_psycopg(sql), rows)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.conn.transaction():
            yield


def split_statements(sql: str) -> list[str]:
    """Split a migration file on top-level semicolons (Data API runs one statement per call).

    Skips semicolons inside quotes and `--` comments; our migrations use no dollar-quoting.
    """
    out: list[str] = []
    buf: list[str] = []
    quote, i = "", 0
    while i < len(sql):
        ch = sql[i]
        if not quote and sql.startswith("--", i):
            end = sql.find("\n", i)
            i = len(sql) if end < 0 else end
            continue
        if ch in ("'", '"'):
            quote = "" if quote == ch else (quote or ch)
        if ch == ";" and not quote:
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def _to_field(value: Any) -> dict[str, Any]:
    if value is None:
        return {"isNull": True}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"longValue": value}
    if isinstance(value, float):
        return {"doubleValue": value}
    if hasattr(value, "isoformat"):  # date / datetime: SQL always CASTs these from text
        return {"stringValue": value.isoformat()}
    return {"stringValue": str(value)}


def _from_field(field: dict[str, Any], type_name: str) -> Any:
    if field.get("isNull"):
        return None
    if "arrayValue" in field:
        arr = field["arrayValue"]
        return next(iter(arr.values())) if arr else []
    value = next(iter(field.values()))
    if type_name == "date":
        return date.fromisoformat(value)
    if type_name in ("timestamptz", "timestamp"):
        dt = datetime.fromisoformat(value)
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
    if type_name == "numeric":
        return Decimal(value)
    return value


class DataApiDb:
    """Aurora through the RDS Data API: HTTPS + IAM, so callers need no VPC access (design.md §4.3)."""

    BATCH = 25  # rows per BatchExecuteStatement; 1024-d vectors are ~11 KB each

    def __init__(
        self, cluster_arn: str, secret_arn: str, database: str, client: Any = None
    ) -> None:
        import boto3

        self.client = client or boto3.client("rds-data")
        self.base = {"resourceArn": cluster_arn, "secretArn": secret_arn, "database": database}
        self._tx = threading.local()

    def _call(self, fn: Any, **kwargs: Any) -> Any:
        from botocore.exceptions import ClientError

        tx = getattr(self._tx, "id", None)
        if tx:
            kwargs["transactionId"] = tx
        delay = 1.0
        for _ in range(8):  # ~1 min: covers resume from 0 ACU (~18 s measured, design.md §9.1)
            try:
                return fn(**self.base, **kwargs)
            except ClientError as e:
                if e.response["Error"]["Code"] != "DatabaseResumingException":
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 10)
        return fn(**self.base, **kwargs)

    @staticmethod
    def _params(params: Params | None) -> list[dict[str, Any]]:
        return [{"name": k, "value": _to_field(v)} for k, v in (params or {}).items()]

    def query(self, sql: str, params: Params | None = None) -> list[dict[str, Any]]:
        r = self._call(
            self.client.execute_statement,
            sql=sql,
            parameters=self._params(params),
            includeResultMetadata=True,
        )
        cols = [(c["label"] or c["name"], c["typeName"]) for c in r.get("columnMetadata", [])]
        return [
            {name: _from_field(f, t) for (name, t), f in zip(cols, rec, strict=True)}
            for rec in r.get("records", [])
        ]

    def execute(self, sql: str, params: Params | None = None) -> None:
        statements = split_statements(sql) if not params else [sql]
        for stmt in statements:
            self._call(self.client.execute_statement, sql=stmt, parameters=self._params(params))

    def execute_many(self, sql: str, rows: Sequence[Params]) -> None:
        for i in range(0, len(rows), self.BATCH):
            self._call(
                self.client.batch_execute_statement,
                sql=sql,
                parameterSets=[self._params(r) for r in rows[i : i + self.BATCH]],
            )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if getattr(self._tx, "id", None):  # nested: join the outer transaction
            yield
            return
        self._tx.id = self.client.begin_transaction(**self.base)["transactionId"]
        try:
            yield
        except BaseException:
            tx, self._tx.id = self._tx.id, None
            self.client.rollback_transaction(
                resourceArn=self.base["resourceArn"],
                secretArn=self.base["secretArn"],
                transactionId=tx,
            )
            raise
        tx, self._tx.id = self._tx.id, None
        self.client.commit_transaction(
            resourceArn=self.base["resourceArn"], secretArn=self.base["secretArn"], transactionId=tx
        )


def connect(url: str | None = None) -> Db:
    """Data API when DB_CLUSTER_ARN / DB_SECRET_ARN are set (Lambda), else local Postgres."""
    from milewise.config import get_settings

    s = get_settings()
    if not url and s.db_cluster_arn and s.db_secret_arn:
        return DataApiDb(s.db_cluster_arn, s.db_secret_arn, s.db_name)
    return PsycopgDb(url or s.database_url)


MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"


def migrate(db: Db, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply db/migrations/*.sql in order, once each. Returns the newly applied names."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS public.schema_migrations ("
        "name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    done = {r["name"] for r in db.query("SELECT name FROM public.schema_migrations")}
    applied = []
    for path in sorted(migrations_dir.glob("*.sql")):
        if path.name in done:
            continue
        with db.transaction():
            db.execute(path.read_text(encoding="utf-8"))
            db.execute(
                "INSERT INTO public.schema_migrations (name) VALUES (:name)", {"name": path.name}
            )
        applied.append(path.name)
    return applied
