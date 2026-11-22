"""Text normalizers + helpers for matching (used by classification and grounding)."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

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
    text = re.sub(r"[ \t\u00a0]+", " ", text)  # spaces, tabs and no-break spaces
    return text


# fillers written instead of a missing value (bank forms: 'Numer referencyjny: brak'; LLM answers: '—', 'N/D')
_PLACEHOLDERS = {"brak", "brak danych", "nie dotyczy", "nie podano", "nie wskazano", "n/d", "nd", "b/d", "bd", "n/a",
                 "null", "none"}  # fmt: skip


def _is_placeholder(s: str) -> bool:
    """'', '—', '-', '…', 'brak', 'Nie dotyczy', 'N/D' → True: a filler, not a value."""
    return not any(ch.isalnum() for ch in s) or s.strip(" .,;:").casefold() in _PLACEHOLDERS


@normalizer("strip")
def strip(value):
    """'  Warszawa ' → 'Warszawa'; empty, punctuation only or a filler ('brak', '—') → None."""
    if value is None:
        return None
    s = str(value).strip()
    return None if _is_placeholder(s) else s


@normalizer("collapse_whitespace")
def collapse_whitespace(value):
    """'Dostawa  sprzętu\\n komputerowego' → 'Dostawa sprzętu komputerowego'; a filler ('brak', '—') → None."""
    if value is None:
        return None
    s = re.sub(r"\s+", " ", str(value)).strip()
    return None if _is_placeholder(s) else s


@normalizer("code")
def code(value):
    """Identifier / reference number (numer transakcji, numer postępowania, znak sprawy): trimmed, sentence
    punctuation and quotes at the ends removed. Every space-separated part must contain a digit ('1234 5678' is
    fine): a word ('transakcji') or a phrase is text caught by mistake → None. Letter case is kept
    ('LPWiK/ZP/2026/09'). Not for names or titles — 'S.A.' and 'Sp. z o.o.' end with a '.'.

    Examples:
        ' CEN2611091860728. ' → 'CEN2611091860728'   (the sentence period after the number)
        'ZP.271.1.2026,'      → 'ZP.271.1.2026'
        'transakcji'          → None                 (a word of the label, not the number)
        'Publicznych, ogłoszenie nr 2026/BZP …' → None
    """
    if value is None or isinstance(value, bool):
        return None
    s = re.sub(r"\s+", " ", str(value)).strip(" .,;:\"'„”“«»")
    return s if s and all(re.search(r"\d", part) for part in s.split(" ")) else None


@normalizer("digits_only")
def digits_only(value):
    """'554-024-05-49' → '5540240549';  'KRS 0000054588' → '0000054588'. No digits → None.
    OCR: a letter o/O between two digits is a zero ('9994328o29' → '9994328029'). Output: ASCII digits only
    (full-width '５５４' → '554'); a number from the LLM such as 85071204278.0 keeps its digits (no '.0' → '0')."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    s = unicodedata.normalize("NFKC", str(value))
    s = re.sub(r"(?<=\d)[oO]+(?=\d)", lambda m: "0" * len(m.group(0)), s)
    s = re.sub(r"[^0-9]", "", s)
    return s or None


# --- strip_quotes --------------------------------------------------------------------------------------------
_QUOTES = "\"'„”“‟«»‹›‚‘’`"
_ALWAYS_OPENING = "„‚"  # Polish low-9 quotes never close a quotation
# rounds of stripping: real values nest 2-3 levels ('"„Tytuł”"'); each round rescans the value, so without a cap
# a degenerate '„„„…x…”””' (an LLM repetition loop) is quadratic — 163 s for 50 000 characters
_MAX_QUOTE_LEVELS = 8


def _encloses(s: str) -> bool:
    """True if the quote at s[0] is closed by the quote at s[-1], not earlier: '„A „B” C”' yes, '"A" i "B"' no.
    Ambiguous quote characters are opening after a space / at the start and closing otherwise."""
    depth = 0
    for i, ch in enumerate(s):
        if ch not in _QUOTES:
            continue
        opening = i == 0 or ch in _ALWAYS_OPENING or (i < len(s) - 1 and s[i - 1].isspace() and not s[i + 1].isspace())
        depth += 1 if opening else -1
        if depth == 0 and i < len(s) - 1:
            return False
    return depth == 0


