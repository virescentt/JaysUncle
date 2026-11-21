"""SPECIFICATION of checksum validators (odwolania2json/validate/checksums.py). Stubs are skipped."""

import pytest

from odwolania2json.validate import checksums as c
from odwolania2json.validate import run_validators
from tests.conftest import call_or_skip


@pytest.mark.parametrize(
    "fn, valid, invalid",
    [
        (c.nip, "5540240549", "5540240548"),
        (c.regon, "000279077", "000279078"),
        (c.pesel, "85071204278", "85071204279"),
        (c.iban_pl, "60101010100081362231000000", "61109010140000071219812875"),
        (c.krs, "0000054588", "12345"),
        # from the real scans (data/uzp/dane/skany): all their identifiers have valid checksums
        (c.nip, "9992208055", "9992208056"),
        (c.regon, "999913617", "999913618"),
        (c.pesel, "69102340858", "69102340857"),
        (c.iban_pl, "62217000089152501756617062", "62217000089152501756617063"),
        # more cases
        (c.regon, "00027907700018", "00027907700019"),  # 14-digit REGON
        (c.iban_pl, "61109010140000071219812874", "16109010140000071219812874"),  # docstring example; swapped digits
        (c.pesel, "02251501239", "85133201234"),  # born 2002 (month + 20); month 13 does not exist
    ],
)
def test_checksums(fn, valid, invalid):
    assert call_or_skip(fn, valid) is True
    assert call_or_skip(fn, invalid) is False


@pytest.mark.parametrize("fn", [c.nip, c.regon, c.krs, c.pesel, c.iban_pl])
@pytest.mark.parametrize(
    "value",
    [
        "",
        "abc",
        "554-024-05-49",
        " 5540240549",
        "554024054９",
        "PL61109010140000071219812874",
        "1" * 30,
        None,
        5540240549,
    ],
)
def test_wrong_length_or_not_digits_is_invalid(fn, value):
    """Validators take normalized digit strings: anything else is invalid, never an exception."""
    assert call_or_skip(fn, value) is False


def test_nip_with_sum_mod_11_equal_10_is_invalid():
    assert c.nip("1000000160") is False  # weighted sum % 11 == 10 — no NIP has this control value


def test_pesel_birth_date_must_exist():
    assert c.pesel("85023001236") is False  # checksum fine, but 30 February 1985
    assert c.pesel("04222901237") is True  # 29 February 2004 (leap year)


def test_run_validators_registry():
    assert run_validators(["nip", "regon"], "5540240549") == {"nip": True, "regon": False}
