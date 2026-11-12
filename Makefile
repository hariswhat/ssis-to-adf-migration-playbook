PY ?= python
ESTATE ?= estate/NorthgateETL

.PHONY: setup estate assess waves convert reconcile test lint clean

setup:
	$(PY) -m pip install -r requirements-dev.txt
	pre-commit install

estate:          ## rewrite the sample .dtsx packages
	$(PY) -m tools.make_sample_estate

assess:
	$(PY) -m playbook.assess $(ESTATE) --csv assessment.csv

waves:
	$(PY) -m playbook.waves $(ESTATE)

convert:         ## regenerate adf/ from the packages
	$(PY) -m playbook.convert $(ESTATE) --out adf

reconcile:
	$(PY) -m playbook.reconcile

test:
	$(PY) -m pytest

lint:
	ruff check .
	ruff format --check .
	sqlfluff lint sql/procedures

clean:
	rm -rf .pytest_cache assessment.csv
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
