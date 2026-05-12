# Local dev helpers for Python + Slack Socket Mode.

.PHONY: install run healthz docker-build docker-run

PORT ?= 8080

install:
	python3 -m pip install -r requirements.txt

run:
	python3 -m app.main

healthz:
	curl -sf "http://localhost:$(PORT)/healthz" && echo

docker-build:
	docker build -t slack-grammar-bot:local .

docker-run:
	docker run --rm -p "$(PORT):$(PORT)" --env-file .env slack-grammar-bot:local
