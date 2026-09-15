# MetroScan API image.
FROM python:3.12-slim

# System libs: OpenCV (libGL/glib), WeasyPrint (pango/cairo/gdk-pixbuf) for PDF,
# Tesseract (the OCR fallback used automatically when the AI reader has no key
# or its API call fails).
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 \
      libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
      fonts-dejavu-core \
      tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-ocr.txt requirements-llm.txt ./
# The deployed image is self-sufficient: the AI reader (needs only
# ANTHROPIC_API_KEY at runtime) and the Tesseract fallback are both installed,
# so no separate image variant is needed for either path.
RUN pip install --no-cache-dir -r requirements.txt -r requirements-ocr.txt -r requirements-llm.txt

COPY backend/ ./backend/
COPY rules/ ./rules/

EXPOSE 8000
CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
