.PHONY: setup migrate seed demo-reset test lint e2e eval dev screenshots

setup:            ## install backend + frontend deps and fetch the embedding model
	cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt && python scripts/fetch_model.py
	cd frontend && npm ci

migrate:
	cd backend && . .venv/bin/activate && python -m app.cli migrate
seed:
	cd backend && . .venv/bin/activate && python -m app.cli seed
demo-reset:
	cd backend && . .venv/bin/activate && python -m app.cli demo-reset
test:
	cd backend && . .venv/bin/activate && pytest
lint:
	cd backend && . .venv/bin/activate && ruff check app tests scripts && alembic check
	cd frontend && npx tsc --noEmit
e2e:
	cd frontend && npx playwright test
eval:
	cd backend && . .venv/bin/activate && python ../eval/run_eval.py
dev:              ## API on :8000 and built frontend on :3000
	cd frontend && npx next build
	./scripts_dev.sh
screenshots:
	cd frontend && node scripts/screenshots.mjs http://localhost:3000 ../docs/screenshots
