# Odwołania2JSON

**Od dokumentów do danych.** Automatyczna zamiana dokumentów postępowania odwoławczego przed KIO (odwołanie, pełnomocnictwo, potwierdzenie przelewu — PDF, także skany) na kanoniczny model JSON.

Zadanie konkursowe Urzędu Zamówień Publicznych, HackNation 2026 ([opis i dane organizatora](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026)).

> Status: **w budowie** (szkielet architektury gotowy, moduły w trakcie implementacji).

---

## Co robi

1. Przyjmuje PDF-y z folderu `input/` (podfoldery = sprawy) — z warstwą tekstową albo skany (OCR).
2. **Rozpoznaje rodzaj dokumentu** na podstawie treści, nie nazwy pliku (odwołanie / pełnomocnictwo / przelew; inne dokumenty, np. ogłoszenie TED czy wyrok KIO, dostają pusty plik `.json`).
3. **Wyodrębnia dane** właściwe dla danej klasy (strony postępowania, numery KRS/NIP/REGON, BZP/TED, czynność zaskarżona w pierwszym zarzucie, dane przelewu…).
4. Zapisuje JSON zgodny z [oczekiwanym modelem](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/tree/main/oczekiwane-json) do `output/` — **ta sama nazwa i struktura katalogów co w `input/`**.
5. Dla każdej wartości zapisuje **ślad** (`traces/`): strona, fragment tekstu, metoda, pewność — przejrzystość ekstrakcji.

```
input/case1/odwolanie_nr_1.pdf        →  output/case1/odwolanie_nr_1.json
input/case1/pelnomocnictwo_nr_1.pdf   →  output/case1/pelnomocnictwo_nr_1.json
input/case1/inny_dokument.pdf         →  output/case1/inny_dokument.json   (pusty plik — nie należy do żadnej klasy)
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
- LLM: **wyłącznie modele open-source** przez API zgodne z OpenAI. Aplikacja działa na laptopie biurowym (16 GB RAM, Intel i7, bez GPU) — zgodnie z wymaganiami organizatora LLM jest wywoływany przez API dostawcy z serwerami w UE/EOG (klucz w `.env`, nie w repozytorium). Lokalnie można też użyć Ollama / vLLM / llama.cpp.
- Model LLM: *(do uzupełnienia — nazwa, liczba parametrów, dostawca, lokalizacja serwerów)*.
- Wszystkie zależności open source o licencjach permisywnych (MIT / BSD / Apache-2.0).

## Wymagania

- **Windows, macOS lub Linux** — obsługiwane wszystkie (skrypty `.bat` dla Windows, `.sh` dla macOS/Linux).
- **Python 3.13+** — [python.org/downloads](https://www.python.org/downloads/) (Windows: przy instalacji zaznacz „Add python.exe to PATH”). Jeśli go brak, `start.bat` / `start.sh` wyświetli komunikat z linkiem i zakończy działanie, niczego nie instalując.
- **Internet** — do wywołań API LLM (OVHcloud AI Endpoints, serwery w UE) i do instalacji zależności przy pierwszym uruchomieniu. Bez internetu lub bez klucza API aplikacja nadal działa — w trybie reguł (bez LLM).
- OCR skanów działa od razu po instalacji (RapidOCR, bez programów systemowych). Opcjonalnie **Tesseract z językiem polskim**:
  - macOS: `brew install tesseract tesseract-lang`
  - Ubuntu/Debian: `sudo apt install tesseract-ocr tesseract-ocr-pol`
  - Windows: [instalator UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki) (zaznacz język *Polish*)

## Instalacja

Najprościej: **nic nie instaluj ręcznie** — przy pierwszym uruchomieniu `start.bat` / `start.sh` sam tworzy środowisko `.venv` i instaluje zależności (`pip install -r requirements.txt`). Poniżej instalacja krok po kroku, jeśli wolisz zrobić to osobno.

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

Klucz API **nie jest** w repozytorium — wpisujemy go do pliku `.env` (kopia `.env.example`, tworzona automatycznie przy instalacji). Dla jury: klucz podajemy w formularzu zgłoszenia.

```ini
# Model open-source przez API z serwerami w UE (OVHcloud AI Endpoints, Francja)
LLM_BASE_URL=https://oai.endpoints.kepler.ai.cloud.ovh.net/v1
LLM_API_KEY=<klucz>
LLM_MODEL=Olala-v1.0-mini-7A1B   # wybór modelu: w trakcie porównania (patrz docs/team)

EXTRACTION_MODE=hybrid     # rules | llm | hybrid
OCR_ENGINE=auto            # auto | rapidocr | easyocr | tesseract | none
```

Do rozwoju lokalnego można użyć dowolnego serwera zgodnego z OpenAI, np. [Ollama](https://ollama.com) (`ollama pull qwen3.5:9b`, `LLM_BASE_URL=http://localhost:11434/v1`). Jeśli LLM jest niedostępny (brak klucza, brak internetu, model nie istnieje), aplikacja wypisze jeden komunikat z przyczyną i przetworzy dokumenty w trybie reguł.

## Uruchomienie

**Windows**
```bat
REM przetwarza .\input → .\output (pierwsze uruchomienie: automatyczna instalacja)
start.bat
REM panel webowy → otwórz http://localhost:8000
start.bat web
```

**macOS / Linux**
```bash
./start.sh                # przetwarza ./input → ./output (pierwsze uruchomienie: automatyczna instalacja)
./start.sh web            # panel webowy → otwórz http://localhost:8000
./start.sh --help         # wszystkie komendy
```

Panel webowy działa lokalnie (serwer w Pythonie na tym samym komputerze) — niczego nie trzeba hostować.

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
python -m evaluation.run_eval                    # 60 spraw syntetycznych + dokumenty negatywne (~20 s)
python -m evaluation.run_eval --subset scans     # 30 prawdziwych skanów organizatora (OCR, kilka minut)
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
