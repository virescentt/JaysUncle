"""SPECIFICATION of checksum validators (odwolania2json/validate/checksums.py). Stubs are skipped."""

import pytest

from odwolania2json.validate import checksums as c
from tests.conftest import call_or_skip


@pytest.mark.parametrize(
    "fn, valid, invalid",
    [
        (c.nip, "5540240549", "5540240548"),
        (c.regon, "000279077", "000279078"),
        (c.pesel, "85071204278", "85071204279"),
        (c.iban_pl, "60101010100081362231000000", "61109010140000071219812875"),
        (c.krs, "0000054588", "12345"),
    ],
)
def test_checksums(fn, valid, invalid):
    assert call_or_skip(fn, valid) is True
    assert call_or_skip(fn, invalid) is False
