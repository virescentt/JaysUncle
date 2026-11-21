"""Text normalizers + helpers for matching (used by classification and grounding)."""

from __future__ import annotations

import re
import unicodedata

from odwolania2json.normalize import normalizer

_LETTER_SPACED = re.compile(r"\b(?:[^\W\d_] ){3,}[^\W\d_]\b")
_PL_FOLD = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


def fold_diacritics(text: str) -> str:
    """'ODWOŁANIE Odwołujący' → 'ODWOLANIE Odwolujacy'. OCR often loses Polish diacritics, so classification
    folds BOTH the text and the regex patterns (Polish letters are not regex metacharacters)."""
    return text.translate(_PL_FOLD)


def collapse_letter_spacing(text: str) -> str:
    """'P E Ł N O M O C N I C T W O' → 'PEŁNOMOCNICTWO' (headings typeset with letter spacing)."""
    return _LETTER_SPACED.sub(lambda m: m.group(0).replace(" ", ""), text)


def normalize_for_matching(text: str) -> str:
    """Text prepared for regex matching: NFC, letter-spacing collapsed, spaces squeezed (newlines kept)."""
    text = unicodedata.normalize("NFC", text)
    text = collapse_letter_spacing(text)
    text = re.sub(r"[ \t ]+", " ", text)
    return text


@normalizer("strip")
def strip(value):
    s = str(value).strip()
    return s or None


@normalizer("collapse_whitespace")
def collapse_whitespace(value):
    """'Dostawa  sprzętu\\n komputerowego' → 'Dostawa sprzętu komputerowego'"""
    s = re.sub(r"\s+", " ", str(value)).strip()
    return s or None


@normalizer("digits_only")
def digits_only(value):
    """'554-024-05-49' → '5540240549';  'KRS 0000054588' → '0000054588'. No digits → None."""
    s = re.sub(r"\D", "", str(value))
    return s or None


@normalizer("strip_quotes")
def strip_quotes(value):
    """Remove surrounding quotes of any kind and trailing punctuation.

    Examples:
        '„Dostawa sprzętu komputerowego”'  → 'Dostawa sprzętu komputerowego'
        '"BIOARCUS" Sp. z o.o.'             → '"BIOARCUS" Sp. z o.o.'   (inner quotes stay!)
        '«Budowa drogi»,'                   → 'Budowa drogi'
    TODO(normalize): implement; see tests/test_normalizers.py
    """
    raise NotImplementedError


@normalizer("email")
def email(value):
    """Extract and lower-case an e-mail address.

    Examples:
        ' Biuro@Kancelaria.PL '            → 'biuro@kancelaria.pl'
        'e-mail: sekretariat@sb.example,'   → 'sekretariat@sb.example'
        'brak'                              → None
    TODO(normalize): implement; see tests/test_normalizers.py
    """
    raise NotImplementedError


@normalizer("person_name")
def person_name(value):
    """Person name without professional titles, single spaces, nominative case is NOT handled here
    (inflection is the LLM's job — this only cleans).

    Examples:
        'r. pr. Aleksandra Marciniak'   → 'Aleksandra Marciniak'
        'adw.  Michał Borkowski'        → 'Michał Borkowski'
        'mec. Jan Kowalski, radca prawny' → 'Jan Kowalski'
        'Prezes Zarządu'                → None   (not a name)
    TODO(normalize): implement; see tests/test_normalizers.py
    """
    raise NotImplementedError
