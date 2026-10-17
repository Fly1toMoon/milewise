import pytest

from milewise.locations import normalize, resolve_city


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("東京", "TYO"),
        ("东京", "TYO"),
        ("tokyo", "TYO"),
        ("ＨＮＤ", "TYO"),  # full-width input
        ("成田空港", "TYO"),
        ("倫敦", "LON"),
        ("伦敦", "LON"),
        ("LHR", "LON"),
        ("胡志明市", "SGN"),
        ("Washington DC", "WAS"),
    ],
)
def test_resolves(query: str, code: str) -> None:
    cities = resolve_city(query)
    assert [c.city_code for c in cities] == [code]


def test_unknown_is_empty_not_guessed() -> None:
    assert resolve_city("東京駅") == []
    assert resolve_city("ロンド") == []


def test_normalize_strips_airport_suffix() -> None:
    assert normalize("Narita Airport") == "narita"
