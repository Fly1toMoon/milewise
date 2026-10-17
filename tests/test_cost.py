import pytest

from milewise.config import cost_usd


def test_luna_and_sol_are_priced_differently() -> None:
    # 1M uncached input + 1M output
    assert cost_usd("gpt-6-luna", 1_000_000, 0, 1_000_000) == pytest.approx(0.60)
    assert cost_usd("gpt-6.1-sol", 1_000_000, 0, 1_000_000) == pytest.approx(12.00)


def test_cached_input_is_cheaper() -> None:
    assert cost_usd("gpt-6-luna", 1_000_000, 1_000_000, 0) == pytest.approx(0.01)


def test_unknown_model_fails_loudly() -> None:
    with pytest.raises(KeyError):
        cost_usd("some-new-model", 1, 0, 1)
