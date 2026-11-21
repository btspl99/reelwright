.PHONY: install check test lint run
install:
	pip install -e ".[dev,download,whisper,mcp]"
lint:
	ruff check src tests
test:
	pytest
check: lint test
run:
	reelwright serve
