.PHONY: setup up down demo-repo test lint web-check logs

# First run only: copy .env.example with fresh secrets, a random admin password and your user id
# (so containers can write your repos).
setup:
	@test -f .env || { pw=$$(openssl rand -hex 16); admin=$$(openssl rand -hex 8); \
	  sed -e "s/^SECRET_KEY=.*/SECRET_KEY=$$(openssl rand -hex 32)/" \
	      -e "s/^ADMIN_PASSWORD=.*/ADMIN_PASSWORD=$$admin/" \
	      -e "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$$pw/" \
	      -e "s#//orchestrator:orchestrator@#//orchestrator:$$pw@#" \
	      -e "s/^HOST_UID=.*/HOST_UID=$$(id -u)/" -e "s/^HOST_GID=.*/HOST_GID=$$(id -g)/" \
	      .env.example > .env && echo "created .env; sign in as admin / $$admin (also in .env as ADMIN_PASSWORD)"; }

demo-repo:
	./samples/make_demo_repo.sh

up: setup demo-repo
	docker compose up --build -d
	@echo "UI: http://localhost:$$(grep ^WEB_PORT= .env | cut -d= -f2)   API docs: http://localhost:$$(grep ^API_PORT= .env | cut -d= -f2)/docs"

down:
	docker compose down

logs:
	docker compose logs -f api worker

test:
	cd backend && .venv/bin/pytest tests
	cd frontend && npm test

lint:
	cd backend && .venv/bin/ruff check .

web-check:
	cd frontend && npm run typecheck && npm run build
