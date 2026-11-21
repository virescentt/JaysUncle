# Wprowadzenie do projektu: nad czym pracujemy (PL)

> Dla osób, które dopiero dołączają. Czytać od góry do dołu, ~15 minut. Wersja rosyjska: [WPROWADZENIE_RU.md](WPROWADZENIE_RU.md)
> Dalej: warianty rozwiązania — [WARIANTY_PL.md](WARIANTY_PL.md), kto co robi — [ZADANIA_PL.md](ZADANIA_PL.md).

---

## Krok 1. Skąd to zadanie (kontekst w minutę)

W Polsce instytucje publiczne kupują towary i usługi w **przetargach** (*zamówienia publiczne*). Jeśli firma uważa, że zamawiający przeprowadził przetarg niezgodnie z prawem (np. bezprawnie odrzucił jej ofertę), wnosi **odwołanie** do **KIO** (*Krajowa Izba Odwoławcza*).

Odwołanie to nie jeden dokument, tylko paczka PDF-ów:
- **odwołanie** — samo pismo: kto się odwołuje, od kogo, w jakim przetargu, co zarzuca;
- **pełnomocnictwo** — firma upoważnia prawnika do działania w jej imieniu;
- **potwierdzenie przelewu** — dowód wpłaty (wpis, opłata skarbowa).

Dziś pracownicy **UZP** (*Urząd Zamówień Publicznych*, organizator zadania) czytają to ręcznie. Chcą, żeby **program sam wyciągał potrzebne dane z PDF-ów** i zapisywał je w formie czytelnej dla maszyn — JSON.

**Nasze zadanie:** PDF na wejściu → poprawny JSON na wyjściu, dla każdego dokumentu.

---

## Krok 2. Jak wygląda jedna sprawa — prawdziwy przykład

Weźmy sprawę nr 1 z danych treningowych organizatora. Wszystkie trzy dokumenty opowiadają jedną historię: firma **Belmoria Rozwiązania S.A.** odwołuje się od zamawiającego **Aurelink Automatyka S.A.** w przetargu **ZS.791.2026**.

Pliki: [na GitHubie organizatora](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/tree/main/dane/przyklad_01) · lokalnie (po `python scripts/fetch_data.py`): `data/uzp/dane/przyklad_01/`

### 2.1. Potwierdzenie przelewu (najprostsze)

Co widzi człowiek w [PDF](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/blob/main/dane/przyklad_01/potwierdzenie_przelewu.pdf):
```
POLECENIE PRZELEWU
Potwierdzenie realizacji polecenia przelewu · Ekobank
Płatnik             Belmoria Rozwiązania S.A.
Rachunek obciążany  PL05470680287581539721455024
Adresat przelewu    Aurelink Automatyka S.A.
Rachunek odbiorcy   PL68438223049992295262834736
Kwota operacji      200,00 PLN
Data transakcji     2026-03-21
Opis                opłata skarbowa od pełnomocnictwa dla Aleksandra Marciniak i Jolanta Zajkowski
                    w postępowaniu przed KIO w przetargu ZS.791.2026 CEN2604016344389
Nr ref.             CEN2604016344389
```
Co ma zwrócić program ([wzorzec organizatora](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/blob/main/dane/przyklad_01/przelew.json)):
```json
{
  "przelew_bankowy": {
    "data_operacji": "2026-03-21",
    "kwota": 200.0,
    "na_rachunek": "68438223049992295262834736",
    "numer_transakcji": "CEN2604016344389",
    "tytul": "opłata skarbowa od pełnomocnictwa dla Aleksandra Marciniak i Jolanta Zajkowski w postępowaniu przed KIO w przetargu ZS.791.2026 CEN2604016344389",
    "z_rachunku": "05470680287581539721455024"
  }
}
```
Co tu widać:
- bierzemy **nie wszystko**: adresy i nazwa banku nie są potrzebne;
- wartości **sprowadzamy do jednego formatu**: `200,00 PLN` → `200.0`, `PL05…` → same cyfry;
- trzeba rozumieć, **który rachunek jest czyj**: „obciążany” = skąd poszły pieniądze (`z_rachunku`), „odbiorcy” = dokąd (`na_rachunek`).

### 2.2. Pełnomocnictwo

