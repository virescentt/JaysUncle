"""Domain normalizers (dates, amounts, identifiers). Each returns None when the value cannot be parsed.

TODO(normalize): implement the functions below — specification = docstrings + tests/test_normalizers.py.
"""

from __future__ import annotations

from odwolania2json.normalize import normalizer

POLISH_MONTHS = {
    # genitive forms used in dates ("3 lipca 2026") + nominative, both lower-case
    "stycznia": 1, "styczeń": 1, "lutego": 2, "luty": 2, "marca": 3, "marzec": 3,
    "kwietnia": 4, "kwiecień": 4, "maja": 5, "maj": 5, "czerwca": 6, "czerwiec": 6,
    "lipca": 7, "lipiec": 7, "sierpnia": 8, "sierpień": 8, "września": 9, "wrzesień": 9,
    "października": 10, "październik": 10, "listopada": 11, "listopad": 11, "grudnia": 12, "grudzień": 12,
}  # fmt: skip


@normalizer("date_pl")
def date_pl(value):
    import re
    from datetime import date

    if value is None:
        return None

    text = str(value).strip().lower()

    text = re.sub(r"\b(dnia)\b\s*", "", text)
    text = re.sub(r"\s+r\.$", "", text)
    text = re.sub(r"\s+roku$", "", text)
    text = text.strip()

    # ISO YYYY-MM-DD
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        try:
            return date(
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3))
            ).strftime("%Y-%m-%d")
        except ValueError:
            return None

    # DD.MM.YYYY lub DD-MM-YYYY
    m = re.fullmatch(r"(\d{1,2})[.-](\d{1,2})[.-](\d{4})", text)
    if m:
        try:
            return date(
                int(m.group(3)),
                int(m.group(2)),
                int(m.group(1))
            ).strftime("%Y-%m-%d")
        except ValueError:
            return None

    # 3 lipca 2026
    m = re.fullmatch(r"(\d{1,2})\s+([a-ząćęłńóśźż]+)\s+(\d{4})", text)
    if m:
        day = int(m.group(1))
        month = POLISH_MONTHS.get(m.group(2))
        year = int(m.group(3))

        if month is None:
            return None

        try:
            return date(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            return None

    return None

    """Any Polish date notation → ISO 'YYYY-MM-DD'.

    Examples:
        '2026-03-21'               → '2026-03-21'
        '02.07.2026'               → '2026-07-02'
        '2.7.2026 r.'              → '2026-07-02'
        '3 lipca 2026 roku'        → '2026-07-03'
        'dnia 16 czerwca 2025 r.'  → '2025-06-16'
        '4 marca 2026'             → '2026-03-04'
        'brak daty'                → None
        '31.02.2026'               → None   (invalid date)
    """
    # raise NotImplementedError


@normalizer("amount_pl")
def amount_pl(value):
    import re

    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()

    # usuń waluty
    text = re.sub(r"\b(zł|PLN)\b", "", text, flags=re.IGNORECASE)

    # zostaw tylko cyfry, przecinki, kropki i minus
    text = re.sub(r"[^\d,.-]", "", text)

    # usuń separatory tysięcy
    text = text.replace(".", "")

    # polski separator dziesiętny
    text = text.replace(",", ".")

    if not text:
        return None

    try:
        return float(text)
    except ValueError:
        return None
    """Polish money notation → float.

    Examples:
        '15 000,00 zł'   → 15000.0
        '200,00 PLN'     → 200.0
        '1.234,56'       → 1234.56
        '17,00'          → 17.0
        10000            → 10000.0
        'brak'           → None
    """
    #raise NotImplementedError


@normalizer("account_number")
def account_number(value):
    import re

    if value is None:
        return None

    digits = re.sub(r"\D", "", str(value))

    if len(digits) != 26:
        return None

    return digits
    """Polish bank account (NRB/IBAN) → 26 digits, without 'PL' and spaces.

    Examples:
        'PL55 9947 7458 7693 6814 0572 7867' → '55994774587693681405727867'
        'PL05470680287581539721455024'       → '05470680287581539721455024'
        '60 1010 1010 0081 3622 3100 0000'   → '60101010100081362231000000'
        '1234'                               → None   (not 26 digits)
    """
    #raise NotImplementedError


@normalizer("bzp")
def bzp(value):
    import re

    if value is None:
        return None

    text = str(value).strip()

    m = re.fullmatch(
        r"\s*(\d{4})\s*/\s*BZP\s*(\d{8})(/\d{2})?\s*",
        text,
        flags=re.IGNORECASE,
    )

    if not m:
        return None

    year = m.group(1)
    number = m.group(2)
    suffix = m.group(3) or ""

    return f"{year}/BZP{number}{suffix}"

    """BZP notice number → 'YYYY/BZP ########' without spaces (optional '/##' suffix for corrections).

    Examples:
        '2026/BZP 00999263'     → '2026/BZP00999263'
        '2025/BZP 00270698/01'  → '2025/BZP00270698/01'
        '2026 / BZP 00999778'   → '2026/BZP00999778'
        '641651-2025'           → None   (that is a TED number)
    """
    #raise NotImplementedError


@normalizer("ted")
def ted(value):
    import re

    if value is None:
        return None

    text = str(value).strip()

    # BZP to nie TED
    if "BZP" in text.upper():
        return None

    # Format: 641651-2025
    m = re.fullmatch(r"(\d{6})-(\d{4})", text)
    if m:
        return text

    # Format: 2026/S 241-999983
    m = re.fullmatch(
        r"(\d{4})\s*/\s*S\s*\d+\s*-\s*(\d{6})",
        text,
        flags=re.IGNORECASE,
    )

    if m:
        year = m.group(1)
        number = m.group(2)
        return f"{number}-{year}"

    return None
    """TED notice number → 'NNNNNN-YYYY'.

    Examples:
        '641651-2025'           → '641651-2025'
        '2026/S 241-999983'     → '999983-2026'
        '2025/S 123-363730'     → '363730-2025'
        '2026/BZP 00999263'     → None   (that is a BZP number)
    """
    #raise NotImplementedError
