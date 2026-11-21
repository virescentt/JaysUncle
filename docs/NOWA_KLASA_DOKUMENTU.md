# Jak dodać nową klasę dokumentu (bez zmian w kodzie)

Klasa dokumentu to **jeden plik YAML** w `config/doc_types/`. Opisuje, jak rozpoznać dokument, jaki JSON zwrócić i gdzie w tekście szukać każdej wartości. Kod potoku nie zna żadnej klasy — po dodaniu pliku nowa klasa działa od razu w CLI, w panelu webowym i w LLM (schemat JSON i lista klas dla klasyfikatora są generowane z YAML).

Dwie drogi:

- **edytor tekstu + CLI** — kroki 1–6 poniżej;
- **panel webowy**, zakładka „Klasy dokumentów” — [sekcja na końcu](#w-panelu-webowym).

## Przykład krok po kroku: ogłoszenie TED

Gotowy przykład: [examples/ogloszenie_ted.yaml](examples/ogloszenie_ted.yaml) — klasa dla ogłoszenia o zamówieniu z Dziennika Urzędowego UE (TED), które w danych organizatora jest „negatywnym przykładem”.

### Krok 1. Utwórz plik klasy

```bash
cp docs/examples/ogloszenie_ted.yaml config/doc_types/        # Windows: copy docs\examples\ogloszenie_ted.yaml config\doc_types\
```

Nową klasę zwykle zaczyna się od kopii podobnej klasy (np. `config/doc_types/przelew.yaml`) albo od szablonu z panelu („+ Nowa klasa”). Nazwa pliku = `id` klasy.

### Krok 2. Opisz klasę i jej rozpoznawanie

```yaml
id: ogloszenie_ted                       # małe litery, cyfry, _
name: Ogłoszenie o zamówieniu (TED)
description: Ogłoszenie o zamówieniu opublikowane w Dzienniku Urzędowym UE (TED).
classification:
  min_score: 4                           # klasa wygrywa, gdy suma wag trafionych sygnałów ≥ 4
  signals:
    - { pattern: 'ogłoszenie o zamówieniu', weight: 2 }
    - { pattern: 'oficjalna nazwa', weight: 2 }
    - { pattern: '\d{5,6}-\d{4} - ', weight: 2, scope: head }    # numer TED na początku dokumentu
```

Sygnały to wyrażenia regularne; wielkość liter i polskie znaki nie mają znaczenia (tekst z OCR często ich nie ma). Każdy sygnał liczy się raz. Do odróżnienia podobnych dokumentów służą `negative_signals` (odejmują punkty).

### Krok 3. Opisz wynik JSON i wskaż, gdzie szukać wartości

```yaml
output:
  ogloszenie:
    type: object
    properties:
      numer_ted:
        type: string
        description: Numer ogłoszenia TED, format NNNNNN-YYYY.     # opis pola — także dla LLM
        patterns: ['(?P<value>\d{5,6}-\d{4})']                     # wartość = grupa „value”
        scope: head
      nabywca:
        type: string
        description: Oficjalna nazwa zamawiającego (nabywcy).
        patterns: ['Oficjalna nazwa:\s*(?P<value>[^\n]+)']
      tytul:
        type: string
        description: Tytuł postępowania.
        patterns: ['Tytuł:\s*(?P<value>[^\n]+)']
      numer_postepowania:
        type: string
        description: Wewnętrzny identyfikator postępowania.
        patterns: ['Wewnętrzny identyfikator:\s*(?P<value>[^\n]+)']
```

Drzewo `output` = struktura oczekiwanego JSON-a (kolejność kluczy jak w YAML). Brakująca wartość → `null`.

### Krok 4. Sprawdź konfigurację

```bash
python -m odwolania2json doc-types               # lista klas + kontrola: regexy, normalizatory, walidatory, strategie, słowniki
python -m odwolania2json schema ogloszenie_ted   # pusty szablon JSON (--json-schema: schemat przekazywany do LLM)
```

Literówka w nazwie klucza (np. `pattern` zamiast `patterns`) jest zgłaszana jako błąd z nazwą pliku — nie jest po cichu ignorowana.

### Krok 5. Uruchom na dokumencie

```bash
python -m odwolania2json classify "data/uzp/dane/negatywne/2025_S 112-383751.pdf"   # punkty każdej klasy
./start.sh                                                                           # cały folder input/ → output/
```

Wynik (tryb reguł, sprawdzony 21.11.2026): klasa `ogloszenie_ted`, 6 pkt (odwołanie 3, pełnomocnictwo 1, przelew 0), pewność 0.95:

```json
{"ogloszenie": {"numer_ted": "383751-2025", "nabywca": "Przedsiębiorstwo Wodociągów i Kanalizacji Sp. z o.o. w Gdyni", "tytul": "Przebudowa Stacji Uzdatniania Wody \"Rumia\" wraz z przebudową ujęć wód i", "numer_postepowania": "ZP/15/FENIKS/2025"}}
```

Ogłoszenie krajowe z BZP (`2025_BZP 00143067_01.pdf`) dostaje tylko 2 pkt < `min_score` i nadal zwraca pusty plik — klasa rozpoznaje tylko ogłoszenia TED.

### Krok 6. Popraw pole, które wychodzi źle

Tytuł jest ucięty, bo wzorzec `[^\n]+` bierze jedną linię. Zamiast wzorca wystarczy etykieta z kontynuacją na kolejne linie:

```yaml
      tytul:
        type: string
        description: Tytuł postępowania.
        labels: [Tytuł]                  # „Tytuł: …”; etykiety bez względu na wielkość liter i polskie znaki
        max_lines: 3                     # wartość może zajmować do 3 linii; linia „Opis: …” ją kończy
        normalize: [collapse_whitespace] # złamania linii → spacje
```

Wynik: „Przebudowa Stacji Uzdatniania Wody "Rumia" wraz z przebudową ujęć wód i infrastruktury towarzyszącej” (sprawdzone na dwóch ogłoszeniach TED z danych organizatora).

> **Uwaga dla zgłoszenia konkursowego:** ogłoszenie TED jest u organizatora dokumentem negatywnym (ma dawać pusty `.json`). Po demonstracji usuń `config/doc_types/ogloszenie_ted.yaml` albo ustaw w nim `enabled: false`.

## Format pliku klasy — wszystkie klucze

Źródło prawdy: [`odwolania2json/spec.py`](../odwolania2json/spec.py). Nieznany klucz = błąd.

### Poziom główny

| Klucz | Znaczenie |
|---|---|
| `id` | identyfikator klasy (`[a-z0-9_]`, do 64 znaków); w panelu = nazwa pliku |
| `name`, `description` | nazwa i opis (opis trafia też do klasyfikatora LLM) |
| `version` | numer wersji konfiguracji (domyślnie 1) |
| `enabled` | `false` = klasa wyłączona (domyślnie `true`) |
| `classification` | rozpoznawanie (wymagane) |
| `extraction` | wskazówki dla LLM (opcjonalne) |
| `output` | drzewo pól wyniku (wymagane) |

### `classification`

| Klucz | Znaczenie |
|---|---|
| `min_score` | próg punktów (domyślnie 3) |
| `signals` | lista `{pattern, weight, scope, case_sensitive}`: `weight` domyślnie 1; `scope: all` (cały tekst) lub `head` (pierwsze 1500 znaków, `classification.head_chars` w `settings.yaml`); `case_sensitive` domyślnie `false` |
| `negative_signals` | jak `signals`, ale wagi są odejmowane |
| `description` | dodatkowy opis dla klasyfikatora LLM (inaczej używany jest `description` klasy) |

Wygrywa klasa z najwyższym wynikiem, jeśli osiąga swój `min_score`; jeśli żadna — dokument dostaje pusty plik `.json`.

### `extraction` (dla LLM)

| Klucz | Znaczenie |
|---|---|
| `instructions` | dodatkowe reguły w języku naturalnym (np. „numery rachunków: same cyfry”) |
| `max_pages` | ile pierwszych stron przekazać do LLM w całości |
| `keyword_windows` | regexy: z dalszych stron dołączane są fragmenty (±300 znaków) wokół trafień |
| `few_shot` | zarezerwowane na przykłady (ścieżki względem `config/`); ekstraktor LLM jeszcze ich nie używa |

### Pole (`output` → `properties` / `items`)

**Typ i opis**

| Klucz | Znaczenie |
|---|---|
| `type` | `string` (domyślnie) · `integer` · `number` · `boolean` · `date` · `object` (pola w `properties`) · `array` (element w `items`) |
| `description` | opis pola — najważniejszy dla LLM |
| `nullable` | czy `null` jest dozwolony (domyślnie `true`) |
| `enum` | lista dozwolonych wartości |
| `min_items`, `max_items` | dla `array`: minimalna długość listy w wyniku (uzupełniana `null`-ami) i limit elementów |

**Wskazówki dla ekstraktora regułowego** (dopasowanie bez względu na polskie znaki; bez względu na wielkość liter, chyba że `case_sensitive: true`; `^`/`$` = początek/koniec linii)

| Klucz | Znaczenie |
|---|---|
| `patterns` | regexy; wartość = grupa `(?P<value>…)` (albo grupa 1); każdy trafiony wzorzec daje kandydata |
| `labels` | etykiety: „Etykieta: wartość”, „ETYKIETA …… wartość”, etykieta i wartość w następnej linii, etykieta rozbita przez OCR na dwie linie |
| `value_pattern` | kształt wartości po etykiecie (wartość zaczyna się od niego albo zawiera dokładnie jedno trafienie), np. numer rachunku |
| `max_lines` | na ile linii może się ciągnąć wartość etykiety (domyślnie 1); kończy ją pusta linia, inna etykieta klasy, `stop` albo linia „Coś:” |
| `stop` | regexy: linia kontynuacji, która się od nich zaczyna, kończy wartość |
| `scope` | `all` (domyślnie) · `first_page` · `head` (początek dokumentu, 5000 znaków) |
| `section` | obszar `{start, end, optional}`: szukanie od końca trafienia `start` do pierwszego `end`; dla obiektu/listy obowiązuje też dzieci; `optional: true` = brak `start` → cały obszar nadrzędny |
| `item_split` | dla `array`: regex początku każdego elementu w obszarze (np. „1)”, „2)”) → `X[0]`, `X[1]`, … |
| `item_prefer` | dla `array`: elementy pasujące do regexu idą na początek (np. lider konsorcjum przy `max_items: 1`) |
| `strategy`, `params` | nazwana heurystyka i jej parametry (tabela niżej) |
| `confidence` | bazowa pewność kandydatów pola (domyślnie 0.8 dla `patterns`, 0.7 dla `labels` i większości strategii); końcowa pewność < 0.5 → `null` |

