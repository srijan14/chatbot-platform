# Chatbot Platform — Architecture & Solution

> Status: Living document. Audience: engineers, PMs, and ops onboarding.
> **Note:** the Telecom Support and BI Assistant bots (and their `telecom_api` /
> `mcp_telecom` / `bi_warehouse` services) were POC demos and have been removed.
> The live bot is **`am_marketplace`** (RAG over the API Marketplace docs); the
> `tool_call` and `tag` skills described below remain as available platform
> capabilities. [README](../README.md) is the operational source of truth.

## 1. Overview

This is a multi-bot chatbot platform. One service hosts many bots; each bot is a YAML config plus a set of pluggable capabilities ("skills"). Today two bots ship with the platform:

- **Telecom Support** — answers customer-care questions and changes a customer's plan, addons, bills, etc. through a Telecom REST API that's exposed over MCP.
- **BI Assistant** — answers natural-language business questions over a SQLite analytics warehouse. It turns the question into SQL, runs the SQL safely, and replies in prose with a markdown data table.

Both bots share the same orchestrator, the same conversation-state persistence, the same clarification UX, and the same observability pipeline. They differ only in which **skills** they enable and what those skills point at.

The platform is built on:

| Layer | Framework |
|---|---|
| Web API | FastAPI |
| Agent orchestration | LangChain v1 (`langchain.agents.create_agent`) on top of LangGraph 1.x |
| Tool transport | Model Context Protocol (MCP) for the telecom bot |
| NL → SQL | LlamaIndex `NLSQLRetriever` + `ObjectIndex` (optional schema RAG) |
| SQL safety | sqlglot AST validation |
| LLM | Azure OpenAI (`o4-mini` by default) |
| State | LangGraph `AsyncSqliteSaver` (conversation) + SQLAlchemy (audit / turn logs) |

---

## 2. Goals & Non-Goals

**Goals**

- One process can host many bots without code duplication.
- Adding a new bot is a YAML file plus enabling skills — no orchestrator changes.
- Adding a new capability is a new `Skill` subclass plus an entry in a bot's `enabled_skills` list.
- Clarification ("which plan did you mean?") works uniformly for every bot, with no domain logic in the platform.
- The bot can never execute SQL it didn't compose, and the SQL it composes can never mutate the warehouse.
- Costs are observable and capped per tenant.

**Non-goals (today)**

- High-scale horizontal deployment (single-process; `AsyncSqliteSaver` is sqlite-backed).
- Multi-LLM-provider failover (Azure OpenAI only).
- Streaming responses.
- Real-time analytics ingestion. The BI warehouse is a static seeded SQLite file.

---

## 3. Architecture at a Glance

```
┌─────────────────────────────────────────────────────────────────┐
│ Browser / API client                                             │
└──────────────────┬──────────────────────────────────────────────┘
                   │ POST /chat   {session_id, customer_id, bot_id, message}
                   ▼
┌─────────────────────────────────────────────────────────────────┐
│ FastAPI handler                              src/chatbot/api/    │
│  • Guardrails the input                                          │
│  • Loads BotConfig + Skills + Session                            │
│  • Calls orchestrator.run_turn()                                 │
│  • Writes TurnLog row                                            │
└──────────────────┬──────────────────────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────────────────────┐
│ Orchestrator (LangChain v1 create_agent)                         │
│   src/chatbot/core/langgraph_orchestrator.py                     │
│                                                                  │
│   Middlewares (run on every model call):                         │
│     • dynamic_prompt — persona + skill rules + auth context      │
│     • TokenUsageMiddleware — accumulates tokens into state       │
│     • BudgetGuardMiddleware — short-circuits over-cap            │
│                                                                  │
│   State (per session_id) — AsyncSqliteSaver checkpointer         │
└──────────────────┬──────────────────────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────────────────────┐
│ Skills (pluggable)             src/chatbot/skills/               │
│   • ClarificationSkill — ask_clarification tool                  │
│   • ToolCallSkill — exposes MCP server tools                     │
│   • TagSkill — query_business_data + list_business_metrics       │
└──────────────────┬──────────────────────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────────────────────┐
│ Engines                        src/chatbot/engines/              │
│   • tool_engine/  — MCP client (HTTP streamable transport)       │
│   • tag_engine/   — NL→SQL pipeline (LlamaIndex + sqlglot)       │
└──────────────────┬──────────────────────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────────────────────┐
│ External services                                                │
│   • MCP Telecom (FastMCP) → Telecom REST API → SQLite           │
│   • BI Warehouse SQLite (read-only)                              │
│   • Azure OpenAI (chat + optional embeddings)                    │
└─────────────────────────────────────────────────────────────────┘
```

