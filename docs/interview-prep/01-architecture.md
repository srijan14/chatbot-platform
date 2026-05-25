# Session 1 — Architecture & the Skill Plugin Pattern

**Goal:** After this session you can (a) draw the system on a whiteboard in 90 seconds, (b) explain why every box exists without using the word "microservices," and (c) defend the skill plugin pattern against the "why not just call the function directly?" challenge.

---

## Concepts you must own

### 1. The five processes

```
[Web/REST channel] → chatbot (:8000) → [skills]
                          │
                          ├── ToolCallSkill ──► mcp_telecom (:8765) ──► telecom_api (:8001) ──► SQLite
                          └── RagSkill       ──► rag_mcp     (:8766) ──► rag_api      (:8002) ──► Chroma + SQLite
```

Two pairs, same shape. Each pair = **control/data plane separation**: a backing REST service that owns business logic and persistence, fronted by an MCP server that turns those capabilities into LLM-callable tools. The chatbot only ever talks MCP.

Why the same shape twice: because the MCP-as-tool-surface pattern proved itself with the telecom slice, and RAG slotted in identically. **That symmetry is the architectural win** — adding the 4th capability (TAG/SQL, Web Scrape) will follow the same template.

### 2. The Skill plugin interface

`src/chatbot/skills/base.py:66` — the entire skill contract:

```python
class Skill(ABC):
    name: str
    async def prepare_tools(self) -> list[dict]: ...   # OpenAI-shape tool schemas
    async def execute_tool(self, name, arguments) -> ToolResult: ...
    def owns_tool(self, name) -> bool: ...
    def system_prompt_addition(self) -> str | None: ...  # optional
```

Four methods. That's it. The `LLMOrchestrator` is skill-agnostic — it loops over `enabled_skills`, unions their tools, and dispatches by ownership. **No skill can break another skill.** No core change to add a skill.

