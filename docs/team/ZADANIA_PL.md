# Zadania dla poszczególnych osób (wariant C — hybryda)

> Jeśli wybierzemy A albo B — zob. sekcję „Co się zmienia” na końcu. Warianty: [WARIANTY_PL.md](WARIANTY_PL.md) · Wersja rosyjska: [ZADANIA_RU.md](ZADANIA_RU.md)

## Start dla wszystkich (15 minut)

```bash
git pull
./setup.sh                 # Windows: setup.bat   (potrzebny Python 3.13)
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python scripts/fetch_data.py --sample-input 3
python -m evaluation.run_eval --mode rules     # powinno pokazać F1 ≈ 0.18 — to nasz punkt wyjścia
pytest -q                                      # wszystko zielone; 43 testy "skipped" = jeszcze niezaimplementowane
```

**Zasady:**
- Własna gałąź `feat/<imię>-<co>`, małe PR-y do `master`. Przed PR: `pytest -q`, `ruff check .`, `python -m evaluation.run_eval`. W opisie PR — **F1 przed → po**.
- **W VS Code wszystko jednym przyciskiem:** `Cmd/Ctrl+Shift+P` → `Tasks: Run Test Task` → „✅ Przed PR”. Przed rozpoczęciem pracy, na świeżym `master`, uruchomcie raz `Tasks: Run Task` → „Eval: zapisz baseline”. Wtedy „Przed PR” sam wypisze `F1: przed → po` do opisu PR. Zadania działają na macOS i Windows.
- Nie ruszamy cudzych plików bez uzgodnienia. Wspólne pliki (`spec.py`, `models.py`, `pipeline.py`) zmieniamy tylko przez Polinę.
- **Praca z webowym LLM:** wklejacie do czatu (1) cały swój plik, (2) testy do niego, (3) odpowiedni YAML z `config/doc_types/`, (4) zdanie „zaimplementuj TODO, nie zmieniaj sygnatur funkcji”. Wynik → `pytest` → PR.
- Debugowanie: `python -m odwolania2json inspect plik.pdf` (tekst stron), `... classify plik.pdf` (punkty klas). Błędy według pól — w `runs/<czas>/report.json`, sekcja `errors`.

---

## Polina (Claude Code) — rdzeń, LLM, integracja

1. **Pierwsza godzina: pytania do mentorów** (lista w WARIANTY_PL.md §8). Najważniejsze: jak liczone jest F1, jak jury uruchomi rozwiązanie i jaki LLM będzie miało, co oznacza `czynnosc_powod_id = 12`.
2. **Endpoint LLM**: lokalna Ollama (Qwen3) i/lub darmowe API modelu z otwartymi wagami. Wpisać do `.env.example`, przekazać zespołowi.
3. **Ekstrakcja LLM** (`odwolania2json/extract/llm.py`, `config/prompts/`): prompty, few-shot, dobór kontekstu, cytaty-dowody dla każdego pola.
4. **Merge** (`extract/merge.py`): wagi pewności, poprawianie błędów OCR w numerach przez sumy kontrolne, weryfikacja spójności między dokumentami sprawy.
5. **czynnosc_powod_id / czynnosc_dzien** — najtrudniejsze pola (LLM + słownik).
6. Integracja, review PR-ów, przebieg ewaluacji na wszystkich 60 sprawach co godzinę, finalne złożenie całości.

**Gotowe, gdy:** tryb hybrydowy daje F1 > 0.9 na zbiorze treningowym i nie wywraca się bez LLM.

## Sewa — PDF, OCR, skany, Docker

1. Zainstalować Tesseract (`brew install tesseract tesseract-lang`) i sprawdzić na prawdziwym skanie: `python -m odwolania2json inspect "input/szablon_case1/przyklad odwolanie.pdf"`. Porównać z RapidOCR (`OCR_ENGINE=rapidocr`).
2. `odwolania2json/ingest/ocr.py`: wstępne przetwarzanie obrazu (prostowanie, binaryzacja), **słowa ze współrzędnymi z OCR** → `Page.words` (potrzebne do podświetlania w panelu), pewność OCR dla strony.
3. `odwolania2json/ingest/pdf.py`: łączenie przeniesień („postępo-\nwania”), artefakty justowania, „śmieciowa” warstwa tekstowa → OCR.
4. `scripts/make_scans.py`: funkcja `degrade()` (obrót ±1.5°, szum, rozmycie, JPEG, szare tło, czarne zamazania jak w zanonimizowanym przykładzie) → `python -m evaluation.run_eval --data data/scans/dane`. **To nasz pomiar jakości na „prawdziwych” dokumentach.**
5. Cache ingestu (`.cache/ingest/<hash>.json`), żeby OCR nie liczył się od nowa przy każdej ewaluacji.
6. Docker: zbudować obraz, sprawdzić `docker compose run --rm app` i `docker compose up web` (wymagane do nagrody).