[PDF](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/blob/main/dane/przyklad_01/pelnomocnictwo.pdf), fragment:
```
PEŁNOMOCNICTWO
Łódź, 1 kwietnia 2026
§ 1. Mocodawca
Mocodawcą jest Belmoria Rozwiązania S.A., ul. Zaczarowana 78, 90-803 Łódź, KRS 9999405188, NIP 9999284065 ...
§ 2. Pełnomocnik
Mocodawca ustanawiam pełnomocnikiem r. pr. Aleksandra Marciniak (...), r. pr. Jolanta Zajkowski (...)
...
Mocodawca:                      Pełnomocnictwo przyjmuję:
.............................   .............................
Oliwia Kaczmarek                r. pr. Aleksandra Marciniak
Prezes Zarządu                  .............................
                                r. pr. Jolanta Zajkowski
```
→ [JSON](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/blob/main/dane/przyklad_01/pelnomocnictwo.json):
```json
{
  "mocodawca": [
    {
      "data": { "imie_nazwisko": null, "krs": "9999405188", "nazwa": "Belmoria Rozwiązania S.A.",
                "nip": "9999284065", "typ": "osoba_prawna" },
      "podpisujacy": [ { "imie_nazwisko": "Oliwia Kaczmarek" } ]
    }
  ],
  "pelnomocnicy": [
    { "imie_nazwisko": "Aleksandra Marciniak", "pesel": null },
    { "imie_nazwisko": "Jolanta Zajkowski", "pesel": null }
  ]
}
```
- **mocodawca** — kto udziela pełnomocnictwa (firma); **podpisujacy** — kto podpisał w imieniu firmy (lewa kolumna podpisów, stanowisko „Prezes Zarządu” niepotrzebne); **pelnomocnicy** — komu udzielono (prawnicy; po prawej tylko „przyjmują” pełnomocnictwo, do `podpisujacy` nie trafiają);
- tytuł `r. pr.` (radca prawny) **pomijamy**;
- `"pesel": null` — w dokumencie nie ma PESEL, więc **puste, nie zgadujemy**;
- `typ`: firma → `osoba_prawna`; jeśli pełnomocnictwa udziela przedsiębiorca — osoba fizyczna → `osoba_fizyczna` i wtedy wypełniamy `imie_nazwisko`.

### 2.3. Odwołanie (najtrudniejsze)

[PDF](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/blob/main/dane/przyklad_01/odwolanie.pdf), początek pierwszej strony:
```
Prezes Krajowej Izby Odwoławczej, ul. Postępu 17A, 02-676 Warszawa
Odwołujący:
  Belmoria Rozwiązania S.A., ul. Zaczarowana 78, 90-803 Łódź
  KRS: 9999405188, NIP: 9999284065, REGON: 999642785
  reprezentowana przez: r. pr. Aleksandra Marciniak, r. pr. Jolanta Zajkowski
  adres do korespondencji: Kancelaria Adwokacka Spółka z ograniczoną odpowiedzialnością, ...
  tel. 000 000 000, biuro@poczta.example
Zamawiający:
  Aurelink Automatyka S.A., ul. Rybacka 79, 50-580 Wrocław
  KRS: 9999716254, NIP: 9997518659, REGON: 999184816
ODWOŁANIE
dotyczy: postępowania ... pn. „Zakup i dostawa sprzętu komputerowego wraz z oprogramowaniem oraz usługą
wdrożenia” nr referencyjny sprawy: ZS.791.2026, ogłoszenie o zamówieniu nr 2026/BZP00999778,
numer ogłoszenia TED: 999137-2026
...
Zamawiający w dniu 20 marca 2026 roku unieważnił postępowanie.
Zarzut nr 1 naruszenia obowiązku sporządzenia dokumentacji postępowania w sposób przejrzysty: ...
```
Skąd pochodzi każde pole [wzorcowego JSON-a](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/blob/main/dane/przyklad_01/odwolanie.json):

