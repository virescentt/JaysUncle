"""Rule-based extractor (extract/rules.py): labels, sections, arrays, strategies.

Unit tests build tiny document classes inline; integration tests use the real config/doc_types/*.yaml on short
texts written like the training documents and the OCR of the real scans (lost diacritics, glued words).
"""

from pathlib import Path

import pytest

from odwolania2json.extract.merge import merge
from odwolania2json.extract.rules import STRATEGIES, extract_rules
from odwolania2json.models import Document, Page
from odwolania2json.settings import ExtractionSettings
from odwolania2json.spec import DocTypeSpec, load_registry


def doc(*pages: str) -> Document:
    return Document(path=Path("test.pdf"), pages=[Page(number=i + 1, text=t) for i, t in enumerate(pages)])


def spec(output: dict) -> DocTypeSpec:
    return DocTypeSpec.model_validate({"id": "t", "name": "T", "classification": {}, "output": output})


def values(document: Document, doc_spec: DocTypeSpec) -> dict:
    """{path: first candidate value} — candidates come in priority order."""
    out: dict = {}
    for c in extract_rules(document, doc_spec):
        out.setdefault(c.path, c.value)
    return out


@pytest.fixture(scope="module")
def registry():
    return load_registry()


def merged(registry, doc_type: str, *pages: str) -> dict:
    """Final JSON (rules → merge → normalizers) for a document of a real class."""
    dt = registry.doc_types[doc_type]
    document = doc(*pages)
    data, _ = merge(document, dt, registry, extract_rules(document, dt, registry), ExtractionSettings())
    return data


# ------------------------------------------------------------------------------------------------- labels
LABELS = spec(
    {
        "p": {
            "type": "object",
            "properties": {
                "kwota": {"type": "number", "labels": ["Kwota przelewu", "Kwota"]},
                "data": {"type": "date", "labels": ["Data wykonania", "Data operacji"]},
                "tytul": {"labels": ["Tytułem", "Tytuł"], "max_lines": 3, "stop": ["Status"]},
                "konto": {"labels": ["Rachunek odbiorcy"], "value_pattern": r"(?:PL)?\d{2}(?: ?\d{4}){6}"},
                "nr": {"labels": ["Numer transakcji"], "value_pattern": r"(?=[A-Z/-]*\d)[A-Z0-9][A-Z0-9/-]{5,}"},
            },
        }
    }
)


def test_label_same_line_colon_and_dot_leaders():
    v = values(doc("KWOTA PRZELEWU ........ 50,00 PLN\nData operacji: 17.02.2026"), LABELS)
    assert v["p.kwota"] == "50,00 PLN"
    assert v["p.data"] == "17.02.2026"


def test_label_value_on_next_line():
    v = values(doc("Rachunek odbiorcy\nPL68 4382 2304 9992 2952 6283 4736\nTytułem\nopłata skarbowa"), LABELS)
    assert v["p.konto"] == "PL68 4382 2304 9992 2952 6283 4736"
    assert v["p.tytul"] == "opłata skarbowa"


def test_label_split_over_lines_by_ocr():
    v = values(doc("Kwota\nprzelewu\n10 000,00 PLN\nData\nwykonania\n2026-03-26"), LABELS)
    assert v["p.kwota"] == "10 000,00 PLN"
    assert v["p.data"] == "2026-03-26"


def test_label_split_around_value_by_ocr():
    """Narrow label column: the value sits between the label words ('Numer 305494' / 'transakcji')."""
    v = values(doc("Kwota 10 000,00 PLN\nprzelewu\nData 2026-03-26\nwykonania\nNumer 305494\ntransakcji"), LABELS)
    assert v["p.kwota"] == "10 000,00 PLN"
    assert v["p.data"] == "2026-03-26"
    assert v["p.nr"] == "305494"


def test_label_diacritics_insensitive_value_keeps_original_characters():
    v = values(doc("Tytutem opłata skarbowa od pełnomocnictwa"), LABELS)  # OCR: 'ł' read as 't'
    assert v["p.tytul"] == "opłata skarbowa od pełnomocnictwa"


def test_label_multiline_value_until_stop_or_next_label():
    text = "Tytuł opłata skarbowa dla Weronika\nChmielewski w przetargu SWZ.613.2026\nCEN2610289710500\nStatus Wykonany"
    assert values(doc(text), LABELS)["p.tytul"] == (
        "opłata skarbowa dla Weronika Chmielewski w przetargu SWZ.613.2026 CEN2610289710500"
    )
    text = "Tytułem\nopłata skarbowa\nZP.447.2026\nKwota\n17,00 PLN"  # 'Kwota' is a label of the class
    assert values(doc(text), LABELS)["p.tytul"] == "opłata skarbowa ZP.447.2026"


def test_label_inside_running_text_is_ignored():
    v = values(doc("wykonano przelew w kwocie 17,00 na rachunek odbiorcy wskazany w umowie"), LABELS)
    assert "p.kwota" not in v and "p.konto" not in v


def test_label_column_header_row_value_below():
    """'Kwota Data transakcji' / '7 500,00 PLN 11.09.2026 …': each field takes its shape from the row below."""
    v = values(doc("Kwota Data operacji Numer transakcji\n7 500,00 PLN 11.09.2026 CEN9057564440515"), LABELS)
    assert "7 500,00 PLN" in v["p.kwota"]
    assert v["p.data"] == "11.09.2026"
    assert v["p.nr"] == "CEN9057564440515"


