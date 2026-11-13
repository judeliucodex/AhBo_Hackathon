# Ah Bo — everything runs in Docker (§26.1); the host needs nothing but Docker.
COMPOSE := docker compose -f deploy/compose.yaml -p ahbo
RUN     := $(COMPOSE) run --rm --no-deps api
EXEC    := $(COMPOSE) exec -T api
HOST    ?= https://srv2135273.hstgr.cloud

.PHONY: help up down restart logs ps build test eval demo-fake render-fixed doctor set-password show-app-secret \
        show-initial-password init-secrets screens backup chat

help:
	@grep -E '^[a-z-]+:' Makefile | cut -d: -f1 | tr '\n' ' '; echo

.env:
	@$(MAKE) --no-print-directory init-secrets

init-secrets:                 ## create/complete .env (mode 600) + initial operator password (BUILD_LOG D-4)
	@mkdir -p data
	docker run --rm -v $(CURDIR):/repo ahbo-api python -m ahbo.ops.cli init-secrets
	@chmod 600 .env

build:
	$(COMPOSE) build api

up: .env                      ## build + start (fake mode unless keys are set)
	$(COMPOSE) up -d --build --remove-orphans
	@$(COMPOSE) ps

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart

logs:
	$(COMPOSE) logs -f --tail=200

ps:
	$(COMPOSE) ps

test:                         ## full test suite inside the api image
	$(COMPOSE) run --rm --no-deps api python -m pytest -q

EVAL_FAKE ?= all
eval:                         ## eval suites (§16.2) → evals/report.md; e.g. make eval EVAL_ARGS="--suite scenarios"
	$(COMPOSE) run --rm --no-deps -e AHBO_FAKE=$(EVAL_FAKE) api ahbo eval $(EVAL_ARGS)

demo-fake: .env               ## fake mode, reset the demo, print the URLs
	@grep -q '^AHBO_FAKE=all' .env || echo "note: .env AHBO_FAKE is not 'all' — edit it for a pure fake demo"
	$(COMPOSE) up -d --build api caddy
	@for i in 1 2 3 4 5 6 7 8 9 10; do $(EXEC) python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=2)" 2>/dev/null && break; sleep 2; done
	@$(EXEC) python -c "import httpx,os; r=httpx.post('http://127.0.0.1:8000/internal/reset', headers={'Authorization':'Bearer '+os.environ['INTERNAL_TOKEN']}, timeout=30); print('reset demo:', r.json())"
	@echo "Console:      $(HOST)/operator/   (Reset demo + S1–S4 buttons)"
	@echo "Browser iPad: $(HOST)/sim/"
	@echo "Login:        $(HOST)/login   (make show-initial-password)"

render-fixed:
	$(RUN) ahbo render-fixed

doctor:
	$(EXEC) ahbo doctor || $(RUN) ahbo doctor

set-password:                 ## interactive; replaces the initial password and deletes its file
	$(COMPOSE) run --rm --no-deps -it api ahbo set-password
	$(COMPOSE) up -d --force-recreate api

show-app-secret:
	@$(RUN) ahbo show-app-secret

show-initial-password:
	@cat data/secrets/operator_password.initial 2>/dev/null || echo "no initial password file (already replaced)"

chat:                         ## text REPL on the real Kernel (in-memory demo DB)
	$(COMPOSE) run --rm --no-deps -it api ahbo chat --text

backup:                       ## one-off SQLite backup into data/backups (keeps 7; ahbo.ops.backup)
	$(EXEC) ahbo backup || $(RUN) ahbo backup

screens:                      ## headless screenshots of /sim/ and /operator/ into docs/screens/ (evals/visual/screens.py)
	@test -f evals/visual/screens.py || { echo "evals/visual/screens.py not built yet (G3); kit frames: evals/visual/kit_capture.py"; exit 2; }
	docker run --rm --network ahbo_default -v $(CURDIR):/repo -w /repo --env-file .env \
	  -e AHBO_BASE_URL=http://api:8000 ahbo-browser python evals/visual/screens.py
