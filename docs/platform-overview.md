# Chatbot Platform — Architecture & Strategy

> Audience: Tech leadership, architecture review board, product leadership.
> Purpose: Explain what the platform does, why it exists, what it costs to operate, and how it extends.
> Status: v1.0 live with two bots (Support / BI). Web Scrape capability on the roadmap.

---

## 1. Executive Summary

We have built a **single platform that hosts many specialised chatbots**. Each bot is a configuration choice (a YAML file plus a small set of capabilities), not a separate codebase. Today the platform runs in production with two bots; the architecture supports four bot archetypes (Support, BI, Website, Transactional) on the same engine.

**Why this matters.** Conversational interfaces are now the expected front door for customer support, internal analytics, transactional self-service, and website search. Building each as a one-off project means duplicating authentication, conversation memory, safety guardrails, cost controls, and observability for every team that wants a chatbot. This platform makes those concerns common infrastructure. New bots ship in **days**, not quarters.

**What we ask of leadership in this doc.**
- Confirm the platform direction and the four target bot archetypes.
- Approve the next-quarter investment in RAG and Web Scrape capabilities (already designed; not yet built).
- Sign off on the cost-governance model (per-tenant token caps, deployment routing).

---

## 2. Business Context

### 2.1 The problem we solve

| Today (without the platform) | With the platform |
|---|---|
| Each business team building a chatbot reinvents the same plumbing — session management, prompt engineering, tool invocation, safety filters, cost tracking. | One platform team owns the plumbing. Business teams own only their bot's YAML config and domain knowledge. |
| LLM costs are unpredictable. No per-tenant accountability. | Per-tenant daily token cap is enforced before every model call. Costs are queryable per bot, per customer, per day. |
| Each chatbot has its own observability story — or none. | Every turn produces a structured log with trace ID, token usage, latency, tools called. One dashboard covers every bot. |
| Onboarding a new use case requires an engineering project. | Onboarding a new bot is a config + skill enablement. |

### 2.2 Bot archetypes — the four ways the platform creates value

The platform supports **four reusable bot archetypes**. Each corresponds to a category of business problem and uses a different combination of skills.

| # | Bot Archetype | Business Problem | Skills Used | Example Use Case | Status |
|---|---|---|---|---|---|
| 1 | **Support Bot** | Customers asking "how do I…" / "what does my product do" — answers come from a corpus of documentation that changes weekly. | RAG | First-line product support; deflects ~40% of help-desk tickets. | Roadmap (Q-next) |
| 2 | **BI Assistant** | Business users ("show me revenue by segment last month") who don't write SQL but need answers from the warehouse. | TAG (NL→SQL) | Marketing, Finance, Ops self-service analytics. | **Live** |
| 3 | **Website Chatbot** | Public-website visitors asking about products / policies / pricing. Content is whatever's on the corporate site. | RAG + Web Scrape | Pre-sales, lead capture, FAQ deflection. | Roadmap (Q-next) |
| 4 | **Transactional Bot** | Customers performing a guarded action — change plan, reset PIN, pay a bill, file a complaint. Each action calls a real internal API. | Tool Call | Telecom customer self-service (live). Generalises to any vertical with backend APIs. | **Live** |

These four archetypes cover the large majority of internal and customer-facing chatbot use cases. A fifth bot type — multi-skill bots that combine, say, RAG + Tool Call ("look up the policy, then process the refund") — is supported by the architecture and is a natural extension once the foundational four are live.

### 2.3 Two bots in production today

| Bot | What it does | Business owner |
|---|---|---|
| **Telecom Support** | Customer-care + self-service for telecom subscribers. Answers questions about plans, bills, network status; performs guarded actions like plan changes with two-step confirmation. | Customer Operations |
| **BI Assistant** | Translates plain-English business questions into safe read-only SQL against the analytics warehouse. Returns a prose answer plus a markdown data table. | Data / Analytics |

---

