# Odwołania2JSON — analiza zadania i warianty rozwiązania (PL)

> Dla zespołu: Polina, Sewa, Artem, Lesza · 21.11.2026 · Wersja rosyjska: [WARIANTY_RU.md](WARIANTY_RU.md)

## ⚠️ Aktualizacja od organizatora (21.11, ~16:00) — przeczytać najpierw

Organizator uzupełnił README i dane. Co się zmieniło:

1. **Maszyna jury:** laptop biurowy — 16 GB RAM, Intel i7, bez GPU. LLM tylko przez API, a **rozwiązanie oddajemy razem z naszym kluczem API**. Dostawca musi przetwarzać dane na serwerach w **UE/EOG** i mieć to w polityce prywatności. → Amerykańskie Groq / Cerebras / OpenRouter odpadają. Kandydaci: Mistral AI, Scaleway, OVHcloud (sprawdzić aktualne modele). Model nadal musi być open-source.
2. **Finał (50% „elastyczność”) liczony teraz tak:** 1–20 pkt za wielkość modelu — **im mniejszy model, tym więcej punktów**; 1–20 — łatwość dodawania nowych typów dokumentów; 1–10 — dodatkowe funkcje (np. GUI). → Hybryda z naciskiem na reguły i mały model to dokładnie to, czego chcą.
3. **GUI** oficjalnie daje punkty, ale nie jest wymagane: ważniejsza jest skuteczność ekstrakcji.
4. **Dokumenty negatywne → pusty plik `.json`**, a nie brak pliku. Już zrobione. Doszedł folder `dane/negatywne/` (12 PDF: orzeczenia KIO, ogłoszenia TED/BZP, broszury); takie pliki są też w niektórych sprawach.
5. **Skany organizatora z wzorcami:** `dane/skany/` — 30 sztuk (10 odwołań, 10 pełnomocnictw, 10 przelewów). To teraz główny pomiar OCR: `python -m evaluation.run_eval --subset scans`.
6. **Kategoria 12** = „Projektowane postanowienia umowy”. Dodana do słownika.
7. W dokumentacji potrzebny jest **opis wykorzystanego LLM**.
8. Na maszynie jury najpewniej **nie będzie Tesseracta** → OCR musi działać z pip (RapidOCR / EasyOCR) albo przez API.

**Pytania do mentorów (§8) — co zostało:** jak dokładnie liczone jest F1; czy jeden PDF może zawierać kilka dokumentów; limit czasu. Nowe: jak przekazać klucz API inaczej niż przez publiczny GitHub; „pusty plik” to 0 bajtów czy `{}`; treść kategorii 6 („Odpowiedzi i wyjaśnienia” w danych vs „Odpowiedzi/wyjaśnienia” w słowniku); numery rachunków z `PL` czy bez (we wzorcu skanu `z_rachunku` ma `PL`, w syntetyku nie). Zadanie prowadzą osoby z UZP: **Robert Król, Mateusz, Dominik Olczak** — nie ma ich na ogólnej liście mentorów, szukać przez Discord albo mentorzy@hacknation.pl.

## TL;DR

- **Trening na syntetyku, test na prawdziwych sprawach.** Mamy 60 syntetycznych spraw (180 PDF z warstwą tekstową), a ocena idzie na ukrytym zbiorze ≥10 *prawdziwych* spraw. Jedyny prawdziwy przykład w repozytorium organizatora to **skan bez warstwy tekstowej, z czarnymi zamazaniami (anonimizacja)**. Wniosek: OCR jest konieczny, a regexy dopasowane do generatora rozsypią się na prawdziwych dokumentach.
- **Etap 1 (wybór top-3 do finału) = 100% F1.** Finał = 50% F1 + 50% rozszerzalność (nowe klasy dokumentów wyłącznie przez konfigurację, bez zmian w kodzie) + przejrzystość.
- **Rekomendacja — wariant C, „hybryda sterowana konfiguracją”:** reguły/regex dla identyfikatorów + otwartoźródłowy LLM dla semantyki + weryfikacja, czy wartość naprawdę występuje w tekście + normalizacja. Każda klasa dokumentu to jeden plik YAML. Dla każdego pola zapisujemy źródło (strona, fragment, metoda, pewność). Bez LLM rozwiązanie też działa (spada do trybu reguł) — to zabezpieczenie na wypadek, gdyby jury nie miało dostępu do naszego API.

