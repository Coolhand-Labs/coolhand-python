.PHONY: help install test test-live live-examples lint format type-check verify check build publish clean

# --all-extras: the dev/test tools live in extras, which a bare `uv run` does not install.
# In a fresh venv that makes `uv run pytest` fall through to a global pytest on PATH
# (possibly another Python), so every target that runs a tool goes through this.
UV_RUN := uv run --all-extras

help:
	@echo "Available commands:"
	@echo "  install       Install all dependencies (dev + test)"
	@echo "  test          Run tests"
	@echo "  test-live     Run the opt-in live tests against a real server (see below)"
	@echo "  live-examples Run live provider examples (needs real keys)"
	@echo "  lint          Run ruff linter"
	@echo "  format        Format code with ruff"
	@echo "  type-check    Run mypy"
	@echo "  verify        Run all CI checks (lint + format check + tests)"
	@echo "  check         Alias for verify"
	@echo "  build         Build package"
	@echo "  publish       Publish package to PyPI"
	@echo "  clean         Remove build artifacts"

install:
	uv sync --all-extras

# --ignore=tests/live: the live suite is opt-in (`make test-live`). Exporting
# COOLHAND_LIVE_BASE_URL for smoke tests would otherwise make a default run collect it
# and fail, so `test` and `verify` skip it regardless of the caller's environment.
test:
	$(UV_RUN) pytest --ignore=tests/live

# Real HTTP against a real Coolhand server, no mocks. Deliberately outside `verify` — CI has
# neither a server nor a private key. Needs both of (run
# `pytest tests/live/test_public_content_live.py` directly to need only the base URL):
#   COOLHAND_LIVE_BASE_URL=http://127.0.0.1:3111 COOLHAND_LIVE_API_KEY=<private key> make test-live
# The feedback link live tests also need COOLHAND_LIVE_OPTIMIZATION_ID and
# COOLHAND_LIVE_FEEDBACK_IDS (comma-separated, 2+); see tests/live/test_feedback_links_live.py.
test-live:
	$(UV_RUN) pytest tests/live

# Real OpenAI / Anthropic calls through the interceptor, confirmed delivered to Coolhand.
# Deliberately outside `verify` — CI has no keys. Needs COOLHAND_API_KEY plus OPENAI_API_KEY
# and/or ANTHROPIC_API_KEY (a script whose key is missing SKIPs). Re-execs under `zsh -ic`
# to pick up keys from ~/.zshrc; COOLHAND_EXAMPLES_NO_ZSH=1 opts out. See examples/README.md.
live-examples:
	scripts/live-examples.sh

lint:
	$(UV_RUN) ruff check src tests examples

format:
	$(UV_RUN) ruff check --fix src tests examples
	$(UV_RUN) ruff format src tests examples

type-check:
	$(UV_RUN) mypy src

# Canonical "is this ready to commit?" — mirrors exactly what CI runs
verify:
	$(UV_RUN) ruff check src tests examples
	$(UV_RUN) ruff format --check src tests examples
	$(UV_RUN) pytest --ignore=tests/live

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
