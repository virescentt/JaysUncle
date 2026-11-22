# JaysUncle

Zamiana dokumentów postępowania odwoławczego przed KIO (odwołanie, pełnomocnictwo, potwierdzenie przelewu — PDF, także skany) na kanoniczny model JSON. Zadanie konkursowe Urzędu Zamówień Publicznych „Odwołania2JSON”, HackNation 2026 ([opis i dane organizatora](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026)).

## Dokumentacja

- [Architektura](docs/ARCHITEKTURA.md) — jak to działa.
- [Nowy typ dokumentu](docs/NOWA_KLASA_DOKUMENTU.md) — jak dodać typ bez zmian w kodzie.

## Możliwości

- Rozpoznaje typ dokumentu po treści; dokumenty spoza zakresu → pusty `.json`.
- Czyta skany (lokalny OCR, bez GPU).
- Nowy typ dokumentu = plik YAML, bez zmian w kodzie (także kreatorem w panelu).
- LLM: Qwen3.5-9B przez OVHcloud (serwery w UE); bez klucza działa na regułach, offline.
- Panel webowy z podświetleniem źródła każdej wartości.

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

Zespół *Wanna Pizza* — HackNation 2026.
