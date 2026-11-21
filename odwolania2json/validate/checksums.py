"""Checksums of Polish identifiers. Input: string of digits (already normalized). Output: bool.

Pure functions: anything that is not a str of ASCII digits of the right length is invalid (False).
Merge may use them to repair OCR confusions in identifiers (O→0, l/I→1, S→5, B→8): keep the variant that passes.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from odwolania2json.validate import validator


def _digits(value: Any, *lengths: int) -> list[int] | None:
    """[d0, d1, …] if value is a str of ASCII digits with one of the given lengths, else None."""
    if not isinstance(value, str) or len(value) not in lengths or not (value.isascii() and value.isdigit()):
        return None
    return [int(ch) for ch in value]


def _weighted(digits: list[int], weights: tuple[int, ...]) -> int:
    return sum(d * w for d, w in zip(digits, weights, strict=False))


@validator("nip")
def nip(value: str) -> bool:
    """NIP: 10 digits; weights 6,5,7,2,3,4,5,6,7 for the first 9; (sum % 11) == digit 10 (sum%11 == 10 → invalid).
    Valid example: '5540240549'."""
    d = _digits(value, 10)
    if d is None:
        return False
    control = _weighted(d, (6, 5, 7, 2, 3, 4, 5, 6, 7)) % 11
    return control != 10 and control == d[9]


@validator("regon")
def regon(value: str) -> bool:
    """REGON: 9 digits (weights 8,9,2,3,4,5,6,7) or 14 digits (weights 2,4,8,5,0,9,7,3,6,1,2,4,8);
    control = (sum % 11) % 10 == last digit. Valid example: '000279077'."""
    d = _digits(value, 9, 14)
    if d is None:
        return False
    weights = (8, 9, 2, 3, 4, 5, 6, 7) if len(d) == 9 else (2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8)
    return _weighted(d, weights) % 11 % 10 == d[-1]


@validator("krs")
def krs(value: str) -> bool:
    """KRS: exactly 10 digits (no checksum). Example: '0000054588' → True, '12345' → False."""
    return _digits(value, 10) is not None


_PESEL_CENTURY = {0: 1900, 20: 2000, 40: 2100, 60: 2200, 80: 1800}  # month offset → century


@validator("pesel")
def pesel(value: str) -> bool:
    """PESEL: 11 digits; weights 1,3,7,9,1,3,7,9,1,3; control = (10 - sum % 10) % 10 == digit 11.
    Digits 1-6 must also encode a real birth date (YYMMDD, month + 20 for 2000-2099, + 80 for 1800-1899 …).
    Valid example: '85071204278'."""
    d = _digits(value, 11)
    if d is None:
        return False
    month = d[2] * 10 + d[3]
    offset = month - (month - 1) % 20 - 1  # 1-12 → 0, 21-32 → 20, …
    if offset not in _PESEL_CENTURY:
        return False
    try:
        date(_PESEL_CENTURY[offset] + d[0] * 10 + d[1], month - offset, d[4] * 10 + d[5])
    except ValueError:
        return False
    return (10 - _weighted(d, (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)) % 10) % 10 == d[10]


@validator("iban_pl")
def iban_pl(value: str) -> bool:
    """Polish account (26 digits, without 'PL'): move 'PL'+first two digits to the end, letters P=25 L=21,
    i.e. int(value[2:] + '2521' + value[:2]) % 97 == 1. Valid example: '61109010140000071219812874'."""
    if _digits(value, 26) is None:
        return False
    return int(value[2:] + "2521" + value[:2]) % 97 == 1
