.PHONY: install db-up db-down seed catalog dq test test-unit lint fmt safety-report check-golden eval eval-test eval-pipeline serve llm llm-3b docker-build docker-up requirements clean

UV ?= uv

install:            ## install Python dependencies
	$(UV) sync

db-up:              ## start PostgreSQL (first start runs the schema + role scripts)
	docker compose up -d --wait warehouse

db-down:
	docker compose down

seed:               ## generate and load the synthetic data (seed 42)
	$(UV) run acp db seed

catalog:            ## refresh configs/catalog_values.json from the database
	$(UV) run acp catalog

dq:                 ## warehouse data-quality audit -> docs/results/data_quality.md
	$(UV) run acp dq

test:               ## all tests (database tests skip if PostgreSQL is not running)
	$(UV) run pytest -q

test-unit:          ## tests that need neither the database nor the LLM
	$(UV) run pytest -q -m "not db"

lint:
	$(UV) run ruff check src tests scripts

fmt:
	$(UV) run ruff check --fix src tests scripts

safety-report:      ## validator vs keyword blocklist -> docs/results/safety.md
	$(UV) run python scripts/safety_report.py

check-golden:       ## every gold query validates, runs and returns rows
	$(UV) run python scripts/check_golden.py

eval:               ## golden set, all splits (needs the LLM: make llm)
	$(UV) run acp eval --label all

eval-test:          ## held-out test split only
	$(UV) run acp eval --label test --split test

eval-pipeline:      ## why-questions, memory, caching, explanations -> docs/results/pipeline.md
	$(UV) run python scripts/eval_pipeline.py

serve:              ## web UI + API on http://localhost:8765
	$(UV) run acp serve

llm:                ## start Qwen2.5-Coder-7B + inference-gateway in WSL
	wsl -d Ubuntu -- bash -lc 'bash "$$(wslpath "$(CURDIR)")/scripts/serve_llm.sh" 7b'

llm-3b:
	wsl -d Ubuntu -- bash -lc 'bash "$$(wslpath "$(CURDIR)")/scripts/serve_llm.sh" 3b'

docker-build:
	docker compose --profile app build

docker-up:
	docker compose --profile app up -d --build

requirements:       ## regenerate requirements.txt from uv.lock
	$(UV) export --no-hashes --no-dev --no-emit-project -o requirements.txt

clean:
	rm -rf .pytest_cache .ruff_cache data/cache.db
