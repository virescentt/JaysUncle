# JaysUncle — uruchomienie na serwerze oceny

## Komenda

Uruchamiać z katalogu `/opt/solution` (z `sudo`):

```bash
docker run --rm --env-file /opt/solution/.env -v /opt/solution/input:/app/input -v /opt/solution/output:/app/output jaysuncle:latest
```

- Przetwarza wszystkie pliki PDF z `/opt/solution/input` (także w podfolderach) i zapisuje wyniki do `/opt/solution/output`: ta sama struktura katalogów i nazwa pliku, rozszerzenie `.json`.
- Dokument spoza zakresu (nie odwołanie, pełnomocnictwo ani potwierdzenie przelewu) → plik z pustym obiektem `{}`.
- Nie wymaga interakcji i kończy się sama po przetworzeniu wszystkich plików.
- Obraz `jaysuncle:latest` jest zbudowany wcześniej na serwerze; kod źródłowy: `/opt/solution/repo` (https://github.com/virescentt/JaysUncle).

## Usługi zewnętrzne

- **OCR** — lokalnie w kontenerze (RapidOCR, modele PP-OCRv6, CPU). Dokumenty nie są wysyłane do żadnej usługi OCR.
- **LLM** — OVHcloud AI Endpoints (Francja, serwery w UE), model open-weights **Qwen3.5-9B**. Klucz w `/opt/solution/.env` (`LLM_API_KEY`).
- Domyślny tryb `EXTRACTION_MODE=rules`: wbudowane typy dokumentów działają na regułach, bez LLM i bez internetu. LLM odczytuje dane typów dodanych w kreatorze panelu.