Each layer only knows about the one below. Skills do not know they are running inside LangGraph. The orchestrator does not know what MCP is. This is what makes adding a new skill or a new bot a small, contained change.

---

## 4. Core Concepts

| Concept | What it is | Where it lives |
|---|---|---|
| **Bot** | A named configuration: persona system prompt, list of enabled skills, LLM deployment, guardrails. | `configs/bots/<bot_id>.yaml` |
| **Skill** | A pluggable unit of capability. Declares tool schemas, executes those tools, optionally contributes to the system prompt. | `src/chatbot/skills/` |
| **Tool** | One callable function exposed to the LLM (e.g. `change_plan`, `query_business_data`, `ask_clarification`). | declared by a skill, dispatched by the agent |
| **Session** | A multi-turn conversation. Identified by `session_id`; conversation state persists across requests. | `SessionRow` + LangGraph checkpoint |
| **Turn** | One user message + one assistant reply (with any tool calls in between). Each turn writes one `TurnLog` row. | `TurnLog` table |
| **Trace** | Unique per turn; threads through every log line so you can reconstruct one user interaction from logs alone. | generated in `langgraph_orchestrator.run_turn` |
| **Signal** | A typed event a skill wants the caller to see (`clarification`, `handoff`, `end_conversation`). Surfaces on `ChatResponse.signals`. | `TurnSignal` in `skills/base.py` |

---

## 5. Components in Detail

### 5.1 HTTP API

Single endpoint group:

| Method | Path | Purpose |
|---|---|---|
| POST | `/chat` | Submit a user message; receive an assistant reply (with optional clarification signal, tool-call trace, token usage). |
| GET | `/chat/history?session_id=…` | Hydrate the UI on page load. Returns the visible message bubbles for a session. |
| POST | `/chat/reset` | Drop a session (delete the SessionRow). |
| GET | `/health` | Liveness. |

Handler logic is intentionally thin: validate input, fetch dependencies, delegate to the orchestrator, persist the audit log, build the response. See `src/chatbot/api/chat.py`.

### 5.2 Bot Router

`src/chatbot/router/bot_router.py` does two things:

1. Loads and caches the `BotConfig` dataclass from a bot's YAML.
2. Mounts the bot's skills based on its `enabled_skills` list.

```python
get_skills("telecom_support")
  → [ClarificationSkill, ToolCallSkill(MCPClient(http://localhost:8765/mcp))]

get_skills("bi_assistant")
  → [ClarificationSkill, TagSkill(TagPipeline(...))]
```

The Skill instances are built once per process and reused across all requests for that bot.

### 5.3 Orchestrator

The orchestrator (`src/chatbot/core/langgraph_orchestrator.py`) wraps LangChain v1's `create_agent` factory. Per bot, it builds one `CompiledStateGraph` and caches it.

**State schema** (`src/chatbot/core/state.py`):

```python
class ChatbotAgentState(AgentState):
    bot_id: NotRequired[str]
    customer_id: NotRequired[str | None]
    prompt_tokens_used: NotRequired[int]
    completion_tokens_used: NotRequired[int]
    cached_tokens_used: NotRequired[int]
```

Inherits `messages`, `jump_to`, `structured_response` from LangChain's `AgentState`.

**Middlewares** (`src/chatbot/core/middleware.py`):

| Middleware | Hook | Purpose |
|---|---|---|
| `dynamic_prompt` (decorator) | `wrap_model_call` | Builds the system prompt every model call: bot persona + each skill's calling convention + `Authenticated customer: <id>` when signed in. |
| `TokenUsageMiddleware` | `after_model` | Reads `usage_metadata` off the just-appended AIMessage and accumulates tokens into typed state fields. |
| `BudgetGuardMiddleware` | `before_model` | Checks per-customer daily token tally. If over the cap, returns `{"messages": [refusal], "jump_to": "__end__"}` and the model is never called for this turn. |

