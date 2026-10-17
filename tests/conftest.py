from __future__ import annotations

import pytest

from milewise.charts.store import FareStore


@pytest.fixture(scope="session")
def fares() -> FareStore:
    """The committed charts, parsed exactly as the Lambda does at init."""
    return FareStore.load()