@normalizer("strip_quotes")
def strip_quotes(value: Any) -> str | None:
    """Remove surrounding quotes of any kind and leading/trailing punctuation (',', ';', ':'). A '.' is kept —
    it belongs to 'S.A.' or 'Sp. z o.o.' — unless it follows a closing quote. Inner quotes stay.

    Examples:
        '„Dostawa sprzętu komputerowego”'  → 'Dostawa sprzętu komputerowego'
        '"BIOARCUS" Sp. z o.o.'             → '"BIOARCUS" Sp. z o.o.'   (inner quotes stay!)
        '«Budowa drogi»,'                   → 'Budowa drogi'
        '„Budowa „Orlika” w Gminie”.'       → 'Budowa „Orlika” w Gminie'
        '„, Usługa ochrony”'                → 'Usługa ochrony'           (OCR: stray comma after the quote)
        '„Dostawa sprzętu'                  → 'Dostawa sprzętu'          (lone quote, the closing one was cut off)
        '—', 'brak', '„”'                   → None                       (a filler, not a name or title)
    """
    if value is None:
        return None
    s = str(value).strip()
    for _ in range(_MAX_QUOTE_LEVELS):
        before = s
        s = s.strip(" \t\r\n,;:")
        if len(s) >= 2 and s[-1] == "." and s[-2] in _QUOTES:
            s = s[:-1]  # '„Tytuł”.' — the sentence period after a closing quote
        if len(s) >= 2 and s[0] in _QUOTES and s[-1] in _QUOTES and _encloses(s):
            s = s[1:-1]
        elif s and s[0] in _QUOTES and not any(ch in _QUOTES for ch in s[1:]):
            s = s[1:]  # lone opening quote
        elif s and s[-1] in _QUOTES and not any(ch in _QUOTES for ch in s[:-1]):
            s = s[:-1]  # lone closing quote
        if s == before:
            break
    return None if _is_placeholder(s) else s


# --- email ---------------------------------------------------------------------------------------------------
_EMAIL = re.compile(
    r"(?<![\w.%+-])"  # not glued to a preceding word ('wróbel@…' must not give 'bel@…')
    r"[A-Za-z0-9_%+-]+(?:\.[A-Za-z0-9_%+-]+)*"  # local part
    r"@(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,24}"  # domain + alphabetic TLD
    r"(?![\w-]|\.[A-Za-z0-9])"
)
_SPACED_AT = re.compile(r"(?<=[A-Za-z0-9_%+-])\s*@\s*(?=[A-Za-z0-9])")
_SPACED_INITIAL = re.compile(r"(?<![\w.])([a-z])(?:\s+\.\s*|\s*\.\s+)(?=[A-Za-z0-9_%+-]+@)")  # 'a. rybak@'
_SPACED_DOMAIN_DOT = re.compile(r"@([A-Za-z0-9-]+)(?:\s+\.\s*|\s*\.\s+)(?=[a-z])")  # '@kancelaria . pl'
_GLUED_WORD = re.compile(r"(?<=[a-z])[A-Z][a-z]{2}")  # 'plTel': a capitalised word glued to the TLD


def _trim_glued_word(domain: str) -> str:
    """'kancelaria.pl.Tel' / 'kancelaria.plTel' → 'kancelaria.pl': OCR lost the space before the next word, which
    shows as a capitalised last label after an all-lower-case domain. Unchanged when no valid domain would remain."""
    head, _, last = domain.rpartition(".")
    if not head or any(ch.isupper() for ch in head):
        return domain  # 'Kancelaria.PL' — the case carries no word boundary
    if len(last) >= 3 and last[0].isupper() and last[1:].islower():
        trimmed = head
    elif m := _GLUED_WORD.search(last):
        trimmed = f"{head}.{last[: m.start()]}"
    else:
        return domain
    tld = trimmed.rpartition(".")[2]
    return trimmed if "." in trimmed and re.fullmatch(r"[A-Za-z]{2,24}", tld) else domain


