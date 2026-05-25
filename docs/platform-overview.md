# Chatbot Platform — Overview

## What this is

A single platform that hosts many chatbots. Each chatbot is a small configuration file plus a chosen set of capabilities. They all share one engine — the same conversation logic, the same safety rules, the same memory, the same monitoring.

Two bots run on the platform today:

- **Telecom Support** — answers customer questions and performs guarded actions like changing a plan or paying a bill.
- **BI Assistant** — answers natural-language business questions ("revenue by segment last month") by writing safe SQL against an analytics database and replying with a short prose summary plus a data table.

The same platform is designed to host other kinds of bots — a documentation Q&A bot, a website chatbot, others — without rewriting the engine. Those are described later in this document.

---

## The idea

Most chatbots end up being the same software repeated: a way to take a user message, look up who the user is, decide what to do, call one or more services, and reply. The platform extracts that shared scaffolding into a single product so the team building a new chatbot only has to think about the bot's *purpose* and *capabilities*, not its plumbing.

A new bot, in this model, is mostly:

1. A short YAML file describing the bot's persona (the system prompt it uses) and which capabilities it should have access to.
2. The configuration for those capabilities — which database, which API server, which document store, etc.

Everything else — conversation memory, prompt assembly, model calls, tool dispatching, safety checks, cost tracking, logging — comes from the platform.

---

## The big picture

```
   ┌──────────────────────────────────────────────────────────┐
   │  Channels: Web Widget · Mobile · REST · WhatsApp · iframe │
   └────────────────────────────┬─────────────────────────────┘
                                ▼
                ┌───────────────────────────────┐
                │  API Gateway                   │
                │  Auth · Rate Limit · Routing   │
                └───────────────┬───────────────┘
                                ▼
                ┌───────────────────────────────┐
                │  Bot Router                    │
                │  Picks the bot, activates the  │
                │  capabilities listed in config │
                └───────────────┬───────────────┘
                                ▼
   ┌──────────────────────────────────────────────────────────┐
   │  Core Engine                                              │
   │   • Conversation Manager — session, history, memory      │
   │   • LLM Orchestrator — prompt build, model call, parse   │
   │   • Response Formatter                                    │
   │   • Persona Store, Guardrails, Bot Config Store          │
   │                                                           │
   │  Pluggable skills (turned on per bot):                    │
   │    RAG  ·  TAG / SQL  ·  Tool Call  ·  Web Scrape         │
   └──┬───────────────┬───────────────┬───────────────┬───────┘
      ▼               ▼               ▼               ▼
  ┌────────┐    ┌────────┐    ┌──────────┐    ┌────────────┐
  │ RAG    │    │ TAG    │    │ Tool     │    │ Web        │
  │ Engine │    │ Engine │    │ Engine   │    │ Scraper    │
  └────────┘    └────────┘    └──────────┘    └────────────┘
       │             │              │                │
       │             ▼              ▼                │
       │   ┌──────────────────────────────┐          │
       │   │ Databases, internal APIs,    │          │
       └──→│ document stores, websites…   │←─────────┘
           └──────────────────────────────┘

   ┌──────────────────────────────────────────────────────────┐
   │  Observability: traces, latency, cost, conversation logs  │
   └──────────────────────────────────────────────────────────┘
```

Each layer only talks to the one below it. That's what lets us add a new bot — or a new capability — without disturbing the rest.

---

## The moving parts

### Channels

Where a user actually reaches the bot. The same backend serves a web chat widget, a REST API, a mobile app, WhatsApp, or an embedded iframe on a corporate site. The bot doesn't know or care which channel a message came from.

### API Gateway

The front door. It authenticates the user, rate-limits abusive callers, routes to the right backend, and handles streaming responses where needed. Bots inherit these protections; they don't implement them.

### Bot Router

Reads the bot's configuration file and decides which capabilities to switch on for this conversation. If a bot's config lists `tag` and `clarification`, the router mounts those two skills and ignores the rest.

### Core Engine

The brain. It handles five recurring jobs that every bot needs:

| Component | What it does |
|---|---|
| **Conversation Manager** | Keeps track of the conversation: session ID, history of messages, the user's pending state (for example, "still waiting for a clarification answer"). |
| **LLM Orchestrator** | Assembles the prompt from the bot's persona + recent history + any tool definitions, calls the language model, and parses the result. Re-runs the loop when the model wants to call a tool. |
| **Response Formatter** | Shapes the engine's output into the final chat response the channel expects. |
| **Persona Store** | Each bot's system prompt and per-skill instructions. |
| **Guardrails** | PII redaction, prompt-injection defences, input-size limits. |
| **Bot Config Store** | The YAML/JSON file that defines every bot — its persona, its skills, its limits. |

### Skills (the pluggable parts)

A skill is one unit of capability. Bots opt in to skills via config. Today the platform has four skill slots:

| Skill | What it lets a bot do |
|---|---|
| **RAG** | Answer questions from a set of documents — manuals, knowledge base articles, PDFs, internal wikis. |
| **TAG / SQL** | Translate a natural-language question into safe SQL, run it against a database, and summarise the result. |
| **Tool Call** | Call internal services to actually *do* things — change a plan, reset a PIN, pay a bill, create a ticket. |
| **Web Scrape** | Pull content from public websites and turn it into knowledge the bot can use. Feeds into RAG. |

**Live today**: Tool Call (powering Telecom Support) and TAG (powering BI Assistant). RAG and Web Scrape are designed in the architecture and not yet built.

### Engines

Behind each skill is an engine — the heavier-lifting part. The skill is a small wrapper that knows how to expose itself to the bot's brain; the engine is where the actual work happens.

| Engine | Pieces |
|---|---|
| **RAG Engine** | Document ingestion → chunking + embedding → vector store (per tenant) → retrieve + rerank at query time. |
| **TAG Engine** | Schema + semantic layer → NL→SQL generator → read-only executor → summariser + (later) charts. |
| **Tool Engine** | Registry of available tools → auth & scope management → request building → response parsing. |
| **Web Scraper** | URL / sitemap crawler → HTML to clean text → feeds into RAG → scheduler for refresh. |

### Observability

Every conversation turn — one user message and the bot's reply — produces a structured log entry: a unique trace ID, the bot involved, latency, tokens used, which tools fired, whether a clarification was triggered. This is what powers dashboards, cost reports, and incident debugging.

---

## How a conversation flows

A simple example. A telecom customer asks the Telecom Support bot: *"What plan am I on right now?"*

1. **Channel → Gateway → Router.** The message hits the gateway, gets authenticated, and is routed to the Telecom Support bot.
2. **Conversation Manager** loads any earlier messages in this session and the user's identity (say `CUST001`).
3. **Orchestrator** builds the prompt: the Telecom persona, the rules for each enabled skill, the conversation history, the new question, and a line telling the model the authenticated customer ID so it doesn't have to ask.
4. **The model decides** the right move is to call the `get_current_plan` tool with `customer_id="CUST001"`.
5. **The Tool Call skill picks up the call** and hands it to the Tool Engine.
6. **Tool Engine**: looks up the tool in its registry, applies the bot's auth/scope rules, makes the call to the underlying internal service (the Telecom API in this case) over the platform's tool protocol, and parses the response.
7. **The result comes back to the orchestrator** — the customer's current plan, billing date, monthly cost. The model writes a natural reply (*"You're on the PRO_599 plan, ₹599/month, renewing on the 15th."*) and the turn finishes.
8. **Observability**: a log line per stage, all tied together by the same trace ID — `[chat]` for the gateway, `[orch]` for the orchestrator, `[mcp]` for the tool call, `[tool_engine]` for the request/response.

If the user had instead said something ambiguous — *"Change my plan"* — without naming which plan, the **clarification mechanism** kicks in: the bot pauses the conversation, asks the user a follow-up question (*"Which plan would you like to switch to?"*) with suggested replies, and resumes from exactly where it left off when the user replies. This works the same way for every bot on the platform, regardless of which skills it has enabled.