def test_label_shaped_value_on_next_line_when_line_has_none():
    v = values(doc("Data operacji PRZELEW ELIXIR -17,00 PLN\n2026-04-09 Na rachunek: 43 1030"), LABELS)
    assert v["p.data"] == "2026-04-09"


def test_label_value_pattern_requires_a_single_match():
    v = values(doc("Rachunek odbiorcy\n12 3456 7890 1234 5678 9012 3456 98 7654 3210 9876 5432 1098 7654"), LABELS)
    assert "p.konto" not in v  # two accounts in the value line: ambiguous → nothing


def test_candidates_carry_evidence():
    cands = extract_rules(doc("strona 1", "Kwota: 200,00 PLN"), LABELS)
    c = next(c for c in cands if c.path == "p.kwota")
    assert c.evidence.page == 2 and "200,00" in c.evidence.snippet
    assert c.method == "rules:label" and c.note == "Kwota"


# ------------------------------------------------------------------------------------------------- sections
PARTIES = spec(
    {
        "Odw": {
            "type": "object",
            "section": {"start": r"^\s*Odwołując\w*\s*:", "end": r"^\s*Zamawiając\w*\s*:|^\s*ODWOŁANIE\s*$"},
            "properties": {"nip": {"patterns": [r"NIP[\s:]*(?P<value>\d{10})"]}},
        },
        "Zam": {
            "type": "object",
            "section": {"start": r"^\s*Zamawiając\w*\s*:", "end": r"^\s*Odwołując\w*\s*:|^\s*ODWOŁANIE\s*$"},
            "properties": {"nip": {"patterns": [r"NIP[\s:]*(?P<value>\d{10})"]}},
        },
    }
)


@pytest.mark.parametrize("zam_first", [False, True])
def test_section_keeps_blocks_apart_in_any_order(zam_first):
    odw = "Odwołujący:\nAlfa S.A.\nNIP: 1111111111\n"
    zam = "Zamawiający:\nGmina Beta\nNIP: 2222222222\n"
    v = values(doc((zam + odw if zam_first else odw + zam) + "ODWOŁANIE\nNIP: 3333333333"), PARTIES)
    assert v["Odw.nip"] == "1111111111"
    assert v["Zam.nip"] == "2222222222"


def test_section_ocr_without_diacritics():
    v = values(doc("Odwolujacy:\nAlfa S.A.\nNIP: 1111111111\nZamawiajacy:\nGmina\nNIP: 2222222222"), PARTIES)
    assert v == {"Odw.nip": "1111111111", "Zam.nip": "2222222222"}


def test_missing_section_gives_no_candidates():
    assert values(doc("NIP: 1111111111"), PARTIES) == {}


# ------------------------------------------------------------------------------------------------- arrays
ARRAYS = spec(
    {
        "Zam": {
            "type": "array",
            "section": {"start": r"^\s*Zamawiając\w*\s*(:|\()", "end": r"^\s*Dotyczy"},
            "item_split": r"^\s*\d\s*\)\s*",
            "items": {
                "type": "object",
                "properties": {
                    "nazwa": {"strategy": "first_line"},
                    "nip": {"patterns": [r"NIP[\s:]*(?P<value>\d{10})"]},
                    "lider": {"type": "integer", "strategy": "item_flag", "params": {"pattern": "prowadząc"}},
                },
            },
        },
        "Odw": {
            "type": "array",
            "max_items": 1,
            "section": {"start": r"^\s*Odwołując\w*\s*:", "end": r"^\s*Zamawiając"},
            "item_split": r"^\s*\d\s*\)\s*",
            "item_prefer": "lider",
            "items": {"type": "object", "properties": {"nip": {"patterns": [r"NIP[\s:]*(?P<value>\d{10})"]}}},
        },
    }
)


def test_array_items_from_item_split():
    text = (
        "Zamawiający (wspólnie):\n1) Zamawiający – Lider, prowadzący postępowanie:\nMiasto Alfa\nNIP: 1111111111\n"
        "2) Zamawiający – Partner:\nSpółka Beta S.A.\nNIP: 2222222222\nDotyczy: postępowania"
    )
    v = values(doc(text), ARRAYS)
    assert v["Zam[0].nazwa"] == "Miasto Alfa" and v["Zam[1].nazwa"] == "Spółka Beta S.A."
    assert v["Zam[0].nip"] == "1111111111" and v["Zam[1].nip"] == "2222222222"
    assert v["Zam[0].lider"] == 1 and v["Zam[1].lider"] == 0


def test_array_single_item_has_no_leader_flag():
    v = values(doc("Zamawiający: Gmina Alfa\nNIP: 1111111111\nDotyczy: x"), ARRAYS)
    assert v["Zam[0].nazwa"] == "Gmina Alfa" and "Zam[0].lider" not in v and "Zam[1].nip" not in v


def test_array_max_items_keeps_preferred_item_consortium_leader():
    text = "Odwołujący: Konsorcjum:\n1) Partner sp. z o.o.\nNIP: 2222222222\n2) Lider S.A. (Lider)\nNIP: 1111111111\n"
    v = values(doc(text + "Zamawiający: X"), ARRAYS)
    assert v["Odw[0].nip"] == "1111111111" and "Odw[1].nip" not in v


