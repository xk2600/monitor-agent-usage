.DEFAULT_GOAL := help
SHELL := /bin/bash

VENV ?= .venv
PYTHON := $(VENV)/bin/python3
PIP := $(VENV)/bin/pip
CLI := $(VENV)/bin/monitor_agent_usage
MKDOCS := $(VENV)/bin/mkdocs

.PHONY: help venv install install-docs run serve sync status test docs-build docs-serve clean

help: ## Show this help message and target descriptions
	@echo "========================================================================"
	@echo " monitor-agent-usage Build & Execution Management"
	@echo "========================================================================"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""

$(VENV)/bin/activate:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip setuptools

venv: $(VENV)/bin/activate ## Create local python virtual environment in .venv

install: $(VENV)/bin/activate ## Install package in editable mode with CLI into .venv
	$(PIP) install -e .
	@echo "Installed monitor_agent_usage CLI at $(CLI)"

install-docs: $(VENV)/bin/activate ## Install documentation dependencies (mkdocs, mkdocs-material) into .venv
	$(PIP) install -e ".[docs]"
	@echo "Installed MkDocs at $(MKDOCS)"

run: serve ## Alias for serve

serve: $(VENV)/bin/activate ## Run historical sync and launch local web dashboard (port 8420)
	@if [ ! -f $(CLI) ]; then $(MAKE) install; fi
	$(CLI) serve --port 8420

sync: $(VENV)/bin/activate ## Run one-shot historical & incremental sync
	@if [ ! -f $(CLI) ]; then $(MAKE) install; fi
	$(CLI) sync

status: $(VENV)/bin/activate ## Print 24-hour summary status of tracked users and token usage
	@if [ ! -f $(CLI) ]; then $(MAKE) install; fi
	$(CLI) status --hours 24

test: $(VENV)/bin/activate ## Run unit tests with unittest discover
	$(PYTHON) -m unittest discover -s tests -v

docs-build: $(VENV)/bin/activate ## Build static MkDocs site and bundle into web portal
	@if [ ! -f $(MKDOCS) ]; then $(MAKE) install-docs; fi
	$(MKDOCS) build --clean
	@mkdir -p monitor_agent_usage/web/docs
	@cp -r site/* monitor_agent_usage/web/docs/
	@echo "Documentation built and integrated into monitor_agent_usage/web/docs/"

docs-serve: $(VENV)/bin/activate ## Launch live MkDocs preview server (port 8000)
	@if [ ! -f $(MKDOCS) ]; then $(MAKE) install-docs; fi
	$(MKDOCS) serve -a 127.0.0.1:8000

clean: ## Clean build, egg-info, test caches, and site artifacts
	rm -rf build dist *.egg-info site monitor_agent_usage/web/docs .pytest_cache
	find . -type d -name "__pycache__" -exec rm -rf {} +
