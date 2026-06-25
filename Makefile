# QueueStorm Ticket Sorter — developer task runner
# Run `make` or `make help` to see all available targets.

# --- Configuration (override on the CLI, e.g. `make run PORT=9000`) ----------
PYTHON         ?= python3
VENV           := .venv
BIN            := $(VENV)/bin
PORT           ?= 8000
IMAGE          := queuestorm-ticket-sorter:latest
# This machine's active Docker context (desktop-linux) points at a stopped
# daemon; the running daemon is the `default` context. Override if needed:
#   make up DOCKER_CONTEXT=desktop-linux
DOCKER_CONTEXT ?= default
DOCKER         := docker --context $(DOCKER_CONTEXT)
COMPOSE        := $(DOCKER) compose
DEPS           := $(VENV)/.deps-installed

.DEFAULT_GOAL := help

# --- Help --------------------------------------------------------------------
.PHONY: help
help: ## Show this help
	@echo "QueueStorm Ticket Sorter — make targets:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Tunables: PORT=$(PORT)  DOCKER_CONTEXT=$(DOCKER_CONTEXT)"

# --- Local Python (no Docker) ------------------------------------------------
$(DEPS): requirements.txt requirements-dev.txt
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -U pip
	$(BIN)/pip install -r requirements-dev.txt
	@touch $(DEPS)

.PHONY: install
install: $(DEPS) ## Create the virtualenv and install all dependencies

.PHONY: run
run: install ## Run the API locally on http://localhost:$(PORT)
	$(BIN)/uvicorn app.main:app --host 0.0.0.0 --port $(PORT)

.PHONY: dev
dev: install ## Run the API locally with auto-reload (development)
	$(BIN)/uvicorn app.main:app --host 0.0.0.0 --port $(PORT) --reload

.PHONY: test
test: install ## Run the full test suite (pytest)
	$(BIN)/pytest -q

.PHONY: smoke
smoke: ## Hit /health and a sample /sort-ticket on a running server
	@curl -sf http://localhost:$(PORT)/health && echo "  <- /health OK" \
		|| (echo "No server on :$(PORT). Start one with 'make run' or 'make up'."; exit 1)
	@echo "Sample POST /sort-ticket:"
	@curl -s -X POST http://localhost:$(PORT)/sort-ticket \
		-H 'Content-Type: application/json' \
		-d '{"ticket_id":"T-001","message":"I sent 3000 to wrong number"}'; echo

# --- Docker ------------------------------------------------------------------
.PHONY: build
build: ## Build the Docker image
	$(DOCKER) build -t $(IMAGE) .

.PHONY: up
up: ## Build + start the stack via docker compose (detached) and check health
	$(COMPOSE) up --build -d
	@echo "Waiting for the service to become healthy..."
	@for i in $$(seq 1 40); do \
		curl -sf http://localhost:8000/health >/dev/null && break; sleep 0.25; \
	done
	@curl -s http://localhost:8000/health; echo
	@echo "API is up at http://localhost:8000  (docs: /docs)"

.PHONY: down
down: ## Stop and remove the compose stack
	$(COMPOSE) down

.PHONY: logs
logs: ## Follow the container logs
	$(COMPOSE) logs -f

.PHONY: docker-run
docker-run: build ## Run the built image directly (foreground, no compose)
	$(DOCKER) run --rm -p $(PORT):8000 -e PORT=8000 $(IMAGE)

.PHONY: health
health: ## Curl the /health endpoint
	@curl -s http://localhost:$(PORT)/health; echo

# --- Housekeeping ------------------------------------------------------------
.PHONY: clean
clean: ## Remove Python/test caches
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache

.PHONY: clean-all
clean-all: clean ## Also remove the virtualenv
	rm -rf $(VENV)
