.PHONY: install seed reset run telecom_api mcp_telecom chatbot test smoke clean

install:
	python -m venv .venv && . .venv/bin/activate && pip install --upgrade pip && \
	pip install -e . && \
	pip install -e services/telecom_api && \
	pip install -e services/mcp_telecom && \
	pip install -e ".[dev]"

seed:
	. .venv/bin/activate && telecom-seed

reset:
	. .venv/bin/activate && telecom-seed --reset

telecom_api:
	. .venv/bin/activate && TELECOM_API_RELOAD=1 telecom-api

mcp_telecom:
	. .venv/bin/activate && mcp-telecom

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