## 3. Architecture Overview

```
   ┌──────────────────────────────────────────────────────────┐
   │  Channels: Web Widget · Mobile · REST · WhatsApp · iframe │
   └────────────────────────────┬─────────────────────────────┘
                                ▼
                ┌───────────────────────────────┐
                │  API Gateway                   │
                │  Auth · Rate Limit · Tenant    │
                │  Routing · SSE / WebSocket     │
                └───────────────┬───────────────┘
                                ▼
                ┌───────────────────────────────┐
                │  Bot Router                    │
                │  Reads bot config →            │
                │  activates the right modules   │
                └───────────────┬───────────────┘
                                ▼
   ┌──────────────────────────────────────────────────────────┐
   │  CORE ENGINE                                              │
   │  Conversation Manager · LLM Orchestrator · Response       │
   │  Formatter · Intent Classifier · Persona Store ·          │
   │  Guardrails (PII / Safety / Limits) · Bot Config Store    │
   │                                                           │
   │  Pluggable skill slots, activated by bot config:          │
   │    RAG Skill │ TAG / SQL Skill │ Tool Call │ Web Scrape   │
   └──┬───────────────┬───────────────┬───────────────┬───────┘
      ▼               ▼               ▼               ▼
  ┌────────┐    ┌────────┐    ┌──────────┐    ┌────────────┐
  │ RAG    │    │ TAG    │    │ Tool     │    │ Web        │
  │ Engine │    │ Engine │    │ Engine   │    │ Scraper    │
  └───┬────┘    └───┬────┘    └────┬─────┘    └─────┬──────┘
      │             │              │                │
      └─────────────┴──── External Integrations ────┴────────┐
                        Internal APIs (Pay / Account / KYC)   │
                        Databases (Analytics RO / Warehouse)  │
                        Document Sources (S3 / Confluence)    │
                                                              ▼
   ┌──────────────────────────────────────────────────────────┐
   │  OBSERVABILITY                                            │
   │  Traces · Cost / Query · Latency P50/P99 · User Feedback  │
   │  · Eval / Accuracy · Conversation Logs                    │
   └──────────────────────────────────────────────────────────┘
```

### 3.1 The four-layer mental model

| Layer | Responsibility | What it isolates |
|---|---|---|
| **Channels** | How a user reaches the bot. Web widget, mobile, WhatsApp, REST API. | UI / channel-specific concerns. |
| **API Gateway** | Authentication, rate limiting, tenant routing, streaming protocol. | Cross-cutting platform policies. Bots never deal with auth directly. |
| **Bot Router + Core Engine** | The brain. Picks the right bot for the request, builds the prompt, calls the LLM, dispatches tools, formats the response. | The decision of *which bot* from *what the bot does*. |
| **Skill Engines** | Capability implementations: retrieval over documents, NL→SQL, calling internal APIs, scraping web content. | Domain complexity. The engine layer is where vendor frameworks (LlamaIndex, MCP, sqlglot) live. |

### 3.2 Why a single platform instead of N bot projects

| Lever | Value |
|---|---|
| **Time-to-market** | New bot ≈ 1 YAML file + skill enablement. Days, not quarters. |
| **Cost control** | Per-tenant token caps and per-stage deployment routing live in one place. Finance gets one bill, one report. |
| **Safety** | PII redaction, prompt-injection defences, and SQL safety are platform features. Every new bot inherits them on day one. |
| **Observability** | One trace ID per turn, one log shape, one dashboard. Incident response is consistent across bots. |
| **Hiring leverage** | One small platform team can support many business teams. Business teams own their bot's content, not its plumbing. |

---

## 4. Core Capabilities (The Skills)

Skills are the unit of *what a bot can do*. Each is independently developed, tested, and reused. A bot's YAML enables the skills it needs.

### 4.1 RAG Skill — "Answer from documents" (Roadmap)

