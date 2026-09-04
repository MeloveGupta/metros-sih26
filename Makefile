.PHONY: help venv install install-ocr install-llm test run seed card frontend-dev frontend-build up down

PY ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

help:
	@echo "make install        create venv and install core backend deps"
	@echo "make install-ocr    add PaddleOCR (optional; Python 3.12 recommended)"
	@echo "make install-llm    add the Claude extractor (optional; ant auth login)"
	@echo "make test           run the backend test suite"
	@echo "make seed           create default users in the database"
	@echo "make card           generate the printable ArUco calibration card"
	@echo "make run            start the API (uvicorn, :8000)"
	@echo "make frontend-dev   start the Vite dev server (:5173)"
	@echo "make up / down      docker compose up / down"

$(BIN)/python:
	$(PY) -m venv $(VENV)

install: $(BIN)/python
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -r requirements.txt

install-ocr: $(BIN)/python
	$(BIN)/pip install -r requirements-ocr.txt

install-llm: $(BIN)/python
	$(BIN)/pip install -r requirements-llm.txt

test:
	$(BIN)/python -m pytest tests/ -q

seed:
	$(BIN)/python scripts/seed_users.py

card:
	$(BIN)/python scripts/gen_calibration_card.py

run:
	$(BIN)/uvicorn backend.api.main:app --reload --port 8000

frontend-dev:
	cd frontend && npm install && npm run dev

frontend-build:
	cd frontend && npm install && npm run build

up:
	docker compose up --build

down:
	docker compose down
