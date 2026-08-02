.PHONY: up down logs test test-integration cleanup local

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs --tail 120 -f

test:
	docker compose run --rm slideshow pytest -q

test-integration:
	docker compose run --rm slideshow pytest -q -m integration

cleanup:
	docker compose exec slideshow python -m app.cleanup --once

local:
	uvicorn app.main:app --reload --port 8000