**Business use.** Support bot and website chatbot. Any time the answer lives in unstructured text (PDFs, Confluence, knowledge-base articles, marketing pages).

**How it works.** Documents are ingested, chunked, embedded into a per-tenant vector store. At query time the question is embedded, the most relevant chunks are retrieved, optionally re-ranked, and inserted into the LLM prompt as context.

**Tenant isolation.** Vector stores are partitioned per tenant. One support bot's documents cannot leak into another's responses. This is the basis of the multi-tenant deployment model.

**Refresh story.** Documents are re-ingested on a schedule (daily for marketing content, on-write for product KB). Web Scrape feeds directly into this pipeline for the website chatbot.

### 4.2 TAG / SQL Skill — "Answer from the data warehouse" (Live)

**Business use.** BI Assistant. Marketing, Finance, Ops asking ad-hoc questions of the analytics warehouse without writing SQL.

**How it works (five guarded stages):**

1. **Schema retrieval.** The bot has a *semantic layer* — a curated description of tables, metrics, dimensions. For large schemas the platform retrieves only the relevant tables per question (schema RAG).
2. **NL → SQL generation.** The LLM produces a single SQL `SELECT` against the retrieved tables.
3. **Safety validation.** The SQL is parsed into an abstract syntax tree (sqlglot) and rejected if it isn't a single read-only `SELECT`. A `LIMIT` is injected automatically.
4. **Read-only execution.** The query runs against a *read-only* database connection. Even if validation has a bug, the connection itself cannot mutate.
5. **Summarisation.** A dedicated, cheaper LLM turns the SQL result into a 2-3 sentence prose answer with a markdown data table beneath.

**Safety as a platform feature.** No business team has to remember to lock down their warehouse. The platform enforces read-only, statement timeouts, and result-size caps for every TAG bot uniformly.

**Self-correction.** If the LLM produces SQL that fails validation or execution, the error is fed back to the model and it tries again. Up to 3 attempts before a graceful surrender. This keeps end-to-end success rate high even when the model misunderstands the schema.

### 4.3 Tool Call Skill — "Perform an action via internal APIs" (Live)

**Business use.** Transactional bot. Any guarded action that hits a real backend system — change a customer's plan, reset a PIN, pay a bill, file a complaint, create a support ticket.

**How it works.** Internal services expose their tools through the **Model Context Protocol (MCP)** — an open standard for LLM-callable tools. The platform's Tool Call skill discovers and invokes them. Each service is a separate process and can be written in any language.

**Why MCP matters strategically.** It decouples the bot from the service implementation. Adding a Sales service tomorrow doesn't require changing the bot — only registering the new MCP server. Replacing the LLM vendor doesn't require rewriting the services.

**Two-step confirmation pattern.** Mutating actions (plan change, payment) follow a *preview-then-confirm* protocol enforced by the tool's contract. The bot must always show a preview to the user and get explicit confirmation before committing. This is a platform-level safety net for high-stakes operations.

### 4.4 Web Scrape Skill — "Use website content as a knowledge source" (Roadmap)

**Business use.** Website chatbot. Public-website FAQ deflection, pricing questions, product information. Whenever the answer is *on the corporate site* but you don't want to maintain a parallel KB.

**How it works.** A crawler discovers pages from a sitemap; content is extracted from HTML, cleaned (boilerplate stripped), and fed into the RAG ingestion pipeline. A scheduler triggers re-crawls on configurable cadence.

**Why this matters.** It eliminates the duplicate-content problem (the website team owns one source of truth, not two). It also handles content churn — pages change, the chatbot's answers stay current automatically.

---

## 5. Cross-Cutting Platform Concerns

### 5.1 Security & Compliance

