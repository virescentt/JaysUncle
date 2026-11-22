# Odwołania2JSON

Zamiana dokumentów postępowania odwoławczego przed KIO (odwołanie, pełnomocnictwo, potwierdzenie przelewu — PDF, także skany) na kanoniczny model JSON. Zadanie konkursowe Urzędu Zamówień Publicznych, HackNation 2026 ([opis i dane organizatora](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026)).

## Dokumentacja

- [Architektura](docs/ARCHITEKTURA.md) — jak działa potok (OCR, klasyfikacja, ekstrakcja, weryfikacja) i dlaczego tak, wybór modelu LLM, panel webowy, komendy, bezpieczeństwo.
- [Raport jakości](docs/RAPORT_JAKOSCI.md) — metryka F1, wyniki na danych organizatora, porównanie modeli LLM, jak odtworzyć pomiary.
- [Nowy typ dokumentu bez zmian w kodzie](docs/NOWA_KLASA_DOKUMENTU.md) — format pliku YAML krok po kroku; działający przykład: [ogłoszenie TED](docs/examples/ogloszenie_ted.yaml).

## Możliwości

- **Dokładność:** F1 po polach **0.969** na 30 skanach organizatora i **1.000** na 180 dokumentach syntetycznych; 16/16 dokumentów spoza zakresu → pusty plik `.json`.
- **Rozpoznawanie typu po treści**, nie po nazwie pliku; `output/` ma te same nazwy i strukturę folderów co `input/`.
- **Skany:** lokalny OCR (RapidOCR PP-OCRv6) — bez GPU, dokumenty nie trafiają do żadnej usługi OCR.
- **Rozszerzalność:** typ dokumentu = jeden plik YAML; nowy typ dodaje się bez zmian w kodzie, także z panelu (kreator w 3 krokach).
- **Model LLM:** Qwen3.5-9B (open-weights, 9 mld parametrów) przez OVHcloud AI Endpoints z serwerami w UE — odczytuje dane nowych typów dokumentów. Wbudowane typy działają na regułach: bez klucza API i offline.
- **Przejrzystość:** dla każdej wartości ślad — strona, fragment tekstu, metoda, pewność; wartości niepewne → `null`.
- **Panel webowy:** podgląd PDF z podświetlonym źródłem każdej wartości, wynik JSON do pobrania, przeglądanie, edycja i włączanie/wyłączanie typów dokumentów, słowniki.
- **Laptop biurowy** (16 GB RAM, bez GPU): Windows, macOS, Linux albo Docker; wyłącznie zależności open source (MIT / BSD / Apache-2.0).

## Instalacja i uruchomienie

Wymagany **Python 3.13+** ([python.org/downloads](https://www.python.org/downloads/); Windows: przy instalacji zaznacz „Add python.exe to PATH”). Przy pierwszym uruchomieniu skrypt sam tworzy środowisko `.venv`, instaluje zależności i kopiuje `.env.example` → `.env`.

PDF-y umieść w `input/` (podfoldery = sprawy) — wyniki pojawią się w `output/`.

**Windows**
```bat
REM przetwarza input\ → output\
start.bat
REM panel webowy → http://localhost:8000
start.bat web
```

**macOS / Linux**
```bash
./start.sh        # przetwarza input/ → output/
./start.sh web    # panel webowy → http://localhost:8000
```

**Docker**
```bash
docker compose run --rm app    # przetwarza input/ → output/
docker compose up web          # panel webowy → http://localhost:8000
```

**LLM** — w pliku `.env` wpisz klucz OVHcloud AI Endpoints (klucz nie jest w repozytorium; dla jury podajemy go w formularzu zgłoszenia):

```ini
LLM_API_KEY=<klucz>
LLM_MODEL=Qwen3.5-9B
LLM_EXTRA_BODY={"reasoning_effort": "none"}
```

Bez klucza lub bez internetu aplikacja wypisuje jeden komunikat i działa w trybie reguł. Pozostałe ustawienia: [`.env.example`](.env.example) i [`config/settings.yaml`](config/settings.yaml); komendy CLI: [Architektura → Komendy](docs/ARCHITEKTURA.md#komendy).

## Zespół

Zespół *zschie* — HackNation 2026.
