# Telecom Chatbot POC — Tool Call Skill via MCP

> **Historical design note (superseded).** This captures the original telecom POC
> that bootstrapped the platform's `tool_call` → MCP path. Those demo bots and
> their services (`telecom_api`, `mcp_telecom`, `bi_warehouse`) have been removed.
> The current live bot is **`am_marketplace`** (RAG); see [README](README.md) for
> how to run the platform today. The telecom flow below remains only as a worked
> example of the still-present `tool_call` skill.

## Context

The repo (`/Users/srijansharma/Desktop/chatbot-platform/chatbot-platform/`) is empty — just a README. We're building the **first vertical slice** of the larger chatbot-platform architecture: a single bot ("Bot 4: Transactional") that demonstrates the **Tool Call Skill → Tool Engine → Internal APIs** path end-to-end.

**Why this slice first:** it exercises the most novel part of the platform — letting the LLM choose which API to call with what parameters — and it's the foundation other skills (RAG, TAG, Web Scrape) plug into later. A telecom customer-support bot is a strong demo domain: rich enough to require multi-tool reasoning ("low data + plan expiring"), familiar enough that judges/stakeholders don't need domain explanation.

**Intended outcome:** three running processes (mock telecom REST API, MCP server, chatbot) wired so that a user types a question into a web chat page and Claude figures out which telecom MCP tools to call to answer it. Structure stays generalized so later skills don't require core rewrites.

**Locked decisions (from clarifying questions):** Python + FastAPI · MCP server from day 1 · `/chat` REST endpoint + minimal web page · SQLite-backed mock data.

---

## Architecture: three processes

| Process | Port | Role |
|---|---|---|
| `telecom_api` | 8001 | Mock internal telecom REST API. SQLite-backed. Stands in for the "Internal APIs (Bot 4)" box. |
| `mcp_telecom` | 8765 | MCP server (FastMCP, **Streamable HTTP** transport). Wraps the REST API as MCP tools. The "Tool Engine". |
| `chatbot` | 8000 | FastAPI chatbot service. `/chat` endpoint, static web UI, Anthropic tool-use loop, MCP client. The "Core Engine + Channels". |

**Why Streamable HTTP for MCP, not stdio:** the chatbot is a long-running web service; spawning a stdio subprocess per request is awkward and shared-subprocess-across-uvicorn-workers is messy. HTTP makes the MCP server an independent peer that scales independently and matches the diagram's "Tool Engine" box. The same FastMCP server can still be exposed over stdio later for Claude Desktop integration.

---

## Directory layout

```
chatbot-platform/
├── pyproject.toml                # deps: fastapi, uvicorn, anthropic, mcp, pyyaml, httpx, pydantic
├── .env.example                  # ANTHROPIC_API_KEY, MCP_TELECOM_URL, TELECOM_API_URL
├── Procfile                      # honcho: chatbot, mcp_telecom, telecom_api
├── Makefile                      # make seed | run | smoke
├── configs/bots/telecom_support.yaml
├── data/
│   ├── telecom.db                # gitignored
│   └── seed/seed_telecom.py
├── src/
│   ├── chatbot/                  # Maps 1:1 to Core Engine boxes
│   │   ├── app.py                # FastAPI: /chat, /health, mounts /static
│   │   ├── api/{chat.py,schemas.py}
│   │   ├── core/
│   │   │   ├── conversation_manager.py    # in-memory session dict for POC
│   │   │   ├── llm_orchestrator.py        # tool-use loop (HEART of the system)
│   │   │   ├── response_formatter.py
│   │   │   ├── intent_classifier.py       # POC stub: returns "transactional"
│   │   │   ├── guardrails.py              # POC stub: input length + log redaction
│   │   │   ├── prompt_store.py
│   │   │   └── bot_config_store.py
│   │   ├── router/bot_router.py
│   │   ├── skills/
│   │   │   ├── base.py                    # abstract Skill
│   │   │   ├── tool_call_skill.py         # only one implemented
│   │   │   ├── rag_skill.py               # NotImplementedError stub (proves slot exists)
│   │   │   └── tag_skill.py               # stub
│   │   ├── engines/tool_engine/
│   │   │   ├── mcp_client.py              # wraps `mcp` SDK client
│   │   │   └── tool_translator.py         # MCP schema ↔ Anthropic schema (CRITICAL)
│   │   ├── observability/{logger.py,tracing.py}
│   │   └── static/{index.html,chat.js,style.css}
│   ├── mcp_servers/telecom/
│   │   ├── server.py                      # FastMCP app on :8765
│   │   ├── tools.py                       # @mcp.tool() functions, ~5 lines each
│   │   └── telecom_client.py              # httpx wrapper to :8001
│   └── telecom_api/
│       ├── app.py                         # FastAPI on :8001
│       ├── routes/{accounts,plans,billing,usage,recharge,addons,sim,network,complaints}.py
│       ├── db.py
│       └── models.py
└── tests/
    ├── test_telecom_api.py
    ├── test_mcp_tools.py
    └── test_smoke_chat.py                 # end-to-end across all 3 services
```

