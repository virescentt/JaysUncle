"""Domain normalizers (dates, amounts, identifiers). Each returns None when the value cannot be parsed.

Inputs are strings captured by the rules extractor or returned by the LLM (sometimes numbers), often from OCR
of scans: the parsers tolerate missing or OCR-mangled Polish diacritics ('wrzesnia', 'wrzešnia'), unusual
separators ('20 04 2026', '2026.04.20'), stray punctuation and the letter 'o' read instead of the digit '0'.
Anything invalid or ambiguous gives None — a null costs only a false negative, a wrong value costs FP + FN.
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from odwolania2json.normalize import normalizer

POLISH_MONTHS = {
    # genitive forms used in dates ("3 lipca 2026") + nominative, both lower-case
    "stycznia": 1, "styczeń": 1, "lutego": 2, "luty": 2, "marca": 3, "marzec": 3,
    "kwietnia": 4, "kwiecień": 4, "maja": 5, "maj": 5, "czerwca": 6, "czerwiec": 6,
    "lipca": 7, "lipiec": 7, "sierpnia": 8, "sierpień": 8, "września": 9, "wrzesień": 9,
    "października": 10, "październik": 10, "listopada": 11, "listopad": 11, "grudnia": 12, "grudzień": 12,
}  # fmt: skip
_MONTH_ABBREVIATIONS = {"sty": 1, "lut": 2, "mar": 3, "kwi": 4, "cze": 6, "lip": 7, "sie": 8, "wrz": 9, "paz": 10,
                        "lis": 11, "gru": 12}  # fmt: skip
_ROMAN_MONTHS = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10, "XI": 11,
                 "XII": 12}  # fmt: skip


def _fold(text: str) -> str:
    """Lower-case without diacritics: 'Września' → 'wrzesnia'; also OCR variants such as 'wrzešnia'."""
    text = unicodedata.normalize("NFKD", text.lower().replace("ł", "l"))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


_MONTHS = {_fold(name): month for name, month in POLISH_MONTHS.items()} | _MONTH_ABBREVIATIONS

# --- date_pl -------------------------------------------------------------------------------------------------
_DATE_PREFIX = re.compile(r"^(?:(?:z\s+dnia|w\s+dniu|dnia|data)\b|dn\.)[\s,.:]*")
_ISO_DATETIME = re.compile(r"(\d{4}-\d{1,2}-\d{1,2})t\d{1,2}:\d{2}\S*")  # '2026-03-21T10:00:00Z' (folded)
_DATE_TAIL = re.compile(
    r"(?:(?<=\d)|\s)(?:r|roku)$"  # '2.7.2026 r.', '05.06.2026r.', '3 lipca 2026 roku' (trailing '.' already gone)
    r"|,?\s*(?:godz\.?|g\.)?\s*\d{1,2}:\d{2}(?::\d{2})?(?:\s*[a-z]{3,4})?$"  # time: '17:58:34 cest', 'godz. 17:24'
)
_DATE_DMY = re.compile(r"(\d{1,2})\s*[./,:-]\s*(\d{1,2})\s*[./,:-]\s*(\d{4})|(\d{1,2})\s+(\d{1,2})\s+(\d{4})")
_DATE_YMD = re.compile(r"(\d{4})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})|(\d{4})\s+(\d{1,2})\s+(\d{1,2})")
_DATE_WORD = re.compile(r"(\d{1,2})\s*([a-z]+)\.?\s*(\d{4})")
_DATE_ROMAN = re.compile(r"(\d{1,2})\s*[./-]?\s*(xii|xi|ix|x|viii|vii|vi|iv|v|iii|ii|i)\s*[./-]?\s*(\d{4})")
# the longest accepted form is ~55 chars ('z dnia 21 października 2026 roku, godz. 12:00:00 CEST'); a longer value is
# never a single date, and the tail-stripping loop below is quadratic in the length (a degenerate LLM answer)
_MAX_DATE_CHARS = 80


def _iso(year: int, month: int, day: int) -> str | None:
    if not 1900 <= year <= 2100:
        return None
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


@normalizer("date_pl")
def date_pl(value: Any) -> str | None:
    """Any Polish date notation → ISO 'YYYY-MM-DD'.

    Examples:
        '2026-03-21'               → '2026-03-21'
        '02.07.2026'               → '2026-07-02'
        '2.7.2026 r.'              → '2026-07-02'
        '3 lipca 2026 roku'        → '2026-07-03'
        'dnia 16 czerwca 2025 r.'  → '2025-06-16'
        '4 marca 2026'             → '2026-03-04'
        '9 wrzesnia 2026'          → '2026-09-09'   (OCR: diacritics lost)
        '20 04 2026'               → '2026-04-20'   (OCR: separators lost)
        '2026.04.20 17:58:34 CEST' → '2026-04-20'
        '5.XI.2025 r.'             → '2025-11-05'
        'brak daty'                → None
        '31.02.2026'               → None   (invalid date)
        '21.03.26'                 → None   (two-digit year: ambiguous)
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    raw = re.sub(r"\s+", " ", str(value)).strip()
    if len(raw) > _MAX_DATE_CHARS:
        return None
    text = _ISO_DATETIME.sub(r"\1", _DATE_PREFIX.sub("", _fold(raw))).rstrip(" .,;:*)")
    while (shorter := _DATE_TAIL.sub("", text).rstrip(" .,;:*)")) != text:
        text = shorter

    # ISO / year first: 2026-03-21, 2026.04.20, 2026/4/2, 2026 04 20
    if m := _DATE_YMD.fullmatch(text):
        y, mo, d = (g for g in m.groups() if g is not None)
        return _iso(int(y), int(mo), int(d))
    # day first: 02.07.2026, 12-01-2026, 2/7/2026, 20 04 2026
    if m := _DATE_DMY.fullmatch(text):
        d, mo, y = (g for g in m.groups() if g is not None)
        return _iso(int(y), int(mo), int(d))
    # month as a word: 3 lipca 2026, 9 wrzesnia 2026, 21 mar. 2026
    if (m := _DATE_WORD.fullmatch(text)) and (month := _MONTHS.get(m.group(2))):
        return _iso(int(m.group(3)), month, int(m.group(1)))
    # month as a Roman numeral, written in upper case in the source: 5.XI.2025, 5 XI 2025
    if (m := _DATE_ROMAN.fullmatch(text)) and re.search(rf"(?<![A-Za-z]){m.group(2).upper()}(?![A-Za-z])", raw):
        return _iso(int(m.group(3)), _ROMAN_MONTHS[m.group(2).upper()], int(m.group(1)))
    return None