| Pole JSON | Wartość | Skąd |
|---|---|---|
| `Odwolujacy[0].nazwa` | `Belmoria Rozwiązania S.A.` | blok „Odwołujący” |
| `Odwolujacy[0].krs` / `nip` / `regon` | `9999405188` / … | tamże |
| `Odwolujacy[0].pelnomocnik.imie_nazwisko` | `Aleksandra Marciniak` | **tylko pierwszy** z dwóch prawników |
| `Odwolujacy[0].pelnomocnik.nazwa` | `Kancelaria Adwokacka Spółka z o.o.` | „adres do korespondencji” |
| `Odwolujacy[0].pelnomocnik.mail` | `biuro@poczta.example` | e-mail w bloku |
| `Zamawiajacy[0].nazwa` / `krs` / … | `Aurelink Automatyka S.A.` / … | blok „Zamawiający” (on **też ma** KRS i NIP — nie pomylić!) |
| `ZamowieniePubliczne.tytul` | `Zakup i dostawa sprzętu…` | tekst w cudzysłowie po „pn.” |
| `ZamowieniePubliczne.numer_postepowania` | `ZS.791.2026` | „nr referencyjny” |
| `ZamowieniePubliczne.numer_bzp` | `2026/BZP00999778` | numer ogłoszenia w BZP |
| `ZamowieniePubliczne.numer_ted` | `999137-2026` | numer w TED (rejestr europejski) |
| `OdwolanieKIO.czynnosc_dzien` | `2026-03-20` | „w dniu 20 marca 2026” → format ISO |
| `OdwolanieKIO.czynnosc_powod_id` | `9` | **ze znaczenia**: „unieważnił postępowanie” = kategoria 9 ze słownika |
| `OdwolanieKIO.czynnosc_opis` | `Decyzja o unieważnieniu postępowania` | opis kategorii 9 ze słownika |
| `OdwolanieKIO.data` / `miejscowosc` | `null` / `null` | w nagłówku **nie ma** linii „Miejscowość, data” → puste |

Słownik kategorii (1–12): [config/dictionaries/czynnosc_powod.yaml](../../config/dictionaries/czynnosc_powod.yaml). Opis wszystkich pól od organizatora: [oczekiwane-json/odwolanie.json](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/blob/main/oczekiwane-json/odwolanie.json).

---

## Krok 3. Co robi nasz program (wejście → wyjście)

Jury wkłada sprawy do folderu `input/`, uruchamia `start.bat` (albo `./start.sh`), a program zapisuje JSON-y do `output/` **pod tymi samymi nazwami**:
```
input/                                  output/
├── case1/                              ├── case1/
│   ├── odwolanie_nr_1.pdf       ──►    │   ├── odwolanie_nr_1.json
│   ├── pelnomocnictwo_nr_1.pdf  ──►    │   ├── pelnomocnictwo_nr_1.json
│   ├── przelew_nr_1.pdf         ──►    │   ├── przelew_nr_1.json
│   └── ogloszenie_TED.pdf       ──►    │   (nic — to „obcy” dokument)
```
- **Typu dokumentu nie poznamy po nazwie pliku**: w teście nazwy mogą być dowolne. Program sam czyta treść i decyduje: „to pełnomocnictwo”.
- **Obce dokumenty pomijamy.** Organizator dał [„przykład negatywny”](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/blob/main/szablon-aplikacja/input/case1/negatywny%20przyklad.pdf) — ogłoszenie o zamówieniu z TED. Dla niego nie tworzymy JSON-a.

---

## Krok 4. Dlaczego to trudne

**1. Każdy dokument wygląda inaczej.** W 60 przykładach jest ponad 20 różnych „banków”, a to samo pole bywa różnie opisane:

| Pole | Jak bywa opisane |
|---|---|
| rachunek odbiorcy | `Rachunek odbiorcy` · `Rachunek beneficjenta` · `Rachunek uznawany` |
| numer operacji | `Nr ref.` · `Numer referencyjny` · `Identyfikator operacji` · `Numer transakcji` |
| data | `2026-03-21` · `21.03.2026` · `4 marca 2026` · `3 lipca 2026 roku` |
| tytuł | `PEŁNOMOCNICTWO` · `P E Ł N O M O C N I C T W O` · `Pełnomocnictwo` |

**2. Prawdziwe dokumenty to skany.** Jedyny prawdziwy przykład od organizatora to [skan odwołania](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/blob/main/szablon-aplikacja/input/case1/przyklad%20odwolanie.pdf): obrazy stron bez tekstu, część danych zamazana na czarno (anonimizacja). Żeby to przeczytać, potrzebny jest **OCR** (rozpoznawanie tekstu z obrazu), a on się myli: `Odwołujący` → `Odwolujacy`, `Spółka` → `Spóka`.

**3. Część pól wymaga zrozumienia treści.**
- `czynnosc_powod_id`: „unieważnił postępowanie” = **9**, a „zaniechanie unieważnienia” (NIE unieważnił) = **10**. Słowa prawie te same, sens przeciwny.
- Przy **konsorcjum** (kilka firm odwołuje się razem) bierzemy tylko **Lidera**.
- W dokumencie bywa „**Wykonawca wybrany**” (zwycięzca przetargu). To trzecia firma — nie wolno jej pomylić z Odwołującym.
- W prawdziwych pismach nazwiska są **odmienione**: „przez radcę prawnego **Jana Kowalskiego**”, a w JSON-ie ma być „**Jan Kowalski**”.