Every directory under `src/chatbot/` corresponds to a box in the architecture diagram. Adding RAG/TAG/Web Scrape later means adding files in `skills/` and `engines/` only — `core/` doesn't change.

---

## MCP tool surface (14 tools)

All tools take `customer_id: str` (no auth in POC; chatbot passes from session). `snake_case` names per Anthropic conventions. Mutating tools use a two-step `confirm: bool = false` pattern — first call returns a preview, second call with `confirm=true` applies. This keeps the LLM honest and matches real telecom IVR flows.

| # | Tool | Params | Purpose |
|---|---|---|---|
| 1 | `get_customer_profile` | customer_id | name, phone, email, plan_id, status |
| 2 | `get_current_plan` | customer_id | active plan with quotas + expiry |
| 3 | `list_available_plans` | category? | switchable plans |
| 4 | `change_plan` | customer_id, new_plan_id, confirm | preview/apply plan switch |
| 5 | `get_balance_and_usage` | customer_id | prepaid balance + cycle usage |
| 6 | `get_recent_bills` | customer_id, limit=3 | recent bills with status |
| 7 | `pay_bill` | customer_id, bill_id, payment_method | mock payment |
| 8 | `recharge_prepaid` | customer_id, amount, payment_method | top-up |
| 9 | `list_addons` | category? | data/roaming/intl addons |
| 10 | `purchase_addon` | customer_id, addon_id, confirm | preview/buy |
| 11 | `block_sim` | customer_id, reason | block lost/stolen SIM |
| 12 | `check_network_status` | area_code? / customer_id? | outages in area |
| 13 | `file_complaint` | customer_id, category, description | open ticket |
| 14 | `get_complaint_status` | customer_id, ticket_id? | track ticket |

Breadth is deliberate: it forces interesting routing decisions (e.g., "internet is slow" → `get_balance_and_usage` + `list_addons`, not just one call).

---

## SQLite schema

Tables: `customers`, `plans`, `subscriptions`, `usage_current`, `bills`, `addons`, `customer_addons`, `sim_events`, `complaints`, `network_outages`, `transactions`. Single-cycle usage snapshot (no time series). Indexes on `bills(customer_id, status)`, `complaints(customer_id, status)`, `customer_addons(customer_id, status)`. Full DDL goes in `data/seed/seed_telecom.py` with idempotent `--reset` flag.

---

## Bot config YAML

```yaml
# configs/bots/telecom_support.yaml
bot_id: telecom_support
name: Telecom Customer Support

llm:
  provider: anthropic
  model: claude-sonnet-4-6
  max_tokens: 1024
  temperature: 0.2
  max_tool_iterations: 6

persona:
  system_prompt: |
    You are TelcoBot, a customer support agent...
    - Greet by name when known.
    - Before any mutating action, restate intent and ask for explicit confirmation.
      Pass confirm=true ONLY after user confirms.
    - Never invent customer data — call the appropriate tool.

skills:
  enabled: [tool_call]
  disabled: [rag, tag, web_scrape]

tool_call:
  mcp_servers:
    - name: telecom
      transport: streamable_http
      url: http://localhost:8765/mcp
  tool_allowlist: [get_customer_profile, get_current_plan, ...]   # explicit list of all 14

guardrails:
  max_input_chars: 2000
  pii_redaction_in_logs: true

observability:
  log_level: info
  log_format: json
```

