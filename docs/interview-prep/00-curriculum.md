# Interview Prep — Curriculum

**Target:** Senior (6+ YoE) Applied ML / LLM Engineer
**Positioning:** This is my project. Design decisions are mine; I drove the architecture end-to-end.
**Rounds in scope:** System design • AI/LLM-specific • Data science generalist

---

## How to use this folder

Eight sessions, each ~45-60 minutes. Each session has its own file and follows the same shape:

1. **Goal** — what you'll be able to do at the end
2. **Concepts** — the underlying knowledge an interviewer expects you to have
3. **Code map** — exact files and line numbers to read, in order
4. **Whiteboard exercise** — draw it from memory; that's the real test
5. **Pitch lines** — 60-second and 3-minute versions to rehearse aloud
6. **Anticipated Q&A** — 8-10 questions with strong answers + the next-level follow-up most interviewers ask
7. **What I'd improve** — credible self-critique. Senior interviewers want this; "everything's great" is a tell

**Drill rule:** after reading a session, close the doc and answer the Q&A out loud, no notes. If you can't, re-read and try again the next day. The goal is fluency, not familiarity.

---

## The sessions

| # | Topic | Why it matters in interviews |
|---|---|---|
| 1 | Architecture & the skill plugin pattern | First 60 seconds of any "tell me about a project" round. Sets up everything else. |
| 2 | Agentic tool-use loop | "How do you build agents?" — you have a working answer. Cover errors, parallelism, termination. |
| 3 | RAG fundamentals | Universally asked: chunking, embeddings, vector stores, distance metrics. Don't fumble vocabulary. |
| 4 | TAG / NL-to-SQL | The most senior-LLM-engineering topic on the platform: schema RAG, two-model architecture, SQL AST safety, repair loops. |
| 5 | Tenant isolation & multi-tenant data | Security-adjacent question every senior ML eng round touches. Defense-in-depth story is gold. |
| 6 | Ingestion pipeline & async patterns | Backend judgment: idempotency, job durability, crash recovery. Shows production maturity. |
| 7 | Retrieval quality & evals | The Applied ML differentiator. "How do you know it works?" — most candidates wave hands; you won't. |
| 8 | System design tradeoffs | The actual system-design round. Practice the alternatives-considered framing. |
| 9 | Production-readiness gap | "What's POC and what's prod?" — shows you can think beyond the demo. |

---

## The 60-second pitch (memorize this verbatim)

> I built a multi-tenant chatbot platform where each bot composes "skills" — API tool calling, knowledge-base retrieval, natural-language SQL, structured clarification — behind a unified plugin interface. The first vertical is a telecom support bot: it answers operational questions by calling backend APIs through MCP, policy questions by retrieving from a per-tenant vector store, and business questions by translating natural language into SQL against a warehouse.
>
> The architecture is a control plane / data plane split per capability. The chatbot's LLM orchestrator doesn't know whether a tool is a SQL call, a REST call, or a vector search — it just sees OpenAI-shape tool schemas. That's what made each new skill additive instead of invasive.
>
> A few non-obvious calls: tenant isolation is enforced twice — physical collection name plus a metadata filter the retriever always appends, so a caller-supplied filter can't override it. For the SQL skill, validation runs on the AST not a regex, so `INSERT … SELECT` and mutations hidden inside CTEs get caught — and execution opens the warehouse with a `mode=ro` file URI as defense in depth. Ingestion is durable async jobs with content-hash dedupe and deterministic chunk ids, so re-ingestion is idempotent across crashes. And every seam — vector store, embedder, chunker, reranker, source connector, the SQL gen LLM, the summarizer LLM — sits behind a Protocol or dataclass so swaps don't ripple.

**Time it.** Should land 55-65 seconds at a normal speaking pace. Cut a sentence if it runs over.

---

## The 3-minute deep version

Same opener (the multi-tenant chatbot + four-skills framing). Then walk through three pillars — pick the two most relevant to the interviewer's focus:

1. **The skill plugin contract** (~40 sec) — every skill implements `prepare_tools()` + `execute_tool()` + `owns_tool()` and can contribute to the system prompt. The orchestrator iterates skills, takes the union of their tools, and dispatches each LLM-emitted tool_call to the owning skill. Adding a skill is: implement the interface, register in the bot router, add a config block. The four skills today (clarification, tool_call, rag, tag) ride exactly this contract — no special-cases in the core engine.

2. **Pick one of these two depending on the interviewer:**
   - **RAG sub-platform** (~75 sec) — three processes: REST control plane (collections, ingestion jobs, scheduler), MCP data plane (just `search_knowledge_base` + `list_collections` as tools), engine library. Tenant isolation enforced two ways, dedupe via content hash, deterministic chunk ids, pluggable Protocols at every seam.
   - **TAG (NL→SQL) skill** (~75 sec) — semantic-layer YAML declares tables + metrics + dimensions; LlamaIndex ObjectIndex does schema RAG (embed table descriptions, retrieve top-K relevant tables per question so the prompt doesn't bloat); SQL gen LLM emits a SELECT; sqlglot AST validation rejects mutations / multi-statement / PRAGMA; read-only sqlite file URI as defense in depth; repair loop up to 3 attempts on validator/exec errors; dedicated summarizer LLM turns rows into analyst prose. Two LLM deployments for cost (precise low-temp for SQL, friendlier for summary).

3. **What I'd take to production** (~60 sec) — pick 2-3: hybrid BM25+vector retrieval with a real cross-encoder reranker, eval harness with golden Q&A + faithfulness checks, Redis-backed job queue, structured citation parsing for clickable sources, an eval suite for TAG that runs question→SQL→exec golden pairs in CI. Pick what fits the role.

---

## Universal "tell-me-about" anti-patterns to avoid

- **Don't lead with tech stack.** "I used FastAPI and Chroma" is what juniors say. Lead with the problem and the design call.
- **Don't list features.** Tell the story of one hard decision: "The interesting problem was..."
- **Don't say "we"** when it was you. And don't say "I" when it was a team — that catches up to you.
- **Don't be afraid of "I haven't shipped this to production."** Frame it as a deliberate POC scope: "Out of scope on purpose were X, Y, Z — but here's exactly what would change for prod."

---

## Universal Q&A you'll get on any project pitch

| Q | Strong answer pattern |
|---|---|
| "Why X instead of Y?" | Name Y. Acknowledge what it's good at. Explain the specific reason X fit better *for this problem* — not "X is just better." |
| "What was the hardest part?" | Have one prepared. Mine: tenant isolation at the retrieval layer — the easy mistake (filter-only) silently leaks; the right design (physical name + filter) requires discipline at every site. |
| "What would you do differently?" | Have *three* prepared (see Session 8). Always include one that costs effort, not just "I'd add monitoring." |
| "How would you scale this to N tenants / Y QPS?" | Identify the actual bottleneck first (don't guess). For us: vector store fanout and embedding API rate limits — not the chatbot loop. |
| "What if X service goes down?" | Walk the failure modes one at a time. Be specific: "If `rag_mcp` is down, the skill's `prepare_tools()` fails at bot startup — we currently log-and-continue; the LLM just doesn't see those tools." |