**Przetwarzanie wartości**

| Klucz | Znaczenie |
|---|---|
| `normalize` | lista normalizatorów, stosowanych po kolei: `strip`, `collapse_whitespace`, `strip_quotes`, `code`, `person_name`, `email`, `date_pl`, `amount_pl`, `digits_only`, `account_number`, `account_number_as_printed`, `bzp`, `ted` |
| `validate` | miękkie walidatory (zmieniają pewność, nie usuwają wartości): `nip`, `regon`, `krs`, `pesel`, `iban_pl` |
| `ground` | jak sprawdzić, że wartość jest w tekście: `exact` · `digits` (te same cyfry, dowolne separatory) · `fuzzy` (podobieństwo ≥ 90%) · `none` · `auto` (domyślnie: `fuzzy` dla tekstu; `none` dla dat, liczb, wartości logicznych, `enum`, słowników i pól pochodnych) |
| `dictionary` | nazwa słownika z `config/dictionaries/` (wartość = `id` wpisu) |
| `derived_from`, `derived_attr` | pole pochodne: wartość z wpisu słownika wskazanego przez pole-rodzeństwo (`derived_attr`, domyślnie `opis`); nie jest wyodrębniane ani pytane w LLM |

### Strategie (`strategy` + `params`)

| Strategia | Co robi | Główne `params` |
|---|---|---|
| `first_line` | nazwa podmiotu = pierwsza sensowna linia obszaru (np. po „Zamawiający:”) | `skip`, `stop`, `cut`, `max_scan` |
| `persons` | pole: pierwsza osoba po frazie; lista: osoby wymienione po frazie | `after`, `until`, `max_chars`, `max` |
| `item_text` | tekst elementu listy utworzonego przez strategię listy (np. imię i nazwisko) | — |
| `signatories` | lista osób podpisujących (podpis w treści, podpis elektroniczny, blok podpisów) | `inline`, `inline_until`, `esign`, `block_start`, `block_end`, `group_start`, `block_skip` |
| `dictionary` | wpis słownika, którego słowo kluczowe występuje najwcześniej w tekście za kotwicą (np. pierwszy zarzut) | `anchors`, `end`, `until`, `max_chars`, `first_item` |
| `date` | pierwsza data w tekście za kotwicą | jak `dictionary` + `skip_preceded_by` |
| `flag` | 1/0: czy w obszarze występuje wzorzec | `patterns`, `fallback`, `absent_patterns`, `present`, `absent`, `absent_confidence` |
| `item_flag` | 1/0 dla elementów listy (tylko gdy elementów jest więcej niż jeden) | `pattern`, `present`, `absent` |
| `entity_type` | osoba fizyczna czy prawna | `natural`, `legal`, `natural_value`, `legal_value` |