@normalizer("email")
def email(value: Any) -> str | None:
    """Extract the first e-mail address, lower-cased. OCR spaces around '@' and around a dot that would otherwise
    leave the domain without a TLD are removed ('biuro @ kancelaria . pl' → 'biuro@kancelaria.pl').

    Examples:
        ' Biuro@Kancelaria.PL '            → 'biuro@kancelaria.pl'
        'e-mail: sekretariat@sb.example,'   → 'sekretariat@sb.example'
        'a. rybak @rybak-kancelaria.example' → 'a.rybak@rybak-kancelaria.example'
        'biuro@kancelaria.pl.Tel. 22 123'   → 'biuro@kancelaria.pl'      (OCR: the next word glued on)
        'brak'                              → None
    """
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value))  # NFKC: fullwidth '＠' → '@'
    text = _SPACED_AT.sub("@", text)
    text = _SPACED_INITIAL.sub(r"\1.", text)
    text = _SPACED_DOMAIN_DOT.sub(r"@\1.", text)
    m = _EMAIL.search(text)
    if not m:
        return None
    local, _, domain = m.group(0).rpartition("@")
    return f"{local}@{_trim_glued_word(domain)}".lower()


# --- person_name ---------------------------------------------------------------------------------------------
_OCR_PL_LETTERS = str.maketrans("šŠžŽňŇ", "śŚżŻńŃ")  # RapidOCR reads Polish ś/ż/ń as Czech š/ž/ň
_LETTER = r"[^\W\d_]"
_TITLES = re.compile(
    rf"(?<!{_LETTER})(?:"
    r"r\.\s*pr\b\.?"  # r. pr. / r.pr.
    r"|radc\w*\s+prawn\w*"  # radca prawny (any grammatical case)
    r"|aplikant\w*(?:\s+(?:adwokack|radcowsk)\w*)?"
    r"|adwokat\w*|mecenas\w*|profesor\w*"
    rf"|(?:adw|apl|mec|prof|dr|hab|doc|mgr|in[żz]|lic|ll\.\s*m|ks|mba|ph\.?\s*d)(?:\.|(?!{_LETTER}))"  # abbreviations
    rf"|pan(?:i|ią|ia|a|u)?(?!{_LETTER})"  # Pan / Pani
    r")",
    re.IGNORECASE,
)
# separators between a name and what follows it: 'Jan Kowalski, radca prawny', '… – Prezes Zarządu', '(PESEL …)';
# titles are separators too — a title starts the next person ('Oliwia Kaczmarek r. pr. Aleksandra Marciniak');
# a dash run is matched from its first dash only (otherwise a long run of dashes costs quadratic time)
_NAME_SEPARATORS = re.compile(
    r"[,;:()\[\]{}<>/|\\\"„”“«»]|\s[-–—]+\s*|(?<![-–—])[-–—]+\s|\d+|(?<!\w)(?:i|oraz|lub|albo|jako)(?!\w)",
    re.IGNORECASE,
)
# 'Kow-⏎alski' / 'Nowak-⏎Kowalska': a word or a double surname hyphenated at a line end
_LINE_HYPHEN = re.compile(rf"(?<={_LETTER})-[ \t]*\r?\n\s*(?={_LETTER})")
_LETTERS = re.compile(rf"{_LETTER}+")
# roles of company signatories / attorneys, as stems of the folded word (any grammatical form: 'Pełnomocnicy',
# 'pełnomocnikiem'); public offices such as Starosta or Marszałek are left out — they are common surnames
_ROLE_STEMS = (
    "prezes", "wiceprezes", "prokurent", "pelnomocni", "mocodaw", "dyrektor", "wicedyrektor", "zastepc", "kierowni",
    "wlasciciel", "wspolni", "likwidator", "przewodnicz", "ksiegow", "glowny", "glowna", "specjalist", "partner",
    "lider", "pracowni", "czlon", "reprezentant", "upowaznion", "odwolujac", "zamawiajac", "wykonawc",
)  # fmt: skip
# genitive complements of a role noun ('Członek Zarządu', OCR 'Czionek Zarzadu'): dropped with the role word
_ROLE_COMPLEMENTS = {"zarzadu", "rady", "nadzorczej", "konsorcjum", "spolki", "mocodawcy", "firmy"}
# words that continue a role after its noun ('Prokurent samoistny', 'Dyrektor Generalny', 'Przewodniczący Komisji',
# 'Członek Zarządu ds. Finansowych'): never left behind as part of a name
_ROLE_FOLLOWERS = _ROLE_COMPLEMENTS | {
    "samoistny", "samoistna", "laczny", "laczna", "generalny", "generalna", "naczelny", "naczelna", "zarzadzajacy",
    "zarzadzajaca", "finansowy", "finansowa", "handlowy", "handlowa", "techniczny", "techniczna", "operacyjny",
    "operacyjna", "komisji", "oddzialu", "ds",
}  # fmt: skip
_NOT_NAME_WORDS = {
    "kancelaria", "kancelarii", "spolka", "sp", "urzad", "gmina", "miasto", "powiat", "szpital", "szkola", "zespol",
    "centrum", "fundacja", "stowarzyszenie", "instytut", "uniwersytet", "politechnika", "akademia", "przedsiebiorstwo",
    "zaklad", "firma", "konsorcjum", "grupa", "bank", "muzeum", "osrodek", "komenda", "zarzad", "dyrekcja", "agencja",
    "biuro", "partnerzy", "prawna", "prawnych", "adwokacka", "adwokackie", "radcow", "radcy", "prawnego", "notariusz",
    "ul", "al", "nip", "regon", "krs", "pesel", "tel", "email", "mail", "www", "brak", "nie", "dotyczy", "null",
    # capitalised words of headings, labels and institutions that are never names ('Krajowa Izba Odwoławcza',
    # 'Data Operacji', 'Podpis Elektroniczny', 'Skarb Państwa', 'Rachunek Odbiorcy')
    "krajowa", "krajowej", "izba", "izby", "odwolawcza", "odwolawczej", "skarb", "panstwa", "panstwo", "szanowni",
    "szanowny", "szanowna", "sad", "sadu", "okregowy", "rejonowy", "apelacyjny", "rzeczpospolita", "rzeczypospolitej",
    "podpis", "podpisano", "podpisal", "podpisala", "podpisany", "podpisana", "elektroniczny", "elektronicznie",
    "kwalifikowany", "dokument", "data", "kwota", "tytul", "tytulem", "nazwa", "rachunek", "rachunku", "odbiorca",
    "odbiorcy", "nadawca", "nadawcy", "zleceniodawca", "zleceniodawcy", "beneficjent", "beneficjenta", "kontrahent",
    "kontrahenta", "przelew", "przelewu", "operacji", "strona", "zalacznik", "zamowienie", "zamowienia", "zamowien",
    "publicznych", "postepowanie", "postepowania", "odwolanie", "odwolania", "uzasadnienie", "wniosek", "ogloszenie",
    "umowa", "minister", "ministerstwo", "adres", "siedziba", "telefon", "faks", "fax", "sygn", "akt", "nr",
}  # fmt: skip
_NAME_WORD = re.compile(rf"{_LETTER}{{2,}}(?:-{_LETTER}{{2,}})*")
# common Polish first names with diacritics; OCR drops them ('Rafal', 'Lukasz') or reads 'ł' as 't' ('Rafat', 'Pawet')
_FIRST_NAMES = (
    "Łukasz", "Paweł", "Michał", "Rafał", "Mikołaj", "Przemysław", "Stanisław", "Władysław", "Bolesław", "Mirosław",
    "Jarosław", "Radosław", "Sławomir", "Wiesław", "Zdzisław", "Czesław", "Bronisław", "Bogusław", "Wacław", "Józef",
    "Jędrzej", "Gaweł", "Bogumił", "Małgorzata", "Elżbieta", "Bożena", "Grażyna", "Żaneta", "Józefa", "Stanisława",
    "Władysława", "Mirosława", "Jarosława", "Radosława", "Wiesława", "Zdzisława", "Czesława", "Bronisława",
    "Bogusława", "Wacława", "Sławomira", "Ludmiła", "Bogumiła",
)  # fmt: skip


