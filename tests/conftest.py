from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest

from milewise.charts.load import load_all
from milewise.db import Db, PsycopgDb, migrate

ADMIN_URL = os.environ.get(
    "TEST_DATABASE_ADMIN_URL", "postgresql://milewise:milewise@localhost:5432/milewise"
)
TEST_DB = "milewise_test"


@pytest.fixture(scope="session")
def db() -> Iterator[Db]:
    """Fresh database with all migrations and the committed charts loaded.

    Skips (rather than fails) when no local Postgres is running: start it with `make db-up`.
    """
    try:
        admin = psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable; run `make db-up`")
    admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    admin.execute(f"CREATE DATABASE {TEST_DB}")
    url = ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DB}"
    test_db = PsycopgDb(url)
    migrate(test_db)
    load_all(test_db)
    yield test_db
    test_db.conn.close()
    admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    admin.close()
