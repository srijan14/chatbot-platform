.PHONY: install seed reset run telecom_api mcp_telecom rag_api rag_mcp chatbot test smoke clean rag-bootstrap rag-ingest

install:
	python -m venv .venv && . .venv/bin/activate && pip install --upgrade pip && \
	pip install -e . && \
	pip install -e services/telecom_api && \
	pip install -e services/mcp_telecom && \
	pip install -e services/rag_api && \
	pip install -e services/rag_mcp && \
	pip install -e ".[dev]"

seed:
	. .venv/bin/activate && telecom-seed

reset:
	. .venv/bin/activate && telecom-seed --reset

bi-seed:
	. .venv/bin/activate && bi-seed

bi-reset:
	. .venv/bin/activate && bi-seed --reset

telecom_api:
	. .venv/bin/activate && TELECOM_API_RELOAD=1 telecom-api

mcp_telecom:
	. .venv/bin/activate && mcp-telecom

rag_api:
	. .venv/bin/activate && RAG_API_RELOAD=1 rag-api

rag_mcp:
	. .venv/bin/activate && rag-mcp

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
	. .venv/bin/activate && uvicorn src.chatbot.app:app --port 8000 --reload

run:
	. .venv/bin/activate && honcho start

test:
	. .venv/bin/activate && pytest tests/ -v

notebook:
	. .venv/bin/activate && jupyter notebook notebooks/mcp_demo.ipynb

smoke:
	@curl -sS http://localhost:8001/health && echo
	@curl -sS http://localhost:8000/health && echo

clean:
	rm -rf .venv data/chatbot.db services/telecom_api/data/telecom.db logs __pycache__ .pytest_cache