def _ocr_variants(name: str) -> set[str]:
    variants = {""}
    for ch in name:
        options = {ch, fold_diacritics(ch)} | ({"t"} if ch == "ł" else set())
        variants = {v + o for v in variants for o in options}
    return variants - {name}


_FIRST_NAME_FIXES = {v: name for name in _FIRST_NAMES for v in _ocr_variants(name)}
# common Polish given names (folded, lower-case): three words make a name only as two given names + a surname
# ('Anna Maria Nowak'); otherwise the third word is a role, a firm or a place ('Jan Kowalski Warszawa')
_GIVEN_NAMES = frozenset(fold_diacritics(n).lower() for n in _FIRST_NAMES + (
    "Adrianna", "Agata", "Agnieszka", "Aldona", "Aleksandra", "Alicja", "Alina", "Amelia", "Aneta", "Angelika", "Anita",
    "Anna", "Antonina", "Aurelia", "Barbara", "Beata", "Bernadeta", "Blanka", "Cecylia", "Celina", "Danuta", "Daria",
    "Diana", "Dominika", "Dorota", "Edyta", "Eliza", "Emilia", "Ewa", "Ewelina", "Felicja", "Franciszka", "Gabriela",
    "Genowefa", "Halina", "Hanna", "Helena", "Henryka", "Iga", "Ilona", "Irena", "Iwona", "Izabela", "Jadwiga",
    "Jagoda", "Janina", "Joanna", "Jolanta", "Julia", "Julianna", "Justyna", "Kamila", "Karina", "Karolina",
    "Katarzyna", "Kazimiera", "Kinga", "Klara", "Klaudia", "Kornelia", "Krystyna", "Laura", "Lena", "Leokadia",
    "Lidia", "Liliana", "Lucyna", "Łucja", "Magdalena", "Maja", "Malwina", "Maria", "Marianna", "Mariola", "Marta",
    "Martyna", "Marzena", "Matylda", "Michalina", "Milena", "Monika", "Natalia", "Nikola", "Nina", "Olga", "Oliwia",
    "Patrycja", "Paula", "Paulina", "Regina", "Renata", "Roksana", "Róża", "Rozalia", "Sabina", "Sandra", "Sara",
    "Stefania", "Sylwia", "Tamara", "Teresa", "Urszula", "Wanda", "Weronika", "Wiktoria", "Wioletta", "Zofia",
    "Zuzanna", "Adam", "Adrian", "Albert", "Aleksander", "Andrzej", "Antoni", "Arkadiusz", "Artur", "August",
    "Bartłomiej", "Bartosz", "Bogdan", "Cezary", "Damian", "Daniel", "Dariusz", "Dawid", "Dominik", "Edward", "Emil",
    "Eryk", "Eugeniusz", "Feliks", "Filip", "Franciszek", "Gabriel", "Grzegorz", "Henryk", "Hubert", "Igor",
    "Ignacy", "Ireneusz", "Jacek", "Jakub", "Jan", "Janusz", "Jerzy", "Julian", "Juliusz", "Kacper", "Kamil", "Karol",
    "Kazimierz", "Konrad", "Krystian", "Krzysztof", "Lech", "Leon", "Leszek", "Ludwik", "Maciej", "Maksymilian",
    "Marcel", "Marcin", "Marek", "Marian", "Mariusz", "Mateusz", "Mieczysław", "Nikodem", "Norbert", "Oliwer",
    "Oskar", "Patryk", "Piotr", "Remigiusz", "Robert", "Roman", "Ryszard", "Sebastian", "Stefan", "Szymon",
    "Tadeusz", "Teodor", "Tomasz", "Tymoteusz", "Waldemar", "Wiktor", "Witold", "Wojciech", "Zbigniew", "Zenon",
    "Zygmunt",
))  # fmt: skip


