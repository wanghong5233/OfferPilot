# Pulse — local development helpers

SHELL := /usr/bin/env bash
PROJECT ?= $(CURDIR)
SCRIPTS := $(PROJECT)/scripts

.PHONY: setup setup-pg boss-login start start-pg start-backend start-frontend ps health

setup:
	bash "$(SCRIPTS)/setup.sh"

setup-pg:
	bash "$(SCRIPTS)/setup-pg.sh"

boss-login:
	bash "$(SCRIPTS)/boss-login.sh"

start:
	bash "$(SCRIPTS)/start.sh"

start-pg:
	bash "$(SCRIPTS)/start.sh" pg

start-backend:
	bash "$(SCRIPTS)/start.sh" backend

start-frontend:
	bash "$(SCRIPTS)/start.sh" frontend

ps:
	@echo "backend: $$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8010/health || echo DOWN)"
	@echo "frontend: $$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000 || echo DOWN)"

health:
	@curl -s http://127.0.0.1:8010/health >/dev/null && echo "Backend: OK" || echo "Backend: DOWN"
	@curl -s http://127.0.0.1:3000 >/dev/null && echo "Frontend: OK" || echo "Frontend: DOWN"
