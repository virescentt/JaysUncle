# Odwołania2JSON — obraz CPU-only (bez GPU). LLM dostępny przez API (np. serwis ollama z docker-compose).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Tesseract + polski model językowy (OCR skanów); biblioteki wymagane przez opencv (rapidocr)
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-pol libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN mkdir -p input output traces

EXPOSE 8000
CMD ["python", "-m", "odwolania2json", "run", "--input", "input", "--output", "output"]
