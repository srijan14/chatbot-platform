# chatbot-platform

A generalized chatbot platform. The first vertical slice implemented the
**Tool Call Skill → Tool Engine → Internal APIs** path end-to-end ("Bot 4:
Transactional") for a **telecom customer support** demo. Since then two more
skills have come online:

- **RAG** — a multi-tenant knowledge-base sub-platform (`rag_api` + `rag_mcp` +
  the `rag_engine` library). The telecom bot uses it to answer policy / FAQ
  questions with cited passages. See [RAG knowledge-base demo](#rag-knowledge-base-demo).
- **TAG / SQL** — NL→SQL over an analytics warehouse, powering the **BI
  Assistant** bot (`configs/bots/bi_assistant.yaml`).

Web Scrape remains a designed-but-unbuilt slot (it plugs into the RAG engine as
another connector).

LLM provider: **Azure OpenAI** (gpt-4o / o-series or compatible deployment).

The user types a question, the model decides which telecom MCP tools to call
with what arguments, those calls happen, and the model writes a final answer.
The structure stays generalized so new skills slot in without touching the core.

## Architecture

Local processes (the telecom demo needs the first three; RAG adds two more):

| Process | Port | Role |
|---|---|---|
| `telecom_api` | 8001 | Mock internal telecom REST API. SQLite-backed. |
| `mcp_telecom` | 8765 | MCP server (FastMCP, Streamable HTTP). Wraps the REST API as 14 MCP tools. |
| `chatbot`     | 8000 | FastAPI chatbot service. `/chat` REST endpoint, web UI, Azure OpenAI tool-use loop, MCP client. |
| `rag_api`     | 8002 | RAG control plane. Collections, ingestion jobs, admin search, scheduler. SQLite + Chroma. |
| `rag_mcp`     | 8766 | RAG data plane. MCP server exposing `search_knowledge_base` + `list_collections`. One process per tenant. |

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
# 0. Activate your Python 3.11 environment FIRST, then run make from it.
#    pyenv:  pyenv activate env_311
#    venv:   python -m venv .venv && . .venv/bin/activate
#    (venv users who prefer make to activate for them can instead pass
#     ACTIVATE='. .venv/bin/activate' to any target, e.g. `make run ACTIVATE=...`.)

# 1. Install — platform + all services + dev deps into the active env
make install

# 2. Configure credentials
cp .env.example .env
# Edit .env and fill in:
#   AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_API_VERSION

# 3. Set the deployment name in the bot config
# Open configs/bots/telecom_support.yaml and set llm.deployment to whatever
# you named your deployment in the Azure portal (default in the file: gpt-4o).

# 4. Seed SQLite with 5 demo customers
make seed

# 5. Run the services. Easiest: honcho (one terminal) — reads the Procfile
make run                                  # starts all 5: telecom_api, mcp_telecom,
                                          # chatbot, rag_api, rag_mcp

# Or, in separate terminals:
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
| `CUST003` | Rohan Kapoor | Postpaid, **suspended, ₹299 overdue** — try "why is my number not working?" |
| `CUST004` | Sneha Reddy | Prepaid, normal — try "I lost my phone" |
| `CUST005` | Vikram Singh | Postpaid in BLR-04, **active outage** — try "calls keep dropping" |
| `CUST006` | Ananya Sharma | Postpaid in MUM-01, **3 outstanding bills** — try "pay my bill" → should trigger `ask_clarification` for `bill_id` |
| `CUST007` | Karan Malhotra | Postpaid Premium 799, **4 active addons, 88% data used** — try "buy more data" → 4 data-pack options → `addon_id` clarification |
| `CUST008` | Meera Joshi | Prepaid Smart 199, **3 open complaints** (network/billing/service) — try "what's the status of my complaint" → `ticket_id` clarification |

Other prompts that exercise clarification:
- *"upgrade my plan"* (any customer) — 10 plans across prepaid/postpaid/data-only → must ask which `new_plan_id`.
- *"recharge my number"* (CUST002, CUST004, CUST008) — needs amount and payment method.
- *"file a complaint"* (any customer) — needs category (billing/network/service/other) and description.
- *"block my SIM"* (any customer) — needs reason (lost/stolen/damaged/other).

The web UI has a customer-picker dropdown — switch identity without re-seeding.

## RAG knowledge-base demo

The telecom bot also answers **policy / FAQ** questions from a document corpus
via the RAG skill — no new bot needed, it's already enabled in
`configs/bots/telecom_support.yaml` (`skills.enabled: [tool_call, clarification, rag]`).
This exercises the **RAG Skill → rag_mcp → rag_api → rag_engine** path the same
way the telecom tools exercise the Tool Call path.

**What's in the box:**
- Corpus: `data/rag_corpus/telecom_policies/*.md` (cancellation, fair-usage,
  KYC/activation, refunds/billing, roaming).
- Tenant: `telecom_demo` (set via `RAG_TENANT_ID`, injected as `X-Tenant-Id` by
  `rag_mcp` on every `rag_api` call).
- Collection `telecom_policies` is auto-created on `rag_api` startup from
  `configs/rag/collections.yaml`, and a scheduled file sync is declared in
  `configs/rag/sources.yaml` (fires every 15 min).

**Prerequisite:** an Azure embedding deployment. Set
`AZURE_OPENAI_EMBEDDING_DEPLOYMENT` in `.env` to an Azure deployment of
`text-embedding-3-small` (1536 dims — the collection pins the dimensions).

```bash
# 1. Start the two RAG processes (in addition to the telecom ones).
#    `make run` (honcho) already starts all five — see the Procfile.
make rag_api       # :8002 — auto-creates the telecom_policies collection
make rag_mcp       # :8766 — MCP server pinned to RAG_TENANT_ID=telecom_demo

# 2. Ingest the seed corpus (idempotent — re-running skips unchanged files).
#    Pre-req: rag_api running on :8002.
make rag-bootstrap

# 3. Confirm the chunks landed (admin search, bypasses the LLM).
curl -sS -X POST http://localhost:8002/search \
  -H 'Content-Type: application/json' \
  -H 'X-Tenant-Id: telecom_demo' \
  -d '{"query":"how long is the cancellation notice period","collection":"telecom_policies","top_k":3}' | jq .

# 4. In the chat UI (:8000), ask a policy question — the bot routes to RAG:
#   "What is the cancellation policy for postpaid plans?"
#   → search_knowledge_base → grounded answer citing [1] (source_uri [heading])
# vs. a record question, which routes to the telecom tools instead:
#   "What plan am I on right now?"  → get_current_plan (tool_call skill)
```

The model decides per turn whether the question needs the knowledge base (RAG)
or a customer-specific tool — the routing hints live in the bot YAML's
`rag.search_instructions`. Citations render as `[N] (source_uri [heading])`;
the `[heading]` comes from the markdown header chunker, which `rag_api` selects
automatically for `.md` sources.

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
# With your env active (pyenv activate env_311 / . .venv/bin/activate):

# Unit + REST API (no LLM, no MCP server needed)
pytest tests/test_telecom_api.py tests/test_tool_translator.py -v

# MCP server integration (spawns subprocesses; opt-in)
RUN_INTEGRATION=1 pytest tests/test_mcp_tools.py -v

# Everything
make test
```

## Layout

```
configs/bots/telecom_support.yaml   Bot config (system prompt, allowlist, MCP url, deployment name)
data/chatbot.db                     Chatbot DB (sessions, messages, turn_logs) — gitignored
services/
  telecom_api/                      Mock REST API service (independent pyproject)
    src/telecom_api/                  app, db, models, routes/, seed.py
    data/telecom.db                   telecom SQLite — gitignored
  mcp_telecom/                      MCP server service (independent pyproject)
    src/mcp_telecom/                  server, tools, telecom_client
src/chatbot/                        Main chatbot package
  app.py                            FastAPI entrypoint (lifespan inits DB + LLM client)
  api/                              /chat handler + Pydantic schemas
  core/                             Conversation Manager (DB-backed), LLM Orchestrator,
                                    Bot Config Store, Guardrails, Intent Classifier
  persistence/                      Async SQLAlchemy models + engine bootstrap
  router/                           Bot Router (composes skills)
  skills/                           clarification (active), tool_call (active); rag/tag stubs
  engines/tool_engine/              MCP client + MCP→OpenAI translator
  observability/                    Structured turn logger (DB + optional JSONL)
  static/                           Demo web UI (vanilla JS, persists session_id)
```

Each service in `services/` is installed independently (`pip install -e services/telecom_api`),
exposing entry-point scripts (`telecom-api`, `telecom-seed`, `mcp-telecom`). They communicate
only over HTTP — zero shared imports.

## Follow-up / clarification

When the bot lacks a critical identifier (which plan, which bill, which SIM) it calls a
synthetic `ask_clarification` tool. The orchestrator short-circuits that call and surfaces
a structured signal on the response:

```json
{
  "text": "Which plan would you like to switch to?",
  "awaiting_clarification": true,
  "clarification": {
    "question": "Which plan would you like to switch to?",
    "expected": "plan_id",
    "suggested_replies": ["LITE_299", "PRO_599", "MAX_999"]
  }
}
```

The UI renders the bubble with a yellow accent and renders `suggested_replies` as clickable
quick-reply chips. The flag is persisted on the session row in `data/chatbot.db` and cleared
on the next user turn.

## Out of scope (POC)

Real auth (customer_id flows in cleartext), response streaming, the Web-Scrape
skill (designed only — plugs into the RAG engine as another connector), alembic
migrations (`create_all` on startup), Postgres (SQLite only). RAG is multi-tenant
at the engine level (per-tenant collections + metadata filter); the chatbot
front door is still single-tenant.