**Per-bot LLM**:

The orchestrator builds an `AzureChatOpenAI` per bot using the bot's deployment name and parameters. For reasoning-class deployments (o4-mini, o-series, gpt-5+) it omits `temperature` (Azure only accepts the default 1.0 for these) and relies on LangChain's automatic `max_tokens → max_completion_tokens` alias.

**Conversation state**:

Persisted by LangGraph's `AsyncSqliteSaver` (`data/chatbot_checkpoints.db`). Keyed by `thread_id = session_id`. Survives process restart.

### 5.4 Skills

Skill ABC (`src/chatbot/skills/base.py`):

```python
class Skill(ABC):
    name: str
    async def prepare_tools(self) -> list[dict]: ...        # OpenAI-format schemas
    async def execute_tool(self, name, arguments) -> ToolResult: ...
    def owns_tool(self, name) -> bool: ...
    def system_prompt_addition(self) -> str | None: return None
```

The platform ships three concrete skills:

| Skill | Tool(s) | What it does |
|---|---|---|
| `ClarificationSkill` | `ask_clarification(question, expected, suggested_replies)` | Returns a terminal `ToolResult` with a `clarification` signal. The skill→tool adapter translates this into a LangGraph `interrupt()` call so the graph pauses until the next user message. Domain-agnostic; the `expected` enum is per-bot YAML. |
| `ToolCallSkill` | All tools exposed by an MCP server, allow-listed in YAML | Discovers tools at startup (`MCPClient.list_tools()`), wraps each as an OpenAI-shape schema, dispatches at runtime via `MCPClient.call_tool()`. Generic — any MCP server works. |
| `TagSkill` | `list_business_metrics()`, `query_business_data(question, time_range)` | Bridges a `TagPipeline` to the bot. The pipeline owns NL→SQL → validate → execute → summarize. |

**Adapter — Skill → LangChain Tool**

`src/chatbot/adapters/skill_to_tool.py` is the bridge that lets a `Skill` run inside LangGraph without knowing about LangGraph. It:

1. Calls `skill.prepare_tools()` to get OpenAI-format schemas.
2. Builds a Pydantic args model dynamically from each schema (`adapters/json_schema_to_pydantic.py`).
3. Wraps each tool in a LangChain `StructuredTool` whose async coroutine calls `skill.execute_tool(name, args)`.
4. Translates the `ToolResult` to a LangChain return value:
   - If the result has a `clarification` signal and `terminal=True`, calls `langgraph.types.interrupt(payload)` so the graph pauses.
   - If `is_error=True`, raises so LangGraph emits an error ToolMessage.
   - Otherwise, returns the result text.

### 5.5 Engines

**Tool engine** (`src/chatbot/engines/tool_engine/`)

A thin MCP client (`mcp_client.py`) over the official `mcp` Python SDK using streamable HTTP transport. Two methods used by skills:

- `list_tools()` — fetches and caches the MCP server's tool definitions
- `call_tool(name, arguments)` — invokes the tool, joins the multi-block text response

`tool_translator.py` converts MCP `inputSchema` to OpenAI function schema for the LLM.

**TAG engine** (`src/chatbot/engines/tag_engine/`)

The natural-language → SQL pipeline. Files:

| File | Responsibility |
|---|---|
| `semantic_layer.py` | Parses `configs/semantic_layers/ecommerce.yaml`: table descriptions, metrics, dimensions, few-shot examples. |
| `index_builder.py` | Builds `SQLDatabase` + (optionally) `ObjectIndex` + `NLSQLRetriever`. |
| `pipeline.py` | Orchestrates: NL→SQL via NLSQLRetriever → sqlglot validate → execute → repair loop → summarize. |
| `sql_validator.py` | sqlglot AST walk; rejects non-SELECT and dangerous constructs; injects `LIMIT N` if missing. |
| `summarizer.py` | A separate Azure OpenAI call that turns (question, sql, rows) into prose + markdown table. |

**Two operating modes** for index_builder:

1. **Schema RAG** — when an embeddings deployment is configured. Embed per-table descriptions in an `ObjectIndex`; `NLSQLRetriever` picks the top-K relevant tables per question. Necessary for large schemas.
2. **No-RAG fallback** — when no embeddings deployment. Pass all tables directly to `NLSQLRetriever`. Fine for small schemas (the demo warehouse has 6 tables).

