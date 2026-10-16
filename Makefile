.DEFAULT_GOAL := up
.PHONY: up stop logs test

up:
	docker compose up -d --build
	@printf '\nDashboard: http://127.0.0.1:8090\nGenieACS: http://127.0.0.1:3000\nStatus: docker compose ps\n'

stop:
	docker compose stop

logs:
	docker compose logs --tail=100 -f bridge

test:
	python3 -m unittest discover -s tests -v