def test_optional_section_absent_means_one_unsplit_item():
    s = spec(
        {
            "m": {
                "type": "array",
                "section": {"start": "Konsorcjum:", "optional": True},
                "item_split": r"\d\)\s",
                "items": {"type": "object", "properties": {"krs": {"patterns": [r"KRS (?P<value>\d{10})"]}}},
            }
        }
    )
    v = values(doc("Alfa S.A., KRS 1234567890 upoważnia do: 1) wniesienia odwołania, 2) cofnięcia"), s)
    assert v == {"m[0].krs": "1234567890"}
    v = values(doc("Konsorcjum: 1) A KRS 1111111111; 2) B KRS 2222222222"), s)
    assert v == {"m[0].krs": "1111111111", "m[1].krs": "2222222222"}


# ------------------------------------------------------------------------------------------------- strategies
def test_strategies_registered():
    names = {"first_line", "persons", "signatories", "flag", "item_flag", "entity_type", "dictionary", "date"}
    assert names <= set(STRATEGIES)
    assert STRATEGIES["persons"].leaf and STRATEGIES["persons"].array and STRATEGIES["signatories"].array


def test_first_line_joins_unfinished_name_and_cuts_address():
    s = spec(
        {"z": {"type": "object", "section": {"start": "Zamawiający:"}, "properties": {
            "nazwa": {"strategy": "first_line", "params": {"cut": [r",\s*ul\."]}}}}}
    )  # fmt: skip
    text = "Zamawiający: Komenda Wojewódzka Straży Pożarnej w\nJarzębinie\nul. Piłsudskiego 60B"
    assert values(doc(text), s)["z.nazwa"] == "Komenda Wojewódzka Straży Pożarnej w Jarzębinie"
    text = "Zamawiający:\nKancelaria Adwokacka Spółka z ograniczoną\nodpowiedzialnością, ul. Jasionowa 19"
    assert values(doc(text), s)["z.nazwa"] == "Kancelaria Adwokacka Spółka z ograniczoną odpowiedzialnością"
    assert "z.nazwa" not in values(doc("Zamawiający:\nul. Polna 1\n00-001 Warszawa"), s)  # address, no name


PERSONS = spec(
    {
        "pierwszy": {"strategy": "persons", "params": {"after": [r"reprezentowan\w*\s*przez"], "until": ["adres"]}},
        "lista": {
            "type": "array",
            "strategy": "persons",
            "params": {"after": [r"udziela\w*\s+pełnomocnictwa\s*:?"], "until": [r"do\s+reprezentowania"]},
            "items": {
                "type": "object",
                "properties": {
                    "imie_nazwisko": {"strategy": "item_text"},
                    "pesel": {"patterns": [r"PESEL[\s:]*(?P<value>\d{11})"]},
                },
            },
        },
    }
)


def test_persons_first_attorney_one_per_line_and_inline():
    text = "reprezentowany przez pełnomocnika:\nradca prawny Aleksandra Rybak\nadw. Wojciech Kaczmarek\nadres: x"
    assert "Aleksandra Rybak" in values(doc(text), PERSONS)["pierwszy"]
    text = "reprezentowana przez: r. pr. Jolanta\nZajkowski, adw. Jan Nowak\nadres: x"  # name wrapped over lines
    assert values(doc(text), PERSONS)["pierwszy"].replace("\n", " ") == "r. pr. Jolanta Zajkowski"


def test_persons_array_items_keep_their_pesel():
    text = (
        "niniejszym udziela pełnomocnictwa:\nAnna Topolska (pracownik, PESEL: 68091502944) oraz Piotr Wróbel "
        "(radca prawny, nr wpisu WA-1), Jan Kowalski\ndo reprezentowania Mocodawcy"
    )
    v = values(doc(text), PERSONS)
    assert v["lista[0].imie_nazwisko"].startswith("Anna Topolska") and v["lista[0].pesel"] == "68091502944"
    assert v["lista[1].imie_nazwisko"].startswith("Piotr Wróbel") and "lista[1].pesel" not in v
    assert v["lista[2].imie_nazwisko"] == "Jan Kowalski"


def test_signatories_skip_attorney_column_and_split_side_by_side_names(registry):
    data = merged(
        registry,
        "pelnomocnictwo",
        "PEŁNOMOCNICTWO\nAlfa sp. z o.o. z siedzibą w Gdańsku, KRS 1234567890, udziela pełnomocnictwa r. pr. "
        "Cezary Walczak do reprezentowania.\nMocodawca: Pełnomocnictwo przyjmuję:\n......... .........\n"
        "Halina Mazurkiewicz r. pr. Cezary Walczak\nCzłonek Zarządu\n.........\n"
        "Zbigniew Dobrowolski Marek Rutkiewicz\nPrezes Zarządu Prokurent",
    )
    signers = [p["imie_nazwisko"] for p in data["mocodawca"][0]["podpisujacy"]]
    assert signers == ["Halina Mazurkiewicz", "Zbigniew Dobrowolski", "Marek Rutkiewicz"]
    assert [p["imie_nazwisko"] for p in data["pelnomocnicy"]] == ["Cezary Walczak"]


