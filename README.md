# chatbot-platform

A generalized chatbot platform. The first vertical slice implemented the
**Tool Call Skill → Tool Engine → Internal APIs** path end-to-end ("Bot 4:
Transactional") for a **telecom customer support** demo. Since then two more
skills have come online:

- **RAG** — an **in-process** knowledge base. The chatbot imports the
  `rag_engine` library directly (no separate services): it indexes each bot's
  documents into its own vector-DB collection and retrieves with citations at
  query time. The telecom bot uses it for policy / FAQ answers. See
  [RAG knowledge-base demo](#rag-knowledge-base-demo).
- **TAG / SQL** — NL→SQL over an analytics warehouse, powering the **BI
  Assistant** bot (`configs/bots/bi_assistant.yaml`).

Web Scrape remains a designed-but-unbuilt slot (it plugs into the RAG engine as
another connector).

LLM provider: **Azure OpenAI** (gpt-4o / o-series or compatible deployment).

The user types a question, the model decides which telecom MCP tools to call
with what arguments, those calls happen, and the model writes a final answer.
The structure stays generalized so new skills slot in without touching the core.

## Architecture

Three local processes. RAG runs **in-process** inside `chatbot` (it imports the
`rag_engine` library), so there are no separate RAG services.

| Process | Port | Role |
|---|---|---|
| `telecom_api` | 8001 | Mock internal telecom REST API. SQLite-backed. |
| `mcp_telecom` | 8765 | MCP server (FastMCP, Streamable HTTP). Wraps the REST API as 14 MCP tools. |
| `chatbot`     | 8000 | FastAPI chatbot service. `/chat` REST endpoint, web UI, Azure OpenAI tool-use loop, MCP client (Tool Call), and the in-process RAG engine (Milvus + Azure embeddings). |

## Prerequisites

1. **Azure subscription with Azure OpenAI access.** Azure OpenAI is gated — if
   your subscription doesn't have it yet, request access in the Azure portal first.
2. **Python 3.11** (the repo includes `.python-version` for pyenv).
3. **An Azure OpenAI resource and a model deployment.**
4. **Docker + Docker Compose** — for the local Postgres + Milvus instances
   (`docker-compose.yml`). Skip only if you opt into the zero-infra SQLite /
   Milvus-Lite fallback (see [Local infrastructure](#local-infrastructure-postgres--milvus)).

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
# The DB/Milvus URLs already point at the local docker-compose stack.

# 3. Set the deployment name in the bot config
# Open configs/bots/telecom_support.yaml and set llm.deployment to whatever
# you named your deployment in the Azure portal (default in the file: gpt-4o).

# 3b. Start local infrastructure (Postgres + Milvus). See "Local
#     infrastructure" below. Tables are auto-created on first boot.
make infra-up

# 4. Seed the telecom demo backend (its own SQLite mock — unrelated to the
#    platform's Postgres) with 5 demo customers
make seed

# 5. Run the services. Easiest: honcho (one terminal) — reads the Procfile
make run                                  # starts telecom_api, mcp_telecom, chatbot
                                          # (RAG runs inside chatbot — no extra process)

# Or, in separate terminals:
make telecom_api
make mcp_telecom
make chatbot

# 6. Open the demo UI
open http://localhost:8000/
```

## Local infrastructure (Postgres + Milvus)

The platform's operational state lives in **Postgres** (relational plane) and
**Milvus** (vector store). `docker-compose.yml` runs both locally.

```bash
make infra-up        # start Postgres + Milvus (+ Milvus' etcd/minio deps),
                     # waits until both report healthy
make infra-ps        # show container status
make infra-logs      # tail logs
make infra-down      # stop (keeps data volumes)
make infra-reset     # stop AND wipe data volumes (fresh start)
```

**What runs where:**

| Service | Port | Used by | Holds |
|---|---|---|---|
| Postgres | 5432 | `CHATBOT_DB_URL`, `RAG_DB_URL`, `CHATBOT_CHECKPOINT_DB_URL` | sessions, messages, turn_logs, RAG control plane, LangGraph checkpoints |
| Milvus | 19530 | `MILVUS_URI` | RAG embeddings (per-bot collections) |

Schema is created automatically on first chatbot boot
(`Base.metadata.create_all` for the SQLAlchemy stores; the LangGraph
checkpointer runs its own `setup()`). No migration step for local dev.

**Two driver conventions — keep them straight in `.env`:**

- SQLAlchemy stores (`CHATBOT_DB_URL`, `RAG_DB_URL`) use **asyncpg**:
  `postgresql+asyncpg://chatbot:chatbot@localhost:5432/chatbot`
- The LangGraph checkpointer (`CHATBOT_CHECKPOINT_DB_URL`) uses **psycopg** — a
  plain libpq DSN with **no** `+driver`:
  `postgresql://chatbot:chatbot@localhost:5432/chatbot`

**Zero-infra fallback (no Docker).** Both backends still support embedded files.
In `.env`, set the SQLite/Milvus-Lite values shown (commented) in
`.env.example`: `CHATBOT_DB_URL=sqlite+aiosqlite:///data/chatbot.db`,
`RAG_DB_URL=sqlite+aiosqlite:///data/rag.db`, leave `CHATBOT_CHECKPOINT_DB_URL`
unset (falls back to a SQLite file), and `MILVUS_URI=./data/milvus.db`.

> **Milvus Lite is Linux/macOS only.** Windows devs must use the Docker Milvus.

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
RAG runs **in-process**: `RagSkill` calls the `rag_engine` library directly
inside the chatbot — no separate services.

**What's in the box:**
- Corpus: `data/rag_corpus/telecom_policies/*.md` (cancellation, fair-usage,
  KYC/activation, refunds/billing, roaming).
- Each bot is its own tenant: the physical Milvus collection is
  `{bot_id}__{collection}` → here `telecom_support__telecom_policies`.
- The collection + its sources are declared in the bot YAML's `rag:` block.
  On chatbot startup the platform ensures the collection exists and ingests
  those sources (idempotent — dedupe skips unchanged files).

**Prerequisite:** an Azure embedding deployment. Set
`AZURE_OPENAI_EMBEDDING_DEPLOYMENT` in `.env` to an Azure deployment of
`text-embedding-3-small` (1536 dims — the collection pins the dimensions).

```bash
# 1. Run the platform (RAG is inside the chatbot — no extra process).
make run

# 2. Index the corpus deterministically (optional — startup also enqueues it).
#    Builds the same in-process engine and waits for the job to finish.
make rag-ingest        # → python -m src.chatbot.cli.rag_ingest telecom_support
#    Prints e.g.  job=... status=succeeded counts={'documents':5,'upserted':...}

# 3. In the chat UI (:8000), ask a policy question — the bot routes to RAG:
#   "What is the cancellation policy for postpaid plans?"
#   → search_knowledge_base → grounded answer citing [1] (source_uri [heading])
# vs. a record question, which routes to the telecom tools instead:
#   "What plan am I on right now?"  → get_current_plan (tool_call skill)
```

The model decides per turn whether the question needs the knowledge base (RAG)
or a customer-specific tool — the routing hints live in the bot YAML's
`rag.search_instructions`. Citations render as `[N] (source_uri [heading])`;
the `[heading]` comes from the markdown header chunker, which the engine selects
automatically for `.md` sources.

**Adding RAG to another bot** is config-only: add a `rag:` block (collection +
sources) to its YAML and put `rag` in `skills.enabled`. It gets its own isolated
collection automatically.

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
migrations (`create_all` on startup — fine for local, but add Alembic before a
shared/prod schema exists). RAG is multi-tenant at the engine level (per-tenant
collections + metadata filter); the chatbot front door is still single-tenant.

Storage is now Postgres (relational) + Milvus (vector) by default, with an
embedded SQLite / Milvus-Lite fallback for zero-infra runs — see
[Local infrastructure](#local-infrastructure-postgres--milvus).
