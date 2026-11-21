"""SPECIFICATION of normalizers (odwolania2json/normalize/). Stubs are skipped; implemented → must pass.
Examples come from the training data (data/uzp/dane) and the organizers' field descriptions."""

import pytest

from odwolania2json.normalize import text as t
from odwolania2json.normalize import values as v
from tests.conftest import call_or_skip


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-03-21", "2026-03-21"),
        ("02.07.2026", "2026-07-02"),
        ("2.7.2026 r.", "2026-07-02"),
        ("3 lipca 2026 roku", "2026-07-03"),
        ("dnia 16 czerwca 2025 r.", "2025-06-16"),
        ("4 marca 2026", "2026-03-04"),
        ("12-01-2026", "2026-01-12"),
        ("brak daty", None),
        ("31.02.2026", None),
    ],
)
def test_date_pl(raw, expected):
    assert call_or_skip(v.date_pl, raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [("15 000,00 zł", 15000.0), ("200,00 PLN", 200.0), ("1.234,56", 1234.56), ("17,00", 17.0), (10000, 10000.0),
     ("brak", None)],
)  # fmt: skip
def test_amount_pl(raw, expected):
    assert call_or_skip(v.amount_pl, raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("PL55 9947 7458 7693 6814 0572 7867", "55994774587693681405727867"),
        ("PL05470680287581539721455024", "05470680287581539721455024"),
        ("60 1010 1010 0081 3622 3100 0000", "60101010100081362231000000"),
        ("1234", None),
    ],
)
def test_account_number(raw, expected):
    assert call_or_skip(v.account_number, raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [("2026/BZP 00999263", "2026/BZP00999263"), ("2025/BZP 00270698/01", "2025/BZP00270698/01"),
     ("2026 / BZP 00999778", "2026/BZP00999778"), ("641651-2025", None)],
)  # fmt: skip
def test_bzp(raw, expected):
    assert call_or_skip(v.bzp, raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [("641651-2025", "641651-2025"), ("2026/S 241-999983", "999983-2026"), ("2025/S 123-363730", "363730-2025"),
     ("2026/S086-999073", "999073-2026"), ("2026/BZP 00999263", None)],
)  # fmt: skip
def test_ted(raw, expected):
    assert call_or_skip(v.ted, raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("„Dostawa sprzętu komputerowego”", "Dostawa sprzętu komputerowego"),
        ('"BIOARCUS" Sp. z o.o.', '"BIOARCUS" Sp. z o.o.'),
        ("«Budowa drogi»,", "Budowa drogi"),
    ],
)
def test_strip_quotes(raw, expected):
    assert call_or_skip(t.strip_quotes, raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [(" Biuro@Kancelaria.PL ", "biuro@kancelaria.pl"), ("e-mail: sekretariat@sb.example,", "sekretariat@sb.example"),
     ("brak", None)],
)  # fmt: skip
def test_email(raw, expected):
    assert call_or_skip(t.email, raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("r. pr. Aleksandra Marciniak", "Aleksandra Marciniak"),
        ("adw.  Michał Borkowski", "Michał Borkowski"),
        ("mec. Jan Kowalski, radca prawny", "Jan Kowalski"),
        ("Prezes Zarządu", None),
    ],
)
def test_person_name(raw, expected):
    assert call_or_skip(t.person_name, raw) == expected


# --- already implemented helpers -------------------------------------------------------------------
def test_digits_only():
    assert t.digits_only("554-024-05-49") == "5540240549"
    assert t.digits_only("brak") is None


def test_collapse_letter_spacing():
    assert t.collapse_letter_spacing("P E Ł N O M O C N I C T W O") == "PEŁNOMOCNICTWO"
    assert t.collapse_letter_spacing("w dniu 3 lipca") == "w dniu 3 lipca"


def test_fold_diacritics():
    assert t.fold_diacritics("ODWOŁANIE Odwołujący") == "ODWOLANIE Odwolujacy"