---

## 1. Co ustaliliśmy (fakty z PDF zadania i repozytorium UZP)

### Wejście / wyjście
- `input/<sprawa>/*.pdf` → `output/<sprawa>/<ta sama nazwa>.json`, drzewo katalogów odwzorowane 1:1.
- **Nazwa pliku nie gwarantuje typu**: w szablonie są „przyklad odwolanie.pdf” i „negatywny przyklad.pdf”. Klasyfikujemy po treści.
- 3 klasy: `odwolanie`, `pelnomocnictwo`, `przelew` (potwierdzenie przelewu). W jednej sprawie może być kilka pełnomocnictw.
- **Dokumenty negatywne**: w szablonie jest ogłoszenie TED i *nie powstaje* dla niego JSON. Potrzebne jest odrzucenie (klasa „inny”).

### Wymagania formalne (README repozytorium UZP)
- Python **3.13+**, `requirements.txt`, uruchomienie przez CLI albo `start.bat`, katalogi `input` i `output` w katalogu głównym.
- **Wyłącznie open source**, także LLM (otwarte wagi). Uruchomienie on-premise **bez GPU**, LLM przez API.
- Premiowane: nowe klasy dokumentów **tylko przez pliki konfiguracyjne**; **przejrzystość** ekstrakcji.
- Do wypłaty nagrody potrzebne są: kod, instrukcja uruchomienia, **Docker Compose**, dokumentacja (architektura, konfiguracja ekstrakcji danej klasy, dodanie nowej klasy).

### Ocena
- Etap 1: F1 na ukrytym zbiorze (≥10 prawdziwych spraw) — 100% oceny.
- Finał (maks. 3 zespoły): 50% F1 + 50% elastyczność/rozszerzalność. W PDF z opisem zadania wagi są inne: 50 / 30 / 20 (F1 / elastyczność / architektura i jakość).

### Dane
- 60 spraw × (`odwolanie.pdf`, `pelnomocnictwo.pdf`, `potwierdzenie_przelewu.pdf`) + referencyjne JSON-y. Generator: „LAPFormalDoc”.
- Syntetyk jest **celowo zróżnicowany**:
  - ponad 20 wariantów nagłówka „banku” w przelewach i dziesiątki wariantów etykiet („Rachunek odbiorcy / beneficjenta / uznawany”, „Nr ref. / Numer referencyjny / Identyfikator operacji”…);
  - rozstrzelone tytuły („P E Ł N O M O C N I C T W O”), bloki w różnej kolejności;
  - konsorcja (6 spraw), 2 zamawiających (3 sprawy), mocodawca — osoba fizyczna (4 sprawy);
  - podchwytliwy „Wykonawca wybrany”;
  - daty słownie („3 lipca 2026 roku”), numer TED w postaci „2026/S 241-999983”, który trzeba sprowadzić do „999983-2026”.
- Odwołanie: 2–11 stron, średnio ok. 11 700 znaków (~4 tys. tokenów), maksymalnie ok. 30 tys. znaków. Mieści się w kontekście LLM.

### Pola nieoczywiste (regex ich praktycznie nie rozwiąże)
- `czynnosc_powod_id` (1–12) + kanoniczny `czynnosc_opis` ustalamy **wyłącznie na podstawie pierwszego zarzutu**. W danych występują tylko wartości 1–11. **Czym jest 12 — pytamy mentorów.**
- `czynnosc_dzien` — data zaskarżonej czynności z pierwszego zarzutu.
- Przy konsorcjum do `Odwolujacy` trafia tylko Lider. `prowadzacy_postepowanie`: null przy jednym zamawiającym, w przeciwnym razie 1 albo 0.
- Imiona i nazwiska pełnomocników mają być w mianowniku. W prawdziwych pismach będą odmienione: „przez radcę prawnego Jana Kowalskiego” → „Jan Kowalski”.
- Normalizacja: daty ISO, BZP bez spacji (`2026/BZP00999263`), rachunki — same 26 cyfr, kwota jako liczba.