### 5.6 Persistence

Two stores, deliberately separate:

| Store | What it holds | Schema location |
|---|---|---|
| `data/chatbot_checkpoints.db` (LangGraph) | The agent's `messages` list and other state per `thread_id` (= `session_id`). | Owned by LangGraph; opaque to us. |
| `data/chatbot.db` (SQLAlchemy async) | `SessionRow` (customer_id, bot_id, awaiting_clarification flag) + `TurnLog` (one row per turn — tokens, latency, tool calls, trace ID). | `src/chatbot/persistence/models.py` |

The session table is the source of truth for *"did the previous turn pause on a clarification"* — read on every request, written after every turn.

The TurnLog table is the source of truth for *"what did this user pay, how long did it take, what tools fired"* — never read by the orchestrator, consumed by analytics / ops.

---

## 6. End-to-End Flows

### 6.1 Simple turn (no clarification, no tools)

User: `"Hello"` to telecom bot.

```
1.  POST /chat            session_id=s1, customer_id=CUST001, bot_id=telecom_support
2.  Handler               load BotConfig, Skills, Session (new row → awaiting=False)
3.  Orchestrator          fetch cached graph for telecom_support
4.  graph.ainvoke         { messages: [HumanMessage("Hello")], customer_id: "CUST001", bot_id: "telecom_support" }
5.  Middleware            BudgetGuard: 0 < cap → pass. dynamic_prompt: builds system message.
6.  Agent → Azure LLM     no tools needed → AIMessage("Hi! How can I help you today?")
7.  TokenUsageMiddleware  state.prompt_tokens_used += 80; state.completion_tokens_used += 12
8.  Handler               persist awaiting_clarification=False; write TurnLog row
9.  Response              { text: "Hi! ...", iterations: 1, tokens: {...} }
```

### 6.2 Clarification turn (telecom)

User: `"change my plan"` (no specific plan named).

**Request 1**:

```
1.  Agent → Azure LLM     decides it needs more info → tool_call ask_clarification(
                            question="Which plan would you like to switch to?",
                            expected="plan_id",
                            suggested_replies=["LITE_299", "PRO_599", "MAX_999"]
                          )
2.  Skill adapter         sees terminal clarification signal → calls interrupt(payload)
3.  LangGraph             pauses the graph; ainvoke returns with result["__interrupt__"]
4.  Orchestrator          extracts payload → TurnSignal(type="clarification", ...)
5.  Handler               persist awaiting_clarification=True on SessionRow
6.  Response              { text: "Which plan ...", awaiting_clarification: true, clarification: {...} }
```

**Request 2** (user replies `"PRO_599"` on the same `session_id`):

```
1.  Handler               sees session.awaiting_clarification=True
2.  Orchestrator          graph.ainvoke(Command(resume="PRO_599"), config={thread_id: s1})
3.  LangGraph             resumes the paused graph; interrupt() inside the adapter returns "PRO_599"
4.  Adapter               returns "User replied: PRO_599" to the agent as the tool result
5.  Agent → Azure LLM     now has enough info → tool_call change_plan(customer_id=CUST001, new_plan_id=PRO_599, confirm=false)
6.  ToolCallSkill         dispatches over MCP → REST API returns proration preview
7.  Agent → Azure LLM     summarizes the preview; may ask for confirmation or commit
```

The key property: clarification pause/resume is the LangGraph `interrupt()` primitive. We do not maintain a custom state machine for "this session is waiting on the user".

### 6.3 BI turn (TAG end-to-end)

User: `"Top 3 products by revenue last 30 days"`.