def _name_parts(segment: str) -> list[list[str]]:
    """Words of one segment (titles already removed), cut at role words: a role ends the name before it and is
    dropped together with its complements and modifiers ('Anna Nowak Prokurent samoistny', 'Prezes Zarządu Jan
    Kowalski', 'Członek Zarządu ds. Finansowych …'), so no role word is ever glued to a name."""
    words = [w.strip(".'’`") for w in segment.split()]
    words = [w for w in words if w]
    folded = [fold_diacritics(w).lower() for w in words]
    parts: list[list[str]] = [[]]
    i = 0
    while i < len(words):
        if folded[i].startswith(_ROLE_STEMS):
            parts.append([])
            i += 1
            while i < len(words) and (folded[i] in _ROLE_FOLLOWERS or folded[i].startswith(_ROLE_STEMS)):
                i += 2 if folded[i] == "ds" else 1  # 'ds. Finansowych'
            continue
        if folded[i] in _ROLE_COMPLEMENTS:  # complement of a role word mangled by OCR ('Czionek Zarzadu'): drop both
            if parts[-1]:
                parts[-1].pop()
            parts.append([])
        else:
            parts[-1].append(words[i])
        i += 1
    return [p for p in parts if p]


def _plausible_name(words: list[str]) -> str | None:
    """'Jan', 'S.A.', 'Kancelaria Adwokacka', 'w imieniu' … → None; 'KRZYSZTOF JODŁOWSKI' → 'Krzysztof Jodłowski'.
    Two words, or three when the first two are given names ('Anna Maria Nowak'): other three capitalised words are
    a name next to a firm, role or place ('Jan Kowalski Warszawa'), four are two people side by side."""
    if words and not words[-1].isupper():  # 'RAVELO-BUD Sebastian Tomaszewski': upper-case firm name before the owner
        while words and words[0].isupper():
            words = words[1:]
    if not 2 <= len(words) <= 3:
        return None
    out = []
    for w in words:
        if not _NAME_WORD.fullmatch(w) or fold_diacritics(w).lower() in _NOT_NAME_WORDS:
            return None
        if w.isupper():
            w = "-".join(part.capitalize() for part in w.split("-"))
        if not all(part[0].isupper() for part in w.split("-")):
            return None
        out.append(_FIRST_NAME_FIXES.get(w, w))
    if len(out) == 3 and not all(fold_diacritics(w).lower() in _GIVEN_NAMES for w in out[:2]):
        return None
    return " ".join(out)