**4. Lepiej puste niż błędne.** Wynika to ze sposobu oceny (krok 5).

---

## Krok 5. Jak nas oceniają — miara F1

Jury uruchomi program na **≥10 prawdziwych sprawach, których nie widzieliśmy**, i porówna **każde pole** naszego JSON-a z poprawną odpowiedzią. Na pierwszym etapie cała ocena to jedna liczba F1 (od 0 do 1). Do finału przechodzą 3 najlepsze zespoły.

Mini-przykład na 4 polach:

| Pole | Poprawnie | My zwróciliśmy | Wynik |
|---|---|---|---|
| `kwota` | `200.0` | `200.0` | ✅ trafienie (TP) |
| `numer_transakcji` | `CEN2604…` | `CEN9999…` | ❌ błąd: minus precyzja **i** kompletność (FP + FN) |
| `tytul` | `opłata skarbowa…` | `null` | ⚪ pominięte: minus kompletność (FN) |
| `pesel` | `null` | `85120133547` | ❌ wymyślone: minus precyzja (FP) |

- **Precyzja (precision)** — z tego, co wypełniliśmy, ile jest poprawne.
- **Kompletność (recall)** — z tego, co trzeba było wypełnić, ile znaleźliśmy.
- **F1** — ich średnia. Wysoka tylko wtedy, gdy obie są dobre.

Stąd zasada: **błędna wartość kosztuje więcej niż pusta.** Jeśli nie jesteśmy pewni — lepiej `null`.

Nasz skrypt `python -m evaluation.run_eval` liczy F1 na 60 sprawach treningowych w ~10 sekund i pokazuje najgorsze pola. To nasze główne narzędzie. Jak dokładnie liczy organizator (dokładne dopasowanie czy z tolerancją), dopytujemy mentorów.

---

## Krok 6. Jak nasz program przetwarza jeden dokument

Ten sam przelew z kroku 2.1 przechodzi pięć etapów:

```
potwierdzenie_przelewu.pdf
   │
   ▼ 1. INGEST — wyciągamy tekst
   │    Jest warstwa tekstowa → bierzemy ją. Skan → OCR.
   │
   ▼ 2. CLASSIFY — co to za dokument?
   │    Szukamy „znaków rozpoznawczych” każdego typu (opisanych w YAML) i liczymy punkty:
   │      przelew: +10 (jest „polecenie przelewu”, „rachunek odbiorcy”, kwota, IBAN)
   │      pelnomocnictwo: −4    odwolanie: −4
   │    → to przelew. Jeśli wszyscy mają mało punktów → „obcy dokument”, nie zapisujemy JSON-a.
   │
   ▼ 3. EXTRACT — szukamy wartości pól
   │    Reguły z YAML: pole na_rachunek szukane jest po etykietach
   │      [Rachunek odbiorcy, Rachunek beneficjenta, Rachunek uznawany] → "PL68438223…"
   │    + LLM (sieć neuronowa) dla pól wymagających zrozumienia — dostaje opisy pól z tego samego YAML.
   │
   ▼ 4. MERGE — wybieramy najlepsze i sprawdzamy
   │    Czy wartość naprawdę jest w tekście? Czy suma kontrolna rachunku się zgadza?
   │    Brak pewności → null.
   │
   ▼ 5. NORMALIZE — sprowadzamy do formatu
   │    "PL68 4382 2304 …" → "68438223049992295262834736",  "200,00 PLN" → 200.0
   │
   ▼
output/.../potwierdzenie_przelewu.json   +   traces/.../potwierdzenie_przelewu.trace.json
                                               (skąd pochodzi każde pole: strona, fragment tekstu,
                                                metoda, pewność — pokazujemy to w panelu)
```

**Główna idea architektury:** wszystko, co dotyczy konkretnego typu dokumentu (znaki rozpoznawcze, pola, etykiety, formaty), jest w **jednym pliku YAML** — [config/doc_types/przelew.yaml](../../config/doc_types/przelew.yaml). Kod jest wspólny dla wszystkich typów. Żeby nauczyć program nowego typu dokumentu, wystarczy **dodać YAML bez zmiany kodu**. Za to w finale jest połowa punktów.

