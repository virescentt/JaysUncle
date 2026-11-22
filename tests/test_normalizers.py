"""SPECIFICATION of normalizers (odwolania2json/normalize/). Stubs are skipped; implemented → must pass.
Examples come from the training data (data/uzp/dane) and the organizers' field descriptions; surface forms marked
'scan' were read by RapidOCR from the real scans in data/uzp/dane/skany (the source document is named)."""

import pytest

from odwolania2json.normalize import apply_normalizers
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
        # OCR: month names without (or with mangled) Polish diacritics
        ("9 wrzesnia 2026", "2026-09-09"),
        ("9 wrzešnia 2026 roku", "2026-09-09"),  # scan pelnomocnictwa/0001
        ("1 styczen 2026", "2026-01-01"),
        ("15 pazdziernika 2025", "2025-10-15"),
        # separators, year first, single-digit parts
        ("20 04 2026", "2026-04-20"),  # scan potwierdzenia/0008
        ("2026.04.20 17:58:34 CEST", "2026-04-20"),  # scan: e-signature date
        ("2026-04-09 godz. 17:24", "2026-04-09"),  # scan potwierdzenia/0002
        ("2026-03-21T10:00:00Z", "2026-03-21"),  # ISO datetime (LLM)
        ("11.09.2026 18:53", "2026-09-11"),  # scan potwierdzenia/0001
        ("2026/03/05", "2026-03-05"),
        ("2026-3-5", "2026-03-05"),
        ("24.08:2026", "2026-08-24"),  # scan: OCR ':' for '.'
        # 'r' / 'r.' / 'roku', prefixes, trailing punctuation
        ("05.06.2026r.", "2026-06-05"),  # scan odwolania/0009
        ("2026-02-12*", "2026-02-12"),  # scan potwierdzenia/0004
        ("6 września 2026 r.;", "2026-09-06"),  # scan odwolania/0001
        ("dnia,18 kwietnia 2026 roku", "2026-04-18"),  # scan pelnomocnictwa/0008
        ("z dnia 1 kwietnia 2026 roku", "2026-04-01"),
        ("dn. 3.07.2026", "2026-07-03"),
        ("21 mar. 2026", "2026-03-21"),
        ("5.XI.2025 r.", "2025-11-05"),
        ("z dnia 21 października 2026 roku, godz. 12:00:00 CEST", "2026-10-21"),  # the longest form under the cap
        # invalid or ambiguous → None
        ("5 i 2025", None),
        ("21.03.26", None),
        ("20 04 2026 16 38", None),
        ("31 listopada 2026", None),
        ("2026-13-01", None),
        ("29.02.2025", None),
        (None, None),
    ],
)
def test_date_pl(raw, expected):
    assert call_or_skip(v.date_pl, raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [("15 000,00 zł", 15000.0), ("200,00 PLN", 200.0), ("1.234,56", 1234.56), ("17,00", 17.0), (10000, 10000.0),
     ("brak", None),
     # the decimal separator is the last '.' or ',' followed by 1-2 digits
     ("17.00 PLN", 17.0),  # scan potwierdzenia/0008 (was 1700.0)
     ("1,234.56", 1234.56), ("10.000", 10000.0), ("10 000.00", 10000.0), ("0,5", 0.5),
     ("7 500,00 PLN", 7500.0),  # scan potwierdzenia/0001
     # debits are printed with a minus; the amount of a transfer is its magnitude
     ("-17,00 PLN", 17.0), ("-17.00 PLN", 17.0), ("-10 000,00 PLN", 10000.0),  # scans 0002, 0004, 0007
     # several numbers: the one next to the currency
     ("200,00 PLN 2026-03-21", 200.0),  # przyklad_01 (table row: amount + date)
     ("200.00 28.12.2025", 200.0), ("100,00 24.01.2026", 100.0),  # przyklad_15, _36: a date is never an amount
     ("PLN 17,00", 17.0), ("17,- zł", 17.0), ("WARTOŚĆ ......200,00", 200.0),
     ("10 o00 zl", 10000.0),  # scan odwolania/0009: OCR letter o for 0
     # malformed / ambiguous → None
     ("12.34.56", None), ("1 7.00", None), ("2026-03-21", None), (True, None),
     ("Kwota operacji 200,00 PLN, prowizja 0,00 PLN", None),
     # review: magnitude words multiply the number; a unit stated elsewhere is ambiguous (was 1.5 / 15.0 / 1500.0)
     ("1,5 mln zł", 1500000.0), ("15 tys. zł", 15000.0), ("2 mld zł", 2000000000.0), ("3 miliony zł", 3000000.0),
     ("Kwota (w tys. zł): 1 500", None),
     # review: grosze written apart are not dropped silently (was 200.0 / 50.0); the słownie '00/100 gr' is fine
     ("200 zł 50 gr", None), ("50 gr", None), ("2 000,00 zł (słownie: dwa tysiące złotych 00/100 gr)", 2000.0),
     # review: a fraction without its integer part is not an amount (was 50.0)
     (",50", None), (".50", None), ("Kwota .50 zł", None),
     # review: digits glued to letters are an identifier (scan potwierdzenia/0001: a bank table read column by column
     # put the transaction id under 'Kwota' — was 9057564440515.0); a glued currency is fine
     ("CEN9057564440515", None), ("ul. Postępu 17A", None), ("PLN200,00", 200.0), ("200,00PLN", 200.0),
     ("200zł", 200.0), ("200zt", 200.0),
     # review: NaN / inf from an LLM would make the output invalid JSON (a 400-digit string also gave inf)
     (float("nan"), None), (float("inf"), None), (10**400, None), (-5.5, 5.5), ("1" * 400, None),
     ("1234567890123456 zł", None), ("123 456 789 012 345,00 zł", 123456789012345.0)],
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
        # scan potwierdzenia/0001: the account follows an address on the same line
        ("Urzad Zamówień Publicznych, ul. Postępu 17A, 60 1010 1010 0081 3622 3100 0000", "60101010100081362231000000"),
        ("Z rachunku: 50 1090 1098 5413 5699 1037 3779", "50109010985413569910373779"),  # scan potwierdzenia/0002
        ("nr PL10 3863 9361 8709 7408 4853\n4519, prowadzony", "10386393618709740848534519"),  # przyklad_05: wrapped
        ("PL62 2170 0008 9152 5017 5661 7O62", "62217000089152501756617062"),  # OCR letter O for 0
        ("60 1010 1010 0081 3622 3100 0000 i 50 1090 1098 5413 5699 1037 3779", None),  # two accounts
    ],
)
def test_account_number(raw, expected):
    assert call_or_skip(v.account_number, raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        # real scans keep 'PL' in the gold exactly when the document prints it (potwierdzenia/0001, 0006)
        ("PL62 2170 0008 9152 5017 5661 7062", "PL62217000089152501756617062"),
        ("pl75217000080889261999999470", "PL75217000080889261999999470"),
        ("Z rachunku: PL 50 1090 1098 5413 5699 1037 3779", "PL50109010985413569910373779"),
        ("Z rachunku: 50 1090 1098 5413 5699 1037 3779", "50109010985413569910373779"),  # potwierdzenia/0002
        ("60 1010 1010 0081 3622 3100 0000 i 50 1090 1098 5413 5699 1037 3779", None),
        ("1234", None),
        (None, None),
    ],
)
def test_account_number_as_printed(raw, expected):
    assert v.account_number_as_printed(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [("2026/BZP 00999263", "2026/BZP00999263"), ("2025/BZP 00270698/01", "2025/BZP00270698/01"),
     ("2026 / BZP 00999778", "2026/BZP00999778"), ("641651-2025", None),
     ("nr 2026/BZP 00767819 / 01,", "2026/BZP00767819/01"),
     ("2026/BZP o0692455/03", "2026/BZP00692455/03"),  # scan odwolania/0009: OCR letter o for 0
     ("2026/bzp 00999263", "2026/BZP00999263"),
     ("2026/BZP 0099926", None), ("2026/BZP 00999263/011", None),
     # review: the 'YYYY/BZP' numbering exists since 2021 (was '3026/BZP00999263'); an unknown '-01' tail is not
     # silently dropped (was '2026/BZP00999263'); a dash before a following date is fine
     ("3026/BZP 00999263", None), ("1999/BZP 00999263", None), ("2026/BZP 00999263-01", None),
     ("2026/BZP 00999263 - 2026-03-21", "2026/BZP00999263")],
)  # fmt: skip
def test_bzp(raw, expected):
    assert call_or_skip(v.bzp, raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [("641651-2025", "641651-2025"), ("2026/S 241-999983", "999983-2026"), ("2025/S 123-363730", "363730-2025"),
     ("2026/S086-999073", "999073-2026"), ("2026/BZP 00999263", None),
     # 1 to 8 digits in both notations
     ("2026/S 086-12345", "12345-2026"), ("12345678-2026", "12345678-2026"), ("1-2026", "1-2026"),
     ("2025-OJS107-00364525", "364525-2025"),
     ("nr 2026/S\n153-706707", "706707-2026"),  # scan odwolania/0007: line break inside
     ("Dz.U. UE nr 570628-2026", "570628-2026"),
     ("123456789-2026", None), ("01-12-2025", None),
     # review: notice number 0 does not exist (was '0-2025'); two different numbers in different notations are
     # ambiguous (the plain one was ignored); the same number in both notations is fine
     ("0-2025", None), ("2026/S 000-000000", None), ("Nr ogłoszenia: 2026/S 241-999983, 641651-2025", None),
     ("2026/S 241-999983 (999983-2026)", "999983-2026"), ("2026/S 153-706707 z dnia 2026-08-11", "706707-2026")],
)  # fmt: skip
def test_ted(raw, expected):
    assert call_or_skip(v.ted, raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("„Dostawa sprzętu komputerowego”", "Dostawa sprzętu komputerowego"),
        ('"BIOARCUS" Sp. z o.o.', '"BIOARCUS" Sp. z o.o.'),
        ("«Budowa drogi»,", "Budowa drogi"),
        ("„Budowa „Orlika” w Gminie”.", "Budowa „Orlika” w Gminie"),
        ('"Budowa "Orlika" w Gminie"', 'Budowa "Orlika" w Gminie'),
        ("„, Usługa ochrony fizycznej”", "Usługa ochrony fizycznej"),  # scan odwolania/0007
        ("„ Dostawa sprzętu", "Dostawa sprzętu"),  # scan odwolania/0003: closing quote on another line
        ("Dostawa sprzętu”", "Dostawa sprzętu"),
        ('"ABC" i "XYZ"', '"ABC" i "XYZ"'),  # two quotations, not one surrounding pair
        ("Belmoria Rozwiązania S.A.", "Belmoria Rozwiązania S.A."),  # the final '.' belongs to the name
        ("WPIS OD ODW., ZAM: X, BUDOWA DR. POŻAR.", "WPIS OD ODW., ZAM: X, BUDOWA DR. POŻAR."),  # gold tytul
        ('""', None),
        # review: punctuation only or a filler is not a name / title (was '.', '-', 'brak')
        (".", None),
        ("—", None),
        ("-", None),
        ("brak", None),
        ("Nie dotyczy.", None),
    ],
)
def test_strip_quotes(raw, expected):
    assert call_or_skip(t.strip_quotes, raw) == expected


