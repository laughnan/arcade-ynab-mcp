import pytest

from arcade_ynab.money import from_milliunits, to_milliunits


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (0, 0),
        (1, 1000),
        (-42.5, -42500),
        ("19.99", 19990),
        (0.1, 100),
        (1234.567, 1234567),
        (0.0005, 0),  # half-even rounding
        (0.0015, 2),
    ],
)
def test_to_milliunits(amount, expected):
    assert to_milliunits(amount) == expected


@pytest.mark.parametrize(
    ("milliunits", "expected"),
    [(0, 0.0), (1000, 1.0), (-42500, -42.5), (19990, 19.99), (1234567, 1234.567), (None, None)],
)
def test_from_milliunits(milliunits, expected):
    assert from_milliunits(milliunits) == expected


def test_round_trip():
    for amount in (-1500.25, 0.01, 99999.999):
        assert from_milliunits(to_milliunits(amount)) == amount