For mutating actions like *"Change my plan to PRO_599"* the platform's **two-step confirm** pattern fires automatically: the bot first calls the tool with `confirm=false`, shows the user a preview (proration cost, billing date impact), waits for explicit confirmation, then calls again with `confirm=true` to commit.

---

## How a new bot is created

The platform is designed for bots to be a config-and-content exercise rather than a code project.

A team that wants a new bot does roughly this:

1. **Pick the bot's purpose** — customer support, internal analytics, FAQ deflection, transactional self-service.
2. **Decide which skills it needs** — for a documentation bot that's RAG; for an analytics bot that's TAG; for a "do things" bot that's Tool Call.
3. **Write the YAML config**: the bot's name, its system prompt (its personality + rules), the skills to enable, and per-skill configuration.
4. **Provide the content or service the skills need**:
   - For RAG: the document set.
   - For TAG: a semantic layer describing the warehouse tables.
   - For Tool Call: a service that exposes the actions over the platform's tool protocol.
5. **Smoke test** in staging, tune the persona, roll out.

The platform code does not change. The other bots are unaffected.

---

## The four bot archetypes

Today's two bots are examples of two of these four patterns. The same engine supports the other two with the corresponding skills.

| Pattern | What it looks like | Skill | Examples |
|---|---|---|---|
| **Support / Q&A bot** | "How do I…", "What does my product cover…" — answers from a document corpus that changes over time. | RAG | Help-desk deflection, product onboarding, internal policy lookup. |
| **BI / analytics bot** | "Show me revenue by segment last month" — answers from the data warehouse. | TAG | Marketing, finance, ops self-service. |
| **Website bot** | A chat widget on a public site that knows the latest pricing, FAQs, and product pages. | RAG + Web Scrape | Pre-sales, lead capture. |
| **Transactional bot** | "Change my plan", "Reset my PIN" — actions performed against real internal services. | Tool Call | Customer self-service. |

These aren't rigid categories — a bot can combine skills (a transactional bot that also does Q&A from documentation, for instance). The platform supports the combinations; the bot's config decides.

---

## Safety, cost, and trust

Because the platform sits between users and powerful models / data / APIs, three things are non-negotiable and live in the engine itself — never delegated to the bot's author.

**Safety.** PII patterns are scrubbed from logs. User-supplied content and tool results are clearly delimited in prompts so the model treats them as data, not instructions. For TAG, SQL generated by the model is parsed into a syntax tree and rejected if it isn't a single read-only `SELECT`; the database connection itself is read-only as a second line of defence.

**Cost control.** Every conversation turn updates a per-customer token count. A daily cap is enforced before each model call — over the cap, the bot politely refuses. Different stages of a bot can use different model deployments (a cheap model for SQL writing, a stronger one for the user-facing answer), set per-bot in config.

**Trust.** Every turn writes one structured audit record: trace ID, tokens used, latency, tools called. Conversation history is retained per tenant policy. Failures are visible in logs with the same trace ID that threads through the entire turn.

---

## Glossary

| Term | Meaning |
|---|---|
| **Bot** | A configured chatbot — a persona plus a set of enabled skills plus their settings. The platform hosts many. |
| **Skill** | A pluggable capability — RAG, TAG, Tool Call, Web Scrape. Bots opt in. |
| **Engine** | The implementation behind a skill. The TAG engine, for example, owns SQL generation, validation, execution, and summarisation. |
| **Session** | One ongoing conversation, identified by a session ID. Memory persists across messages. |
| **Turn** | One user message and the bot's reply (with any tool calls in between). One audit record per turn. |
| **Trace ID** | A unique identifier per turn that threads through every log line for that turn. Lets you reconstruct what happened from logs alone. |
| **Persona** | The system prompt and rules that give a bot its identity and behaviour. |
| **Semantic layer** | A short, human-written description of the warehouse tables and metrics a TAG bot can query. Helps the model write better SQL. |
| **Clarification** | The mechanism a bot uses to pause and ask the user for missing information, then resume the same conversation when the user replies. |
| **Tool** | A function the model can call — get a customer profile, change a plan, query the warehouse. The bot has a list of tools its skills expose. |
