# Jak dodać nową klasę dokumentu (bez zmian w kodzie)

> Wersja robocza — do rozbudowania (przykład „krok po kroku” + zrzuty ekranu z UI).

1. Skopiuj istniejący plik, np. `config/doc_types/przelew.yaml` → `config/doc_types/<nowa_klasa>.yaml`.
2. Ustaw `id`, `name`, `description`.
3. **Rozpoznawanie** — `classification.signals`: wzorce regex z wagami (`scope: head` = tylko początek dokumentu), `negative_signals`, próg `min_score`. Polskie znaki są ignorowane przy dopasowaniu (odporność na OCR).
4. **Struktura wyniku** — `output`: drzewo pól jak w oczekiwanym JSON-ie. Typy: `string | integer | number | boolean | date | object | array` (`properties` dla obiektu, `items` dla listy).
5. **Każde pole** może mieć:
   - `description` — opis dla LLM (najważniejsze w trybie `llm`/`hybrid`),
   - `labels` / `patterns` / `section` / `item_split` / `scope` / `case_sensitive` — wskazówki dla ekstraktora regułowego,
   - `normalize: [...]` — normalizatory (`date_pl`, `amount_pl`, `digits_only`, `account_number`, `bzp`, `ted`, `strip_quotes`, `collapse_whitespace`, `person_name`, `email`, `strip`),
   - `validate: [...]` — miękkie walidatory (`nip`, `regon`, `krs`, `pesel`, `iban_pl`),
   - `ground: exact | digits | fuzzy | none` — jak sprawdzić, że wartość występuje w tekście,
   - `dictionary` / `derived_from` — wartości ze słownika w `config/dictionaries/`.
6. `extraction.instructions` — dodatkowe reguły dla LLM w języku naturalnym.
7. Sprawdź konfigurację: `python -m odwolania2json doc-types` oraz `python -m odwolania2json schema <nowa_klasa>`.
8. Uruchom na przykładowym dokumencie: `python -m odwolania2json classify plik.pdf`, potem `./start.sh`.

Przykład demonstracyjny (TODO): klasa `ogloszenie_ted` dla „negatywnego przykładu” organizatora.