### Pułapki
- **Sumy kontrolne w syntetyku często się nie zgadzają**: NIP jest poprawny w 115 na 189 przypadków, REGON w 85 na 123, IBAN w 43 na 120. Nie wolno odrzucać wartości po sumie kontrolnej, to tylko miękki sygnał. Na skanach organizatora się przyda: pomaga poprawiać błędy OCR.
- **F1 liczone po polach**: błędna wartość daje FP + FN, a null tylko FN. Przy niepewności bardziej opłaca się zwrócić null. Halucynacje LLM bezpośrednio obniżają precyzję.
- **OCR**: sprawdziliśmy RapidOCR (instalacja przez pip). Skan czyta, ale gubi polskie znaki („Odwolujacy”, „Spóka”), a to szkodzi dokładnemu dopasowaniu nazw. Dlatego głównym silnikiem jest **Tesseract z modelem `pol`**, plus korekta błędów przez LLM; RapidOCR zostaje jako zapasowy.
- Spójność między dokumentami sprawy (NIP w odwołaniu = NIP w pełnomocnictwie) służy tylko do **weryfikacji**, nie do uzupełniania. W referencji każdego JSON-a jest tylko to, co jest w *tym* dokumencie.

---

## 2. Warianty

### Wariant A — „Reguły” (OCR + regex/kotwice + słowniki, bez LLM)
**Jak działa:** tekst → klasyfikacja po słowach kluczowych → dla każdego pola regex lub „etykieta → wartość” z konfiguracji → normalizacja.
- ✅ Deterministyczny, szybki, bez zewnętrznych API, w pełni przejrzysty.
- ❌ Kruchy na prawdziwych dokumentach: inne układy, odmiana nazwisk, błędy OCR.
- ❌ `czynnosc_powod_id`, `czynnosc_dzien`, wybór Lidera i mianownik imion praktycznie nie do zrobienia regexami.
- ❌ Nowa klasa = pisanie regexów. To nie jest „tylko konfiguracja” dla użytkownika biznesowego.
- **Jakość:** wysoka na syntetyku, średnia lub niska na prawdziwych dokumentach. **Ryzyko na etapie 1: wysokie.**

### Wariant B — „LLM-first” (OCR → tekst → otwartoźródłowy LLM ze schematem JSON)
**Jak działa:** tekst z numerami stron + opisy pól z YAML → LLM zwraca JSON zgodny ze schematem → walidacja Pydantic → normalizacja.
- ✅ Odporny na różne układy, rozumie semantykę i polską fleksję.
- ✅ Nowa klasa = YAML z opisami pól, idealnie pod kryterium „bez zmian w kodzie”. MVP powstaje szybko.
- ❌ Halucynacje, zwłaszcza w cyfrach NIP/KRS/IBAN przy mniejszych modelach. Niedeterministyczność.
- ❌ Pełna zależność od API i modelu. Jeśli jury nie będzie miało endpointu LLM, F1 ≈ 0.
- ❌ Bez dodatkowej pracy przejrzystość jest słaba.
- **Jakość:** dobra, ale sufit wyznaczają błędy modelu. **Ryzyko: średnie** (zależność od API).

### Wariant C — „Hybryda sterowana konfiguracją” ⭐ rekomendujemy
**Jak działa:** ten sam potok, ale każde pole ma kilku kandydatów:
1. **reguły** — regex i etykiety dla NIP, KRS, REGON, PESEL, IBAN, dat, kwot, BZP, TED;
2. **LLM** — semantyka: role, Lider, czynność, mianownik, nazwy;
3. **weryfikacja** — wartość musi występować w tekście (fuzzy), sumy kontrolne jako miękki sygnał, spójność między dokumentami sprawy;
4. **pewność poniżej progu → null**, chronimy precyzję.

