# Raport jakości

Pomiar z 21–22.11.2026. Wyniki poniżej dotyczą domyślnego trybu `rules` (same reguły, bez LLM); porównanie z LLM (Qwen3.5-9B, Mistral-Small, Olala) — sekcja [„Tryb reguł a hybryda z LLM”](#tryb-reguł-a-hybryda-z-llm).

## Podsumowanie

| Zbiór | Dokumenty | F1 | Precyzja | Pełność | TP | FP | FN |
|---|---|---|---|---|---|---|---|
| Sprawy syntetyczne organizatora (60 spraw) | 180 PDF | **1.000** | 1.000 | 1.000 | 2032 | 0 | 0 |
| Dokumenty negatywne | 16 PDF | — | — | — | — | 0 | — |
| **Skany organizatora** (bez warstwy tekstowej, OCR) | 30 PDF | **0.969** | 0.971 | 0.968 | 299 | 9 | 10 |

- Wszystkie 16 dokumentów negatywnych (ogłoszenia TED/BZP, decyzje, wyrok KIO, broszura itp.) dostają pusty plik `.json` (0 bajtów), zgodnie z README organizatora.
- Klasyfikacja: sprawy syntetyczne **180/180**, skany **30/30**, dokumenty negatywne **16/16** (żaden nie został przypisany do klasy).
- Testy automatyczne: `pytest -q` → **532 passed**.

## Metryka

Liczona przez [`evaluation/score.py`](../evaluation/score.py) — nasze przybliżenie miary F1 organizatora (dokładne zasady UZP nie są opublikowane):

- porównywane jest każde pole-liść JSON-a po normalizacji (białe znaki; liczby `200` = `200.0`);
- wartość w referencji i ta sama wartość w wyniku → **TP**; wartość w referencji, a w wyniku `null` → **FN**; `null` w referencji, a w wyniku wartość → **FP**; inna wartość → **FP + FN**; oba `null` → pomijane;
- elementy list (`Odwolujacy`, `mocodawca`, `pelnomocnicy`, …) są dopasowywane po podobieństwie, nie po pozycji;
- dokumenty w sprawie są dopasowywane po typie (klucze najwyższego poziomu), więc źle sklasyfikowany dokument liczy się jako brakujący (same FN) plus nadmiarowy (same FP);
- folder z PDF-ami bez referencyjnych JSON-ów (np. `dane/negatywne`) = dokumenty negatywne: każdy niepusty wynik to FP;
- Precyzja = TP / (TP + FP), Pełność = TP / (TP + FN), F1 = 2·P·R / (P + R) — sumowane po wszystkich polach wszystkich dokumentów (micro).

Liczby w tym raporcie są w trybie ścisłym (bez opcji `--lenient` i `--fuzzy`): „Lódź” ≠ „Łódź”.

## Zbiory danych

Dane organizatora pobiera `python scripts/fetch_data.py` (do `data/uzp/`).

| Zbiór | Ścieżka | Zawartość |
|---|---|---|
| Sprawy syntetyczne | `data/uzp/dane/przyklad_01 … przyklad_60` | 60 spraw: odwołanie, pełnomocnictwo, potwierdzenie przelewu + referencyjne JSON-y (180) |
| Negatywne | `data/uzp/dane/negatywne` + foldery spraw | 12 PDF w `negatywne` i 4 dodatkowe PDF w folderach spraw (np. ogłoszenie TED) |
| Skany organizatora | `data/uzp/dane/skany/{odwolania,pelnomocnictwa,potwierdzenia}` | 10 odwołań, 10 pełnomocnictw, 10 potwierdzeń przelewu; skany bez warstwy tekstowej + referencyjne JSON-y |

## Wyniki według typu dokumentu

**Skany organizatora (30 PDF, OCR):**

| Typ | F1 | Precyzja | Pełność | TP | FP | FN |
|---|---|---|---|---|---|---|
| odwołanie | 0.989 | 0.989 | 0.989 | 175 | 2 | 2 |
| pełnomocnictwo | 0.987 | 0.987 | 0.987 | 77 | 1 | 1 |
| potwierdzenie przelewu | 0.878 | 0.887 | 0.870 | 47 | 6 | 7 |
| **razem** | **0.969** | 0.971 | 0.968 | 299 | 9 | 10 |

**Sprawy syntetyczne (180 PDF):** odwołanie 1.000 (TP 1214), pełnomocnictwo 1.000 (TP 458), przelew 1.000 (TP 360), zero FP i FN.

## Pozostałe błędy na skanach (pełna lista: 9 FP, 10 FN)

| Pole | FP | FN | Przyczyna |
|---|---|---|---|
| `przelew_bankowy.tytul` | 4 | 4 | Banki drukują tytuł bez polskich liter („OPLATA SKARBOWA OD PELNOMOCNICTWA…”, „Wpis od odwolania…”), a referencja ma polskie litery. Na jednym słabym skanie dodatkowo błędy OCR i doklejony tekst pieczęci podpisu („Signature Not Verified”). |
| `przelew_bankowy.z_rachunku` | 2 | 2 | W 2 referencjach numer rachunku zachowuje prefiks „PL” (tak jak wydrukowano), w pozostałych dokumentach i w danych syntetycznych to same cyfry. Pytanie do UZP. Gotowy jest normalizator `account_number_as_printed` (zachowuje „PL”, jeśli jest w tekście) — przełączenie to zmiana w `config/doc_types/przelew.yaml`, bez zmian w kodzie. |
| `przelew_bankowy.kwota` | 0 | 1 | Kwota (17 zł) nieodczytana na jednym skanie → `null`. |
| `OdwolanieKIO.miejscowosc` | 1 | 1 | OCR: „Lódź” zamiast „Łódź”. |
| `Zamawiajacy[].nazwa` | 1 | 1 | OCR zgubił spację („Technicznychw Wrzosowie”). |
| `mocodawca[].data.nazwa` | 1 | 1 | Dwa dokumenty: w jednym nazwa spółki nieodczytana (`null`), w drugim zwróciliśmy nazwę, a referencja ma `null`. |

Na danych syntetycznych błędów nie ma, więc ewentualny spadek F1 na zbiorze testowym będzie wynikał głównie z jakości skanów (OCR) i z rozbieżności konwencji referencji (polskie litery w tytule, prefiks „PL”).

## Tryb reguł a hybryda z LLM

Ten sam zbiór 30 skanów organizatora, ta sama metryka:

| Konfiguracja | F1 | Uwagi |
|---|---|---|
| **Reguły (domyślnie)** | **0.969** | sekundy na dokument, bez internetu i klucza API |
| Hybryda: reguły + Qwen3.5-9B (OVHcloud, bez „myślenia”), LLM tylko uzupełnia puste pola | 0.955 | P 0.940 / R 0.971 — LLM podnosi kompletność, ale wypełnia pola puste w referencji (FP 19 vs 7); ok. 3,5 s/dokument |
| Hybryda: reguły + Olala-v1.0-mini-7A1B (OVHcloud), LLM tylko uzupełnia puste pola | 0.942 | brak odpowiedzi w 15/30 dokumentów (rozumowanie zużywa cały limit 16 000 tokenów), dodatkowe FP w polach pustych w referencji (`mocodawca[].data.imie_nazwisko`, `prowadzacy_postepowanie`, `typ`), ok. 1 min/dokument |

Sam LLM (tryb `llm`, bez reguł) na 10 sprawach (40 PDF, ten sam zbiór dla wszystkich modeli):

| Model (OVHcloud AI Endpoints) | Parametry | Pkt za wielkość | F1 | Czas |
|---|---|---|---|---|
| Mistral-Small-3.2-24B-Instruct-2506 | 24B | 12 | 0.964 | ~40 s |
| **Qwen3.5-9B** (`reasoning_effort: none`) | 9B | **18** | 0.940 | ~90 s |
| Olala-v1.0-mini-7A1B | 6,8B | 20 | 0.475 | 12,5 min („myślenia” nie da się wyłączyć) |

Wniosek: w zgłoszeniu domyślnie działa tryb reguł; LLM pozostaje opcją (`EXTRACTION_MODE=hybrid|llm`), m.in. dla nowych klas dokumentów bez reguł. Odtworzenie: `EXTRACTION_MODE=hybrid python -m evaluation.run_eval --subset scans` (wymaga klucza w `.env`).

## Benchmark OCR

Skrypt [`scripts/ocr_benchmark.py`](../scripts/ocr_benchmark.py) uruchamia OCR tą samą ścieżką kodu co aplikacja i mierzy **odnalezione wartości referencji** (gold value recall): jaki odsetek niepustych wartości z referencyjnych JSON-ów 30 skanów da się znaleźć w tekście z OCR.

- Tekst: porównanie bez względu na wielkość liter, po scaleniu białych znaków; numery (NIP, KRS, rachunki, kwoty, BZP): ta sama sekwencja znaków, dopuszczalne separatory; daty ISO także jako `DD.MM.YYYY` i „D miesiąc YYYY”.
- Pomijane są wartości, które nie są tekstem z dokumentu (pola ze słownika, `enum`, `derived_from`, wartości logiczne).
- „strict” zachowuje polskie litery, „folded” porównuje po ich usunięciu — różnica pokazuje, ile wartości traci się tylko na polskich znakach.

| Konfiguracja OCR | Odnalezione wartości (strict) | folded | Uwagi |
|---|---|---|---|
| RapidOCR, PP-OCRv5 latin (poprzednia konfiguracja) | 0.83 | — | |
| EasyOCR 1.7.2 (pl) | 0.82 | — | ~3× wolniejszy na CPU, +≈900 MB (torch) → tylko opcjonalnie: `requirements-ocr.txt` |
| **RapidOCR, PP-OCRv6-small, 300 dpi + korekta polskich liter + ponowny odczyt słabych linii (domyślna)** | **0.955** | **0.970** | modele w paczce pip, bez pobierania, CPU |
| Tesseract (pol) | — | — | opcjonalny; wymaga programu systemowego (nie będzie go na laptopie jury); jest w obrazie Docker |

Ustawienia domyślnej konfiguracji są w [`config/settings.yaml`](../config/settings.yaml) (sekcja `ingest`: `ocr_model`, `ocr_dpi`, `ocr_retry_score`, `ocr_polish_letters`, `ocr_rec_batch`, `ocr_drop_mirrored`, `ocr_auto_rotate`). OCR działa lokalnie — dokumenty nie są wysyłane do żadnej usługi OCR.

## Jak odtworzyć

```bash
python scripts/fetch_data.py                                    # dane UZP → data/uzp/
python -m evaluation.run_eval --mode rules                      # --subset fast (domyślnie): 60 spraw syntetycznych + negatywne
python -m evaluation.run_eval --mode rules --subset scans       # 30 skanów organizatora (OCR: pierwszy przebieg kilka minut, potem cache)
python -m evaluation.run_eval --mode rules --subset negatives   # tylko folder dane/negatywne (12 PDF)
python scripts/make_scans.py && python -m evaluation.run_eval --data data/scans/dane   # syntetyczne skany
python scripts/ocr_benchmark.py                                 # benchmark OCR, ustawienia domyślne
python scripts/ocr_benchmark.py --list                          # dostępne konfiguracje do porównania (--config …)
pytest -q                                                       # testy
```

- Bez `--mode` obowiązuje tryb z `config/settings.yaml` / `.env` (domyślnie `rules`).
- 4 dokumenty negatywne leżące w folderach spraw są liczone w zbiorze `fast`, nie w `negatives`.
- Raport: `runs/<czas>/report.json` (F1 ogółem, wg typu, wg pola, wg sprawy, przykłady błędów) oraz kopia w `runs/latest_report.json`; wyniki i ślady w `runs/<czas>/output/` i `runs/<czas>/traces/`.
- Ocena gotowych wyników: `python -m evaluation.score --pred runs/<czas>/output --gold data/uzp/dane`.