def _join_line_hyphen(m: re.Match) -> str:
    """'Kow-⏎alski' → 'Kowalski', 'Nowak-⏎Kowalska' → 'Nowak-Kowalska'; before a title or a role ('Kowalski-⏎Prezes
    Zarządu') the hyphen stays a separator."""
    word = _LETTERS.match(m.string, m.end()).group(0)
    if _TITLES.match(m.string, m.end()) or fold_diacritics(word).lower().startswith(_ROLE_STEMS):
        return " | "
    return "" if word[0].islower() else "-"


@normalizer("person_name")
def person_name(value: Any) -> str | None:
    """Person name without professional titles, single spaces, nominative case is NOT handled here
    (inflection is the LLM's job — this only cleans).

    Titles (r. pr., radca prawny, adw., adwokat, mec., dr hab., prof., mgr, inż., ks., Pan/Pani …) and roles
    (Prezes Zarządu, prokurent samoistny, pełnomocnik …) are removed, ALL-CAPS names are title-cased and the OCR
    letters š/ž/ň become ś/ż/ń. The text is cut at commas, dashes, parentheses, 'i'/'oraz', digits, titles and
    roles; the first piece that is a plausible name (2 capitalised words, 3 only as two given names + surname) is
    returned — so with several people listed it is the first one. Nothing plausible (an initial, a company, a role
    only, a sentence, a heading such as 'Krajowa Izba Odwoławcza') gives None.

    Examples:
        'r. pr. Aleksandra Marciniak'          → 'Aleksandra Marciniak'
        'adw.  Michał Borkowski'               → 'Michał Borkowski'
        'mec. Jan Kowalski, radca prawny'      → 'Jan Kowalski'
        'Szymon Brzozowski - Wiceprezes Zarzadu' → 'Szymon Brzozowski'
        'Prokurent samoistny Anna Nowak'       → 'Anna Nowak'
        'r. pr. Žaneta Olszewska'              → 'Żaneta Olszewska'
        'Oliwia Kaczmarek r. pr. Aleksandra Marciniak' → 'Oliwia Kaczmarek'   (two people, never merged)
        'Anna Maria Nowak'                     → 'Anna Maria Nowak'
        'Prezes Zarządu'                       → None   (not a name)
        'Bartosz Zajkowski Oliwia Gajewski'    → None   (two people side by side, no separator)
        'Jan Kowalski Warszawa'                → None   (a name next to a place: which two words?)
        'Kancelaria Radców Prawnych'           → None
    """
    if value is None:
        return None
    text = unicodedata.normalize("NFC", str(value)).translate(_OCR_PL_LETTERS)
    text = _LINE_HYPHEN.sub(_join_line_hyphen, text)
    text = _TITLES.sub("|", re.sub(r"\s+", " ", text))
    for segment in _NAME_SEPARATORS.split(text):
        for words in _name_parts(segment):
            if name := _plausible_name(words):
                return name
    return None
