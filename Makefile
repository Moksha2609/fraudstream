# fraudstream developer shortcuts (POSIX make). Windows users: run the
# equivalent commands from the README directly.

SHELL := /bin/sh
VENV_PY := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo .venv/Scripts/python.exe)
DATA ?= data/creditcard.csv
MODELS ?= models
REPLAY_SPEED ?= 120

.PHONY: help setup data synthetic train test lint api consumer replay dashboard up down logs benchmark-gpu smoke clean

help: ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-14s %s\n", $$1, $$2}'

setup: ## create .venv and install backend (dev) + dashboard deps
	python3 -m venv .venv
	$(VENV_PY) -m pip install --upgrade pip
	$(VENV_PY) -m pip install -e "backend[dev,postgres]"
	cd dashboard && npm ci

data: ## download the public fraud dataset to data/creditcard.csv
	sh scripts/download_data.sh $(DATA)

synthetic: ## generate a synthetic dataset with the same schema (no download)
	cd backend && ../$(VENV_PY) -m fraudstream.synthetic --out ../data/creditcard_synthetic.csv

train: ## train the XGBoost model on CPU -> models/
	cd backend && ../$(VENV_PY) -m ml.train --data ../$(DATA) --out-dir ../$(MODELS)

test: ## unit tests (no services needed)
	cd backend && FS_QUEUE_BACKEND=memory ../$(VENV_PY) -m pytest -m "not redis" -q

lint: ## ruff + dashboard typecheck
	cd backend && ../$(VENV_PY) -m ruff check .
	cd dashboard && npm run typecheck

api: ## run the API locally (in-process consumer, in-memory queue)
	cd backend && FS_QUEUE_BACKEND=memory FS_RUN_CONSUMER=true FS_MODEL_DIR=../$(MODELS) ../$(VENV_PY) -m fraudstream.api

consumer: ## run a standalone scoring consumer against Redis
	cd backend && FS_QUEUE_BACKEND=redis FS_MODEL_DIR=../$(MODELS) ../$(VENV_PY) -m fraudstream.consumer

replay: ## replay the dataset into the queue (Redis by default)
	cd backend && ../$(VENV_PY) -m fraudstream.replay --data ../$(DATA) --speed $(REPLAY_SPEED)

dashboard: ## run the React dashboard dev server
	cd dashboard && npm run dev

up: ## full stack with docker compose
	docker compose up --build -d

down: ## stop the stack
	docker compose down

logs: ## follow stack logs
	docker compose logs -f --tail=100

smoke: ## hit a running API with one transaction
	sh scripts/smoke.sh

benchmark-gpu: ## CPU vs GPU XGBoost benchmark via the RAPIDS image (needs NVIDIA runtime)
	docker build -f backend/Dockerfile.gpu -t fraudstream/gpu backend/
	docker run --rm --gpus all -v "$(PWD)/data:/data:ro" -v "$(PWD)/models:/models" -v "$(PWD)/docs:/docs" fraudstream/gpu

clean: ## remove caches and local sqlite db
	rm -rf backend/.pytest_cache backend/.ruff_cache backend/htmlcov backend/coverage.xml backend/*.db
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