```
1.  Agent → Azure LLM     tool_call query_business_data(
                            question="Top 3 products by revenue last 30 days"
                          )
2.  TagSkill              → TagPipeline.answer(question)
3.  Pipeline              NLSQLRetriever.retrieve(question)
                          ↳ LlamaIndex builds the text-to-SQL prompt with table DDLs +
                            few-shot examples from configs/semantic_layers/ecommerce.yaml
                          ↳ calls Azure LLM → SQL string
4.  sqlglot validator     parse → check top-level is SELECT/WITH → reject non-SELECT,
                          multi-statement, DDL/DML, PRAGMA, ATTACH → inject LIMIT 100
5.  Executor              sqlite3.connect("file:data/bi_warehouse.db?mode=ro", uri=True,
                                          timeout=2.0) → run → fetchall()
6.  Summarizer            separate Azure LLM call with prose-rules system prompt →
                          "Last 30 days, Premium Laptop led with ₹4.12 Cr..."
                          + a markdown table of the rows
7.  TagSkill              returns ToolResult(text=<summary + table>)
8.  Agent → Azure LLM     final assistant message often echoes the summary verbatim
```

If step 4 or 5 fails, the pipeline prepends the error to the question and retries up to 3 times. After 3 failures it surrenders with an apologetic message.

---

## 7. Configuration

### 7.1 Bot YAML

Example: `configs/bots/bi_assistant.yaml`.

| Section | Purpose |
|---|---|
| `bot_id` / `name` / `description` | Identity. `bot_id` is the URL key. |
| `llm.deployment` | Azure deployment name. Auto-detects reasoning models from the name. |
| `llm.max_tokens`, `temperature` | LLM call parameters. `temperature` is ignored for reasoning models. |
| `llm.max_tool_iterations` | Safety cap on the agent's tool loop. |
| `persona.system_prompt` | The bot's identity / behavior rules. |
| `skills.enabled` | Which Skill classes the router mounts. |
| `tool_call.mcp_servers`, `tool_allowlist` | Only required if `tool_call` is enabled. |
| `tag.semantic_layer_path` and friends | Only required if `tag` is enabled. |
| `clarification.expected_types`, `max_suggested_replies` | Per-bot vocabulary for `ask_clarification`. |
| `guardrails.max_input_chars` | Rejects messages over the cap. |

### 7.2 Environment variables

| Var | Purpose | Default |
|---|---|---|
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI resource URL | (none — required) |
| `AZURE_OPENAI_API_KEY` | API key | (none — required) |
| `AZURE_OPENAI_API_VERSION` | API version | `2025-01-01-preview` |
| `AZURE_OPENAI_DEPLOYMENT` | Bot LLM deployment (overrides per-bot YAML) | (uses YAML) |
| `AZURE_OPENAI_SQL_GEN_DEPLOYMENT` | TAG SQL-gen deployment override | falls back to bot's deployment |
| `AZURE_OPENAI_SUMMARIZER_DEPLOYMENT` | TAG summarizer deployment override | falls back to bot's deployment |
| `AZURE_OPENAI_EMBED_DEPLOYMENT` | Embeddings deployment for schema RAG | unset → no-RAG mode |
| `CHATBOT_DAILY_TOKEN_CAP` | Per-tenant daily token cap | `1000000` |
| `CHATBOT_DB_URL` | Async SQLAlchemy URL | `sqlite+aiosqlite:///data/chatbot.db` |
| `CHATBOT_CHECKPOINT_DB` | LangGraph checkpoint sqlite path | `data/chatbot_checkpoints.db` |

### 7.3 Adding a new bot

1. Create `configs/bots/<bot_id>.yaml`. Copy from an existing one as a template.
2. List the skills it should mount in `skills.enabled`.
3. Configure any skill-specific blocks (`tool_call`, `tag`, `clarification`).
4. POST to `/chat` with `bot_id: "<bot_id>"`. No code changes.

### 7.4 Adding a new skill

1. Create `src/chatbot/skills/<name>_skill.py` subclassing `Skill`.
2. Implement `prepare_tools()`, `execute_tool()`, `owns_tool()`, optionally `system_prompt_addition()`.
3. Add a branch in `bot_router._build_*_skill` or `get_skills` to instantiate it.
4. Enable it in a bot's YAML.

The orchestrator does not need to change. The skill→tool adapter handles the LangGraph integration.

---

## 8. Operations

### 8.1 Running locally

```sh
# Activate your env first (pyenv activate env_311, or a .venv).
make install         # pip-installs platform + services into the active env
make bi-seed         # populates data/bi_warehouse.db with ~500 orders
make run             # honcho-managed: telecom_api + mcp_telecom + chatbot (RAG is in-process)
```

The `make run` target uses `Procfile` to start three processes:

