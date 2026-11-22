# Architektura

JaysUncle zamienia PDF-y postępowania odwoławczego (także skany) na kanoniczny JSON. Założenia:

- **Konfiguracja zamiast kodu** — kod nie zna żadnej klasy dokumentu; wszystko, co specyficzne dla klasy (sygnały rozpoznawania, struktura JSON, etykiety, wzorce, słowniki), jest w plikach YAML.
- **Precyzja ponad zgadywanie** — błędna wartość kosztuje FP + FN, a `null` tylko FN; niepewne wartości zwracamy jako `null`.
- **Przejrzystość** — każda wartość ma ślad: strona, fragment tekstu, metoda, pewność, odrzuceni kandydaci.
- **Działa offline na laptopie biurowym** (16 GB RAM, bez GPU): reguły i OCR lokalnie; LLM (Qwen3.5-9B) przez API z serwerami w UE — dla nowych typów dokumentów i jako opcja dla wbudowanych.

## Potok

```
input/<sprawa>/<plik>.pdf
        │
        ▼
 1. INGEST     ingest/pdf.py · ocr.py · cache.py
               warstwa tekstowa: pdfplumber (tekst + słowa ze współrzędnymi)
               skan / pusta lub „śmieciowa” warstwa → OCR lokalnie (RapidOCR PP-OCRv6, 300 dpi)
               → Document: strony (tekst, słowa z bbox, źródło, pewność OCR); wynik w cache
        │
        ▼
 2. CLASSIFY   classify/rules.py  (+ classify/llm.py, gdy reguły są niepewne i LLM jest dostępny)
               ważone sygnały regex z YAML, bez polskich znaków (odporność na OCR), próg min_score
               ── żadna klasa ──► output/<sprawa>/<plik>.json z pustym obiektem {}
        │
        ▼
 3. EXTRACT    extract/rules.py → kandydaci z reguł (wartość, metoda, pewność, dowód)
               extract/llm.py   → kandydaci z LLM (tryb hybrid/llm; JSON Schema generowany z YAML)
        │
        ▼
 4. MERGE      extract/merge.py
               normalizacja → weryfikacja w tekście → walidatory (NIP, REGON, KRS, PESEL, IBAN)
               → wybór kandydata → pewność < 0.5 → null → pola pochodne ze słownika
        │
        ▼
 5. OUTPUT     output/writer.py
               output/<sprawa>/<plik>.json   +   traces/<sprawa>/<plik>.trace.json
```

**1. Ingest.** Strona idzie do OCR, gdy warstwa tekstowa ma mniej niż 40 znaków, ma mało liter i cyfr (np. `(cid:12)`), albo strona jest obrazem z niewielką ilością tekstu na wierzchu (np. pieczęć systemu e-doręczeń). Słowa przełamane myślnikiem na końcu linii są scalane. Gdy pdfplumber nie potrafi otworzyć pliku, czyta go PDFium. Wynik (także OCR) trafia do cache `.cache/ingest` (klucz: sha256 pliku + ustawienia), więc każdy plik jest OCR-owany tylko raz.

**2. Classify.** Wynik klasy = suma wag pasujących sygnałów minus wagi `negative_signals` (`scope: head` = tylko początek dokumentu). Wygrywa najlepsza klasa, jeśli osiąga swój `min_score`; pewność zależy od przewagi nad drugą klasą (`classification.min_margin`). Rodzaj dokumentu rozpoznajemy po treści, nigdy po nazwie pliku.

**3. Extract.** Ekstraktor regułowy realizuje ogólne mechanizmy sterowane wskazówkami z YAML: `patterns` (regex), `labels` („Etykieta: wartość”, wartość w następnej linii, etykiety rozbite przez OCR), `section` (obszar dokumentu), `item_split` (elementy listy), `strategy` + `params` (nazwane heurystyki: `first_line`, `persons`, `signatories`, `dictionary`, `date`, `flag`, `item_flag`, `entity_type`, `item_text`). Każdy kandydat ma dowód: stronę i fragment tekstu.