---

## Krok 7. Co już jest i co robimy

**Gotowe (szkielet):**
- cały potok z kroku 6 się uruchamia (`./start.sh`), CLI, API dla panelu, tymczasowa strona-zaślepka;
- klasyfikacja: **180 na 180** PDF-ów treningowych rozpoznanych poprawnie, „obcy” dokument odrzucony, skan rozpoznany;
- miara F1 i przebieg na 60 sprawach; zadania VS Code („Przed PR”);
- konfiguracja trzech typów dokumentów z opisami wszystkich pól.

**Obecne F1 ≈ 0.18** — to punkt wyjścia: większość funkcji to jeszcze zaślepki. Robimy ([kto co — ZADANIA_PL.md](ZADANIA_PL.md)):
- normalizatory (daty, kwoty, rachunki, numery BZP/TED) — **Artem**;
- szukanie po etykietach i blokach dokumentu (reguły) — **Lesza**;
- OCR dla skanów, syntetyczne skany do testów — **Sewa**;
- podłączenie LLM, scalanie wyników, trudne pola — **Polina**;
- panel z podświetlaniem źródeł, dokumentacja, prezentacja — **Artem** (po normalizatorach).

---

## Krok 8. Słowniczek

| Termin | Co to jest |
|---|---|
| **UZP** | Urząd Zamówień Publicznych — organizator zadania |
| **KIO** | Krajowa Izba Odwoławcza — rozpatruje odwołania w przetargach |
| **odwołanie** | środek ochrony prawnej wnoszony przez wykonawcę |
| **Odwołujący** | ten, kto się odwołuje (wykonawca, uczestnik przetargu) |
| **Zamawiający** | instytucja prowadząca przetarg |
| **pełnomocnictwo / pełnomocnik / mocodawca** | dokument upoważnienia / kto je dostał (prawnik) / kto je dał (firma) |
| **konsorcjum, lider** | kilka firm składa ofertę wspólnie; lider — główna z nich |
| **zarzut** | punkt odwołania („zamawiający naruszył…”) |
| **czynność** | działanie (lub zaniechanie) zamawiającego, które jest zaskarżane |
| **SWZ** | Specyfikacja Warunków Zamówienia |
| **OPZ** | Opis Przedmiotu Zamówienia |
| **BZP** | Biuletyn Zamówień Publicznych (numer typu `2026/BZP00999778`) |
| **TED** | europejski dziennik ogłoszeń (numer typu `999137-2026`) |
| **KRS / NIP / REGON** | numery firmy: rejestr sądowy / podatkowy / statystyczny |
| **PESEL** | numer identyfikacyjny osoby (11 cyfr) |
| **IBAN / NRB** | numer rachunku bankowego (26 cyfr, w Polsce z prefiksem PL) |
| **JSON** | tekstowy format „pole: wartość” czytany przez programy |
| **YAML** | prosty tekstowy format konfiguracji — opisujemy w nim typy dokumentów |
| **OCR** | rozpoznawanie tekstu z obrazu (dla skanów) |
| **LLM** | duży model językowy, sieć neuronowa (u nas tylko open-source) |
| **regex** | wzorzec wyszukiwania tekstu („po słowie NIP — 10 cyfr”) |
| **F1** | miara jakości od 0 do 1: precyzja + kompletność (krok 5) |
| **venv (.venv)** | wydzielony folder z bibliotekami Pythona dla projektu |

---

## Krok 9. Od czego zacząć (pierwsze 30 minut)

1. Zainstalować środowisko: `./setup.sh` (Windows: `.\setup.bat`) — szczegóły w [ZADANIA_PL.md](ZADANIA_PL.md), sekcja „Start”.
2. Pobrać dane: `python scripts/fetch_data.py --sample-input 3`.
3. Otworzyć obok siebie PDF i JSON z `data/uzp/dane/przyklad_01/` i porównać z tabelą z kroku 2.3.
4. Uruchomić program: `./start.sh` → zobaczyć, co pojawiło się w `output/` i `traces/`.
5. Zobaczyć, jak program „widzi” dokument: `python -m odwolania2json inspect data/uzp/dane/przyklad_01/odwolanie.pdf`.
6. Uruchomić miarę: `python -m evaluation.run_eval` → tabela „Najgorsze pola” pokazuje, co jeszcze nie działa.
7. Otworzyć swój YAML lub plik z [ZADANIA_PL.md](ZADANIA_PL.md) i zaczynać.
