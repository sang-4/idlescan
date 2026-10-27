# idlescan Makefile.  Targets are phony; run them from the project root.
#
#   make test          run the full offline test suite
#   make lint          ruff check + format check
#   make typecheck     mypy
#   make selftest      offline end-to-end demo
#   make serve-demo    start a demo server on 127.0.0.1:8787
#   make build         build wheel + sdist
#   make install-wheel install the wheel into a fresh venv and smoke-test
#   make docker        build the container image
#   make doctor        run idlescan doctor --offline

PY := python3
VENV := /tmp/opencode/idlescan-venv

.PHONY: help test lint typecheck selftest serve-demo build install-wheel docker doctor clean
.DEFAULT_GOAL := help

help:
	@echo "idlescan targets:"
	@echo "  test            run the full offline test suite"
	@echo "  lint            ruff check + ruff format --check"
	@echo "  typecheck       mypy"
	@echo "  selftest        offline end-to-end demo"
	@echo "  serve-demo      start a demo server on 127.0.0.1:8787"
	@echo "  build           build wheel + sdist"
	@echo "  install-wheel   install the wheel into a fresh venv and smoke-test"
	@echo "  docker          build the container image"
	@echo "  doctor          run idlescan doctor --offline"
	@echo "  clean           remove build artifacts"

test:
	$(PY) -m unittest discover -s tests

lint:
	$(PY) -m ruff check idlescan tests
	$(PY) -m ruff format --check idlescan tests

typecheck:
	$(PY) -m mypy idlescan

selftest:
	$(PY) -m idlescan selftest

serve-demo:
	IDLESCAN_DEMO=true IDLESCAN_HOST=127.0.0.1 IDLESCAN_PORT=8787 $(PY) -m idlescan.cli serve

build:
	$(PY) -m build

install-wheel:
	$(PY) -m venv $(VENV)
	$(VENV)/bin/pip install --quiet --disable-pip-version-check --upgrade pip
	$(VENV)/bin/pip install --quiet --disable-pip-version-check dist/*.whl
	$(VENV)/bin/idlescan --help
	$(VENV)/bin/python -m idlescan selftest

docker:
	docker build -t idlescan .

doctor:
	$(PY) -m idlescan doctor --offline

clean:
	rm -rf build dist *.egg-info .eggs .pytest_cache reports