| Process | Port | Notes |
|---|---|---|
| `telecom_api` | 8001 | FastAPI that owns the telecom REST + its SQLite store |
| `mcp_telecom` | 8765 | FastMCP server that wraps the telecom REST as MCP tools |
| `chatbot` | 8000 | The platform itself |

### 8.2 Testing the platform

Unit + small integration tests:
```sh
make test
# or
python -m pytest tests/ --ignore=tests/test_mcp_tools.py --ignore=tests/test_telecom_api.py
```

Test categories:

| Test file | Covers |
|---|---|
| `tests/test_clarification.py` | ClarificationSkill schema; YAML config wiring |
| `tests/test_chat_history.py` | History-bubble filter on LangChain messages |
| `tests/test_conversation_persistence.py` | SessionRow / MessageRow round-trip |
| `tests/test_middleware.py` | Each of the three middlewares in isolation |
| `tests/test_reasoning_models.py` | Reasoning auto-detect + temperature omission |
| `tests/test_tag_pipeline.py` | End-to-end TAG pipeline against the real seeded warehouse with mocked LLMs |
| `tests/test_tag_sql_validator.py` | sqlglot validator rejection cases |
| `tests/test_tool_translator.py` | MCP → OpenAI schema translator |

### 8.3 Testing the bots manually

