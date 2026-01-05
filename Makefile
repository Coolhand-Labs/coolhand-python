.PHONY: help install install-dev clean test test-cov lint format type-check build publish dev-setup pre-commit

# Default target
help:
	@echo "Available commands:"
	@echo "  install      Install package in current environment"
	@echo "  install-dev  Install package with development dependencies"
	@echo "  clean        Clean build artifacts and cache"
	@echo "  test         Run tests"
	@echo "  test-cov     Run tests with coverage"
	@echo "  lint         Run linting (flake8)"
	@echo "  format       Format code (black + isort)"
	@echo "  type-check   Run type checking (mypy)"
	@echo "  build        Build package"
	@echo "  publish      Publish package to PyPI"
	@echo "  dev-setup    Set up development environment"
	@echo "  pre-commit   Run pre-commit hooks"

# Installation
install:
	pip install .

install-dev:
	pip install -e ".[dev,test]"

# Development setup
dev-setup: install-dev
	pre-commit install

# Cleaning
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

# Testing
test:
	pytest

test-cov:
	pytest --cov=src/coolhand --cov-report=html --cov-report=term-missing

# Code quality
lint:
	flake8 src tests examples

format:
	black src tests examples
	isort src tests examples

type-check:
	mypy src

# Pre-commit
pre-commit:
	pre-commit run --all-files

# Quality check (run all checks)
check: lint type-check test

# Building and publishing
build: clean
	python -m build

# Use local .pypirc if it exists, otherwise use default
PYPIRC := $(shell if [ -f .pypirc ]; then echo "--config-file .pypirc"; fi)

publish: build
	python -m twine upload $(PYPIRC) dist/*

publish-test: build
	python -m twine upload $(PYPIRC) --repository testpypi dist/*

# Development workflow
dev: format lint type-check test
	@echo "Development checks completed successfully!"

# Quick development cycle
quick: format test
	@echo "Quick development cycle completed!"