def test_signatories_consortium_groups_go_to_their_party(registry):
    data = merged(
        registry,
        "pelnomocnictwo",
        "PEŁNOMOCNICTWO\nWykonawcy wspólnie ubiegający się o udzielenie zamówienia (Konsorcjum): 1) Torvex "
        "Budownictwo sp. z o.o., ul. Polna 8, KRS 9995540127; 2) Hydromel Instalacje sp.j., ul. Lipowa 15, "
        "KRS 9997108834 (dalej łącznie: „Mocodawca”), udzielają pełnomocnictwa: adw. Piotr Zalewski.\n"
        "W imieniu Torvex Budownictwo sp. z o.o.:\n........\nMarek Domański\nPrezes Zarządu\n"
        "W imieniu Hydromel Instalacje sp.j.:\n........\nAgnieszka Kurek\nWspólnik\n........\nRobert Kurek\n"
        "Pełnomocnictwo przyjmuję:\n........\nadw. Piotr Zalewski",
    )
    m = data["mocodawca"]
    assert [x["data"]["nazwa"] for x in m] == ["Torvex Budownictwo sp. z o.o.", "Hydromel Instalacje sp.j."]
    assert [x["data"]["krs"] for x in m] == ["9995540127", "9997108834"]
    assert [p["imie_nazwisko"] for p in m[0]["podpisujacy"]] == ["Marek Domański"]
    assert [p["imie_nazwisko"] for p in m[1]["podpisujacy"]] == ["Agnieszka Kurek", "Robert Kurek"]


FLAGS = spec(
    {
        "rep": {
            "type": "integer",
            "strategy": "flag",
            "params": {"patterns": [r"reprezentowan\w*\s*przez"], "absent_patterns": [r"w imieniu własnym"]},
        },
        "typ": {
            "strategy": "entity_type",
            "params": {"natural": ["CEIDG"], "legal": ["KRS"], "natural_value": "fiz", "legal_value": "praw"},
        },
    }
)


def test_flag_and_entity_type():
    assert values(doc("Alfa S.A., KRS 1, reprezentowana przez: Jan Nowak"), FLAGS) == {"rep": 1, "typ": "praw"}
    assert values(doc("Działając w imieniu własnym, wpis do CEIDG"), FLAGS) == {"rep": 0, "typ": "fiz"}
    assert values(doc("Odwołujący: Jan Nowak"), FLAGS)["typ"] == "praw"  # no evidence → weak default
    assert "rep" not in values(doc("Odwołujący: Jan Nowak"), FLAGS)  # no evidence, no absent_confidence


# ------------------------------------------------------------------------------------------------- integration
APPEAL_SCAN = """Kancelaria Radcy Prawnego Krzysztof Jodłowski
Wrocław, 13.02.2026 r.
Prezes Krajowej Izby Odwoławczej
Odwolujacy: Arkomed Serwis S.A.
ul. Kwiatowa 116/31
15-629 Białystok
KRS: 9990692039, NIP: 999-649-46-61
reprezentowany przez petnomocnika:
radca prawny Krzysztof Jodłowski
adres do korespondencji:
Kancelaria Radcy Prawnego Krzysztof Jodłowski
ul. Jana Pawła II 73A, 30-085 Kraków
e-mail: k.jodlowski@jodlowski-kancelaria.example
Zamawiajacy: Zamawiajacy wspólnie udzielajacy zamówienia:
1) Zarząd Dróg Powiatowych w Brzozowcu
NIP: 999-239-80-73
e-mail: zamowienia@brzozowiec.example
- Zamawiający prowadzący postępowanie
2) Zakład Gospodarki Komunalnej w Zielonkowie
REGON: 999806152
Dotyczy: postępowania prowadzonego w trybie przetargu nieograniczonego pn. Rozbudowa oczyszczalni ścieków
wraz z modernizacją sieci kanalizacyjnej”, nr referencyjny: DZP.260.69.2026, ogłoszenie o zamówieniu nr
2026/BZP 00993292/01
ODWOŁANIE
Działając w imieniu Odwołującego, wnoszę odwołanie wobec:
1. czynności unieważnienia postępowania, o której Zamawiający poinformował wykonawców w dniu 4 lutego 2026 roku;
2. zaniechania odrzucenia oferty wykonawcy X;
Informacja o zaskarżonej czynności została opublikowana w dniu 04.02.2026 r.
UZASADNIENIE
W dniu 1 marca 2025 r. Zamawiający zaniechał wykluczenia (ogłoszenie nr 2025/BZP 00111111)."""


def test_appeal_real_scan_layout(registry):
    d = merged(registry, "odwolanie", APPEAL_SCAN)
    assert d["OdwolanieKIO"] == {
        "data": "2026-02-13",
        "czynnosc_dzien": "2026-02-04",
        "czynnosc_powod_id": 9,
        "czynnosc_opis": "Decyzja o unieważnieniu postępowania",
        "miejscowosc": "Wrocław",
    }
    assert d["Odwolujacy"] == [
        {
            "krs": "9990692039",
            "nazwa": "Arkomed Serwis S.A.",
            "nip": "9996494661",
            "regon": None,
            "przez-pelnomocnika": 1,
            "pelnomocnik": {
                "imie_nazwisko": "Krzysztof Jodłowski",
                "nazwa": "Kancelaria Radcy Prawnego Krzysztof Jodłowski",
                "mail": "k.jodlowski@jodlowski-kancelaria.example",
            },
        }
    ]
    assert [(z["nazwa"], z["nip"], z["regon"], z["prowadzacy_postepowanie"]) for z in d["Zamawiajacy"]] == [
        ("Zarząd Dróg Powiatowych w Brzozowcu", "9992398073", None, 1),
        ("Zakład Gospodarki Komunalnej w Zielonkowie", None, "999806152", 0),
    ]
    assert d["ZamowieniePubliczne"] == {
        "numer_bzp": "2026/BZP00993292/01",  # the BZP number of another procedure in the justification is ignored
        "numer_postepowania": "DZP.260.69.2026",
        "numer_ted": None,
        "tytul": "Rozbudowa oczyszczalni ścieków wraz z modernizacją sieci kanalizacyjnej",
    }