Forward-compatible: add `rag:` / `tag:` blocks when those skills come online. `skills.enabled` drives which skills the bot router wires up.

---

## LLM Orchestrator loop

Pseudocode for `core/llm_orchestrator.py`:

```python
def run_turn(session, user_message, bot_config):
    history = session.history + [{"role": "user", "content": user_message}]
    anthropic_tools = tool_translator.mcp_to_anthropic(
        mcp_client.list_tools(), bot_config.tool_call.tool_allowlist
    )
    for _ in range(bot_config.llm.max_tool_iterations):
        resp = anthropic.messages.create(
            model=bot_config.llm.model,
            system=[{"type": "text", "text": bot_config.persona.system_prompt,
                     "cache_control": {"type": "ephemeral"}}],
            messages=history,
            tools=anthropic_tools,         # last tool gets cache_control too
            max_tokens=bot_config.llm.max_tokens,
        )
        history.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            return resp, history
        tool_results = []
        for block in resp.content:
            if block.type != "tool_use": continue
            try:
                result = mcp_client.call_tool(block.name, block.input)
                tool_results.append({"type": "tool_result",
                                     "tool_use_id": block.id,
                                     "content": result.text_content})
            except Exception as e:
                tool_results.append({"type": "tool_result",
                                     "tool_use_id": block.id,
                                     "content": f"Error: {e}", "is_error": True})
        history.append({"role": "user", "content": tool_results})
    return resp, history   # iteration cap hit
```

Critical details:
- **One Anthropic message per iteration**, not per tool call. Claude can emit multiple `tool_use` blocks in parallel; execute them all, return all `tool_result`s in one user message.
- **Prompt caching** on system prompt + last tool definition saves real money on multi-turn conversations. ~5 lines of code, do it from day 1.
- **History persistence** must include assistant `tool_use` blocks and user `tool_result` blocks verbatim, or Claude can't see what it already did.
- **`is_error: true`** on tool failures lets Claude recover gracefully and explain to the user.

---

## Seed data — 5 demo customers

Each customer engineered for a distinct demo scenario:

| ID | Name | State | Demo story |
|---|---|---|---|
| `CUST001` | Aarav Mehta | Postpaid Pro 599, normal | Control case / happy path |
| `CUST002` | Priya Iyer | Prepaid Smart 199, **3 days to expiry, 92% data used** | "Internet is slow" → suggests data addon + renewal |
| `CUST003` | Rohan Kapoor | Postpaid Lite 299, **1 OVERDUE bill ₹620** | "Number is suspended" → detects overdue, offers payment |
| `CUST004` | Sneha Reddy | Prepaid Smart 199, normal | "Lost my phone" → block_sim with confirmation |
| `CUST005` | Vikram Singh | Postpaid Pro 599, **area BLR-04 has active outage** | "Calls keep dropping" → check_network_status + file_complaint |

Plans: `LITE_299`, `SMART_199`, `PRO_599`, `MAX_999`, `DATA_ONLY_399`.
Addons: `DATA_5GB_99`, `DATA_20GB_249`, `ROAM_INTL_7D_499`, `INTL_CALL_100MIN_199`, `VOICE_UNL_99`.
Outages: 1 active in `BLR-04`, 1 resolved in `DEL-02`.

UI gets a customer-picker dropdown so demos switch identities without reseeding.

---

## Observability stub

One JSON Lines file (`logs/turns.jsonl`) + stdout via stdlib `logging`. Per turn:

```json
{"ts":"…","trace_id":"…","session_id":"…","customer_id":"CUST002",
 "user_message":"…","iterations":2,
 "tool_calls":[{"name":"get_balance_and_usage","ok":true,"duration_ms":23}, …],
 "input_tokens":1820,"output_tokens":240,"cache_read_tokens":1500,
 "total_cost_usd":0.0042,"latency_ms":2340}
```

Web UI shows a collapsible "trace" pane next to each assistant message — comes free from this same data. No OTel/Jaeger for v1; fields map cleanly to spans later.

---

## Critical files to create

- `src/chatbot/core/llm_orchestrator.py` — tool-use loop; everything pivots on this
- `src/chatbot/engines/tool_engine/tool_translator.py` — MCP↔Anthropic schema bridge; most likely source of subtle bugs, unit-test first
- `src/mcp_servers/telecom/server.py` — 14-tool MCP surface; sizes the demo's capability ceiling
- `configs/bots/telecom_support.yaml` — system prompt + allowlist; sizes the demo's behavior ceiling
- `data/seed/seed_telecom.py` — 5 engineered customers; sizes the demo's narrative ceiling

---

## Build order

**Phase 1 — Foundation:** `pyproject.toml`, `.env.example`, `Makefile`, `Procfile`, `.gitignore`; `db.py` + seed script; verify with sqlite CLI.

**Phase 2 — Mock REST API:** `telecom_api/app.py` + 9 route files; one test per endpoint.

**Phase 3 — MCP server:** `telecom_client.py` (httpx) → `tools.py` (14 `@mcp.tool()` wrappers) → `server.py`. Verify with `npx @modelcontextprotocol/inspector http://localhost:8765/mcp`.

**Phase 4 — Chatbot core:** `bot_config_store.py` → `mcp_client.py` + `tool_translator.py` → `tool_call_skill.py` → `llm_orchestrator.py` → `conversation_manager.py` → `chatbot/app.py` + `/chat`.

**Phase 5 — UI + observability:** `static/{index.html,chat.js,style.css}` with customer-picker + trace pane; `logger.py` wired into orchestrator.

**Phase 6 — Stubs + polish:** `rag_skill.py` / `tag_skill.py` as `NotImplementedError` (proves the slot); `intent_classifier.py` / `guardrails.py` POC stubs; smoke test; README run instructions.

---

## Verification

```bash
# Setup
cp .env.example .env                          # fill ANTHROPIC_API_KEY
uv sync                                       # or pip install -e .
make seed                                     # creates data/telecom.db

# Run all three services (3 terminals or honcho)
uvicorn src.telecom_api.app:app --port 8001
python -m src.mcp_servers.telecom.server      # :8765
uvicorn src.chatbot.app:app --port 8000

# Smoke
curl http://localhost:8001/customers/CUST002
curl http://localhost:8000/health
npx @modelcontextprotocol/inspector http://localhost:8765/mcp   # lists 14 tools

# Demo scenario A — multi-tool reasoning (UI)
open http://localhost:8000/
# Pick CUST002 → "my internet feels slow today"
# Expect: get_balance_and_usage → list_addons → assistant suggests DATA_5GB_99
#         and asks for confirmation. Trace pane shows both calls.

# Demo scenario B — overdue bill (curl)
curl -X POST http://localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"s1","customer_id":"CUST003","message":"why is my number not working"}'
# Expect: tool_calls includes get_customer_profile + get_recent_bills,
# response mentions overdue bill of ₹620.

# Logs
tail -f logs/turns.jsonl | jq .
# Cache hits should appear by turn 2 (cache_read_tokens > 0).

pytest tests/ -v
```

**Pass criteria:** scenario A shows multi-tool reasoning; scenario B returns the overdue bill amount; turn-2 logs show non-zero `cache_read_tokens`.

---

## Risks / things to watch