| Concern | How the platform handles it |
|---|---|
| **Authentication** | API Gateway. Every request carries a tenant + user identity. Bots receive the identity but cannot bypass authentication. |
| **PII redaction** | Guardrails module redacts known PII patterns from logs and from prompt context before they reach the LLM. |
| **Prompt injection** | All tool / document content treated as untrusted. Wrapped in clear delimiters. Tool authorisation is enforced server-side, not by the LLM — the LLM is never the security boundary. |
| **SQL injection / mutation** | Three layers of defence (parse → validate → read-only connection). See §4.2. |
| **Data residency / tenant isolation** | Per-tenant vector stores. Per-tenant token tally. Each tenant's data never enters another tenant's prompt. |
| **Audit** | Every turn produces a TurnLog with trace ID, tools called, tokens used. Conversation logs (full message history) retained per the tenant's retention policy. |

### 5.2 Cost Governance

LLM costs scale unpredictably with usage. The platform makes them predictable.

| Lever | Where it lives | What it controls |
|---|---|---|
| **Per-tenant daily token cap** | Budget Guard middleware (runs before every LLM call) | Hard cap. Over budget → polite refusal, no LLM call. |
| **Per-stage deployment routing** | Bot config + env overrides | A bot can use a cheap model for SQL generation, a stronger one for the user-facing answer. Saves 60-80% on TAG bots in our measurements. |
| **Reasoning-model awareness** | Orchestrator + TAG engine | Automatically uses `max_completion_tokens` and omits `temperature` for o-series / gpt-5+ deployments. Avoids wasted retries from API rejections. |
| **Prompt caching** | Azure OpenAI automatic + LangChain stable-prefix discipline | System prompts stay structurally identical across turns → automatic prompt-cache hits → ~50% input token reduction on conversational sessions. |
| **Cost report** | Observability layer | Per-bot, per-tenant, per-day. Joins with Finance billing data downstream. |

### 5.3 Observability

Every turn writes a structured TurnLog record:

| Field | Used for |
|---|---|
| `trace_id` (unique per turn) | Cross-system correlation; threads through every log line and downstream service call |
| `bot_id`, `tenant_id`, `customer_id` | Per-bot, per-tenant slicing |
| `iterations`, `tool_calls` | How many LLM calls and tool dispatches this turn took |
| `prompt_tokens`, `completion_tokens`, `cached_tokens` | Cost and prompt-cache effectiveness |
| `latency_ms` | P50 / P95 / P99 SLO tracking |
| `awaiting_clarification` | UX-flow analytics ("how often does the bot have to ask follow-ups?") |

In addition, every stage emits structured log lines (prefix-tagged: `[orch]`, `[tag]`, `[rag]`, `[clar]`) so a single turn can be reconstructed from logs alone.

### 5.4 Evaluation & Quality

Quality of an LLM-driven system is a continuous concern, not a launch milestone.

| Mechanism | What it does | Status |
|---|---|---|
| **Per-skill regression suites** | Gold question → expected SQL / expected tool call / expected response shape. Run pre-deploy. | In progress |
| **User feedback signals** | Thumbs-up / thumbs-down captured per turn, joined with trace ID. | Roadmap |
| **Production sample replay** | Replay last week's turns against a candidate model to compare. | Roadmap |
| **Cost & latency budgets per bot** | Each bot declares a budget; SRE alerts on regressions. | In progress |

---

## 6. Operating Model

### 6.1 How a new bot gets shipped

| Step | Owner | Effort |
|---|---|---|
| 1. Identify the use case and archetype (Support / BI / Website / Transactional / mixed) | Business team | — |
| 2. Author a bot YAML: persona, enabled skills, per-skill config | Business team (with platform team for first bot) | 1–2 days |
| 3. For RAG: hand the platform team the document sources | Business team | 1 day |
| 4. For TAG: define the semantic layer (table descriptions, metrics, dimensions) | Business team + Data team | 2–3 days |
| 5. For Tool Call: register the internal service as an MCP server (one-time, per service) | Owning service team | 3–5 days first time, hours thereafter |
| 6. Smoke test in staging; tune persona | Business team | 1–2 days |
| 7. Roll out | Platform team | — |