# --- amount_pl -----------------------------------------------------------------------------------------------
_SPACES = re.compile(r"[\u00a0\u2007\u202f\u2009]")  # no-break / figure / narrow / thin spaces
_OCR_NUMBER_TOKEN = re.compile(r"(?<!\S)(?=[\d.,]*[oO])(?=[oO.,]*\d)[\doO.,]+(?!\S)")
_CURRENCY = r"(?:pln|zł|zl|złotych|zlotych|złote|zlote|złoty|zloty)(?![^\W\d_])"
_AMOUNT = re.compile(
    r"(?<!\d)(?<!\d[.,])"  # do not start inside another number
    r"(\d{1,3}(?:[ '’.,]\d{3})+|\d+)"  # integer part, optionally with thousands separators
    r"(?:[.,](\d{1,2}))?"  # decimal part: 1-2 digits after the last '.' or ','
    r"(?!\d|[.,]\d)"  # do not stop inside another number
)
_CURRENCY_AFTER = re.compile(r"[\s,.\-–]*" + _CURRENCY, re.IGNORECASE)
_CURRENCY_BEFORE = re.compile(r"(?<![^\W\d_])" + _CURRENCY + r"\.?\s*[-−–]?\s*$", re.IGNORECASE)
_CURRENCY_BEFORE_WINDOW = 16  # chars before a number searched for the currency ('PLN -', 'złotych ')
_GROSZE_AFTER = re.compile(r"\s*gr(?:oszy|osze|osz)?(?![^\W\d_])", re.IGNORECASE)  # '50 gr' = 0.50 zł
_MAGNITUDE_AFTER = re.compile(r"\s*(tys|mln|mld|tysi[aąęe]c\w*|milion\w*|miliard\w*)(?![^\W\d_])", re.IGNORECASE)
_MAGNITUDE_HEADER = re.compile(r"(?<![^\W\d_])w\s+(?:tys|mln|mld|tysi\w*|milion\w*|miliard\w*)", re.IGNORECASE)
# letters that may touch an amount: currency (OCR 'zt' for 'zł'), magnitude, grosze — '200zł', 'PLN200', '15tys'
_GLUED_UNIT = re.compile(r"pln|zł|zl|zt|złotych|zlotych|złote|zlote|złoty|zloty|tys|mln|mld|gr|groszy|grosze", re.I)
_LETTERS_BEFORE = re.compile(r"[^\W\d_]+$")
_LETTERS_AFTER = re.compile(r"[^\W\d_]+")