Telecom (via the UI at http://localhost:8000):
- "What plan am I on?"
- "Change my plan" → bot should ask which plan → reply "PRO_599"

BI (via curl since the UI is hardcoded to telecom):
```sh
curl -s -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "bi-demo",
    "customer_id": "CUST001",
    "bot_id": "bi_assistant",
    "message": "Top 3 products by revenue last 30 days"
  }' | jq .
```

### 8.4 Observability

Every turn writes one row to `TurnLog`:

```sql
SELECT trace_id, bot_id, latency_ms, iterations,
       prompt_tokens, completion_tokens, awaiting_clarification
FROM turn_logs ORDER BY ts DESC LIMIT 10;
```

Structured logs use stage prefixes — grep for any of these to see one stage end-to-end:

| Prefix | Stage |
|---|---|
| `[chat]` | API handler |
| `[orch]` | Orchestrator |
| `[clar]` | Clarification skill |
| `[mcp]` | MCP transport |
| `[tag]` | TAG pipeline |
| `[conv]` | SessionRow operations |

Set `LOG_LEVEL=DEBUG` in `.env` for full payload dumps.

---

## 9. Technology Choices and Why

### 9.1 Why LangChain v1 + LangGraph?

LangChain v1's `langchain.agents.create_agent` is purpose-built for the shape we want: tool-calling agent with stateful conversation across requests, pluggable middleware, persistence via LangGraph checkpointer. We could have built our own state machine but it would re-implement what `create_agent` already does well — and we'd own the maintenance burden.

LangGraph's `interrupt()` primitive is critical: it lets us model clarification as "pause the graph, deliver the question, resume when the user replies" with one call. Before adopting it we maintained a custom history-scan trick to detect paused clarifications. That was brittle and broke on edge cases.

### 9.2 Why LlamaIndex for NL → SQL?

`NLSQLRetriever` and `ObjectIndex` are battle-tested LlamaIndex primitives for this exact problem. Building NL → SQL ourselves means writing the prompt, the schema-fitting logic, and (in the RAG case) the table-retrieval logic. LlamaIndex has done this work.

We do not use LlamaIndex's response synthesizer or executor. The response synthesizer is too generic for our presentation rules; the executor doesn't enforce read-only semantics defensibly. So:

- LlamaIndex generates the SQL.
- We validate it with sqlglot.
- We execute it ourselves on a `mode=ro` sqlite connection.
- We summarize with a dedicated Azure OpenAI call that has our prompt rules.

### 9.3 Why MCP for tool calling?

MCP decouples the agent from the tool implementation. The telecom bot's tools live in a separate process — `services/mcp_telecom` — that could be written in any language and deployed independently. The agent only sees the schema. If we add a Sales MCP server tomorrow, the agent doesn't change.

### 9.4 Why sqlglot for SQL safety?

A regex can reject `DROP TABLE` but misses `INSERT … SELECT`, `CREATE TABLE AS …`, mutations hidden inside CTE bodies, and PRAGMA toggles. sqlglot parses SQL into an AST. We walk the AST and reject any node of the wrong kind, no matter where it appears.

### 9.5 Why a dedicated summarizer LLM in TAG?

The summarizer's system prompt is tuned for data-presentation rules ("never invent numbers, cite the exact row count, use ₹ for currency, round to 2dp"). That is a different concern from SQL generation, and a different concern from the bot's persona. Keeping it separate means we can tune or swap models for one stage without affecting the others.

---

## 10. Known Limitations

| Limitation | Today | What it would take |
|---|---|---|
| Single-provider LLM | Hardcoded Azure OpenAI | Route LLM construction through an LLM gateway (LiteLLM / Portkey) |
| Single-process orchestrator | `AsyncSqliteSaver` keyed by `session_id`; sticky in process | Postgres or Redis checkpointer; horizontally scale |
| In-process budget store | `dict[str, int]`; resets on restart | Redis with midnight-rollover keys |
| No streaming responses | `ainvoke` returns the whole reply | Switch to `agent.astream()` and forward chunks over SSE |
| MCP client is per-request | Each tool call opens a new HTTP session | Long-lived `ClientSession` cached at boot |
| No evals harness | Manual smoke tests | Pull in DeepEval or similar; pre-deploy a small "gold" set per skill |
| BI warehouse is sqlite-only | Local file, mode=ro | `SQLDatabase` abstracts the engine — swap to Postgres/Snowflake at the engine level |
| UI hardcodes telecom | No bot selector in the demo HTML | Add a dropdown; pass `bot_id` on each request |
| No multi-language | English only | i18n the persona prompt + skill descriptions |

---

## 11. Glossary

| Term | Definition |
|---|---|
| **Agent loop** | The LangChain pattern of repeatedly calling the LLM and then any tools it requested, until the LLM returns a final message with no tool calls. |
| **Checkpoint** | A snapshot of graph state at one node-transition boundary. Persisted by `AsyncSqliteSaver`. |
| **Deployment** | Azure OpenAI term for a named instance of a model in your resource. Distinct from "model" (e.g. you can have two deployments of `gpt-4o` for prod / staging). |
| **MCP** | Model Context Protocol. An open protocol for exposing tools to LLM agents over HTTP/stdio. |
| **Middleware** | A LangChain v1 hook that wraps the agent's model call or runs before/after it. Used here for dynamic prompts, token tracking, budget guard. |
| **Reasoning model** | OpenAI's o-series and gpt-5+ family. Locked to temperature=1.0; uses `max_completion_tokens` instead of `max_tokens`. |
| **Schema RAG** | Retrieval-augmented schema selection. Embed per-table descriptions; retrieve the K most relevant tables per question; pass only those into the SQL-gen prompt. |
| **Skill** | The platform's unit of pluggable capability. Subclass of `Skill` ABC. |
| **Thread ID** | LangGraph term for the partition key of conversation state. We set it to `session_id`. |
| **Tool** | A function the LLM can call. Each tool has a JSON-schema for its arguments and a Python coroutine that runs it. |
| **TurnLog** | One row per turn (in `chatbot.db`) capturing tokens, latency, tool calls, awaiting_clarification, and a unique `trace_id` that threads through all logs for that turn. |

---

## 12. Quick Reference

**File map for new joiners**:

| Read this | When you want to understand |
|---|---|
| `src/chatbot/api/chat.py` | The HTTP-to-orchestrator boundary |
| `src/chatbot/core/langgraph_orchestrator.py` | How the agent is built and how a turn is run |
| `src/chatbot/core/middleware.py` | What runs around every model call |
| `src/chatbot/skills/base.py` | The contract every skill follows |
| `src/chatbot/skills/clarification_skill.py` | The simplest skill — one tool, no engine |
| `src/chatbot/adapters/skill_to_tool.py` | The bridge from Skill ABC to LangChain StructuredTool — and where `interrupt()` is called |
| `src/chatbot/engines/tag_engine/pipeline.py` | The NL → SQL pipeline end-to-end |
| `src/chatbot/engines/tag_engine/sql_validator.py` | SQL safety, in one file |
| `configs/bots/bi_assistant.yaml` | A complete bot configuration |
| `configs/semantic_layers/ecommerce.yaml` | A complete semantic layer for TAG |