**Total: 1–2 weeks for a first bot in a new vertical; days for subsequent bots in the same vertical.**

### 6.2 Who owns what

| Concern | Owner |
|---|---|
| Platform code (router, orchestrator, skills, engines) | Platform team |
| Bot YAML, persona, prompt tuning | Business team |
| Semantic layer (TAG bots) | Data team in partnership with the business team |
| MCP server (Tool Call bots) | The service team that owns the API |
| Document corpus (RAG bots) | The business team / content owners |
| SLOs (latency, cost, accuracy) per bot | Business team — defined; Platform team — enforced |

### 6.3 Adding new capabilities

The platform has four skill slots today. Adding a fifth is an engineering project (~2 weeks):
1. Implement the engine (e.g. a Voice Skill that pipes Whisper transcription + TTS).
2. Implement the Skill class that wraps the engine.
3. Document the YAML configuration shape.
4. Add observability hooks.

Every existing bot remains unaffected.

---

## 7. Technology Choices (Summary)

A leadership-level view; engineering details live in the architecture appendix.

| Decision | Choice | Why |
|---|---|---|
| Agent orchestration framework | **LangChain v1 / LangGraph** | Industry-standard tooling for stateful conversational agents. Strong community, active development, first-class support for human-in-the-loop pauses (used for clarification). |
| LLM provider (today) | **Azure OpenAI** (`o4-mini`) | Enterprise compliance posture; existing org commitment. Architecture allows multi-provider failover later via an LLM gateway pattern. |
| Tool invocation protocol | **Model Context Protocol (MCP)** | Open standard. Decouples LLM from service implementation. Lets internal teams expose APIs to chatbots without giving up control of their service. |
| NL → SQL | **LlamaIndex** `NLSQLRetriever` + custom safety layer | Best-in-class for schema-aware SQL generation. We layer sqlglot validation on top because LlamaIndex doesn't enforce read-only semantics defensibly. |
| SQL safety | **sqlglot AST parser** | Catches mutation attempts that regex would miss (e.g. `INSERT … SELECT`, `CREATE TABLE AS`). Defence-in-depth alongside read-only DB connections. |
| Conversation state | **LangGraph SQLite checkpointer** (today); Postgres or Redis (production scale) | Today suffices for single-process operation; trivial to swap when we scale horizontally. |
| Observability | **Structured JSON logs + per-turn audit table** | Vendor-neutral; routes to Splunk / Datadog / Grafana as the org chooses. |

---

## 8. Scale & Roadmap

### 8.1 Where the platform is today

- Two bots live (Telecom Support, BI Assistant)
- Single-process deployment (one chatbot service instance per environment)
- ~1M tokens / tenant / day budget cap (configurable)
- sqlite-backed conversation state and audit logs
- All four engine slots designed; two implemented (Tool Call, TAG); two designed (RAG, Web Scrape)

### 8.2 Path to 10,000 tenants

The architecture is shaped for horizontal scale; the change is mostly persistence.

| Component | Today | At scale |
|---|---|---|
| Orchestrator | Single process, stateless | Horizontally scaled; multiple instances behind a load balancer |
| Conversation state | sqlite checkpointer | Postgres or Redis checkpointer (LangGraph supports both natively) |
| Per-tenant budget store | In-process dict | Redis with midnight-rollover keys |
| MCP client | Per-request HTTP session | Long-lived pooled sessions per MCP server |
| Vector store (RAG) | n/a yet | Per-tenant collections in Pinecone / Weaviate / pgvector |
| LLM | Single Azure OpenAI deployment | LLM gateway (LiteLLM / Portkey) for multi-provider failover and routing |

None of this requires architectural change. It is a configuration / persistence-layer swap.

### 8.3 Quarter-by-quarter outlook (illustrative)

