.PHONY: help install test test-live lint format type-check verify check build publish clean

help:
	@echo "Available commands:"
	@echo "  install      Install all dependencies (dev + test)"
	@echo "  test         Run tests"
	@echo "  test-live    Run the opt-in live tests against a real server (see below)"
	@echo "  lint         Run ruff linter"
	@echo "  format       Format code with ruff"
	@echo "  type-check   Run mypy"
	@echo "  verify       Run all CI checks (lint + format check + tests)"
	@echo "  check        Alias for verify"
	@echo "  build        Build package"
	@echo "  publish      Publish package to PyPI"
	@echo "  clean        Remove build artifacts"

install:
	uv sync --all-extras

test:
	uv run pytest

# Real HTTP against a real Coolhand server, no mocks. Deliberately outside `verify` — CI has
# neither a server nor a private key. Needs both of:
#   COOLHAND_LIVE_BASE_URL=http://127.0.0.1:3111 COOLHAND_LIVE_API_KEY=<private key> make test-live
test-live:
	uv run pytest tests/live

lint:
	uv run ruff check src tests examples

format:
	uv run ruff check --fix src tests examples
	uv run ruff format src tests examples

type-check:
	uv run mypy src

# Canonical "is this ready to commit?" — mirrors exactly what CI runs
verify:
	uv run ruff check src tests examples
	uv run ruff format --check src tests examples
	uv run pytest

check: verify

clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .coverage
	rm -rf htmlcov/
	find . -type d -name __pycache__ -delete
	find . -type f -name "*.pyc" -delete

build: clean
	uv build

PYPIRC := $(shell if [ -f .pypirc ]; then echo "--config-file .pypirc"; fi)

publish: build
	uv run python -m twine upload $(PYPIRC) dist/*

publish-test: build
	uv run python -m twine upload $(PYPIRC) --repository testpypi dist/*
