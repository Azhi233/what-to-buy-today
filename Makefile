.PHONY: install-dev check lint test compile

install-dev:
	python -m pip install -r requirements-dev.txt

compile:
	python -m compileall -q . -x '(\.venv|venv|\.git|browser_profile)'

lint:
	ruff check .

test:
	pytest

check: compile lint test
