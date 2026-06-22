# Production-Readiness Handoff

**Branch:** `claude/fervent-babbage-plofiu`
**Date:** 2026-06-22
**Purpose:** Tighten the chatbot platform for a first production deployment.
This is a *new product* — no backward-compatibility or data-migration concerns.

This doc is a self-contained handoff so a fresh session can continue without
re-deriving context.

---

## 1. Goal & scope

Take the chatbot platform from POC to production-grade, without over-engineering.
Two areas were prioritized by the owner:

1. Move off filesystem/SQLite storage to a real production database.
2. Be able to update bot config YAMLs without redeploying.

During review, two concrete decisions were locked and one (vector store) was
implemented in this session.

---

## 2. Decisions locked this session

- **Relational DB → self-managed PostgreSQL.** Self-managed (not Azure-managed).
  The code is identical either way — connection strings are env-driven.
- **Vector DB → Milvus** (replacing Chroma). **DONE this session.**
- pgvector consolidation idea is **dropped** — Milvus is now the dedicated
  vector store, so Postgres' job narrows to the relational/transactional plane.

### Why PostgreSQL (rationale captured for the record)

- Lowest-friction swap from current SQLite: both DBs already use **async
  SQLAlchemy**; SQLite→Postgres is the best-supported path (change URL + add
  `asyncpg`, no ORM rewrite).
- The data is relational/transactional: `sessions`, `messages`, `turn_logs`,
  ingestion `jobs` (a state machine), `documents` dedupe bookkeeping. Needs
  transactions + row locking, not a document/KV store.
- Real concurrent writes (MVCC) — SQLite serializes writes via file locks,
  which bottlenecks multi-worker uvicorn.
- First-class ecosystem support: LangGraph ships `AsyncPostgresSaver` as a
  drop-in for the current `AsyncSqliteSaver`.
- Boring and reliable — the right default for a new product.

Self-managed vs managed is purely an ops choice (you own HA/backups/upgrades);
**no code difference** — just point `CHATBOT_DB_URL` / `RAG_DB_URL` at the host.

---

## 3. The full state-storage surface (important)

The platform has **four** filesystem-based stores, not one. Production must
address all four:

| Store | File (current default) | Holds | Code | Target |
|---|---|---|---|---|
| Chatbot analytics DB | `data/chatbot.db` | sessions, messages, turn_logs | `src/chatbot/persistence/db.py` | Postgres |
| LangGraph checkpoints | `data/chatbot_checkpoints.db` | conversation/agent state | `src/chatbot/app.py` (`AsyncSqliteSaver`) | Postgres (`AsyncPostgresSaver`) |
| RAG control plane | `data/rag.db` | ingestion jobs, doc metadata, dedupe | `src/rag_engine/storage/db.py` | Postgres |
| Vector store | `data/chroma/` → now Milvus | embeddings | `src/rag_engine/vector_store/` | **Milvus (DONE)** |

(`services/telecom_api/data/telecom.db` is the demo backend mock — not real
platform data; ignore for prod.)

---

## 4. What shipped this session

### Commit A — Milvus vector store (replaces Chroma)

The `VectorStore` Protocol (`src/rag_engine/vector_store/base.py`) was already
the swap boundary, so this was a contained one-implementation change; no caller
(Retriever, ingestion pipeline, RagEngine) was touched.

- **New** `src/rag_engine/vector_store/milvus_store.py` — `MilvusVectorStore`
  implementing the full Protocol.
  - Uses pymilvus `MilvusClient` (synchronous) wrapped in `asyncio.to_thread`,
    matching the prior Chroma pattern.
  - **Milvus Lite for dev/tests, real cluster for prod:** `MILVUS_URI` defaults
    to a local file (`./data/milvus.db`) → embedded Milvus Lite, no server
    needed. Prod sets `MILVUS_URI=http://<host>:19530` + `MILVUS_TOKEN`.
    ⚠️ Milvus Lite is **Linux/macOS only** (heads-up for Windows devs).
  - One Milvus collection per bot collection (physical `{tenant}__{logical}`),
    schema: `id` VARCHAR pk, `vector` FLOAT_VECTOR, `document` VARCHAR,
    `metadata` JSON.
  - **L2 metric** kept so `Retriever`'s `1/(1+distance)` similarity mapping
    stays correct.
  - Equality `where` filters compiled to Milvus expressions over the JSON
    field (e.g. `metadata["tenant_id"] == "t1"`).
- **Deleted** `src/rag_engine/vector_store/chroma_store.py`.
- **Deps:** `chromadb` → `pymilvus>=2.4` in `pyproject.toml`.
- Updated `.env.example`, `tests/rag/test_document_crud.py`, and all
  docstrings/comments referencing Chroma.

