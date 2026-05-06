.PHONY: install seed reset run telecom_api mcp_telecom chatbot test smoke clean

install:
	python -m venv .venv && . .venv/bin/activate && pip install --upgrade pip && pip install -e ".[dev]"

seed:
	. .venv/bin/activate && python -m data.seed.seed_telecom

reset:
	. .venv/bin/activate && python -m data.seed.seed_telecom --reset

telecom_api:
	. .venv/bin/activate && uvicorn src.telecom_api.app:app --port 8001 --reload

mcp_telecom:
	. .venv/bin/activate && python -m src.mcp_servers.telecom.server

chatbot:
	. .venv/bin/activate && uvicorn src.chatbot.app:app --port 8000 --reload

run:
	. .venv/bin/activate && honcho start

test:
	. .venv/bin/activate && pytest tests/ -v

smoke:
	@curl -sS http://localhost:8001/health && echo
	@curl -sS http://localhost:8000/health && echo

clean:
	rm -rf .venv data/telecom.db logs __pycache__ .pytest_cache
