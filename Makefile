.PHONY: install seed reset run telecom_api mcp_telecom chatbot test smoke clean rag-ingest bi-seed bi-reset notebook infra-up infra-down infra-reset infra-logs infra-ps

# ---------------------------------------------------------------------------
# Environment activation
#
# Every target runs through $(ACTIVATE). By default it's a no-op (`true`),
# which assumes you've ALREADY activated your project environment before
# running make — e.g. with pyenv:
#
#     pyenv activate env_311
#     make install
#     make run
#
# make then finds honcho / uvicorn / telecom-seed on the active env's PATH.
#
# If you instead use a local .venv and want make to activate it for you,
# override ACTIVATE (on the command line or by editing the line below):
#
#     make run ACTIVATE='. .venv/bin/activate'
# ---------------------------------------------------------------------------
ACTIVATE ?= true

# ---------------------------------------------------------------------------
# Local infrastructure (Postgres + Milvus) via docker-compose.
# Start these before `make run` when .env points at Postgres/Milvus URLs.
# ---------------------------------------------------------------------------
infra-up:
	docker compose up -d
	@echo "Waiting for Postgres + Milvus to report healthy..."
	@until [ "$$(docker inspect -f '{{.State.Health.Status}}' chatbot-postgres 2>/dev/null)" = "healthy" ] && \
	       [ "$$(docker inspect -f '{{.State.Health.Status}}' chatbot-milvus 2>/dev/null)" = "healthy" ]; do \
	  printf '.'; sleep 3; \
	done; echo " ready."

infra-down:
	docker compose down

# Same as infra-down but also deletes the data volumes (fresh start).
infra-reset:
	docker compose down -v

infra-ps:
	docker compose ps

infra-logs:
	docker compose logs -f

install:
	$(ACTIVATE) && pip install --upgrade pip && \
	pip install -e . && \
	pip install -e services/telecom_api && \
	pip install -e services/mcp_telecom && \
	pip install -e ".[dev]"

seed:
	$(ACTIVATE) && telecom-seed

reset:
	$(ACTIVATE) && telecom-seed --reset

bi-seed:
	$(ACTIVATE) && bi-seed

bi-reset:
	$(ACTIVATE) && bi-seed --reset

telecom_api:
	$(ACTIVATE) && TELECOM_API_RELOAD=1 telecom-api

mcp_telecom:
	$(ACTIVATE) && mcp-telecom

# Index a bot's declared RAG sources into its (in-process) collection.
# No services needed — builds the same in-process RagEngine the chatbot uses.
# Pre-req: AZURE_OPENAI_EMBEDDING_DEPLOYMENT set in .env.
rag-ingest:
	$(ACTIVATE) && python -m src.chatbot.cli.rag_ingest telecom_support

chatbot:
	$(ACTIVATE) && uvicorn src.chatbot.app:app --port 8000 --reload

run:
	$(ACTIVATE) && honcho start

test:
	$(ACTIVATE) && pytest tests/ -v

notebook:
	$(ACTIVATE) && jupyter notebook notebooks/mcp_demo.ipynb

smoke:
	@curl -sS http://localhost:8001/health && echo
	@curl -sS http://localhost:8000/health && echo

clean:
	rm -rf .venv data/chatbot.db services/telecom_api/data/telecom.db logs __pycache__ .pytest_cache
