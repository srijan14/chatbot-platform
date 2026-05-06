# chatbot-platform

A generalized chatbot platform — first vertical slice. The POC implements the
**Tool Call Skill → Tool Engine → Internal APIs** path end-to-end ("Bot 4:
Transactional") for a **telecom customer support** demo. Other skills (RAG,
TAG/SQL, Web Scrape) have stub slots ready.

LLM provider: **Azure OpenAI** (gpt-4o or compatible deployment).

The user types a question, the model decides which telecom MCP tools to call
with what arguments, those calls happen, and the model writes a final answer.
The structure stays generalized so new skills slot in without touching the core.

## Architecture

Three local processes:

| Process | Port | Role |
|---|---|---|
| `telecom_api` | 8001 | Mock internal telecom REST API. SQLite-backed. |
| `mcp_telecom` | 8765 | MCP server (FastMCP, Streamable HTTP). Wraps the REST API as 14 MCP tools. |
| `chatbot`     | 8000 | FastAPI chatbot service. `/chat` REST endpoint, web UI, Azure OpenAI tool-use loop, MCP client. |

## Prerequisites

1. **Azure subscription with Azure OpenAI access.** Azure OpenAI is gated — if
   your subscription doesn't have it yet, request access in the Azure portal first.
2. **Python 3.11** (the repo includes `.python-version` for pyenv).
3. **An Azure OpenAI resource and a model deployment.**

### Setting up Azure OpenAI (one-time)

1. In the [Azure portal](https://portal.azure.com), create an **Azure OpenAI**
   resource. Pick any region that has gpt-4o capacity.
2. Open the resource → **Model deployments** → **Manage deployments** → opens
   Azure AI Studio. Create a new deployment of **gpt-4o** (or `gpt-4o-mini` for
   cheaper/faster). The **deployment name** is yours to pick (e.g. `gpt-4o`).
   Save it — you'll put it in the bot YAML.
3. Back in the resource, open **Keys and Endpoint**. Copy:
   - **Endpoint** → `https://<your-resource>.openai.azure.com/`
   - **KEY 1** (or KEY 2)

## Quick start

```bash
# 1. Install
make install                              # creates .venv, pip install -e ".[dev]"

# 2. Configure credentials
cp .env.example .env
# Edit .env and fill in:
#   AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_API_VERSION

# 3. Set the deployment name in the bot config
# Open configs/bots/telecom_support.yaml and set llm.deployment to whatever
# you named your deployment in the Azure portal (default in the file: gpt-4o).

# 4. Seed SQLite with 5 demo customers
make seed

# 5. Run the three services. Easiest: honcho (one terminal)
make run                                  # uses Procfile

# Or, in three separate terminals:
make telecom_api
make mcp_telecom
make chatbot

# 6. Open the demo UI
open http://localhost:8000/
```

## Demo customers

| ID | Name | Story |
|---|---|---|
| `CUST001` | Aarav Mehta | Postpaid Pro 599 — happy-path control |
| `CUST002` | Priya Iyer | Prepaid, **3 days to expiry, 92% data used** — try "my internet feels slow today" |
| `CUST003` | Rohan Kapoor | Postpaid, **suspended, ₹620 overdue** — try "why is my number not working?" |
| `CUST004` | Sneha Reddy | Prepaid, normal — try "I lost my phone" |
| `CUST005` | Vikram Singh | Postpaid in BLR-04, **active outage** — try "calls keep dropping" |

The web UI has a customer-picker dropdown — switch identity without re-seeding.

## Verifying without the UI

```bash
curl http://localhost:8001/health
curl http://localhost:8000/health

# MCP tools list (Node tool)
npx @modelcontextprotocol/inspector http://localhost:8765/mcp

# REST chat call
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"s1","customer_id":"CUST003","message":"why is my number not working"}'

# Watch the JSON turn log
tail -f logs/turns.jsonl | jq .
```

## Tests

```bash
# Unit + REST API (no LLM, no MCP server needed)
.venv/bin/pytest tests/test_telecom_api.py tests/test_tool_translator.py -v

# MCP server integration (spawns subprocesses; opt-in)
RUN_INTEGRATION=1 .venv/bin/pytest tests/test_mcp_tools.py -v
```

## Layout

```
configs/bots/telecom_support.yaml   Bot config (system prompt, allowlist, MCP url, deployment name)
data/seed/seed_telecom.py           SQLite seed (5 customers, 5 plans, 5 addons)
src/telecom_api/                    Mock REST API on :8001
src/mcp_servers/telecom/            MCP server on :8765 (14 tools)
src/chatbot/
  app.py                            FastAPI app entrypoint (creates AsyncAzureOpenAI client)
  api/                              /chat handler + Pydantic schemas
  core/                             Conversation Manager, LLM Orchestrator (Azure OpenAI),
                                    Bot Config Store, Guardrails, Intent Classifier,
                                    Response Formatter
  router/                           Bot Router
  skills/                           tool_call (active); rag/tag (stub slots)
  engines/tool_engine/              MCP client + MCP→OpenAI translator
  observability/                    Structured JSONL turn logger
  static/                           Demo web UI (vanilla JS)
```

## Out of scope (POC)

Real auth (customer_id flows in cleartext), multi-tenancy, response streaming,
RAG/TAG/Web-Scrape skills (slots only), persistent conversation memory (in-process
dict), production observability (JSONL only).
