.PHONY: install dev-api dev-web test build

install:
	python3 -m venv backend/.venv
	backend/.venv/bin/pip install --index-url https://pypi.org/simple -e 'backend[dev]'
	cd frontend && npm install --registry=https://registry.npmjs.org

dev-api:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

dev-web:
	cd frontend && npm run dev -- --host 127.0.0.1

test:
	cd backend && .venv/bin/pytest
	cd frontend && npm test -- --run

build:
	cd frontend && npm run build
