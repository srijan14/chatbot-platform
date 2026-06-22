# chatbot-platform

A generalized, config-driven chatbot platform. Bots are defined by a YAML file
that selects a persona and a set of **skills**; the platform core stays generic
so new bots and skills slot in without touching it.

The **first production use case** is **`am_marketplace`** — a Retrieval-Augmented
Generation (RAG) assistant that answers questions about the internal **API
Marketplace & Gateway** from its documentation knowledge base, with citations.

LLM provider: **Azure OpenAI** (gpt-4o / o-series deployment for chat,
`text-embedding-3-small` for embeddings).

## Skills

A bot opts into skills via `skills.enabled` in its YAML. Available skills:

| Skill | Status | What it does |
|---|---|---|
| **rag** | **active** (`am_marketplace`) | In-process knowledge base. The platform imports the `rag_engine` library directly, indexes each bot's documents into its own vector-DB collection, and retrieves with citations at query time. |
| **clarification** | active | Lets the bot ask a structured follow-up question when a request is too ambiguous to answer. Always wired in. |
| **tool_call** | available | Calls external tools over MCP (needs an MCP server). No bot currently enables it. |
| **tag** | available | NL→SQL over an analytics warehouse (LlamaIndex schema-RAG + sqlglot-validated SQL). No bot currently enables it. |
| web_scrape | designed-only | Plugs into the RAG engine as another connector. |

`tool_call` and `tag` are kept as generic platform capabilities for future bots;
they are not used by `am_marketplace`.

## Architecture

One local process. RAG runs **in-process** inside `chatbot` (it imports the
`rag_engine` library), so there are no separate RAG/tool services for a RAG bot.

| Process | Port | Role |
|---|---|---|
| `chatbot` | 8000 | FastAPI service. `/chat` REST endpoint, document-management API, web UI, Azure OpenAI agent loop (LangGraph), and the in-process RAG engine (Milvus + Azure embeddings). |

