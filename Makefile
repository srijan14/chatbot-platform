.PHONY: install seed reset run telecom_api mcp_telecom rag_api rag_mcp chatbot test smoke clean rag-bootstrap rag-ingest bi-seed bi-reset notebook

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

install:
	$(ACTIVATE) && pip install --upgrade pip && \
	pip install -e . && \
	pip install -e services/telecom_api && \
	pip install -e services/mcp_telecom && \
	pip install -e services/rag_api && \
	pip install -e services/rag_mcp && \
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

rag_api:
	$(ACTIVATE) && RAG_API_RELOAD=1 rag-api

rag_mcp:
	$(ACTIVATE) && rag-mcp

# Bootstrap the demo RAG collection + ingest the seeded policy corpus.
# Pre-req: rag_api running on :8002.
rag-bootstrap:
	@curl -sS -X POST http://localhost:8002/collections \
	     -H 'Content-Type: application/json' \
	     -H 'X-Tenant-Id: telecom_demo' \
	     -d '{"name":"telecom_policies","embedding_model":"text-embedding-3-small","dimensions":1536,"description":"Telecom policy docs (cancellation, FUP, KYC, roaming, billing)"}' && echo
	@curl -sS -X POST http://localhost:8002/ingest \
	     -H 'Content-Type: application/json' \
	     -H 'X-Tenant-Id: telecom_demo' \
	     -d '{"collection":"telecom_policies","source":"file_path","source_config":{"path":"./data/rag_corpus/telecom_policies","glob":"**/*.md"}}' && echo

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