Przykłady użycia wszystkich strategii: `config/doc_types/odwolanie.yaml` i `pelnomocnictwo.yaml`.

### Słowniki

Plik `config/dictionaries/<id>.yaml`: `id`, `description`, `entries` — każdy wpis ma `id`, `opis`, opcjonalnie `hint` (wskazówka dla LLM) i `keywords` (regexy dla strategii `dictionary`). Przykład: `czynnosc_powod.yaml` (powód odwołania + pole pochodne `czynnosc_opis`).

## W panelu webowym

1. Uruchom `./start.sh web` (Windows: `start.bat web`) i otwórz http://localhost:8000, zakładka **Klasy dokumentów**.
2. Wybierz istniejącą klasę z listy albo kliknij **+ Nowa klasa** (szablon z komentarzami).
3. Edytuj YAML w edytorze (numery linii; Tab = 2 spacje).
4. **Sprawdź** (Ctrl/⌘+Enter) — składnia YAML, nieznane klucze, regexy, nazwy normalizatorów, walidatorów i słowników; błędy i ostrzeżenia z numerami linii (np. „klasa nigdy nie zostanie rozpoznana”).
5. **Testuj na PDF…** — wybrany PDF jest klasyfikowany i przetwarzany z tymczasową konfiguracją (nic nie jest zapisywane): widać, czy klasa została rozpoznana, punkty wszystkich klas oraz pola z podświetlonym źródłem na stronie.
6. **Zapisz** (Ctrl/⌘+S) — ponowna kontrola, zapis do `config/doc_types/<id>.yaml` i przeładowanie potoku; jeśli nowa konfiguracja nie daje się wczytać, przywracana jest poprzednia wersja pliku. **Przywróć** cofa niezapisane zmiany.

Zapisywanie można wyłączyć zmienną `ODWOLANIA2JSON_WEB_READONLY=1` (np. gdy panel jest dostępny dla innych osób) — wtedy działają tylko Sprawdź i Testuj.

## Lista kontrolna

- [ ] `id` unikalne, plik `config/doc_types/<id>.yaml`.
- [ ] Sygnały rozpoznają nowe dokumenty, a istniejące klasy nadal wygrywają na swoich (`python -m odwolania2json classify …`); w razie kolizji dodaj `negative_signals`.
- [ ] Struktura `output` odpowiada oczekiwanemu JSON-owi; każde pole ma `description`.
- [ ] `python -m odwolania2json doc-types` bez błędów.
- [ ] Ewaluacja istniejących klas bez spadku: `python -m evaluation.run_eval --mode rules`.
