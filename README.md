# Odwołania2JSON

**Od dokumentów do danych.** Automatyczna zamiana dokumentów postępowania odwoławczego przed KIO (odwołanie, pełnomocnictwo, potwierdzenie przelewu — PDF, także skany) na kanoniczny model JSON.

Zadanie konkursowe Urzędu Zamówień Publicznych, HackNation 2026 ([opis i dane organizatora](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026)).

> Status: **działa** — potok PDF → JSON, OCR skanów, panel webowy, 532 testy. Wbudowane typy dokumentów działają na **regułach** (bez LLM, offline) — najlepszy zmierzony wynik; **LLM (Qwen3.5-9B, OVHcloud, serwery w UE)** odczytuje dane nowych typów dokumentów dodanych w panelu ([docs/ARCHITEKTURA.md → LLM](docs/ARCHITEKTURA.md#llm)).

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

## Wyniki

Pomiar z 21.11.2026 w trybie reguł (bez LLM), metryka F1 po polach ([`evaluation/score.py`](evaluation/score.py)):

| Zbiór | Dokumenty | F1 | Klasyfikacja |
|---|---|---|---|
| Skany organizatora (OCR) | 30 | **0.969** (P 0.971, R 0.968) | 30/30 |
| Sprawy syntetyczne organizatora | 180 (60 spraw) | **1.000** | 180/180 |
| Dokumenty negatywne (→ pusty `.json`) | 16 | 0 FP | 16/16 |

- Skany wg typu: odwołanie 0.989 · pełnomocnictwo 0.987 · potwierdzenie przelewu 0.878.
- OCR (lokalny, bez GPU): odnalezione wartości referencji na skanach 0.83 → **0.955** po przejściu na RapidOCR PP-OCRv6 i korektach.
- Z LLM (Qwen3.5-9B, bez „myślenia”): hybryda na tych samych 30 skanach **0.955** (LLM podnosi kompletność, ale wypełnia pola puste w referencji), sam LLM na 10 sprawach **0.931**. Dlatego wbudowane typy działają na regułach, a LLM odczytuje nowe typy dokumentów.
- Szczegóły, lista pozostałych błędów i sposób odtworzenia: [docs/RAPORT_JAKOSCI.md](docs/RAPORT_JAKOSCI.md). Architektura: [docs/ARCHITEKTURA.md](docs/ARCHITEKTURA.md).

## Architektura w skrócie

```
PDF ─► INGEST (pdfplumber; skan → lokalny OCR RapidOCR PP-OCRv6)
    ─► CLASSIFY (sygnały z YAML + opcjonalnie LLM)
    ─► EXTRACT (reguły/regex z YAML + LLM ze schematem JSON z YAML)
    ─► MERGE (weryfikacja: wartość musi występować w tekście, sumy kontrolne; niepewne → null)
    ─► NORMALIZE (daty ISO, kwoty, numery, słowniki) ─► output/*.json + traces/*.trace.json
```

- **Klasa dokumentu = jeden plik YAML** w `config/doc_types/` (sygnały rozpoznawania, struktura wyniku, opisy pól, wskazówki ekstrakcji, normalizacja). **Nowa klasa nie wymaga zmian w kodzie** — zob. [docs/NOWA_KLASA_DOKUMENTU.md](docs/NOWA_KLASA_DOKUMENTU.md).
- Tryby ekstrakcji (`config/settings.yaml` lub `EXTRACTION_MODE` w `.env`): `rules` (domyślny; bez LLM, offline) · `llm` · `hybrid`. Każdy typ dokumentu może mieć własny tryb (`extraction.mode` w jego YAML) — typy dodane w panelu używają `llm`.
- LLM: **wyłącznie modele open-source** przez API zgodne z OpenAI. Aplikacja działa na laptopie biurowym (16 GB RAM, Intel i7, bez GPU) — zgodnie z wymaganiami organizatora LLM jest wywoływany przez API dostawcy z serwerami w UE/EOG (klucz w `.env`, nie w repozytorium). Lokalnie można też użyć Ollama / vLLM / llama.cpp.
- Model LLM, dostawca (OVHcloud AI Endpoints, serwery w UE) i polityka hybrydowa (LLM uzupełnia tylko pola puste po regułach): [docs/ARCHITEKTURA.md → LLM](docs/ARCHITEKTURA.md#llm).
- Pełny opis potoku, modułów, OCR i bezpieczeństwa: [docs/ARCHITEKTURA.md](docs/ARCHITEKTURA.md).
- Wszystkie zależności open source o licencjach permisywnych (MIT / BSD / Apache-2.0).

## Wymagania

- **Windows, macOS lub Linux** — obsługiwane wszystkie (skrypty `.bat` dla Windows, `.sh` dla macOS/Linux).
- **Python 3.13+** — [python.org/downloads](https://www.python.org/downloads/) (Windows: przy instalacji zaznacz „Add python.exe to PATH”). Jeśli go brak, `start.bat` / `start.sh` wyświetli komunikat z linkiem i zakończy działanie, niczego nie instalując.
- **Internet** — do wywołań API LLM (OVHcloud AI Endpoints, serwery w UE) i do instalacji zależności przy pierwszym uruchomieniu. Bez internetu lub bez klucza API aplikacja nadal działa — w trybie reguł (bez LLM).
- OCR skanów działa od razu po instalacji (RapidOCR, bez programów systemowych). Tesseract jest opcjonalny (`OCR_ENGINE=tesseract`, wymaga zainstalowanego Tesseracta z językiem polskim).

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
LLM_MODEL=Qwen3.5-9B                           # 9B open-weights; wybór i pomiary: docs/ARCHITEKTURA.md (sekcja LLM)
LLM_EXTRA_BODY={"reasoning_effort": "none"}    # bez „myślenia”: odpowiedź w 1–2 s zamiast ~18 s

EXTRACTION_MODE=rules      # rules | llm | hybrid — wbudowane typy działają na regułach; LLM czyta nowe typy z panelu
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

## Panel webowy

`./start.sh web` (Windows: `start.bat web`) → http://localhost:8000 (w Dockerze: `docker compose up web`).

- **Dokumenty** — wgraj PDF-y (przeciągnij i upuść, kilka naraz); dla każdego: rozpoznana klasa i pewność, pola z metodą i pewnością, JSON do skopiowania lub pobrania (dokładnie taki plik, jaki zapisuje CLI), podgląd stron z **podświetlonym źródłem każdej wartości** (także na skanach).
- **Klasy dokumentów** — edytor YAML klas: **Sprawdź** (błędy z numerami linii) · **Testuj na PDF** (bez zapisywania) · **Zapisz** (do `config/doc_types/`, potok przeładowany od razu). Nowa klasa bez zmian w kodzie — zob. [docs/NOWA_KLASA_DOKUMENTU.md](docs/NOWA_KLASA_DOKUMENTU.md).
- `ODWOLANIA2JSON_WEB_READONLY=1` wyłącza zapisywanie klas z panelu.

## Dane treningowe i ewaluacja (F1)

```bash
python scripts/fetch_data.py --sample-input 3   # dane UZP → data/uzp/ (+ 3 sprawy do input/)
python -m evaluation.run_eval                    # 60 spraw syntetycznych + dokumenty negatywne (~20 s)
python -m evaluation.run_eval --subset scans     # 30 skanów organizatora (OCR, kilka minut)
python -m evaluation.run_eval --subset negatives # dokumenty negatywne z dane/negatywne
python -m evaluation.run_eval --mode rules --limit 10
python scripts/make_scans.py && python -m evaluation.run_eval --data data/scans/dane   # syntetyczne skany
python scripts/ocr_benchmark.py                  # porównanie konfiguracji OCR na skanach organizatora
```

Raport: `runs/<czas>/report.json` (F1 ogółem, wg typu dokumentu, wg pola, przykłady błędów). Metryka: porównanie każdego pola z referencją — trafienie TP, brak FN, nadmiarowa/błędna wartość FP. Opis metryki i wyniki: [docs/RAPORT_JAKOSCI.md](docs/RAPORT_JAKOSCI.md).

## Struktura repozytorium

```
config/            konfiguracja: settings.yaml, doc_types/*.yaml (klasy), dictionaries/, prompts/
odwolania2json/    kod: ingest/ classify/ extract/ normalize/ validate/ llm/ output/ api/ + pipeline.py, cli.py
web/               interfejs webowy (statyczny HTML/JS, bez kroku budowania)
evaluation/        metryka F1 i przebieg na zbiorze treningowym
scripts/           pobieranie danych, generowanie syntetycznych skanów, benchmark OCR
tests/             pytest (testy normalizatorów i walidatorów pełnią rolę specyfikacji)
docs/              dokumentacja (architektura, raport jakości, dodawanie typów dokumentów, przykład nowego typu)
input/ output/     foldery wymagane przez organizatora
```

## Testy

```bash
pytest -q        # 532 testy
ruff check .
```

## Zespół

Zespół *zschie* — HackNation 2026.