@pytest.mark.parametrize(
    ("charge", "expected"),
    [
        ("zaniechania odrzucenia oferty wykonawcy X, mimo że podlegała odrzuceniu", 7),
        ("odrzucenia oferty Odwołującego, o którym poinformowano w dniu 1 kwietnia 2026 r.", 1),
        ("zaniechania unieważnienia postępowania obarczonego wadą", 10),
        ("unieważnienia postępowania z dnia 9 czerwca 2026 r.", 9),
        ("projektowanych postanowień umowy (PPU), stanowiących załącznik nr 4 do SWZ", 12),
        ("treści Specyfikacji Warunków Zamówienia opublikowanej w dniu 6 września 2026 r.", 4),
        ("treści wyjaśnień SWZ (odpowiedzi na pytania wykonawców) udzielonych w dniu 2 czerwca 2026 r.", 6),
    ],
)
def test_first_charge_dictionary_action_vs_omission(registry, charge, expected):
    d = merged(registry, "odwolanie", f"ODWOŁANIE\nwnoszę odwołanie wobec:\n1. {charge};\n2. wyboru oferty;")
    assert d["OdwolanieKIO"]["czynnosc_powod_id"] == expected


def test_first_charge_sentence_before_charges(registry):
    text = "ODWOŁANIE\nZamawiający w dniu 16 lutego 2026 roku nie wykluczył z postępowania wykonawcy X.\nZarzut nr 1"
    k = merged(registry, "odwolanie", text)["OdwolanieKIO"]
    assert (k["czynnosc_powod_id"], k["czynnosc_dzien"]) == (8, "2026-02-16")


def test_appeal_without_attorney_acting_on_own_behalf(registry):
    text = "Odwołujący:\nAlfa S.A.\nKRS: 1234567890\nZamawiający:\nGmina\nODWOŁANIE\nDziałając w imieniu własnym"
    odw = merged(registry, "odwolanie", text)["Odwolujacy"][0]
    assert odw["przez-pelnomocnika"] == 0 and odw["pelnomocnik"]["imie_nazwisko"] is None


@pytest.mark.parametrize(
    "text",
    [
        # label + value on the same line, multi-line title stopped by the next label
        "POTWIERDZENIE WYKONANIA PRZELEWU\nRachunek zleceniodawcy PL47 2692 7374 9064 1859 9264 1235\n"
        "Rachunek odbiorcy PL15 5797 6676 2935 6135 3276 8366\nKwota 34,00 PLN\nData operacji 16 marca 2026\n"
        "Tytuł przelewu opłata skarbowa w\npostępowaniu ZP/1 CEN2603185520647\nNumer referencyjny CEN2603185520647",
        # bank letter (prose)
        "Bank potwierdza, że w dniu 16.03.2026 z rachunku nr 47 2692 7374 9064 1859 9264 1235, prowadzonego dla X, "
        "wykonano przelew krajowy w kwocie 34,00 PLN na rachunek nr 15 5797 6676 2935 6135 3276\n8366, prowadzony "
        "dla Y.\nTytuł przelewu: „opłata skarbowa w\npostępowaniu ZP/1 CEN2603185520647”.\n"
        "Numer referencyjny operacji: CEN2603185520647.",
        # two columns: sender left, recipient right
        "Potwierdzenie przelewu\nPłatnik Beneficjent\nAlfa S.A. Beta sp. j.\n"
        "47269273749064185992641235 15579766762935613532768366\nKwota operacji 34,00 zł\n"
        "Data operacji 2026-03-16\nNumer transakcji CEN2603185520647\n"
        "Tytuł opłata skarbowa w\npostępowaniu ZP/1 CEN2603185520647\nStatus Przelew zrealizowany",
    ],
)
def test_transfer_layouts(registry, text):
    assert merged(registry, "przelew", text)["przelew_bankowy"] == {
        "data_operacji": "2026-03-16",
        "kwota": 34.0,
        "na_rachunek": "15579766762935613532768366",
        "numer_transakcji": "CEN2603185520647",
        "tytul": "opłata skarbowa w postępowaniu ZP/1 CEN2603185520647",
        "z_rachunku": "47269273749064185992641235",
    }


def test_transfer_real_scan_table(registry):
    text = (
        "Bank Nadwiślański\nOpis\nwpis od odwołania - postępowanie nr DA.ZP.242.50.2026\n"
        "Kwota Data transakcji Numer referencyjny transakcji\n7 500,00 PLN 11.09.2026 CEN9057564440515\n"
        "Dane nadawcy\nNazwa i adres Numer rachunku Nazwa banku\n"
        "ALFA SP. K., UL. 62 2170 0008 9152 5017 5661 7062 BANK\n"
        "Dane odbiorcy\nNazwa i adres Numer rachunku Nazwa banku\n"
        "Urząd Zamówień Publicznych, ul. Postępu 17A, 60 1010 1010 0081 3622 3100 0000"
    )
    assert merged(registry, "przelew", text)["przelew_bankowy"] == {
        "data_operacji": "2026-09-11",
        "kwota": 7500.0,
        "na_rachunek": "60101010100081362231000000",
        "numer_transakcji": "CEN9057564440515",
        "tytul": "wpis od odwołania - postępowanie nr DA.ZP.242.50.2026",
        "z_rachunku": "62217000089152501756617062",
    }


