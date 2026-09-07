PYTHON ?= python
.PHONY: install setup run lint typecheck test evaluate check docker
install:
	$(PYTHON) -m pip install -r requirements-dev.txt
setup:
	$(PYTHON) scripts/init_env.py
	$(PYTHON) -m alembic upgrade head
	$(PYTHON) -m mock_bank.seed_data.seed
	$(PYTHON) -m knowledge_base.ingestion.store
run:
	$(PYTHON) agent.py
lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
typecheck:
	$(PYTHON) -m mypy core mock_bank apps agents mcp_servers knowledge_base scripts evaluations
test:
	$(PYTHON) -m pytest -q
evaluate:
	$(PYTHON) -m evaluations.evaluators.run
check: lint typecheck test evaluate
docker:
	docker compose up --build -d