State lives in **Postgres** (relational) and **Milvus** (vectors) — see
[Local infrastructure](#local-infrastructure-postgres--milvus).

## Prerequisites

1. **Azure subscription with Azure OpenAI access.** Azure OpenAI is gated — if
   your subscription doesn't have it yet, request access in the Azure portal first.
2. **Python 3.11** (the repo includes `.python-version` for pyenv).
3. **An Azure OpenAI chat deployment** (gpt-4o / o4-mini) **and an embedding
   deployment** (`text-embedding-3-small`, 1536 dims). They are often on separate
   Azure resources — the env supports a separate embedding endpoint + key.
4. **Docker + Docker Compose** — for the local Postgres + Milvus instances
   (`docker-compose.yml`). Skip only if you opt into the zero-infra SQLite /
   Milvus-Lite fallback (see [Local infrastructure](#local-infrastructure-postgres--milvus)).

## Quick start

```bash
# 0. Activate your Python 3.11 environment FIRST, then run make from it.
#    pyenv:  pyenv activate env_311
#    venv:   python -m venv .venv && . .venv/bin/activate
#    (venv users who prefer make to activate for them can pass
#     ACTIVATE='. .venv/bin/activate' to any target, e.g. `make run ACTIVATE=...`.)

# 1. Install — platform + dev deps into the active env
make install

# 2. Configure credentials
cp .env.example .env
# Edit .env and fill in the Azure chat + embedding endpoints/keys.
# The DB/Milvus URLs already point at the local docker-compose stack.

# 3. (optional) set the deployment name in the bot config
# configs/bots/am_marketplace.yaml → llm.deployment (or override via
# AZURE_OPENAI_DEPLOYMENT in .env).

# 4. Start local infrastructure (Postgres + Milvus). Tables auto-create on boot.
make infra-up

# 5. Index the marketplace corpus into its collection (startup also enqueues it).
make rag-ingest          # → python -m src.chatbot.cli.rag_ingest am_marketplace

# 6. Run the chatbot
make run                 # honcho → uvicorn on :8000  (RAG is in-process)

# 7. Open the demo UI
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

## The `am_marketplace` RAG bot

The bot answers questions from a document corpus via the RAG skill — enabled in
`configs/bots/am_marketplace.yaml` (`skills.enabled: [rag, clarification]`).
RAG runs **in-process**: `RagSkill` calls the `rag_engine` library directly
inside the chatbot.

**What's in the box:**
- Corpus: `data/rag_corpus/api_marketplace/*.md` — the API Marketplace & Gateway
  documentation, split into sections (overview, architecture, governance & roles,
  lifecycle, publishing, bulk onboarding, consumer flow, runtime/consumption).
- Each bot is its own tenant: the physical Milvus collection is
  `{bot_id}__{collection}` → here `am_marketplace__marketplace_docs`.
- The collection + its sources are declared in the bot YAML's `rag:` block. On
  startup the platform ensures the collection exists and ingests those sources
  (idempotent — dedupe skips unchanged files).

**Prerequisite:** an Azure embedding deployment. Set
`AZURE_OPENAI_EMBEDDING_DEPLOYMENT` in `.env` to a deployment of
`text-embedding-3-small` (1536 dims — the collection pins the dimensions).

Try it in the chat UI (:8000):
- *"What is the API lifecycle?"* → grounded answer citing `[N]`.
- *"How does bulk API onboarding work?"*, *"What headers does KONG require?"*
- An off-corpus question → the bot says the knowledge base doesn't cover it
  rather than guessing (the prompt is generic and grounded).

**Adding more documents** is config-free: drop more `.md` (or `.pdf`, `.txt`)
files into `data/rag_corpus/api_marketplace/` and re-run `make rag-ingest`.
Dedupe re-indexes only what changed.

**Adding another bot** is config-only: add `configs/bots/<bot_id>.yaml` with a
`rag:` block (collection + sources) and `rag` in `skills.enabled`. It gets its
own isolated collection automatically.

## Document management API

Besides file-based ingestion, documents can be managed at runtime per bot:

```bash
# Add/update a document in a bot's knowledge base
curl -X PUT "http://localhost:8000/bots/am_marketplace/documents/notes.md" \
  -H 'Content-Type: application/json' \
  -d '{"id":"notes.md","content":"# Note\nFreeform content..."}'

# List / delete
curl "http://localhost:8000/bots/am_marketplace/documents"
curl -X DELETE "http://localhost:8000/bots/am_marketplace/documents/notes.md"
```

(See `src/chatbot/api/documents.py` for the exact routes/shapes.)

## Verifying without the UI

```bash
curl http://localhost:8000/health

# REST chat call
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"s1","customer_id":"guest","message":"What is the API lifecycle?"}'

# Watch the JSON turn log (if LOG_JSONL=1)
tail -f logs/turns.jsonl | jq .
```

`customer_id` is the **end-user identity** (any stable per-user string). It keys
the per-user daily token budget and turn-log attribution — it is not tied to any
particular bot or domain.

## Tests

```bash
# With your env active:
make test                # pytest tests/ -v
```

- `tests/test_thread_repair.py` — orchestrator thread/checkpoint repair.
- `tests/rag/test_document_crud.py` — RAG add/update/dedupe/delete against the
  embedded Milvus Lite backend (uses fixtures in `tests/conftest.py`).

## Layout

```
configs/bots/am_marketplace.yaml    Bot config (persona, skills, RAG corpus/collection, deployment)
data/rag_corpus/api_marketplace/    Markdown corpus for the marketplace bot — gitignored data dir
docker-compose.yml                  Local Postgres + Milvus (standalone) infra
src/chatbot/                        Main chatbot package
  app.py                            FastAPI entrypoint (lifespan: checkpointer, DB, RAG engine, prewarm)
  api/                              /chat + document-management handlers + Pydantic schemas
  core/                             Conversation Manager, LangGraph orchestrator, Bot Config Store,
                                    middleware (dynamic prompt, token usage, budget guard), guardrails
  persistence/                      Async SQLAlchemy models + engine; checkpointer factory (SQLite/Postgres)
  router/                           Bot Router (composes a bot's enabled skills)
  skills/                           rag, clarification (active); tool_call, tag (available)
  engines/                          tool_engine (MCP client), tag_engine (NL→SQL)
  cli/rag_ingest.py                 `rag-ingest <bot_id>` — deterministic corpus indexing
  static/                           Demo web UI (vanilla JS, persists session_id)
src/rag_engine/                     In-process RAG library (connectors, ingestion, embeddings,
                                    chunking, vector_store/milvus_store.py, storage control plane)
```

## Out of scope (for this first production cut)

Real auth (the end-user id flows in cleartext), response streaming, the
Web-Scrape skill (designed only), Alembic migrations (`create_all` on startup —
fine for local; add Alembic before a shared/prod schema exists), a
Redis-backed budget store (the per-user daily cap is currently an in-process
tally), and rate limiting / CORS on `/chat`. RAG is multi-tenant at the engine
level (per-bot collections + metadata filter); the chatbot front door is still
single-tenant.