def test_power_of_attorney_real_scan_layout(registry):
    text = (
        "Gdańsk, 27.03.2026 r.\nPELNOMOCNICTWO\nMedicor IT Solutions S.A. z siedzibą w Gdańsk (80-386), "
        "ul. Marszałkowska 37, wpisana do\nrejestru pod numerem KRS: 9991919038, NIP:\n9992326754, REGON: "
        '999749508, reprezentowana przez: Zbigniew Cieślak – Prokurent (dalej:\nMocodawca"), niniejszym udziela '
        "pełnomocnictwa:\nGrzegorz Kaczmarek (adwokat, nr wpisu PO-17476) oraz Katarzyna Sikora(radca prawny, nr\n"
        "wpisu WR-12316) oraz Karolina Baran (radca prawny, PESEL: 85071204278),\ndo reprezentowania Mocodawcy.\n"
        "Signature Not Verified\nDokument podpisany przez Zbigniew Cieślak"
    )
    assert merged(registry, "pelnomocnictwo", text) == {
        "mocodawca": [
            {
                "data": {
                    "imie_nazwisko": None,
                    "krs": "9991919038",
                    "nazwa": "Medicor IT Solutions S.A.",
                    "nip": "9992326754",
                    "typ": "osoba_prawna",
                },
                "podpisujacy": [{"imie_nazwisko": "Zbigniew Cieślak"}],
            }
        ],
        "pelnomocnicy": [
            {"imie_nazwisko": "Grzegorz Kaczmarek", "pesel": None},
            {"imie_nazwisko": "Katarzyna Sikora", "pesel": None},
            {"imie_nazwisko": "Karolina Baran", "pesel": "85071204278"},
        ],
    }


def test_power_of_attorney_natural_person(registry):
    text = (
        "PEŁNOMOCNICTWO\nJa, niżej podpisany Jarosław Dudek, prowadzący działalność gospodarczą pod firmą DUDEK-TECH\n"
        "Jarosław Dudek, ul. Nadbystrzycka 40, 20-618 Lublin, NIP 9994150873, wpisany do Centralnej Ewidencji "
        "(dalej: „Mocodawca”), niniejszym udzielam pełnomocnictwa: adw. Natalia Górecka (nr wpisu: LU/1).\n"
        "Mocodawca:\n.........\nJarosław Dudek\nPełnomocnictwo przyjmuję:\n.........\nadw. Natalia Górecka"
    )
    m = merged(registry, "pelnomocnictwo", text)["mocodawca"][0]
    assert m["data"] == {
        "imie_nazwisko": "Jarosław Dudek",
        "krs": None,
        "nazwa": "DUDEK-TECH Jarosław Dudek",
        "nip": "9994150873",
        "typ": "osoba_fizyczna",
    }
    assert m["podpisujacy"] == [{"imie_nazwisko": "Jarosław Dudek"}]


def test_config_check_knows_strategies(registry):
    assert registry.check() == []
    bad = DocTypeSpec.model_validate(
        {"id": "x", "name": "X", "classification": {}, "output": {"a": {"strategy": "nie_ma_takiej"}}}
    )
    registry2 = registry.model_copy(update={"doc_types": {"x": bad}})
    assert any("unknown strategy 'nie_ma_takiej'" in p for p in registry2.check())


# ------------------------------------------------------------------------------------------------- review regressions
# Inputs from the review of the rules extractor: each produced a WRONG value (or a crash / quadratic time) before.
SCAN_PARTY = (
    "Poznań, dnia 11 września 2026 roku\nOdwołujący: Asterion Budownictwo sp. k.\nul. Jana Pawła II 11A/15\n"
    "20-380 Lublin\nKRS: 9991022552, NIP:999-295-65-50, REGON: 999913617\n"
)
SCAN_REST = (
    "Zamawiający: Powiatowy Zespół Szkół Technicznych w Zielonkowie\nul. Marszałkowska 97B/13\n32-235 Zielonków\n"
    "Dotyczy: postępowania pn. Odbiór odpadów”, nr referencyjny: DA.ZP.242.50.2026\nODWOŁANIE\n"
)


def test_numbered_attorneys_are_not_consortium_members(registry):
    """'1) radca prawny …' below a complete party block is a list of attorneys, not of parties: before the fix
    the item split made 'radca prawny Aleksandra Rybak' the name of the Odwołujący and lost its KRS/NIP/REGON."""
    attorneys = "reprezentowany przez pełnomocnika:\n1) radca prawny Aleksandra Rybak\n2) adw. Wojciech Kaczmarek\n"
    odw = merged(registry, "odwolanie", SCAN_PARTY + attorneys + SCAN_REST)["Odwolujacy"][0]
    assert (odw["nazwa"], odw["krs"], odw["nip"], odw["regon"]) == (
        "Asterion Budownictwo sp. k.", "9991022552", "9992956550", "999913617"
    )  # fmt: skip
    assert odw["przez-pelnomocnika"] == 1 and odw["pelnomocnik"]["imie_nazwisko"] == "Aleksandra Rybak"


def test_item_split_needs_list_heading_not_entity_details():
    v = values(doc("Odwołujący: Alfa S.A., ul. Polna 1, 80-001 Gdańsk\nNIP: 1111111111\nreprezentowana przez:\n"
                   "1) adw. Jan Nowak\n2) adw. Anna Kos\nZamawiający: X"), ARRAYS)  # fmt: skip
    assert v["Odw[0].nip"] == "1111111111"
    text = "Odwołujący: Konsorcjum wykonawców wspólnie ubiegających się w\nskładzie:\n1) A (Lider)\nNIP: 1111111111\n"
    assert values(doc(text + "2) B\nNIP: 2222222222\nZamawiający: X"), ARRAYS)["Odw[0].nip"] == "1111111111"


