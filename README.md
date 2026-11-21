# Odwołania2JSON

**Od dokumentów do danych.** Automatyczna zamiana dokumentów postępowania odwoławczego przed KIO (odwołanie, pełnomocnictwo, potwierdzenie przelewu — PDF, także skany) na kanoniczny model JSON.

Zadanie konkursowe Urzędu Zamówień Publicznych, HackNation 2026 ([opis i dane organizatora](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026)).

> Status: **w budowie** (szkielet architektury gotowy, moduły w trakcie implementacji).

---

## Co robi

1. Przyjmuje PDF-y z folderu `input/` (podfoldery = sprawy) — z warstwą tekstową albo skany (OCR).
2. **Rozpoznaje rodzaj dokumentu** (odwołanie / pełnomocnictwo / przelew; inne dokumenty, np. ogłoszenie TED, są pomijane).
3. **Wyodrębnia dane** właściwe dla danej klasy (strony postępowania, numery KRS/NIP/REGON, BZP/TED, czynność zaskarżona w pierwszym zarzucie, dane przelewu…).
4. Zapisuje JSON zgodny z [oczekiwanym modelem](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/tree/main/oczekiwane-json) do `output/` — **ta sama nazwa i struktura katalogów co w `input/`**.
5. Dla każdej wartości zapisuje **ślad** (`traces/`): strona, fragment tekstu, metoda, pewność — przejrzystość ekstrakcji.

```
input/case1/odwolanie_nr_1.pdf        →  output/case1/odwolanie_nr_1.json
input/case1/pelnomocnictwo_nr_1.pdf   →  output/case1/pelnomocnictwo_nr_1.json
input/case1/inny_dokument.pdf         →  (pominięty — nie należy do żadnej klasy)
```

## Architektura w skrócie

```
PDF ─► INGEST (pdfplumber; skan → OCR Tesseract/RapidOCR)
    ─► CLASSIFY (sygnały z YAML + opcjonalnie LLM)
    ─► EXTRACT (reguły/regex z YAML + LLM ze schematem JSON z YAML)
    ─► MERGE (weryfikacja: wartość musi występować w tekście, sumy kontrolne; niepewne → null)
    ─► NORMALIZE (daty ISO, kwoty, numery, słowniki) ─► output/*.json + traces/*.trace.json
```

- **Klasa dokumentu = jeden plik YAML** w `config/doc_types/` (sygnały rozpoznawania, struktura wyniku, opisy pól, wskazówki ekstrakcji, normalizacja). **Nowa klasa nie wymaga zmian w kodzie** — zob. [docs/NOWA_KLASA_DOKUMENTU.md](docs/NOWA_KLASA_DOKUMENTU.md).
- Tryby ekstrakcji (`config/settings.yaml` lub `EXTRACTION_MODE` w `.env`): `rules` (bez LLM, offline) · `llm` · `hybrid` (domyślny).
- LLM: **wyłącznie modele open-source** przez API zgodne z OpenAI (Ollama, vLLM, llama.cpp lub hostowane modele z otwartymi wagami). Bez GPU po stronie aplikacji.
- Wszystkie zależności open source o licencjach permisywnych (MIT / BSD / Apache-2.0).

## Wymagania

- **Python 3.13+**
- (zalecane) **Tesseract OCR z językiem polskim** — dla skanów. Bez niego działa zapasowy RapidOCR (instalowany przez pip; gorzej radzi sobie z polskimi znakami).
  - macOS: `brew install tesseract tesseract-lang`
  - Ubuntu/Debian: `sudo apt install tesseract-ocr tesseract-ocr-pol`
  - Windows: [instalator UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki) (zaznacz język *Polish*)
- (opcjonalnie) endpoint LLM z modelem open-source — np. lokalna [Ollama](https://ollama.com): `ollama pull qwen3:8b`

## Instalacja

**macOS / Linux**
```bash
./setup.sh                      # tworzy .venv (Python 3.13), instaluje zależności, kopiuje .env.example → .env
source .venv/bin/activate
```

**Windows**
```bat
setup.bat
.venv\Scripts\activate
```

**Ręcznie** (dowolny system)
```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip install -r requirements.txt
cp .env.example .env                             # Windows: copy .env.example .env
```

**Docker**
```bash
docker compose run --rm app                      # przetwarza ./input → ./output
docker compose up web                            # interfejs webowy na http://localhost:8000
docker compose --profile local-llm up -d ollama  # opcjonalnie: lokalny LLM (CPU)
```

## Konfiguracja LLM (`.env`)

```ini
LLM_BASE_URL=http://localhost:11434/v1   # Ollama / vLLM / inny serwer zgodny z OpenAI
LLM_API_KEY=ollama
LLM_MODEL=qwen3:8b                       # model z otwartymi wagami
EXTRACTION_MODE=hybrid                   # rules | llm | hybrid
OCR_ENGINE=auto                          # auto | tesseract | rapidocr | none
```

Bez skonfigurowanego LLM aplikacja działa w trybie reguł.

## Uruchomienie

```bash
./start.sh                       # Windows: start.bat   →  przetwarza ./input → ./output
./start.sh web                   # interfejs webowy: http://localhost:8000
./start.sh --help                # wszystkie komendy
```

Przydatne komendy (`python -m odwolania2json …`):

| Komenda | Opis |
|---|---|
| `run -i input -o output` | przetwarza wszystkie PDF-y |
| `classify plik.pdf …` | punkty klasyfikacji dla każdej klasy |
| `inspect plik.pdf` | tekst każdej strony (warstwa tekstowa / OCR) |
| `doc-types` | lista klas dokumentów + walidacja konfiguracji YAML |
| `schema przelew [--json-schema]` | pusty szablon JSON / schemat przekazywany do LLM |
| `web` | interfejs webowy |

## Dane treningowe i ewaluacja (F1)

```bash
python scripts/fetch_data.py --sample-input 3   # dane UZP → data/uzp/ (+ 3 sprawy do input/)
python -m evaluation.run_eval                    # 60 spraw treningowych → F1 po polach
python -m evaluation.run_eval --mode rules --limit 10
python scripts/make_scans.py && python -m evaluation.run_eval --data data/scans/dane   # syntetyczne skany
```

Raport: `runs/<czas>/report.json` (F1 ogółem, wg typu dokumentu, wg pola, przykłady błędów). Metryka: porównanie każdego pola z referencją — trafienie TP, brak FN, nadmiarowa/błędna wartość FP.

## Struktura repozytorium

```
config/            konfiguracja: settings.yaml, doc_types/*.yaml (klasy), dictionaries/, prompts/
odwolania2json/    kod: ingest/ classify/ extract/ normalize/ validate/ llm/ output/ api/ + pipeline.py, cli.py
web/               interfejs webowy (statyczny HTML/JS, bez kroku budowania)
evaluation/        metryka F1 i przebieg na zbiorze treningowym
scripts/           pobieranie danych, generowanie syntetycznych skanów
tests/             pytest (testy normalizatorów i walidatorów pełnią rolę specyfikacji)
docs/              dokumentacja (architektura, dodawanie klas, dokumenty zespołu)
input/ output/     foldery wymagane przez organizatora
```

## Testy

```bash
pytest -q        # testy-specyfikacje niezaimplementowanych funkcji są pomijane (skipped)
ruff check .
```

## Zespół

Zespół *zschie* — HackNation 2026.
