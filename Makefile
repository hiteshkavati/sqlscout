.PHONY: install db test lint eval serve

install:
	pip install -e ".[dev,mcp]"

db:
	sqlscout init-db

test:
	pytest

lint:
	ruff check src tests
	ruff format --check src tests

eval:
	sqlscout eval --write docs/eval-report.md

serve: db
	sqlscout serve
