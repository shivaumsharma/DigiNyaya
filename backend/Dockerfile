# Matches production's Python version (Render's backend service pins
# PYTHON_VERSION=3.11.9 to avoid a pydantic-core source build), same
# version this repo's CI runs against (see .github/workflows/ci.yml).
FROM python:3.11-slim

# tesseract-ocr is a system binary pytesseract shells out to for scanned
# document OCR (backend/app/documents/extraction.py) -- pip installing
# pytesseract alone does not provide it.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Alembic migrations must run before the app can serve auth traffic --
# same ordering Render's build command uses in production.
CMD ["sh", "-c", "python -m alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/ai-status', timeout=4)" || exit 1
