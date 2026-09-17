# Metros API image -- Render/Railway/Docker Compose. No GPU needed: Tesseract
# is the local label reader fallback, always installed here.
FROM python:3.12-slim

# System libs: OpenCV (libGL/glib), WeasyPrint (pango/cairo/gdk-pixbuf) for
# PDF, Tesseract (the OCR fallback used automatically when the primary
# reader is unconfigured or its call fails).
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

# Evidence photos/crops and generated reports go to Supabase Storage when
# SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / SUPABASE_BUCKET are set (see
# backend/core/storage.py) -- the norm in production, since Render's free
# tier has no persistent disk (paid-plan only) and the container filesystem
# is wiped on every redeploy/restart. Without those vars, UPLOADS_DIR
# (default data/uploads, inside the image) is used instead and is lost on
# redeploy -- fine only for local `docker run`/Compose testing.

# Render/Railway inject PORT at runtime; default to 8000 for `docker run`
# without one (e.g. local testing, Docker Compose). One uvicorn worker: the
# vision pipeline (OpenCV, and Gemini/WeasyPrint loaded lazily on first use)
# is the memory-heavy part per process, and Render's free plan caps the
# container at 512 MB -- a second worker would double that cost for a
# prototype's request volume, not buy meaningful throughput.
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=4).read()" || exit 1
CMD ["sh", "-c", "uvicorn backend.api.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