W śladzie (trace) dla każdego pola zapisujemy stronę, fragment, bbox, metodę i pewność; UI podświetla źródło.
- ✅ Najlepsze oczekiwane F1: precyzję dają reguły i weryfikacja, kompletność — LLM.
- ✅ Przejrzystość i rozszerzalność przez YAML.
- ✅ Działa bez LLM (tryb `rules`) — zabezpieczenie.
- ✅ Naturalnie dzieli się na 4 równoległe strumienie pracy.
- ❌ Więcej kodu i koordynacji. Interfejsy między modułami trzeba ustalić (w szkielecie już są).
- **Jakość:** najwyższa. **Ryzyko: niskie–średnie.**

### Dodatek D — VLM (multimodalny LLM) dla trudnych skanów
Model wizyjny z otwartymi wagami (np. Qwen2.5-VL lub Qwen3-VL) przez API czyta obraz strony i zwraca tekst albo od razu JSON. W wariancie C podpinamy go jako kolejny silnik OCR (`ocr_engine: vlm`) — jeśli znajdziemy API i starczy czasu.

### Porównanie

| Kryterium | A · Reguły | B · LLM-first | C · Hybryda ⭐ |
|---|---|---|---|
| F1 na syntetyku | ★★★★ | ★★★★ | ★★★★★ |
| F1 na prawdziwych dokumentach i skanach | ★★ | ★★★★ | ★★★★★ |
| Rozszerzalność przez konfigurację (finał) | ★★ | ★★★★★ | ★★★★★ |
| Przejrzystość | ★★★★★ | ★★ | ★★★★★ |
| Niezależność od API LLM | ★★★★★ | ★ | ★★★★ |
| Czas do MVP | ★★★ | ★★★★★ | ★★★ |
| Złożoność / koordynacja | niska | niska | średnia |

---

## 3. Rekomendowana architektura (C)

```
input/<sprawa>/*.pdf
   │
   ▼
[1] INGEST       pdfplumber: warstwa tekstowa (+ słowa ze współrzędnymi)
   │             strona bez tekstu? → OCR: Tesseract (pol) | RapidOCR | VLM
   ▼
[2] CLASSIFY     sygnały z YAML (regex + wagi) → wynik dla każdej klasy
   │             niepewnie → LLM zero-shot na podstawie opisów klas
   │             poniżej progu → „inny” → nie zapisujemy JSON-a
   ▼
[3] EXTRACT      według YAML klasy:
   │               ├─ rules: etykiety-kotwice, regex (NIP/KRS/REGON/PESEL/IBAN/daty/kwoty/BZP/TED)
   │               └─ llm:   JSON Schema z YAML + opisy pól + instrukcje + few-shot
   ▼
[4] MERGE        wybór kandydata · weryfikacja „jest w tekście” · sumy kontrolne (miękko)
   │             pewność < progu → null
   ▼
[5] NORMALIZE    daty ISO, kwoty, same cyfry, formaty BZP/TED, słowniki (czynnosc_powod)
   ▼
output/<sprawa>/<nazwa>.json        ← do oceny
traces/<sprawa>/<nazwa>.trace.json  ← skąd pochodzi każde pole (strona, fragment, metoda, pewność)
   ▼
Web UI: wgranie PDF → typ + JSON + podświetlenie źródeł na stronie
```

### Klasa dokumentu = jeden YAML (to daje punkty w finale)

```yaml
# config/doc_types/przelew.yaml (skrót)
id: przelew
name: Potwierdzenie przelewu
classification:
  min_score: 4
  signals:
    - { pattern: 'potwierdzenie (wykonania |realizacji )?(polecenia )?przelewu', weight: 4, scope: head }
    - { pattern: 'rachunek (odbiorcy|beneficjenta|uznawany)', weight: 2 }
output:
  przelew_bankowy:
    type: object
    properties:
      kwota:
        type: number
        description: Kwota przelewu jako liczba (np. 15000.00).
        labels: [Kwota, Kwota operacji, Kwota przelewu]
        normalize: [amount_pl]
      na_rachunek:
        type: string
        description: Rachunek odbiorcy — same cyfry (26), bez „PL” i spacji.
        labels: [Rachunek odbiorcy, Rachunek beneficjenta, Rachunek uznawany]
        normalize: [digits_only]
        validate: [iban_pl]
```