def test_board_representation_is_not_an_attorney(registry):
    """'reprezentowana przez Prezesa Zarządu …' is the board, not a pełnomocnik: no flag 1, no name, no e-mail."""
    text = (
        SCAN_PARTY + "reprezentowana przez Prezesa Zarządu Jan Kowalski\ne-mail: biuro@asterion.example\n" + SCAN_REST
    )
    odw = merged(registry, "odwolanie", text)["Odwolujacy"][0]
    assert odw["przez-pelnomocnika"] is None
    assert odw["pelnomocnik"] == {"imie_nazwisko": None, "nazwa": None, "mail": None}


@pytest.mark.parametrize(
    "header", ["Wykonawca, którego oferta została wybrana:", "Przystępujący po stronie Zamawiającego", "Uczestnik:"]
)
def test_party_blocks_end_at_other_participants(registry, header):
    """The KRS / NIP / REGON of the selected contractor or an intervener are not the Zamawiający's."""
    other = f"{header}\nTermex Instal S.A., ul. Polna 3, 00-100 Warszawa\nKRS: 0000999999, NIP: 5250001111\n"
    rest = SCAN_REST.replace("Dotyczy:", other + "Dotyczy:")
    zam = merged(registry, "odwolanie", SCAN_PARTY + rest)["Zamawiajacy"][0]
    assert (zam["krs"], zam["nip"]) == (None, None)


APPEAL_HEAD = "Warszawa, 5 marca 2024 r.\nOdwołujący:\nAlfa sp. z o.o.\nZamawiający:\nGmina Beta\nODWOŁANIE\n"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        # the action follows the anchor on its line; the numbered list below lists the violated provisions
        ("wnoszę odwołanie od czynności Zamawiającego z dnia 2 stycznia 2024 r. polegającej na wyborze jako "
         "najkorzystniejszej oferty złożonej przez XYZ S.A., co nastąpiło z naruszeniem:\n1) art. 226 ust. 1 pkt 5 "
         "Pzp poprzez zaniechanie odrzucenia oferty XYZ S.A. z dnia 20 grudnia 2023 r.;\n2) art. 239 Pzp.", (2, None)),
        # 'r.' followed by a lower-case word does not end the sentence of the 'Zamawiający w dniu' anchor
        ("Zamawiający w dniu 16 lutego 2026 r. nie wykluczył z postępowania wykonawcy X.", (8, "2026-02-16")),
        # the date of the act cited in the charge is not the date of the action
        ("Zarzut nr 1 – naruszenie art. 226 ust. 1 pkt 5 ustawy z dnia 11 września 2019 r. Prawo zamówień "
         "publicznych poprzez zaniechanie odrzucenia oferty XYZ;\nZarzut nr 2 – inne.", (7, None)),
        # unchanged: the list after an anchor that ends its line
        ("wnoszę odwołanie od czynności Zamawiającego polegających na:\n1) odrzuceniu oferty Odwołującego, o czym "
         "Zamawiający poinformował w dniu 2 stycznia 2024 r.;\n2) wyborze oferty XYZ.", (1, "2024-01-02")),
    ],
)  # fmt: skip
def test_first_charge_variants(registry, body, expected):
    k = merged(registry, "odwolanie", APPEAL_HEAD + body + "\nUZASADNIENIE\nW dniu 1 marca 2023 r. wybrano ofertę.")
    assert (k["OdwolanieKIO"]["czynnosc_powod_id"], k["OdwolanieKIO"]["czynnosc_dzien"]) == expected


def test_numer_postepowania_value_needs_a_digit(registry):
    """'Numer referencyjny postępowania: ZP…' gave the pattern value 'postępowania' (confidence above the label)."""
    text = APPEAL_HEAD.replace("ODWOŁANIE", "Numer referencyjny postępowania: ZP.271.1.2024\nODWOŁANIE")
    assert merged(registry, "odwolanie", text)["ZamowieniePubliczne"]["numer_postepowania"] == "ZP.271.1.2024"


def test_inflected_person_names_are_weak_and_merged_with_nominative(registry):
    """Dative attorneys ('adwokatowi Janowi Kowalskiemu') are not names in the nominative → null, not a wrong
    value; the genitive 'Jana Nowaka' from 'reprezentowana przez' is the signer 'Jan Nowak', not a second one."""
    text = (
        "PEŁNOMOCNICTWO\nAlfa Sp. z o.o. z siedzibą w Warszawie, ul. Polna 1, KRS 0000123456, reprezentowana przez "
        "Jana Nowaka – Prezesa Zarządu, udziela pełnomocnictwa:\nadwokatowi Janowi Kowalskiemu oraz radcy prawnej "
        "Annie Kowalskiej\ndo reprezentowania Mocodawcy.\n..........\nJan Nowak – Prezes Zarządu"
    )
    data = merged(registry, "pelnomocnictwo", text)
    assert data["mocodawca"][0]["podpisujacy"] == [{"imie_nazwisko": "Jan Nowak"}]
    assert all(p["imie_nazwisko"] is None for p in data["pelnomocnicy"])


