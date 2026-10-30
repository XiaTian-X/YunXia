PYTHON ?= .venv/bin/python

.PHONY: test check build skill-test agreements-check agreements-build skill-install

test:
	$(PYTHON) -B -m unittest discover -s tooling/tests -v

check:
	$(PYTHON) -B tooling/yunxia.py check --test

build:
	$(PYTHON) -B tooling/yunxia.py build

skill-test:
	$(PYTHON) -B -m unittest discover -s tooling/tests -p 'test_yunxia_skill.py' -v

agreements-check:
	$(PYTHON) -B tooling/yunxia.py check

agreements-build: build

skill-install: build
	$(PYTHON) -B tooling/yunxia.py install --update
