.DEFAULT_GOAL := help

.PHONY: help doctor secrets build up migrate health test verify logs down
help:
	@grep -E '^[a-z-]+:.*#' $(MAKEFILE_LIST)

doctor: # check local prerequisites
	@docker --version && docker compose version && docker info >/dev/null

secrets: # create a local .env once
	@./scripts/generate-secrets.sh

build: # build service images
	@docker compose build

up: # start the stack without deleting volumes
	@docker compose up -d

migrate: # rerun idempotent application migrations
	@docker compose run --rm db-migrate

health: # show service health
	@docker compose ps

test: # run service unit tests
	@docker compose run --rm pdf-parser pytest && docker compose run --rm ocr-service pytest

verify: doctor build up health test # basic non-destructive verification

logs: # follow stack logs
	@docker compose logs -f

down: # stop stack and preserve volumes
	@docker compose down