def test_signature_block_does_not_start_at_wrapped_za_line(registry):
    """'za Mocodawcę …' at the start of a wrapped body line is not the signature block (params are matched
    case-insensitively — the YAML uses (?-i:…) for 'Za Firma' lines)."""
    text = (
        "PEŁNOMOCNICTWO\nAlfa S.A. z siedzibą w Gdańsku, KRS 1234567890, udziela pełnomocnictwa do składania\n"
        "za Mocodawcę wszelkich oświadczeń w postępowaniu odwoławczym:\nJan Kowalski\nGdańsk, 1 marca 2026 r.\n"
        "..........\nMarek Nowak\nPrezes Zarządu"
    )
    data = merged(registry, "pelnomocnictwo", text)
    assert data["mocodawca"][0]["podpisujacy"] == [{"imie_nazwisko": "Marek Nowak"}]


def test_exact_label_beats_label_with_extra_words(registry):
    """'Kwota prowizji: 0,00 PLN' matched the label 'Kwota' before 'Kwota: 7 500,00 PLN' → kwota 0.0."""
    text = (
        "Potwierdzenie wykonania przelewu\nKwota prowizji: 0,00 PLN\nKwota: 7 500,00 PLN\nData operacji: 2026-01-05\n"
        "Tytułem: wpis od odwołania w postępowaniu\nnr ZP.271.1.2026\nStatus: zrealizowany"
    )
    p = merged(registry, "przelew", text)["przelew_bankowy"]
    assert p["kwota"] == 7500.0
    assert p["tytul"] == "wpis od odwołania w postępowaniu nr ZP.271.1.2026"  # lower-case 'nr' continues the title


def test_persons_array_trigger_without_names_does_not_crash(caplog):
    """zip(strict=True) of no persons with one end raised ValueError (logged as an exception per document)."""
    text = "niniejszym udziela pełnomocnictwa:\n..........\nPrezes Zarządu\ndo reprezentowania Mocodawcy"
    with caplog.at_level("ERROR"):
        assert values(doc(text), PERSONS) == {}
    assert not caplog.records


def test_long_runs_of_blank_lines_are_not_quadratic(registry):
    """10 000 empty lines made every '^\\s*' pattern backtrack quadratically (6.7 s per document)."""
    import time

    document = doc("ODWOŁANIE\n" + "\n" * 20000 + "Odwołujący:\nAlfa S.A.\nNIP: 1111111111", "  \n" * 5000)
    t0 = time.perf_counter()
    for dt in registry.doc_types.values():
        extract_rules(document, dt, registry)
    assert time.perf_counter() - t0 < 3.0


def test_windows_line_ends_and_unicode_spaces(registry):
    """'\\r\\n' line ends and narrow / thin no-break spaces (thousands separators) broke '[ \\t]*$' and '[ ]?\\d'."""
    nnbsp, thin = "\N{NARROW NO-BREAK SPACE}", "\N{THIN SPACE}"
    text = f"Potwierdzenie przelewu\r\nRachunek odbiorcy\r\nPL15{nnbsp}5797 6676 2935 6135 3276 8366\r\n"
    text += f"Kwota:{thin}17,00 PLN"
    p = merged(registry, "przelew", text)["przelew_bankowy"]
    assert (p["na_rachunek"], p["kwota"]) == ("15579766762935613532768366", 17.0)


def test_first_charge_sentence_wrapped_after_abbreviation(registry):
    """OCR wraps lines anywhere: '… 2026 r.' ⏎ 'nie wykluczył …' is one sentence (the period ended it before)."""
    body = "Zamawiający w dniu 16 lutego 2026 r.\nnie wykluczył z postępowania wykonawcy X.\nUZASADNIENIE\n"
    k = merged(registry, "odwolanie", APPEAL_HEAD + body)["OdwolanieKIO"]
    assert (k["czynnosc_powod_id"], k["czynnosc_dzien"]) == (8, "2026-02-16")


def test_merge_derived_field_with_unknown_dictionary_is_null_not_a_crash(registry):
    """A YAML naming a missing dictionary (a config error reported by Registry.check) raised KeyError in merge,
    which lost the whole document."""
    dt = spec({"a": {"type": "integer", "patterns": [r"a (?P<value>\d)"]},
               "b": {"derived_from": "a", "dictionary": "nie_ma_takiego"}})  # fmt: skip
    document = doc("a 1")
    data, _ = merge(document, dt, registry, extract_rules(document, dt, registry), ExtractionSettings())
    assert data == {"a": "1", "b": None}


@pytest.mark.parametrize("line", ["ul. Polna 1", " ul. Polna 1", "1) ul. Polna 1", "KRS 0000123456"])
def test_first_line_address_after_list_marker_or_indent_is_not_a_name(line):
    """The built-in address / identifier stop began with '^' but is matched at the content start of a line:
    after '1) ' or an indentation it never matched and 'ul. Polna 1' became the name."""
    s = spec({"z": {"type": "object", "section": {"start": "Zamawiający:"}, "properties": {
        "nazwa": {"strategy": "first_line"}}}})  # fmt: skip
    assert values(doc(f"Zamawiający:\n{line}\n00-001 Warszawa"), s) == {}


def test_explicit_zero_confidence_is_kept():
    """'confidence: 0' in YAML (never trust the rules for this field) became the default 0.8 ('0 or 0.8')."""
    s = spec({"a": {"patterns": [r"A: (?P<value>\w+)"], "confidence": 0}})
    (c,) = extract_rules(doc("A: x"), s)
    assert c.confidence == 0.0