| Quarter | Milestone |
|---|---|
| **Now** | Telecom Support + BI Assistant live. Tool Call and TAG engines in production. |
| **Next** | RAG skill + RAG engine live. First Support bot launched. Web Scrape engine designed. |
| **Next +1** | Website chatbot live (combines RAG + Web Scrape). Mixed-skill bots (e.g. RAG + Tool Call). |
| **Next +2** | Multi-provider LLM gateway. Per-tenant cost dashboards GA. Eval harness automated in CI. |

---

## 9. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| LLM cost overrun by a single tenant | Medium | High | Per-tenant daily cap enforced by middleware before every call. Hard ceiling, not a soft alert. |
| Prompt-injection attack via tenant content | Medium | High | Tenant content is delimited and treated as untrusted in prompts. Tool authorisation is server-side, never LLM-decided. |
| LLM-generated SQL mutating the warehouse | Low | Critical | Three layers of defence (AST parse → reject non-SELECT → read-only DB connection). The DB cannot mutate even if validation has a bug. |
| Hallucinated answers in BI | Medium | High | Summarizer prompt explicitly forbids inventing numbers; only data from query results is permitted. SQL + result table are surfaced to the user for spot-checking. |
| Single LLM provider outage | Low | High | Architecture supports gateway-based multi-provider failover; we have not enabled it yet. **Action item: by Q-next +2.** |
| Tenant data leak across vector stores | Low | Critical | Per-tenant partitioning enforced at the index level, not at the query layer. **To be validated during RAG rollout.** |
| Conversation memory drift on long sessions | Medium | Medium | Conversation state owned by LangGraph checkpointer; sliding window strategies available. |
| New skill introduces orchestrator regression | Medium | Medium | All skills bind through one stable adapter. Adapter contract is small and well-tested (60 unit tests today). |

---

## 10. What We Need From Leadership

| Decision needed | Why |
|---|---|
| **Endorse the four-archetype strategy** as the platform's product positioning. | Aligns business-team expectations and prevents one-off "snowflake" bot projects. |
| **Approve next-quarter engineering investment** in the RAG and Web Scrape engines. | Required to ship Support and Website bots. |
| **Approve the cost-governance model** (per-tenant daily caps, per-stage deployment routing). | Finance and procurement need predictable LLM spend. |
| **Identify the first three RAG bot use cases** to pilot. | Prioritises ingestion engineering work. |
| **Confirm the deployment topology** for multi-tenant production (single instance per region vs. shared multi-tenant). | Drives the persistence-layer choice for conversation state. |

---

## Appendix A — Glossary

| Term | Meaning |
|---|---|
| **Bot** | A configuration that combines a persona, a set of enabled skills, and per-skill config. One platform hosts many bots. |
| **Skill** | A pluggable capability (RAG, TAG, Tool Call, Web Scrape). Bots opt in to the skills they need. |
| **Engine** | The implementation behind a skill (e.g. the TAG engine implements SQL generation, validation, execution, summarisation). |
| **Session** | A multi-turn conversation, identified by a session ID. State persists across requests. |
| **Turn** | One user message and the bot's reply. One row in the audit table per turn. |
| **Tool** | A single callable function the LLM can invoke (e.g. `change_plan`, `query_business_data`). |
| **MCP** | Model Context Protocol — an open protocol for exposing tools to LLM agents. |
| **RAG** | Retrieval-Augmented Generation — answering questions by retrieving relevant document chunks and including them in the LLM prompt. |
| **TAG** | Text-to-Analytics-Generation — answering questions by generating SQL against a database. |
| **Semantic layer** | A curated description of warehouse tables, metrics, and dimensions that the TAG engine uses to ground SQL generation. |
| **Trace ID** | A unique identifier per turn that threads through every log line, enabling end-to-end correlation. |

---

## Appendix B — Reference

- Detailed engineering architecture: `docs/architecture.md` (sibling document, file-level depth).
- Demo deployment: `make install && make bi-seed && make run`.
- Source: this repository.