@pytest.mark.parametrize("fn", [t.strip, t.collapse_whitespace])
@pytest.mark.parametrize(
    "raw, expected",
    [("  Warszawa ", "Warszawa"), ("ZP.271.1.2026", "ZP.271.1.2026"), ("x", "x"),
     # review: None gave the string 'None' (direct calls); fillers ('Numer referencyjny: brak', LLM '—') → None
     (None, None), ("", None), ("—", None), ("…", None), ("brak", None), ("Brak.", None), ("N/D", None),
     ("nie dotyczy", None)],
)  # fmt: skip
def test_strip_and_collapse_whitespace_drop_fillers(fn, raw, expected):
    assert fn(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [(" CEN2611091860728. ", "CEN2611091860728"),  # przyklad_05: 'Numer transakcji operacji: CEN….' (sentence period)
     ("ZP.271.1.2026,", "ZP.271.1.2026"), ("„ZP/1/2026”", "ZP/1/2026"), ("LPWiK/ZP/2026/09", "LPWiK/ZP/2026/09"),
     ("1234 5678 9012", "1234 5678 9012"), (123456, "123456"),
     ("transakcji", None),  # scan potwierdzenia/0001: a word of the label 'Numer referencyjny transakcji'
     ("Publicznych, ogłoszenie nr 2026/BZP 00123456", None),  # scan odwolania/0005: a phrase, not a number
     ("CEN 2611091860728", None), ("-", None), ("", None), (None, None), (True, None)],
)  # fmt: skip
def test_code(raw, expected):
    assert t.code(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [(" Biuro@Kancelaria.PL ", "biuro@kancelaria.pl"), ("e-mail: sekretariat@sb.example,", "sekretariat@sb.example"),
     ("brak", None),
     ("e-mail: a.rybak@rybak-kancelaria.example", "a.rybak@rybak-kancelaria.example"),  # scan odwolania/0001
     # OCR spaces around '@' and '.'
     ("biuro @ kancelaria . pl", "biuro@kancelaria.pl"),
     ("biuro@rybak-kancelaria. example", "biuro@rybak-kancelaria.example"),
     ("a. rybak @rybak-kancelaria.example", "a.rybak@rybak-kancelaria.example"),
     ("kontakt: biuro@kancelaria.pl. Tel. 22 123", "biuro@kancelaria.pl"),
     ("biuro@kancelaria.pl, sekretariat@kancelaria.pl", "biuro@kancelaria.pl"),
     ("biuro＠kancelaria.pl", "biuro@kancelaria.pl"),
     # never a fragment of a longer, invalid address
     ("jan.wróbel@x.pl", None), ("biuro@wierzbicki-kancelaría.example", None),  # scan odwolania/0002: OCR 'í'
     ("x@y", None), ("@kancelaria.pl", None),
     # review: OCR glued the next word to the address (was 'biuro@kancelaria.pl.tel' / '…pltel' / '….strona')
     ("biuro@kancelaria.pl.Tel. 22 123 45 67", "biuro@kancelaria.pl"),
     ("biuro@kancelaria.plTel", "biuro@kancelaria.pl"), ("e-mail: jan@x.pl.Strona www", "jan@x.pl"),
     # … but the case of a real address is no word boundary
     ("Jan@X.Pl", "jan@x.pl"), ("jan@firma.com.Pl", "jan@firma.com.pl"), ("jan@kancelaria.Pl", "jan@kancelaria.pl")],
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
        # titles in any form, several titles, Pan/Pani
        ("radca prawny Aleksandra Rybak", "Aleksandra Rybak"),  # scan odwolania/0001
        ("adwokat Sylwia Sikora", "Sylwia Sikora"),  # scan odwolania/0006
        ("r.pr.Jan Kowalski", "Jan Kowalski"),
        ("prof. dr hab. inż. Jan Kowalski", "Jan Kowalski"),
        ("Pani Anna Nowak", "Anna Nowak"),
        ("Pełnomocnik: adw. Jan Kowalski", "Jan Kowalski"),
        # roles after a dash / in parentheses / on the next line, list numbering
        ("Szymon Brzozowski - Wiceprezes Zarzadu", "Szymon Brzozowski"),  # scan pelnomocnictwa/0001
        ("Grzegorz Jaworski – Czionek Zarzadu", "Grzegorz Jaworski"),  # scan pelnomocnictwa/0002 (OCR 'Czionek')
        ("Dorota Topolska Czionek Zarzadu", "Dorota Topolska"),
        ("Dorota Grabowska – Prezes Zarzadu – Lider Konsorcjum", "Dorota Grabowska"),  # scan pelnomocnictwa/0007
        ("Krzysztof Jodłowski (radca prawny, nr wpisu PO-4973, PESEL: 69102340858)", "Krzysztof Jodłowski"),  # 0004
        ("1. Mateusz Wierzbicki – adwokat, nr wpisu KR-8793,", "Mateusz Wierzbicki"),  # scan pelnomocnictwa/0002
        ("Katarzyna Sikora(radca prawny, nr", "Katarzyna Sikora"),  # scan pelnomocnictwa/0005
        ("KANCELARIA RADCY PRAWNEGO ALEKSANDRA RYBAK", "Aleksandra Rybak"),  # scan pelnomocnictwa/0001 letterhead
        ("Oliwia Kaczmarek\nPrezes Zarządu", "Oliwia Kaczmarek"),
        # OCR: Czech letters for Polish ones, lost diacritics in common first names, ALL CAPS
        ("r. pr. Žaneta Olszewska", "Żaneta Olszewska"),  # scan odwolania/0003
        ("adw. Rafat Malinowski", "Rafał Malinowski"),  # scan odwolania/0008
        ("Lukasz Sosnowski – Prezes Zarzadu", "Łukasz Sosnowski"),  # scan pelnomocnictwa/0007
        ("KRZYSZTOF JODŁOWSKI", "Krzysztof Jodłowski"),
        ("Lucja Nowak", "Lucja Nowak"),  # a real name, not 'Łucja' without diacritics
        # firm name of a sole trader before the owner's name (przyklad_47)
        ("RAVELO-BUD Sebastian Tomaszewski, ul. Jagiellońska 91", "Sebastian Tomaszewski"),
        # several people: the first one, never merged
        ("Jan Kowalski i Anna Nowak", "Jan Kowalski"),
        ("Oliwia Kaczmarek r. pr. Aleksandra Marciniak", "Oliwia Kaczmarek"),  # przyklad_01: signature columns
        ("Pełnomocnicy adw. Bartosz Szymanski, nr wpisu WA 33513", "Bartosz Szymanski"),  # przyklad_02
        ("Bartosz Zajkowski Oliwia Gajewski", None),  # przyklad_06: two signers side by side, no separator
        ("Anna Maria Nowak-Kowalska", "Anna Maria Nowak-Kowalska"),
        # review: a role ends the name together with its modifiers (was 'Samoistny Anna Nowak', 'Anna Nowak
        # Samoistny', 'Generalny Jan Kowalski', 'Komisji Jan Kowalski', a 4-word None)
        ("Prokurent samoistny Anna Nowak", "Anna Nowak"),
        ("Anna Nowak Prokurent Samoistny", "Anna Nowak"),
        ("PROKURENT SAMOISTNY ANNA NOWAK", "Anna Nowak"),
        ("Anna Nowak\nProkurent samoistny", "Anna Nowak"),
        ("Dyrektor Generalny Jan Kowalski", "Jan Kowalski"),
        ("Przewodniczący Komisji Jan Kowalski", "Jan Kowalski"),
        ("Przewodniczący Rady Nadzorczej Jan Kowalski", "Jan Kowalski"),
        ("Wiceprezes Zarządu ds. Finansowych Jan Kowalski", "Jan Kowalski"),
        ("Członek Zarządu Halina Baran", "Halina Baran"),
        ("Jan Kowalski Prokurent Anna Nowak", "Jan Kowalski"),
        # review: three words only as two given names + surname (was 'Bartosz Zajkowski Oliwia', 'Jan Kowalski
        # Warszawa', 'Podpisał Jan Kowalski', 'Fhu Jan Kowalski', 'Sebastian Tomaszewski Ravelo-Bud' …)
        ("Anna Maria Nowak", "Anna Maria Nowak"),
        ("Stanisław August Poniatowski", "Stanisław August Poniatowski"),
        ("Bartosz Zajkowski Oliwia", None),
        ("Jan Kowalski Warszawa", None),
        ("Podpisał Jan Kowalski", None),
        ("FHU JAN KOWALSKI", None),
        ("Sebastian Tomaszewski RAVELO-BUD", None),
        ("Minister Jan Kowalski", None),
        # review: more titles (was 'Ks Jan Kowalski', 'Jan Kowalski Mba')
        ("Ks. Jan Kowalski", "Jan Kowalski"),
        ("Jan Kowalski MBA", "Jan Kowalski"),
        ("Jan Kowalski, PhD", "Jan Kowalski"),
        # review: capitalised headings and labels are not names (were returned as names)
        ("Krajowa Izba Odwoławcza", None),
        ("Prezes Krajowej Izby Odwoławczej", None),
        ("Skarb Państwa", None),
        ("Podpis Elektroniczny", None),
        ("Data Operacji", None),
        ("Rachunek Odbiorcy", None),
        # review: hyphenation at a line end (was 'Jan Kow' / 'Anna Nowak')
        ("Jan Kow-\nalski", "Jan Kowalski"),
        ("Anna Nowak-\nKowalska", "Anna Nowak-Kowalska"),
        ("Jan Kowalski-\nPrezes Zarządu", "Jan Kowalski"),
        ("Jan Kowalski-\nadwokat", "Jan Kowalski"),
        # not a person name → None
        ("Kancelaria Radców Prawnych", None),
        ("Belmoria Rozwiązania S.A.", None),
        ("Dokument podpisany przez Szymon Jabłoń", None),
        ("J. Kowalski", None),
        ("jan kowalski", None),
        ("Jan", None),
        ("12345", None),
    ],
)
def test_person_name(raw, expected):
    assert call_or_skip(t.person_name, raw) == expected