**Gotowe, gdy:** F1 na syntetycznych skanach jest najwyżej 10 p.p. niższe niż na PDF-ach tekstowych.

## Lesza — reguły i ekstrakcja regex, analiza błędów

1. `odwolania2json/extract/rules.py` — po kolei według TODO w pliku, po każdym kroku przebieg ewaluacji:
   1. `labels` — „Etykieta: wartość” albo wartość w następnej linii (od razu pomoże w przelewach na wszystkie 6 pól);
   2. `section` — szukanie pól podrzędnych tylko w obrębie bloku (Odwołujący ≠ Zamawiający, obaj mają NIP!);
   3. listy + `item_split` — 2 zamawiających, konsorcja, kilku pełnomocników;
   4. heurystyki (np. `nazwa` = pierwsza linia sekcji).
2. Uzupełniać `labels`/`patterns` w `config/doc_types/*.yaml` na podstawie błędów z `report.json`. **Kod ma pozostać uniwersalny** — wszystko, co specyficzne dla klasy, żyje w YAML.
3. Analiza `czynnosc_powod_id` na 60 sprawach: jakie sformułowania pierwszego zarzutu prowadzą do którego id → `keywords` i `hint` w `config/dictionaries/czynnosc_powod.yaml`.
4. Tabela „pole → F1 → dlaczego błędy” po każdej większej zmianie (wrzucać na czat zespołu).

**Gotowe, gdy:** w trybie `rules` F1 > 0.7 na zbiorze treningowym.

## Artem — normalizatory, panel (web UI), dokumentacja

1. **Pierwsze 1–2 godziny: normalizatory i walidatory** — najszybszy przyrost F1.
   - `odwolania2json/normalize/values.py` (`date_pl`, `amount_pl`, `account_number`, `bzp`, `ted`), `normalize/text.py` (`strip_quotes`, `email`, `person_name`), `validate/checksums.py`.
   - Specyfikacja — docstringi + `tests/test_normalizers.py`, `tests/test_validators.py`. Zaimplementowana funkcja → jej testy przechodzą ze „skipped” na zielone.
   - Sama `date_pl` daje +45 poprawnych pól. Sprawdzenie: `python -m evaluation.run_eval --mode rules`.
2. **Panel (web UI)** — `web/` (teraz jest tam działająca zaślepka). Czysty HTML + CSS + JS, bez budowania. Biblioteki trzymamy lokalnie w `web/vendor/`: on-premise bez internetu!
   - wgrywanie kilku PDF-ów (drag & drop), lista dokumentów z typem i pewnością;
   - wynikowy JSON (podświetlanie składni, przycisk „pobierz”);
   - **podgląd PDF (PDF.js) z podświetleniem, skąd pochodzi każde pole**: klik w pole → strona i fragment. Dane: `fields[].evidence {page, snippet, bbox}`;
   - oznaczenia metody (regex / LLM) i pewności → to nasza „przejrzystość” dla jury;
   - jasny i ciemny motyw, dobry wygląd na laptopie i na projektorze.
   - API i format odpowiedzi: `odwolania2json/api/app.py` (docstring na górze). Uruchomienie: `./start.sh web` → http://localhost:8000
3. **Dokumentacja:** `docs/NOWA_KLASA_DOKUMENTU.md` (przykład krok po kroku), `docs/ARCHITEKTURA.md`, dopracowanie README — bliżej końca.
4. **Prezentacja** (razem z Poliną): problem → architektura → F1 → demo na żywo (dodanie nowej klasy YAML w 5 minut).

**Gotowe, gdy:** panel pokazuje wgranie → JSON → podświetlenie źródła; wszystkie testy normalizatorów są zielone.

---

## Synchronizacje

- Co 2–3 godziny: 10 minut — kto ma jakie F1 i co blokuje.
- 4 godziny przed terminem — **zamrożenie funkcji**: tylko poprawki, dokumentacja, prezentacja.

## Co się zmienia, jeśli wybierzemy inny wariant

- **A (tylko reguły):** punkty 3–5 Poliny przechodzą na pomoc Leszy (reguły dla czynności i imion). LLM niepotrzebny, `EXTRACTION_MODE=rules`.
- **B (tylko LLM):** Lesza zamiast `rules.py` robi weryfikację wartości z LLM w tekście i analizę błędów promptów. `EXTRACTION_MODE=llm`.
- Zadania Sewy i Artema nie zmieniają się w żadnym wariancie.