Nowa klasa = nowy plik w `config/doc_types/`. **Demo na prezentację:** na żywo zamieniamy „przykład negatywny” (ogłoszenie TED) w nową klasę w 5 minut.

---

## 4. Stos technologiczny (wspólny dla wariantów; różni się tylko zestaw ekstraktorów)

| Warstwa | Wybór | Dlaczego / licencja |
|---|---|---|
| Język | Python 3.13 | wymóg organizatora |
| PDF → tekst | pdfplumber (+ pypdfium2 do renderowania stron) | MIT / Apache-2.0; słowa ze współrzędnymi do podświetlania |
| OCR | Tesseract 5 + `pol` (przez pytesseract); zapasowo RapidOCR | Apache-2.0; RapidOCR instaluje się bez pakietów systemowych |
| LLM | dowolny endpoint zgodny z OpenAI z modelem o otwartych wagach: Qwen3, gpt-oss, Bielik, PLLuM, Mistral Small | otwarte wagi (Apache-2.0); lokalnie Ollama, llama.cpp lub vLLM |
| Klient LLM | SDK `openai` (tylko jako klient) | Apache-2.0; działa z każdym zgodnym serwerem |
| Schematy i walidacja | Pydantic v2 + PyYAML | konfiguracja klas → JSON Schema → walidacja |
| Dopasowanie rozmyte | rapidfuzz | weryfikacja „wartość jest w tekście”, metryka |
| CLI | Typer + Rich | `start.sh` / `start.bat` |
| API i UI | FastAPI + statyczny frontend (HTML + Tailwind CDN + Alpine.js + PDF.js) | bez Node i budowania; wygodne przy pracy z webowym LLM |
| Dostarczenie | Docker + docker-compose (app, web, opcjonalnie ollama) | wymóg do nagrody |
| Jakość | pytest, ruff, własny skrypt F1 | metryka od pierwszej godziny |

Wszystkie zależności mają licencje permisywne (MIT, BSD, Apache-2.0). Przy pilotażu w UZP to atut.

**Jaki LLM na hackathonie.** Najpierw pytamy mentorów (zob. §8): może organizator udostępni API do PLLuM lub Bielika. W przeciwnym razie darmowe API modeli z otwartymi wagami (Groq, Cerebras, OpenRouter; aktualność sprawdzić na miejscu) albo lokalna Ollama (Qwen3 8B/14B). Kod nie zależy od dostawcy: zmieniamy tylko `.env`.

---

## 5. Struktura repozytorium (już utworzona w szkielecie)

```
├── input/                  # sprawy do przetworzenia: input/<sprawa>/*.pdf
├── output/                 # wyniki: output/<sprawa>/<nazwa>.json
├── config/
│   ├── settings.yaml       # progi, tryb (rules/llm/hybrid), OCR, LLM
│   ├── doc_types/          # JEDEN YAML = JEDNA KLASA DOKUMENTU
│   │   ├── odwolanie.yaml
│   │   ├── pelnomocnictwo.yaml
│   │   └── przelew.yaml
│   ├── dictionaries/czynnosc_powod.yaml
│   └── prompts/            # prompty systemowe LLM (też konfiguracja)
├── odwolania2json/         # pakiet Pythona
│   ├── cli.py · pipeline.py · settings.py · models.py · spec.py · paths.py
│   ├── ingest/             # PDF → tekst, OCR
│   ├── classify/           # reguły + LLM jako fallback
│   ├── extract/            # rules.py · llm.py · merge.py
│   ├── normalize/          # normalizatory (rejestr po nazwie z YAML)
│   ├── validate/           # sumy kontrolne NIP/REGON/KRS/PESEL/IBAN
│   ├── llm/                # klient API zgodnego z OpenAI + cache
│   ├── output/             # zapis JSON i śladów
│   └── api/                # FastAPI dla interfejsu webowego
├── web/                    # frontend (index.html, app.js, style.css)
├── evaluation/             # metryka F1 i przebieg na zbiorze treningowym
├── scripts/                # fetch_data.py (dane UZP), make_scans.py (syntetyczne skany)
├── tests/                  # pytest; testy normalizatorów = specyfikacja
├── docs/                   # architektura, „jak dodać klasę”, dokumenty zespołu
├── data/  traces/  runs/   # (w .gitignore) dane, ślady, przebiegi ewaluacji
├── Dockerfile · docker-compose.yml
├── requirements.txt · requirements-dev.txt · .env.example
└── setup.sh/.bat · start.sh/.bat
```