def _ocr_digits(text: str) -> str:
    """'10 o00' → '10 000': inside a token made only of digits, separators and the letter o/O, o/O is a 0."""
    return _OCR_NUMBER_TOKEN.sub(lambda m: m.group(0).replace("o", "0").replace("O", "0"), text)


def _lone_fraction(text: str, start: int) -> bool:
    """',50' / '.50': the fractional part of a number whose integer part was lost (a '......200,00' leader is fine)."""
    return start >= 1 and text[start - 1] in ".," and (start < 2 or text[start - 2] not in ".,")


def _inside_word(text: str, m: re.Match) -> bool:
    """'CEN9057564440515', 'ZP2026', '17A': digits glued to letters are an identifier or an address, not an amount
    (a bank table read column by column put a transaction id under 'Kwota'); a glued unit ('200zł') is fine."""
    a, b = m.start(), m.end()
    if a and text[a - 1].isalpha():
        if not _GLUED_UNIT.fullmatch(_LETTERS_BEFORE.search(text, max(0, a - 12), a).group(0)):
            return True
    if b < len(text) and text[b].isalpha():
        if not _GLUED_UNIT.fullmatch(_LETTERS_AFTER.match(text, b).group(0)):
            return True
    return False


def _magnitude_exponent(word: str) -> int:
    """'tys.' → 3, 'mln' / 'miliony' → 6, 'mld' / 'miliardów' → 9."""
    word = word.lower()
    return 3 if word.startswith("t") else 9 if word.startswith(("mld", "miliard")) else 6