**4. Merge.** Dla każdego pola: normalizacja kandydatów (np. `date_pl`, `amount_pl`, `account_number`), weryfikacja, że wartość występuje w tekście (`ground: exact | digits | fuzzy`; brak → kara do pewności), miękkie walidatory sum kontrolnych (nie usuwają wartości, tylko zmieniają pewność), wybór najlepszego kandydata (w trybie `hybrid` najpierw spośród kandydatów z reguł — zob. [LLM](#llm)), próg `extraction.min_confidence: 0.5`. Pola pochodne (np. opis czynności z identyfikatora) wypełnia słownik.

**5. Output.** Struktura katalogów `output/` odwzorowuje `input/` 1:1, z tą samą nazwą pliku. Dokument nierozpoznany → pusty obiekt JSON `{}` (`output.unknown_output: empty_object`; alternatywy: `empty_file` = plik 0 bajtów, `none` = brak pliku).

## Mapa modułów

| Moduł | Rola |
|---|---|
| `odwolania2json/pipeline.py` | orkiestracja potoku, równoległe przetwarzanie dokumentów, zapis wyników |
| `odwolania2json/cli.py` | komendy: `run`, `classify`, `inspect`, `doc-types`, `schema`, `web` |
| `odwolania2json/settings.py` | `config/settings.yaml` + nadpisania ze zmiennych środowiskowych (`.env`) |
| `odwolania2json/spec.py` | format YAML klasy dokumentu (pydantic, literówki w kluczach = błąd), rejestr konfiguracji, kontrola regexów/nazw, JSON Schema dla LLM |
| `odwolania2json/models.py` | `Document`, `Page`, `Word`, `Candidate`, `Evidence`, `FieldResult`, `DocumentResult` (+ ślad) |
| `odwolania2json/paths.py` | ścieżki pól w zagnieżdżonym JSON-ie (`Odwolujacy[0].nip`) |
| `odwolania2json/ingest/` | `pdf.py` (warstwa tekstowa, decyzja o OCR), `ocr.py` (silniki OCR, kolejność czytania, korekty), `cache.py` |
| `odwolania2json/classify/` | `rules.py` (sygnały z YAML), `llm.py` (klasyfikacja zero-shot — opisy klas z YAML) |
| `odwolania2json/extract/` | `rules.py` (ekstraktor regułowy + strategie), `llm.py` (prompt i schemat z YAML), `merge.py` (weryfikacja i wybór) |
| `odwolania2json/normalize/` | normalizatory: daty, kwoty, rachunki, BZP/TED, imiona i nazwiska, kody, białe znaki… |
| `odwolania2json/validate/` | walidatory sum kontrolnych: NIP, REGON, KRS, PESEL, IBAN (PL) |
| `odwolania2json/llm/client.py` | klient dowolnego API zgodnego z OpenAI |
| `odwolania2json/output/writer.py` | ścieżki wyników, `{}` dla dokumentu nierozpoznanego |
| `odwolania2json/api/` | panel webowy: `app.py` (FastAPI), `store.py` (sesja dokumentów), `results.py`, `highlight.py` (podświetlenia), `workshop.py` (edytor klas) |
| `web/` | frontend: czysty HTML/CSS/JS, bez kroku budowania i bez CDN |
| `evaluation/` | `score.py` (metryka F1), `run_eval.py` (przebieg na zbiorze treningowym) |
| `scripts/` | `fetch_data.py` (dane UZP), `make_scans.py` (syntetyczne skany), `ocr_benchmark.py` |

## Konfiguracja zamiast kodu

| Plik | Zawartość |
|---|---|
| `config/doc_types/<klasa>.yaml` | jedna klasa dokumentu: `classification` (sygnały, próg), `extraction` (instrukcje dla LLM), `output` (drzewo pól: typy, opisy, wskazówki dla reguł, `normalize`, `validate`, `ground`, słowniki) |
| `config/dictionaries/<słownik>.yaml` | słowniki wartości, np. `czynnosc_powod`: `id`, `opis`, `hint` (dla LLM), `keywords` (dla reguł) |
| `config/prompts/*.md` | prompty systemowe LLM (`classify_system.md`, `extract_system.md`); miejsca na instrukcje i słowniki wypełniane z YAML |
| `config/settings.yaml` | parametry potoku: ingest/OCR, klasyfikacja, tryb ekstrakcji i próg pewności, LLM, format wyjścia, liczba wątków |

- Nowa klasa = nowy plik YAML — zob. [NOWA_KLASA_DOKUMENTU.md](NOWA_KLASA_DOKUMENTU.md); przykład działający bez zmian w kodzie: [examples/ogloszenie_ted.yaml](examples/ogloszenie_ted.yaml).
- Ta sama konfiguracja steruje regułami, LLM (schemat JSON, opisy pól, lista klas dla klasyfikatora) i panelem webowym.
- `python -m odwolania2json doc-types` sprawdza całą konfigurację (regexy, nazwy normalizatorów, walidatorów, strategii, słowników).
- Zmienne środowiskowe (`.env`) mają pierwszeństwo przed `settings.yaml`: `EXTRACTION_MODE`, `OCR_ENGINE`, `TESSERACT_CMD`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_EXTRA_BODY`, `LLM_PROMPT_SUFFIX`, `PIPELINE_WORKERS`.

## Przejrzystość ekstrakcji

- Ślad każdego dokumentu: `traces/<sprawa>/<plik>.trace.json` — klasyfikacja (wynik punktowy każdej klasy, metoda, uzasadnienie), dla każdego pola: wartość, wartość surowa, metoda (`rules:pattern`, `rules:label`, `rules:<strategia>`, `llm`, `derived`), pewność, dowód (strona + fragment tekstu), kandydaci, uwagi (np. „value not found in text”, „best confidence 0.42 < 0.5 → null”), czasy etapów, błędy.
- Panel webowy pokazuje to samo wizualnie: podgląd stron z **podświetlonym źródłem każdej wartości** (współrzędne słów z pdfplumber, a dla skanów z OCR), tabela pól z metodą, pewnością i źródłem, lista kandydatów, wartości odrzucone z powodem, pewność OCR strony.

## Panel webowy

`./start.sh web` (Windows: `start.bat web`, Docker: `docker compose up web`) → http://localhost:8000. Serwer działa lokalnie, na tym samym komputerze — niczego nie trzeba hostować.

- **Dokumenty** — wgranie PDF-ów (przeciągnij i upuść, kilka naraz). Dla każdego: rozpoznany typ, wynik JSON (dokładnie taki plik, jaki zapisuje CLI) do skopiowania lub pobrania, pola z ludzkimi nazwami, pewnością i fragmentem źródłowym, podgląd stron z **podświetlonym źródłem każdej wartości** (także na skanach; powiększanie, przesuwanie myszą).
- **Typy dokumentów** — lista typów z przełącznikiem „Rozpoznawany / Wyłączony”; podgląd i edycja typu w formularzu (dla urzędnika) albo w YAML (tryb zaawansowany): **Sprawdź** · **Testuj na pliku PDF** (bez zapisywania) · **Zapisz** (do `config/doc_types/`, potok przeładowany od razu; komentarze w pliku zostają). Nowy typ: kreator w 3 krokach (nazwa → frazy rozpoznawania, np. kliknięte w przykładowym PDF → informacje do odczytania).
- **Słowniki** — podgląd słowników wartości (np. powody odwołania).
- `ODWOLANIA2JSON_WEB_READONLY=1` wyłącza zapisywanie typów z panelu.

## LLM

- **Klient:** dowolne API zgodne z OpenAI (`openai` SDK), bez kodu specyficznego dla dostawcy; konfiguracja wyłącznie w `.env` (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`). Lokalnie można użyć Ollama / vLLM / llama.cpp.
- **Dostawca:** OVHcloud AI Endpoints — Francja, serwery w UE; według OVH dane nie są wykorzystywane do trenowania modeli.
- **Modele brane pod uwagę** (wyłącznie open-weights, wszystkie w OVHcloud AI Endpoints; F1 = sam LLM, 10 spraw / 40 PDF):

| Model | Parametry | Punkty za wielkość modelu (regulamin) | F1 (tylko LLM) | Uwagi |
|---|---|---|---|---|
| **Qwen3.5-9B** | 9 mld | > 8–14B → 18 pkt | 0.940 | z `reasoning_effort: none` odpowiada w 1–2 s |
| Mistral-Small-3.2-24B-Instruct-2506 | 24 mld | > 14–32B → 12 pkt | 0.964 | najlepszy wynik, ale −6 pkt za wielkość |
| Olala-v1.0-mini-7A1B | 6,8 mld (łącznie) | ≤ 8B → 20 pkt | 0.475 | „myślenia” nie da się wyłączyć: brak odpowiedzi lub minuty na dokument |
| laguna-xs-2.1 | 33 mld (MoE, 3B aktywnych) | > 32–70B → 8 pkt | — | liczą się wszystkie parametry MoE |

**Wybrany model: Qwen3.5-9B** z wyłączonym rozumowaniem (`LLM_EXTRA_BODY={"reasoning_effort": "none"}` — bez tego model zużywa ~2000 tokenów i ~18 s na „myślenie”). Wbudowane typy (odwołanie, pełnomocnictwo, przelew) działają domyślnie na regułach: na 30 skanach organizatora reguły dają F1 **0.969**, hybryda z Qwen3.5-9B **0.955** (LLM podnosi kompletność, ale wypełnia pola puste w referencji), hybryda z Olala **0.942**. LLM czyta dane **nowych typów dokumentów** dodanych w panelu (tryb `llm` w YAML typu — wystarczą opisy pól), może rozstrzygać niepewną klasyfikację i jest dostępny dla wbudowanych typów jako `EXTRACTION_MODE=hybrid|llm`. Bez LLM aplikacja nie potrzebuje klucza API ani internetu. Lokalnie ten sam model: Ollama `qwen3.5:9b`.

- **Polityka hybrydowa (tryb `hybrid`):** najpierw reguły; LLM uzupełnia tylko pola, które reguły zostawiły puste — nigdy nie nadpisuje wartości z reguł (`extraction.llm_policy: fill_missing` w `config/settings.yaml`; alternatywa `compete` = wygrywa kandydat o wyższej pewności). Wartości z LLM przechodzą tę samą normalizację i weryfikację: wartość tekstowa lub numer, którego nie ma w tekście dokumentu, spada poniżej progu pewności i staje się `null`.
- **Co dostaje model:** prompt systemowy z `config/prompts/extract_system.md` + instrukcje klasy i słowniki z YAML; tekst dokumentu ze znacznikami stron; JSON Schema wygenerowany z sekcji `output` klasy (structured output; gdy serwer go nie obsługuje — automatycznie `json_object`, potem zwykły prompt). Temperatura 0, ponowienia z informacją o błędzie, cache odpowiedzi w `.cache/llm`.
- **Klasyfikacja:** gdy reguły są niepewne, a LLM jest dostępny, o klasę pytany jest LLM (zero-shot; lista klas i opisy z YAML; `classification.use_llm_fallback` w `settings.yaml`).
- **Bez LLM:** tryb `rules` w ogóle nie tworzy klienta LLM (pełny offline). Gdy brak klucza, internetu lub modelu, aplikacja raz wypisuje przyczynę i przetwarza dokumenty w trybie reguł.
- **Klucz API:** tylko w pliku `.env` (w `.gitignore`), nigdy w repozytorium; dla jury podajemy go w formularzu zgłoszenia.

## OCR

- Domyślnie **RapidOCR** (onnxruntime, CPU) z modelami **PP-OCRv6-small**, które są w paczce pip — nic nie jest pobierane przy pierwszym uruchomieniu, nie trzeba instalować programów systemowych.
- Strony renderowane w 300 dpi; rozpoznawanie na wycinkach w pełnej rozdzielczości, linia po linii; linie o pewności < 0.9 czytane ponownie z wycinka o wzmocnionym kontraście; litery spoza polskiego alfabetu (ı ñ š ž à é ç…) zamieniane na polskie/podstawowe; pomijane blade linie przebite z drugiej strony kartki (lustrzane); strona czytana słabo jest próbowana po obrocie o 180° / 90°.
- Wynik: tekst w kolejności czytania + słowa ze współrzędnymi w punktach PDF (te same co z pdfplumber) — dzięki temu podświetlenia w panelu działają także na skanach.
- Alternatywy (`OCR_ENGINE`): `easyocr` (opcjonalnie, `requirements-ocr.txt`, torch), `tesseract` (program systemowy + język `pol`; jest w obrazie Docker), `auto` = pierwszy dostępny z kolejności rapidocr → easyocr → tesseract.
- OCR działa lokalnie (OCR przez API liczyłby się jak LLM). Porównanie konfiguracji: `python scripts/ocr_benchmark.py`.

## Równoległość i wydajność

- CLI przetwarza dokumenty równolegle w wątkach (`pipeline.workers: 4`, `PIPELINE_WORKERS`); wyniki są zapisywane w miarę kończenia; błąd jednego PDF-a nie zatrzymuje partii (błąd trafia do śladu, a reszta się przetwarza).
- Instancje silnika OCR są współdzielone, a wywołania serializowane blokadą (onnxruntime i tak używa wszystkich rdzeni); PDFium nie jest bezpieczne wątkowo, więc każde wywołanie przechodzi przez wspólną blokadę `PDFIUM_LOCK`.
- Zapisy cache są atomowe (plik tymczasowy + `os.replace`), więc równoległe wątki i procesy nie widzą niedokończonych wpisów.
- Panel webowy przetwarza dokumenty w osobnej puli (2 wątki), a podglądy stron i walidację YAML w puli wątków — pętla zdarzeń serwera nie jest blokowana.

## Komendy

`./start.sh` / `start.bat` = `python -m odwolania2json run` na folderach `input/` → `output/`; `./start.sh --help` pokazuje wszystkie komendy.

| Komenda (`python -m odwolania2json …`) | Opis |
|---|---|
| `run -i input -o output` | przetwarza wszystkie PDF-y |
| `classify plik.pdf …` | punkty klasyfikacji dla każdego typu dokumentu |
| `inspect plik.pdf` | tekst każdej strony (warstwa tekstowa / OCR) |
| `doc-types` | lista typów dokumentów + walidacja konfiguracji YAML |
| `schema przelew [--json-schema]` | pusty szablon JSON / schemat przekazywany do LLM |
| `web` | panel webowy |

Lokalnie zamiast OVHcloud można użyć dowolnego serwera zgodnego z OpenAI, np. Ollama (`ollama pull qwen3.5:9b`, `LLM_BASE_URL=http://localhost:11434/v1`; w Dockerze: `docker compose --profile local-llm up -d ollama`).

## Docker

- `Dockerfile`: `python:3.13-slim`, tylko CPU, z Tesseractem i językiem polskim; domyślnie przetwarza `input/` → `output/`.
- `docker-compose.yml`: `app` (przetwarzanie partii), `web` (panel na porcie 8000), opcjonalnie `ollama` (profil `local-llm`). Woluminy: `input/`, `output/`, `traces/` oraz `config/` — klasy dokumentów można edytować bez przebudowy obrazu. Plik `.env` jest opcjonalny.
- Sprawdzone: obraz się buduje, przetwarzanie z CLI i panel webowy działają w kontenerach.

## Bezpieczeństwo

- Klucz API wyłącznie w `.env` (poza repozytorium); `.env.example` zawiera tylko miejsca do uzupełnienia.
- Dane: w trybie `rules` żaden dokument nie opuszcza komputera; w trybie `hybrid`/`llm` tekst dokumentu trafia tylko do skonfigurowanego API LLM (OVHcloud, UE).
- Panel webowy domyślnie nasłuchuje tylko na `127.0.0.1` (w Dockerze `0.0.0.0`, port 8000).
- `ODWOLANIA2JSON_WEB_READONLY=1` wyłącza zapisywanie klas z panelu (HTTP 403) — zalecane, gdy panel jest dostępny dla innych osób.
- Limity uploadu: 50 plików na żądanie, 64 MB na PDF, 256 MB na żądanie (sprawdzane z `Content-Length` i w trakcie odbioru); pliki zapisywane pod losowym identyfikatorem i stałą nazwą w katalogu tymczasowym, usuwane po czasie i przy zamknięciu aplikacji; limit pikseli podglądu strony.
- YAML z panelu jest traktowany jako niezaufany: `yaml.SafeLoader`, limit węzłów po rozwinięciu aliasów (ochrona przed „billion laughs”), limit zagnieżdżenia, identyfikator klasy tylko `[a-z0-9_]`; jeśli po zapisie konfiguracja nie daje się wczytać, przywracana jest poprzednia wersja pliku.
- **Regexy z konfiguracji są zaufanym wejściem:** kto może edytować `config/` (lub zapisywać klasy w panelu), może wpisać regex o katastrofalnym nawracaniu (ReDoS) — moduł `re` nie ma limitu czasu. Dlatego przy współdzielonym panelu należy włączyć tryb tylko do odczytu.