Różnice między wariantami: w **A** zbędne są `llm/` i `extract/llm.py`, w **B** — `extract/rules.py`. Struktura jest ta sama, tryb przełącza parametr `extraction.mode` w `config/settings.yaml`.

---

## 6. Jak pracujemy

- **Metryka od pierwszej godziny:** `python -m evaluation.run_eval` przetwarza 60 spraw i liczy F1 po polach. Przed każdym PR — przebieg, a w opisie PR podajemy F1 przed i po.
- **Dla osób pracujących z webowym LLM:** każdy moduł to mały plik z docstringiem i testami-specyfikacją. Wklejacie do czatu plik, testy i odpowiedni YAML → dostajecie implementację → `pytest` → PR.
- **Git:** gałęzie `feat/<imię>-<moduł>`, PR do `master`, bez force-push. Małe, częste PR-y.
- **Skany:** `scripts/make_scans.py` renderuje syntetyczne PDF-y do obrazów z szumem; tak mierzymy ścieżkę OCR, zanim dostaniemy prawdziwe dane.

## 7. Plan w fazach (~24 h; doprecyzować dokładny termin)

| Faza | Czas | Co |
|---|---|---|
| 0 | pierwsze 1–1,5 h | wybór wariantu; środowisko u wszystkich (`setup.sh` / `setup.bat`); dane (`scripts/fetch_data.py`); pytania do mentorów |
| 1 | do ~6 h | MVP end-to-end na PDF-ach tekstowych: klasyfikacja + podstawowa ekstrakcja → pierwsze wyniki F1 |
| 2 | do ~14 h | LLM + weryfikacja + normalizacja; ścieżka OCR + syntetyczne skany; iteracje na błędach |
| 3 | do ~20 h | UI z podświetlaniem źródeł, Docker, dokumentacja (architektura, „jak dodać klasę”), raport jakości |
| 4 | ostatnie 3–4 h | zamrożenie kodu, finalny przebieg, README, prezentacja, próba demo |

## 8. Pytania do mentorów (zadać w pierwszej godzinie!)

1. **Jak liczone jest F1?** Dokładne dopasowanie czy normalizacja/fuzzy (wielkość liter, spacje, cudzysłowy)? Czy liczą się pola, gdzie referencja ma null? Czy listy porównywane są według kolejności?
2. **Jak jury uruchomi rozwiązanie?** U siebie? Jaki endpoint LLM będzie dostępny (czy dostaniemy PLLuM lub Bielika)? Czy możemy podać własne API?
3. Czy w zbiorze testowym będą **skany i dokumenty zanonimizowane**? Jeśli dane są zamazane, czy w referencji jest null?
4. **Dokumenty negatywne**: nie tworzyć JSON-a wcale (jak w szablonie) czy potrzebny jest pusty/inny plik?
5. Co oznacza **`czynnosc_powod_id = 12`**?
6. Czy **jeden PDF może zawierać kilka dokumentów** (odwołanie + załączniki w jednym pliku)?
7. Czy nazwy plików w zbiorze testowym są dowolne?
8. Czy jest limit czasu przetwarzania?

## 9. Wstępnie — kto co robi (ostateczny podział po wyborze wariantu)

- **Polina (Claude Code):** rdzeń potoku, ekstrakcja LLM, scalanie i weryfikacja, integracja, review PR-ów.
- **Sewa:** ingest i OCR (Tesseract/RapidOCR), syntetyczne skany, Docker.
- **Lesza:** reguły i ekstraktory regex w YAML, analiza błędów z ewaluacji, słownik czynności.
- **Artem:** normalizatory i walidatory (według testów-specyfikacji), interfejs webowy, dokumentacja i prezentacja.

## 10. Pomysły na nazwę projektu

`Odwołania2JSON` (robocza) · `KIOnwerter` · `KanonKIO` · `Ekstraktor KIO`