@normalizer("amount_pl")
def amount_pl(value: Any) -> float | None:
    """Polish money notation → float (a magnitude: banks print debits as '-17,00 PLN', the sign is dropped).

    The last '.' or ',' followed by exactly one or two digits is the decimal separator; other dots, commas,
    spaces and apostrophes are thousands separators (groups of 3 digits). When the text holds several numbers,
    the one written next to the currency is taken; still ambiguous → None. 'tys.' / 'mln' / 'mld' right after
    the number multiply it; grosze written apart ('200 zł 50 gr') or a unit given elsewhere ('w tys. zł') → None.

    Examples:
        '15 000,00 zł'            → 15000.0
        '200,00 PLN'              → 200.0
        '1.234,56'                → 1234.56
        '17,00'                   → 17.0
        '17.00 PLN'               → 17.0
        '-10 000,00 PLN'          → 10000.0
        '200,00 PLN 2026-03-21'   → 200.0
        '1,5 mln zł'              → 1500000.0
        10000                     → 10000.0
        'brak'                    → None
        '12.34.56'                → None
        ',50'                     → None   (the integer part is missing)
        'CEN9057564440515'        → None   (digits of an identifier, not an amount)
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        try:
            number = abs(float(value))
        except OverflowError:
            return None
        return number if math.isfinite(number) else None  # NaN / inf would make the output invalid JSON

    text = _ocr_digits(_SPACES.sub(" ", str(value)))
    if _MAGNITUDE_HEADER.search(text):
        return None  # 'Kwota (w tys. zł): 1 500' — the unit is stated away from the number
    tokens, grosze = [], False
    for m in _AMOUNT.finditer(text):
        if _GROSZE_AFTER.match(text, m.end()):
            grosze = True
        elif not _lone_fraction(text, m.start()) and not _inside_word(text, m):
            tokens.append(m)
    if len(tokens) > 1:  # several numbers: keep the one followed (or else preceded) by the currency
        tokens = [m for m in tokens if _CURRENCY_AFTER.match(text, m.end())] or [
            m for m in tokens if _CURRENCY_BEFORE.search(text, max(0, m.start() - _CURRENCY_BEFORE_WINDOW), m.start())
        ]
    if len(tokens) != 1:
        return None
    integer, decimals = re.sub(r"\D", "", tokens[0].group(1)), tokens[0].group(2)
    if len(integer) > 1 and integer.startswith("0"):
        return None  # '000,00' / '05' — an OCR fragment, not an amount
    if len(integer) > 15:
        return None  # an identifier, not an amount; a float cannot hold it exactly (400 digits gave inf → bad JSON)
    if grosze and decimals is None:
        return None  # '200 zł 50 gr' is 200.50, a lone '50 gr' is 0.50 — not worth guessing
    amount = Decimal(f"{integer}.{decimals or '0'}")
    if magnitude := _MAGNITUDE_AFTER.match(text, tokens[0].end()):
        amount *= 10 ** _magnitude_exponent(magnitude.group(1))
    return float(amount)


# --- account_number ------------------------------------------------------------------------------------------
_ACCOUNT = re.compile(r"(?<![\w])(PL)?\s?(\d{2}(?:[\s-]?\d{4}){6})(?!\d)", re.IGNORECASE)  # groups may wrap lines
_ACCOUNT_ONLY = re.compile(r"\s*(PL)?[\d\s.-]+", re.IGNORECASE)


def _find_account(value: Any) -> tuple[str, bool] | None:
    """(26 digits, printed with the 'PL' country code?) of the single account in the text; None if none / several."""
    if value is None:
        return None
    text = _ocr_digits(str(value))
    found: dict[str, bool] = {}
    for m in _ACCOUNT.finditer(text):
        digits = re.sub(r"\D", "", m.group(2))
        found[digits] = found.get(digits, False) or m.group(1) is not None
    if len(found) == 1:
        return next(iter(found.items()))
    if found:
        return None  # several different accounts — ambiguous

    digits = re.sub(r"\D", "", text)
    if len(digits) != 26 or not (m := _ACCOUNT_ONLY.fullmatch(text)):
        return None
    return digits, m.group(1) is not None


@normalizer("account_number")
def account_number(value: Any) -> str | None:
    """Polish bank account (NRB/IBAN) → 26 digits, without 'PL' and spaces.

    Examples:
        'PL55 9947 7458 7693 6814 0572 7867'                  → '55994774587693681405727867'
        'PL05470680287581539721455024'                        → '05470680287581539721455024'
        '60 1010 1010 0081 3622 3100 0000'                    → '60101010100081362231000000'
        'ul. Postępu 17A, 60 1010 1010 0081 3622 3100 0000'   → '60101010100081362231000000'
        '1234'                                                → None   (not 26 digits)
    """
    found = _find_account(value)
    return found[0] if found else None


@normalizer("account_number_as_printed")
def account_number_as_printed(value: Any) -> str | None:
    """Like account_number, but keeps the 'PL' country code when the text prints it.

    The gold of the real scans copies the number as printed (skany/potwierdzenia: the 2 accounts printed with
    'PL' keep it, the 18 printed without it have none), the synthetic gold always drops it. Switch a field to
    this normalizer in YAML (and say "z PL, jeśli jest w dokumencie" in the LLM instructions) if UZP confirms.

    Examples:
        'PL62 2170 0008 9152 5017 5661 7062'   → 'PL62217000089152501756617062'
        '60 1010 1010 0081 3622 3100 0000'     → '60101010100081362231000000'
    """
    found = _find_account(value)
    if not found:
        return None
    digits, with_pl = found
    return f"PL{digits}" if with_pl else digits


# --- bzp -----------------------------------------------------------------------------------------------------
# the 'YYYY/BZP' numbering exists since 2021; '00999263-01' is not a known suffix form → no match, not a guess
_BZP = re.compile(r"(?<!\d)(20\d{2})\s*/\s*B\s?Z\s?P\s*([\doO]{6,10})(?:\s*/\s*(\d{1,3}))?(?!\d|[-–]\d)", re.IGNORECASE)


@normalizer("bzp")
def bzp(value: Any) -> str | None:
    """BZP notice number → 'YYYY/BZP########' without spaces (optional '/##' suffix for corrections).

    Examples:
        '2026/BZP 00999263'          → '2026/BZP00999263'
        '2025/BZP 00270698/01'       → '2025/BZP00270698/01'
        '2026 / BZP 00999778'        → '2026/BZP00999778'
        'nr 2026/BZP 00767819 / 01,' → '2026/BZP00767819/01'
        '2026/BZP o0692455/03'       → '2026/BZP00692455/03'   (OCR: letter o instead of 0)
        '641651-2025'                → None   (that is a TED number)
    """
    if value is None:
        return None

    found = set()
    for m in _BZP.finditer(str(value)):
        year, number, suffix = m.group(1), m.group(2).replace("o", "0").replace("O", "0"), m.group(3)
        if len(number) != 8 or (suffix is not None and len(suffix) != 2):
            return None  # malformed number — do not guess
        found.add(f"{year}/BZP{number}" + (f"/{suffix}" if suffix else ""))
    return found.pop() if len(found) == 1 else None


# --- ted -----------------------------------------------------------------------------------------------------
_YEAR = r"((?:19|20)\d{2})"
_TED_OJS = re.compile(r"(?<!\d)" + _YEAR + r"\s*/\s*S\s*\d{1,3}\s*-\s*(\d{1,8})(?!\d)", re.I)  # 2026/S 241-999983
# 2025-OJS107-00364525
_TED_OJS_ID = re.compile(r"(?<!\d)" + _YEAR + r"\s*-\s*OJS\s*\d{1,3}\s*-\s*(\d{1,8})(?!\d)", re.I)
_TED_NUMBER = re.compile(r"(?<![\d/.-])(\d{1,8})\s*-\s*" + _YEAR + r"(?![\d-])")  # 641651-2025


@normalizer("ted")
def ted(value: Any) -> str | None:
    """TED notice number → 'NNNNNN-YYYY' (1 to 8 digits, leading zeros dropped).

    Examples:
        '641651-2025'            → '641651-2025'
        '2026/S 241-999983'      → '999983-2026'
        '2025/S 123-363730'      → '363730-2025'
        '2026/S 086-12345'       → '12345-2026'
        '2025-OJS107-00364525'   → '364525-2025'
        '2026/BZP 00999263'      → None   (that is a BZP number)
    """
    if value is None:
        return None

    text = re.sub(r"\s+", " ", str(value)).strip()
    if "BZP" in text.upper():  # BZP to nie TED
        return None

    found = set()
    for pattern, number, year in ((_TED_OJS, 2, 1), (_TED_OJS_ID, 2, 1), (_TED_NUMBER, 1, 2)):
        for m in pattern.finditer(text):
            if int(m.group(number)) == 0:
                return None  # notice numbers start at 1 — '000000' is an OCR / anonymisation artefact
            found.add(f"{int(m.group(number))}-{m.group(year)}")
    return found.pop() if len(found) == 1 else None  # two different numbers (in any notation) — ambiguous
