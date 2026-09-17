# Metros API image -- Render/Railway/Docker Compose. No GPU needed: the
# default label reader is PaddleOCR's hosted API (PADDLEOCR_ACCESS_TOKEN),
# with Tesseract installed as the local fallback.
FROM python:3.12-slim

# System libs: OpenCV (libGL/glib), WeasyPrint (pango/cairo/gdk-pixbuf) for
# PDF, Tesseract (the OCR fallback used automatically when the hosted API
# has no token or its call fails).
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 \
      libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
      fonts-dejavu-core \
      tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-ocr.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-ocr.txt

COPY backend/ ./backend/
COPY rules/ ./rules/

# UPLOADS_DIR should point at a persistent disk mount in production (Render
# "Disks" / Railway volumes) -- see docs/deployment.md. Without one,
# data/uploads is ephemeral and evidence images are lost on redeploy.

# Render/Railway inject PORT at runtime; default to 8000 for `docker run`
# without one (e.g. local testing, Docker Compose).
EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
