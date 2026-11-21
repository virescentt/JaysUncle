"""Checksums of Polish identifiers. Input: string of digits (already normalized). Output: bool.

TODO(validate): implement — specification = docstrings + tests/test_validators.py.
Bonus idea: for OCR-ed values that fail the checksum, try common OCR confusions (O→0, l/I→1, S→5, B→8)
and accept the variant that passes (do this in merge, using these functions).
"""

from __future__ import annotations

from odwolania2json.validate import validator


@validator("nip")
def nip(value: str) -> bool:
    """NIP: 10 digits; weights 6,5,7,2,3,4,5,6,7 for the first 9; (sum % 11) == digit 10 (sum%11 == 10 → invalid).
    Valid example: '5540240549'."""
    raise NotImplementedError


@validator("regon")
def regon(value: str) -> bool:
    """REGON: 9 digits (weights 8,9,2,3,4,5,6,7) or 14 digits (weights 2,4,8,5,0,9,7,3,6,1,2,4,8);
    control = (sum % 11) % 10 == last digit. Valid example: '000279077'."""
    raise NotImplementedError


@validator("krs")
def krs(value: str) -> bool:
    """KRS: exactly 10 digits (no checksum). Example: '0000054588' → True, '12345' → False."""
    raise NotImplementedError


@validator("pesel")
def pesel(value: str) -> bool:
    """PESEL: 11 digits; weights 1,3,7,9,1,3,7,9,1,3; control = (10 - sum % 10) % 10 == digit 11.
    Valid example: '85071204278'."""
    raise NotImplementedError


@validator("iban_pl")
def iban_pl(value: str) -> bool:
    """Polish account (26 digits, without 'PL'): move 'PL'+first two digits to the end, letters P=25 L=21,
    i.e. int(value[2:] + '2521' + value[:2]) % 97 == 1. Valid example: '61109010140000071219812874'."""
    raise NotImplementedError