def test_yaml_chain_strip_quotes_then_collapse_whitespace():
    """The chain used by nazwa/tytul fields in config/doc_types/*.yaml."""
    raw = "„Dostawa sprzętu\nkomputerowego wraz z oprogramowaniem”,"  # quoted title wrapped over two lines
    assert apply_normalizers(["strip_quotes", "collapse_whitespace"], raw) == (
        "Dostawa sprzętu komputerowego wraz z oprogramowaniem",
        [],
    )


def test_normalizers_are_linear_on_degenerate_input():
    """A small LLM can loop ('12:00 12:00 …', '- - - …') up to its token limit; the normalizers were quadratic
    on such values (date_pl 9 s, amount_pl 4 s, person_name 1 s on 8000 chars) — the pipeline must not stall."""
    import time

    from odwolania2json.normalize import NORMALIZERS

    inputs = ["21.03.2026" + " r" * 10000, "21.03.2026" + " 12:00" * 4000, "1 " * 10000, "1o " * 7000,
              "-" * 20000 + "1", "2026/BZP " * 2500, "„" * 20000, "„" * 10000 + "x" + "”" * 10000]  # fmt: skip
    start = time.perf_counter()
    for value in inputs:
        for fn in NORMALIZERS.values():
            fn(value)
    assert time.perf_counter() - start < 10  # ~0.3 s now; minutes with the quadratic versions


# --- already implemented helpers -------------------------------------------------------------------
def test_digits_only():
    assert t.digits_only("554-024-05-49") == "5540240549"
    assert t.digits_only("brak") is None
    assert t.digits_only("9994328o29") == "9994328029"  # scan pelnomocnictwa/0008: OCR letter o between digits
    # review: ASCII digits only (validators reject others), numbers from an LLM keep their digits, None stays None
    assert t.digits_only("５５４０２４０５４９") == "5540240549"
    assert t.digits_only("١٢٣") is None
    assert t.digits_only(85071204278.0) == "85071204278"  # was '850712042780'
    assert t.digits_only(None) is None
    assert t.digits_only(True) is None


def test_collapse_letter_spacing():
    assert t.collapse_letter_spacing("P E Ł N O M O C N I C T W O") == "PEŁNOMOCNICTWO"
    assert t.collapse_letter_spacing("w dniu 3 lipca") == "w dniu 3 lipca"


def test_fold_diacritics():
    assert t.fold_diacritics("ODWOŁANIE Odwołujący") == "ODWOLANIE Odwolujacy"