1. **MCP Streamable HTTP version skew.** The Python `mcp` SDK supports it but pin a recent version. Verify at the start of Phase 3 — don't discover transport bugs at the end.
2. **Tool translator is the bug magnet.** MCP and Anthropic both use JSON Schema but different envelopes. Write `tool_translator.py` first and unit-test it before wiring anything else. Most tool-use bugs trace here.
3. **No auth in POC.** `customer_id` flows in cleartext from the chat request. Document in README; production must derive it from JWT, never trust client-provided.
4. **In-process conversation memory.** Restarts wipe history. Acceptable for POC; Redis/sqlite is the obvious upgrade.
5. **Tool count creep.** Resist adding "while we're at it" tools. Each extra tool is ~30 min of mock SQL + REST + MCP wrapper. Lock the surface at 14 before Phase 2 starts.

**Out of scope (explicit):** streaming responses, multi-tenancy, real auth, RAG/TAG/Web-Scrape skills (slots only), Claude Desktop stdio bridge (HTTP transport doesn't preclude it later).

---

## RAG sub-platform (added later)

> **Superseded (in-process RAG).** RAG no longer runs as standalone `rag_api` +
> `rag_mcp` services. The chatbot now imports the `rag_engine` library
> **in-process**: it owns indexing and retrieval directly, giving each bot its
> own collection (`{bot_id}__{logical}`). The `rag_engine` design below
> (chunking, embedding, retrieval, jobs, tenancy, connectors) is unchanged and
> still accurate — only the deployment shape changed (no REST/MCP hop; `RagSkill`
> calls `RagEngine` directly; sources are declared per-bot in the bot YAML and
> ingested on startup / via `make rag-ingest`). See the README "RAG
> knowledge-base demo" for current run steps. The original service-split design
> is kept below for history.

The RAG capability was originally implemented as its own multi-tenant sub-platform
that served the chatbot as the first consumer; the same `rag_engine` library can
be vendored by any other service. It followed the REST + MCP split the
telecom slice established.

### Processes

| Process | Port | Role |
|---|---|---|
| `rag_api` | 8002 | Control plane. FastAPI. Collections, ingestion jobs, admin search, hosts the scheduler. |
| `rag_mcp` | 8766 | Data plane. MCP server exposing `search_knowledge_base` + `list_collections` to the LLM. Thin httpx wrapper over `rag_api`. One MCP server per tenant (`RAG_TENANT_ID`). |
| `rag_engine` (library) | — | Importable package; the brains. Pluggable Protocols at every seam. |

### Plugin Protocols (the swap boundaries)

`src/rag_engine/`:
- `vector_store/base.py` — `VectorStore` Protocol. Default: `MilvusVectorStore` (Milvus cluster via `MILVUS_URI`; Milvus Lite file-backed for local dev).
- `embeddings/base.py` — `Embedder` Protocol. Default: `AzureOpenAIEmbedder` (`text-embedding-3-small`, 1536 dims; reuses chatbot's Azure env).
- `chunking/base.py` — `Chunker` Protocol. Defaults: `RecursiveCharChunker`, `MarkdownHeaderChunker`.
- `retrieval/reranker.py` — `Reranker` Protocol. Default: `NoOpReranker`. Cross-encoders drop in here.
- `connectors/base.py` — `SourceConnector` Protocol. Built-ins: `file_loader`, `confluence`, `notion`. Web Scraper plugs in here later as another connector — no engine changes.

### Endpoints (`rag_api` :8002)

All requests require an `X-Tenant-Id` header. Cross-tenant access fails closed (404 on jobs / collections that exist for other tenants).

- `POST /collections` — create. `GET /collections` — list (tenant-scoped). `DELETE /collections/{name}` — drop.
- `POST /ingest` — body `{collection, source, source_config, metadata}`. Returns `{job_id, status:"queued"}`.
- `POST /ingest/upload` — multipart variant; stores file under `RAG_UPLOAD_DIR`.
- `GET /jobs/{id}` — poll status. Status enum: `queued | running | succeeded | failed`. Includes counts (`documents`, `chunks`, `embedded`, `upserted`, `skipped`, `errors`).
- `POST /search` — admin/debug. Production search goes through `rag_mcp`.

### MCP surface (`rag_mcp` :8766)

Just two tools — every extra one spends prompt budget on every turn:
- `search_knowledge_base(query, collection, top_k=5, filters?)` — returns `{results, formatted}`. The `formatted` string is `[N] (source_uri[heading]) chunk` so the LLM can cite without parsing JSON.
- `list_collections()` — discovery for multi-collection bots.

### Tenant isolation (security gate)

Enforced two ways; both must be in place:
1. **Physical**: collection name is `{tenant_id}__{logical_name}` — cross-tenant queries against the wrong collection are impossible.
2. **Metadata filter**: `Retriever.search` always appends `where={"tenant_id": tenant_id}`. Caller-supplied filters merge on top but cannot override `tenant_id`.

`tests/rag/test_tenancy.py` fires if either guard is removed.

### Data model (`data/rag.db`, async SQLAlchemy)

- `collections` — PK `{tenant}__{logical}`. Stores embedding model + dimensions per collection so a later model swap requires a new collection (and Milvus will refuse the old one).
- `ingestion_jobs` — async job tracking. Crash-safe: rows persist on creation; worker `recover()` re-enqueues anything left QUEUED/RUNNING.
- `documents` — bookkeeping for dedupe. `content_hash` drives skip-if-unchanged. Chunks themselves live in Milvus — duplicating into SQL was a two-phase commit for zero benefit.
- `connector_runs` — scheduler fires. Answers "when did this source last sync?"

### Ingestion flow

`POST /ingest` → row inserted `status=queued` → worker dequeues → `connector.list_documents()` → for each: `fetch_document()` → dedupe (skip / re-index decision) → `chunker.chunk()` → batched `embedder.embed_documents()` → `vector_store.upsert()` with deterministic `chunk_id = f"{doc_id}:{ordinal:04d}"` → `documents` row upserted → `status=succeeded`.

Re-ingestion is idempotent: changed docs `delete_by_filter({doc_id})` first, then upsert. Same chunk ids mean even a crash mid-upsert recovers cleanly.

### Scheduler

`APScheduler` reads `configs/rag/sources.yaml` on lifespan startup. Each scheduled fire enqueues a job through the same `JobQueue` the REST API uses — no duplicate ingestion path. Each fire writes a `connector_runs` row.

### Chatbot integration

`RagSkill` (`src/chatbot/skills/rag_skill.py`) mirrors `ToolCallSkill` exactly: thin wrapper around the existing `MCPClient`, uses the existing `mcp_to_openai` translator with an allowlist of `["search_knowledge_base", "list_collections"]`. `system_prompt_addition()` teaches the model when to choose KB lookup vs. domain tools.

`BotRouter` (`src/chatbot/router/bot_router.py`) gets one new branch: `if "rag" in cfg.enabled_skills: ...`. Configuration is a `rag:` block in the bot YAML (`mcp_server.url`, `default_collection`, `top_k`, `search_instructions`).

`configs/bots/telecom_support.yaml` now enables `[tool_call, clarification, rag]` so the same bot composes API tool calls and KB lookups — the LLM picks which one the question needs.

### Demo

```bash
make rag_api &      # :8002
make rag_mcp &      # :8766
# Collection + seed corpus ingest:
make rag-bootstrap
# Then through the chatbot UI on :8000, ask:
#   "What is the cancellation policy for postpaid plans?"
#   → triggers search_knowledge_base → grounded answer with [1] citations
# vs. "What plan am I on?" → triggers get_current_plan (tool_call skill)
```

### Critical files

- `src/rag_engine/engine.py` — RagEngine facade
- `src/rag_engine/vector_store/milvus_store.py` — kNN backend
- `services/rag_api/src/rag_api/routes/ingest.py` — pipeline trigger
- `services/rag_mcp/src/rag_mcp/tools.py` — the MCP tool schema the LLM sees
- `src/chatbot/skills/rag_skill.py` — chatbot ↔ RAG bridge
- `configs/rag/collections.yaml` + `configs/rag/sources.yaml` — declarative deployment
- `tests/rag/test_tenancy.py` — the security gate