**Not verified at runtime:** `pymilvus` isn't installed in the dev container,
and the RAG tests rely on `rag_sm`/`fake_embedder` fixtures that are **not
tracked in the repo** (pre-existing gap). All changed files byte-compile.
After `pip install -e .`, `tests/rag/test_document_crud.py` should exercise the
real Milvus Lite backend — but the missing fixtures must be restored first.

### Commit B — Env config scoping

The single root `.env.example` conflated three concerns; tightened to platform
infra/secrets only.

- **Removed dead `MCP_TELECOM_URL`** — nothing reads it. A bot's MCP server URL
  comes from its YAML (`configs/bots/*.yaml` → `tool_call.mcp_servers[].url`),
  read at `src/chatbot/router/bot_router.py` (`MCPClient(cfg.mcp_servers[0].url)`).
  YAML is the single source of truth.
- **Moved telecom demo-service vars out** to per-service example files:
  - `services/telecom_api/.env.example` → `TELECOM_DB_PATH`
  - `services/mcp_telecom/.env.example` → `TELECOM_API_URL`
  - Both have localhost defaults in code, so the honcho demo still runs with no
    `.env`.
- Root `.env.example` reorganized into clear sections (Azure chat / Azure
  embeddings / chatbot persistence / RAG / observability).

**Config split clarified (mental model):**
- Platform/infra/secrets (Azure, DB URIs, Milvus, logging) → **env** ✅
- Per-bot behaviour (MCP servers, tool allowlist, persona, skills) → **bot YAML** ✅
- Separate demo services' config → **their own** `services/<name>/.env.example` ✅

---

## 5. Open work (recommended order)

These were identified but **not yet implemented**.

### High-leverage, low-risk (suggested next PR)
1. **Postgres for the two SQLAlchemy stores.** Add `asyncpg`; point
   `CHATBOT_DB_URL` / `RAG_DB_URL` at Postgres
   (`postgresql+asyncpg://...`). Mostly config; tiny code.
2. **LangGraph checkpointer → `AsyncPostgresSaver`** (`langgraph-checkpoint-postgres`),
   replacing `AsyncSqliteSaver` in `src/chatbot/app.py`. Small code.
3. **Config hot-reload** (update bot YAMLs without redeploy). Caches to bust:
   - `src/chatbot/core/bot_config_store.py` module-level `_cache`
   - `src/chatbot/router/bot_router.py` `_configs` and `_skills`
   Two-part fix: (a) externalize config source off the image (mounted
   volume / blob / a `bot_configs` table); (b) a reload mechanism.
   **Recommended:** an mtime/etag check per request (stateless, self-heals
   across multiple workers) over an admin endpoint (which only hits one
   worker unless backed by Redis pub/sub).

### Migrations
4. **Alembic** replacing `Base.metadata.create_all` (`src/chatbot/persistence/db.py`
   docstring admits the stopgap). Models hang off a single `Base`, so
   `alembic init` + `target_metadata = Base.metadata` is a small lift. Do this
   before any prod schema exists.

### Other production blockers (found, not yet addressed)
5. **Dev server in `Procfile`** — chatbot runs `uvicorn ... --reload` (dev-only,
   single process). Prod needs `uvicorn`/`gunicorn` with multiple workers,
   no `--reload`.
6. **In-process budget cap** — `src/chatbot/app.py` daily token cap is a
   per-process tally (code comment admits "production would back this with
   Redis"). With >1 worker it's effectively unenforced. Move to Redis.
7. **No auth / rate limiting / CORS** on `/chat`. Add before public exposure.
8. **Secrets** — load `AZURE_OPENAI_API_KEY` etc. from a secret manager in
   prod, not a `.env` file.
9. **Logs to stdout/sink**, not local `LOG_DIR=logs`, once on ephemeral infra.

---

## 6. Key files reference

- Vector store Protocol: `src/rag_engine/vector_store/base.py`
- New Milvus impl: `src/rag_engine/vector_store/milvus_store.py`
- RAG runtime wiring: `src/chatbot/core/rag_runtime.py`
- Chatbot DB: `src/chatbot/persistence/db.py`
- RAG DB: `src/rag_engine/storage/db.py`
- App lifespan / checkpointer: `src/chatbot/app.py`
- Bot config loader (+ cache): `src/chatbot/core/bot_config_store.py`
- Bot router (+ skill cache): `src/chatbot/router/bot_router.py`
- Env: `.env.example` (root), `services/*/.env.example`

## 7. Commits on this branch

- Swap vector store from Chroma to Milvus
- Scope env config: platform-only root .env.example, per-service examples