`ToolResult` (same file, line 35) is also generic:
- `text` goes back into LLM history (so the model sees the result)
- `signal` (optional) escapes the loop and surfaces in the response — that's how `clarification` pauses the conversation
- `terminal` (optional) tells the orchestrator to stop iterating
- `user_visible_text` overrides the assistant content (for skills like clarification where the model's content was empty)

This shape is why **clarification, tool_call, and rag are on equal footing.** The platform doesn't bake in "tool use loop" as a special case — it bakes in "skills can emit signals."

### 3. Why MCP and not direct function calling

The temptation: just write a Python function, decorate it as a tool, done.

Why MCP instead:

1. **Process boundary = independent scale.** The MCP server can be rate-limited, restarted, deployed separately. The chatbot doesn't crash when a tool integration breaks.
2. **Multi-consumer.** Same MCP server serves Claude Desktop, other internal bots, and the chatbot. No code copy.
3. **Protocol uniformity.** RAG and tools and (future) SQL all expose the *same* surface — `list_tools` + `call_tool`. The chatbot has *one* client implementation. Compare to N adapters per integration.
4. **Schema discovery.** MCP servers describe themselves. Adding a tool in `mcp_telecom/tools.py` makes it visible to every client without touching the chatbot.

**Interviewer pushback:** "MCP adds latency you don't need for localhost." Correct. The tradeoff: ~5ms of HTTP for a uniform integration model. We're explicit in the code (`mcp_client.py:1` docstring) that we open a fresh session per call — a known shortcut acceptable on localhost; production swaps it for a pooled session.

### 4. Configuration as the only per-bot variance

`configs/bots/telecom_support.yaml` is the entire definition of a bot. The router (`src/chatbot/router/bot_router.py`) reads it and assembles skills. Three skills enabled (`tool_call`, `clarification`, `rag`), three blocks of config (`tool_call:`, `clarification:`, `rag:`), no code changes per bot.

**Add a second bot:** copy the YAML, change `bot_id`, `persona.system_prompt`, and pick a different skill mix. That's the deliverable.

---

## Code map (read in this order)

1. `architecture.md` — top-of-file context section (5 min). Read the original POC framing first, *then* the RAG sub-platform section at the bottom.
2. `src/chatbot/skills/base.py` (90 lines, 5 min) — internalize the four-method contract and the `ToolResult` fields.
3. `src/chatbot/router/bot_router.py` (full file, 5 min) — see how skills get wired. Note the special case for clarification (always wired) vs. config-gated (tool_call, rag).
4. `src/chatbot/core/llm_orchestrator.py` — read only the public `run_turn()` method (~lines 200-350). You don't need the full implementation; you need the *shape*: one LLM call per iteration, tool dispatch by ownership, accumulate, repeat until no tool_calls or terminal signal or iteration cap.
5. `src/chatbot/skills/tool_call_skill.py` (34 lines, 2 min) — the canonical "thin" skill. It's mostly a translator + dispatcher.
6. `src/chatbot/skills/rag_skill.py` (60 lines, 3 min) — same shape, different MCP endpoint + default-injection logic.
7. `configs/bots/telecom_support.yaml` — the *only* per-bot artifact.

---

## Whiteboard exercise

**90 seconds. From memory. No looking.**

Draw:
- Five process boxes (telecom_api, mcp_telecom, chatbot, rag_api, rag_mcp) with ports
- Arrows showing every direction of communication
- Inside the chatbot box: three skill plugins
- One arrow from chatbot to Azure OpenAI

Then label, in 30 sec of speaking:
- The control plane / data plane split (twice — once for tools, once for RAG)
- The plugin boundary inside the chatbot
- Where tenant isolation gets enforced (rag_api's `X-Tenant-Id` header + retriever's metadata filter)

Practice this until you can do it in one continuous take without stopping to think.

---

## Pitch — the 60-second version (memorize)

See `00-curriculum.md`. Then practice tailoring the closing sentence to the interviewer: a system-design interviewer wants to hear "non-obvious design calls"; an applied ML interviewer wants to hear "I'd add an eval harness next."

---

## Anticipated Q&A

### Q1: "Why a separate MCP server for telecom? Why not call the API directly from the chatbot?"

**Strong answer:** Two reasons. First, uniformity — once we knew we'd have multiple tool-bearing integrations (telecom, RAG, eventually SQL and Web Scrape), the chatbot needed *one* tool client, not N. MCP gives that for free: same `list_tools` / `call_tool` surface for any integration. Second, decoupling — the MCP server is independently restartable, scalable, and reusable. Claude Desktop can connect to the same telecom MCP without me changing anything. We paid roughly 5ms of localhost HTTP for that, and I noted in the code that it's deliberate.

**Follow-up they'll ask:** "But the RAG case calls REST under the MCP anyway, so you have two hops." Right — that's a deliberate split between *control plane* (REST: ingestion, jobs, collection management — admin concerns no LLM should touch) and *data plane* (MCP: just `search_knowledge_base`, the only thing the LLM needs). The MCP server is intentionally thin so we can swap the storage backend without changing the tool surface.

### Q2: "What is a 'skill' here, really? Why not just register tools directly?"

**Strong answer:** A skill is a tool-producing unit that owns three things: (1) which tool schemas to expose, (2) how to execute them, (3) what to contribute to the system prompt. The last one is the key insight — `clarification` and `rag` both teach the model *when* to use their tools via `system_prompt_addition()`. If we just registered tools globally, every bot would need to repeat that prompt boilerplate. The skill abstraction puts the model-side instructions next to the tool implementation.

The other reason: `ToolResult` carries optional `signal` and `terminal` fields. A "tool" can pause the conversation (clarification: "I need more info") or hand off to a human. That's not a tool; it's a skill — a richer contract that includes "what should the conversation do next."

### Q3: "How does the orchestrator decide when to stop the tool-use loop?"

**Strong answer:** Three termination conditions, in priority order:
1. The model returns `finish_reason != "tool_calls"` — natural turn end, it's ready to reply.
2. Any tool returns `ToolResult.terminal=True` — clarification/handoff fired; pause until user replies.
3. `max_tool_iterations` (default 6) — hard cap to prevent infinite loops on a stuck model.

The cap is the safety net but should be rare; if you hit it, your prompting or tool set is wrong. We log when it fires.

### Q4: "Two skills want a tool with the same name. What happens?"

**Strong answer:** Today, undefined — last-skill-wins on dispatch, both schemas get sent to the model and confuse it. I'd address this in two ways for production: (1) namespace tools by skill at registration (`rag.search`, `tool_call.get_customer_profile`), (2) reject duplicate registrations at bot-startup time so it's a fail-fast misconfiguration. I left this as POC scope because the current skill set doesn't collide and the fix is cheap when it's needed.

### Q5: "Why YAML for bot config? Why not a database?"

**Strong answer:** Bot configs are version-controlled artifacts — they encode prompts, tool allowlists, security guardrails. Putting them in a database means changes don't go through code review and you can't bisect a bad prompt change. YAML in the repo means each bot definition has a commit, a reviewer, and a CI gate. For tenant-specific overrides on top of a base config, a config service is appropriate; the base behavior of a bot belongs in git.

### Q6: "You have Azure OpenAI for chat and embeddings, MCP for tools. What happens when Azure rate-limits you?"

**Strong answer:** Two surfaces. The chat call: `AsyncAzureOpenAI` raises `RateLimitError`, the chatbot route currently surfaces it as a 5xx. Production: per-tenant token bucket + exponential backoff with jitter. The embedding call inside ingestion: we batch in 64s and the job marks itself FAILED on persistent embed failures — the retry path is "retry the job" because each chunk has a deterministic id, so re-running is safe. The cost path is also worth flagging: we'd want a per-tenant cost ceiling and a 429 once exceeded.

### Q7: "Where does this fall down at 1000 tenants?"

**Strong answer:** Three bottlenecks I'd hit in order: (1) Chroma's collection-per-tenant works fine to ~hundreds; at 1000s I'd move to pgvector with a `tenant_id` partition column. (2) The in-process job queue caps at one worker per `rag_api` process; I'd lift it to Redis + N workers. (3) The MCP one-tenant-per-process model means 1000 `rag_mcp` processes — at that scale you'd centralize and pass tenant via tool argument with a signed JWT, not a per-process header. None of these is a rewrite — each one is a single-component swap behind a Protocol that's already in place.

### Q8: "What's the most fragile part of this design?"

**Strong answer:** The tool schema bridge — `mcp_to_openai()` in `src/chatbot/engines/tool_engine/tool_translator.py`. Both sides use JSON Schema but the envelope and field names differ. We wrote a small unit test specifically because it's the highest-leverage place for subtle bugs that *don't crash* — they just make the model behave weirdly. If I were doing this again I'd generate the translator from an OpenAPI-style schema definition rather than hand-rolling it, so it's not a place anyone can edit by accident.

---

## What I'd improve (have these ready)

- **Tool namespacing across skills** (Q4) — prevent silent collisions
- **Streaming responses** — currently we wait for the whole tool loop to finish before any token reaches the user. For multi-step turns that's seconds of staring at a spinner. Real fix: streaming with progressive tool-status updates in the UI.
- **The chatbot's MCP client opens a fresh session per call** — fine on localhost, a real latency tax in prod. The `MCPClient.__init__` docstring already flags it. Fix: a long-lived session on `app.state`.
- **No retry policy on tool failures** — if a tool errors transiently, the model has to recover via reasoning rather than the platform retrying. A skill-level retry policy (`retry: {max: 2, backoff: 0.5}`) would harden this without changing the orchestrator.
- **Observability is JSON logs, not OTel** — fine for POC; for prod, the `TurnLog` schema we built maps cleanly to OTel spans and that's the migration I'd do first because cost-per-turn becomes the operating question.

---

## Drill (do this before Session 2)

1. Close this file. Out loud, deliver the 60-second pitch from memory. Time it.
2. Whiteboard the architecture, then explain the plugin contract in 30 seconds. Time it.
3. Answer Q3 and Q7 out loud. If you stumble, re-read the answer, sleep on it, try tomorrow.
4. Pick one "What I'd improve" item and explain *why* you'd do that one first. (The "why" is the senior signal — anyone can list improvements.)

When all four feel automatic, you're ready for Session 2.
