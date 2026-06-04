# Session 3 — RAG: How it Works in This Module

**Goal:** After this session you can (a) draw the ingestion and retrieval flows on a whiteboard, (b) defend every parameter (chunk size, top_k, embedding dims, dedupe strategy), (c) run the live demo and read the logs to see each stage firing.

---

## The two phases — keep them separate in your head

RAG is two pipelines that share a vector store. They run on totally different schedules and call totally different APIs.

```
INGESTION (offline, batch)                      RETRIEVAL (online, per-query)
─────────────────────────                       ─────────────────────────────
source                                          user query
  │ connector.list_documents()                    │
  ▼                                               ▼
documents                                       embedder.embed_query()
  │ dedupe by content_hash                        │
  ▼                                               ▼
chunker.chunk()                                 vector_store.query(top_k,
  │ deterministic chunk_id                          where={tenant_id, ...})
  ▼                                               │
embedder.embed_documents()                        ▼
  │ batch 64                                    SearchResults
  ▼                                               │
vector_store.upsert()                             ▼ reranker.rerank()
  │ + chunk metadata                            ranked passages
  ▼                                               │
documents table (SQL)                             ▼
  │ content_hash for next dedupe                MCP tool result → LLM
```

The same `RagEngine` instance owns both — `engine.ingest(...)` puts work on the job queue, `engine.search(...)` runs synchronously. You see this in `src/rag_engine/engine.py:205` and `:233`.

---

## Concepts you must own

### 1. Ingestion is durable async; retrieval is synchronous

**Why:** ingestion is slow (network fetch + embedding + vector store writes) and bursty (operator uploads a 500-page PDF). Retrieval is on the user's hot path. Mixing them would either block the request thread or starve the queue.

Implementation: `POST /ingest` enqueues a job, returns a `job_id`. A background `JobRunner` (single in-process worker) pulls from the `JobQueue`, runs the `IngestionPipeline`, writes status to the `ingestion_jobs` SQL table. `GET /jobs/{id}` polls.

**Interview line:** "Ingestion is a durable job queue with crash recovery — `runner.recover()` on startup re-queues anything in `RUNNING` state because we crashed mid-flight."

### 2. Chunking: structural-then-recursive

**Why two chunkers?** Different document formats have different "semantic boundaries":
- Markdown has `#` headings — `MarkdownHeaderChunker` (in `chunking/structural.py`) splits on those first so a chunk is "the cancellation policy section," not "the last paragraph of intro plus the first half of cancellation."
- Plain text has no structure — `RecursiveCharChunker` (in `chunking/recursive.py`) splits by paragraph → line → sentence → word → char, picking the **coarsest separator** that yields pieces under the size budget.

The structural pass runs first, then any oversized section gets handed to the recursive pass. Default: **800 chars per chunk, 120 chars overlap.** The overlap exists so a question whose answer straddles a chunk boundary is still retrievable.

**The non-obvious detail:** `chunk_id = f"{doc_id}:{ordinal:04d}"` (`chunking/recursive.py:82`). Deterministic. This is what makes re-ingestion idempotent — re-running emits the same chunk IDs, `vstore.upsert` overwrites them in place, no orphans.

### 3. Embeddings: model identity is pinned per-collection

`AzureOpenAIEmbedder` uses `text-embedding-3-small` (1536 dims) by default. The collection's `embedding_model` and `dimensions` are recorded in the `collections` SQL table on creation.

**Why pin per-collection:** vector spaces from different models are not comparable. If someone swaps `text-embedding-3-small` for `-large` (3072 dims), the existing index is silently meaningless — every query returns garbage. By recording the model + dims on the collection, the right answer to "change embedding model" is "create a new collection," and the system *can't* be in an inconsistent state where some chunks use one model and others use another.

**Batching:** ingestion calls `embed_documents([chunk_texts])` and the embedder batches in groups of 64 (`embeddings/azure_openai.py:21`). Smaller than the 2048 hard limit because long chunks would otherwise blow the per-request payload size. Latency stays predictable.

### 4. Vector store: Chroma behind a Protocol

`VectorStore` is a `Protocol` in `vector_store/base.py` — `create_collection`, `query`, `upsert`, `delete_by_filter`, `drop_collection`. The Chroma implementation is ~150 lines. **Swapping to pgvector or Qdrant is a one-file change** — every other module talks only to the Protocol.

Chroma uses **HNSW** for ANN search and returns **L2 (squared euclidean) distance**. The retriever converts that into a "higher = better" similarity score with `score = 1 / (1 + distance)` — exact units don't matter, only the ordering, but humans (and any downstream sort/threshold) expect bigger = more relevant.

### 5. Retrieval: tenant filter is mandatory and cannot be overridden

The single most important line in this module is `retrieval/retriever.py:39`:

```python
where: dict[str, Any] = {"tenant_id": spec.tenant_id}
if filters:
    for k, v in filters.items():
        if k == "tenant_id":
            continue   # caller cannot override
        where[k] = v
```

Combined with the physical collection name (`{tenant_id}__{logical_name}`), tenant isolation is **enforced twice**: the collection physically scoped, and the metadata filter scoped. If an operator misconfigures the collection registry, the metadata filter still keeps tenants separated. **This is the defense-in-depth story** — and there's a unit test (`test_tenancy.py`) that asserts callers can't pass `tenant_id` to escape their tenant.

### 6. Reranker hook, currently NoOp

`Retriever` accepts a `Reranker | None` and defaults to `NoOpReranker`. The seam exists so that adding a cross-encoder reranker (Cohere, BGE-reranker, or an LLM-as-judge) is a one-component swap, not a refactor. Today's POC ships without one because the dataset is tiny and the LLM does the heavy lifting; for scale, a reranker is the cheapest accuracy lift.

### 7. The MCP wrapping — why two tools, not seven

`services/rag_mcp/src/rag_mcp/tools.py` exposes exactly two tools to the LLM:
- `search_knowledge_base(query, collection, top_k=5, filters?)` — the workhorse
- `list_collections()` — discovery for multi-collection bots

Why so few: every tool schema spends prompt tokens on every turn. Ingestion, jobs, collection CRUD all live on the **REST control plane** (`rag_api`) which only operators and the scheduler hit. The LLM should never see those tools — there's no scenario where you want an LLM enqueueing ingestion jobs.

The returned tool payload includes a `formatted` field — passages rendered as `[1] (source_uri) <chunk>\n\n[2] ...` — so the model can cite by index without parsing JSON. The system prompt instructs the model to use those `[N]` markers.

---

## Code map (read in this order)

1. `src/rag_engine/engine.py` (200 lines, 10 min) — the facade. Look at `__init__`, `ingest`, `search`. Don't dive into the repos.
2. `src/rag_engine/models.py` (skim) — `Document`, `Chunk`, `CollectionSpec`, `SearchResult`, the `content_hash` function.
3. `src/rag_engine/tenancy/resolver.py` (26 lines) — the *entire* tenant naming convention. Tiny, deliberate.
4. `src/rag_engine/chunking/recursive.py` (98 lines, 8 min) — read the `_split` function carefully. The separator-selection logic is the interesting bit.
5. `src/rag_engine/chunking/structural.py` (skim) — markdown header splitter.
6. `src/rag_engine/embeddings/azure_openai.py` (52 lines, 3 min) — note the batching + the default dims.
7. `src/rag_engine/ingestion/pipeline.py` (full, 10 min) — the heart of ingestion. Read the dedupe branches and the "delete then re-upsert" logic.
8. `src/rag_engine/ingestion/dedupe.py` (38 lines) — `content_hash` + the three-state decision (new / changed / unchanged).
9. `src/rag_engine/retrieval/retriever.py` (69 lines, 5 min) — short, read every line. Note the immutable tenant filter.
10. `src/rag_engine/jobs/runner.py` — skim. Focus on `recover()` (the crash-recovery story).
11. `services/rag_api/src/rag_api/app.py` (lifespan) — how it all gets wired at startup, including declarative collection bootstrap from YAML.
12. `services/rag_mcp/src/rag_mcp/tools.py` (62 lines) — the data-plane MCP surface.
13. `src/chatbot/skills/rag_skill.py` (68 lines) — how the chatbot consumes it.

---

## The retrieval request flow (whiteboard this)

When the user asks the telecom bot *"What's the refund window for prepaid cancellation?"*:

1. **chatbot:8000** receives the message, hands to `LLMOrchestrator.run_turn`.
2. Orchestrator calls Azure OpenAI with the conversation + the union of all skills' tool schemas (telecom tools + `search_knowledge_base` + `list_collections` + `ask_clarification`).
3. Model emits `tool_calls=[{name: search_knowledge_base, args: {query: "prepaid cancellation refund window"}}]`.
4. Orchestrator asks each skill `owns_tool("search_knowledge_base")` → `RagSkill` says yes.
5. `RagSkill.execute_tool` injects defaults (`collection=telecom_policies`, `top_k=5`) and calls the MCP client.
6. MCPClient → **rag_mcp:8766** over streamable HTTP → tool body in `tools.py:32` runs.
7. `rag_client.search(...)` → HTTP POST to **rag_api:8002** `/search` with `X-Tenant-Id: telecom_demo`.
8. `rag_api` route → `RagEngine.search(query, collection, tenant_id, top_k, filters)`.
9. `Retriever.search`:
   - `embedder.embed_query("prepaid cancellation refund window")` → 1536-dim vector (one Azure call)
   - Build `where={"tenant_id": "telecom_demo"}` (immutable)
   - `vstore.query(collection="telecom_demo__telecom_policies", query_embedding, top_k=5, where)` → 5 chunks with L2 distances
   - Convert distances to similarity scores
   - `reranker.rerank(...)` → NoOp pass-through today
10. Response bubbles back: `rag_api` → `rag_mcp` (which formats `[N] (source_uri) text`) → `RagSkill` → orchestrator records `tool` message in history.
11. Orchestrator calls Azure OpenAI again with the new history. Model generates the answer citing `[1]`, `[2]`.
12. Response returned to the user.

**Latency budget (POC):** ~300-600ms RAG round-trip — most of it is the two Azure round-trips (embedding + LLM). The Chroma kNN itself on 5 docs of policies is ~5ms.

---

## The ingestion flow

When you run `make rag-bootstrap`:

1. `POST /collections` with `{name: "telecom_policies", embedding_model, dimensions}`.
   - Validates tenant_id + name through `physical_collection_name()` regex
   - `vstore.create_collection("telecom_demo__telecom_policies", dims=1536, metadata)` — creates the Chroma collection
   - Upserts a row in the `collections` SQL table
2. `POST /ingest` with `{collection, source: "file_path", source_config: {path, glob}}`.
   - Validates the collection exists for this tenant
   - Validates the connector name is registered (`file_path`, `notion`, `confluence`)
   - Creates an `ingestion_jobs` row (status=PENDING)
   - Puts `job_id` on the `JobQueue` (asyncio queue)
   - Returns `job_id` immediately
3. **JobRunner worker** (started in lifespan) picks up the job:
   - Marks status=RUNNING
   - Resolves the collection → `CollectionSpec`
   - Builds the connector: `FileLoaderConnector(path, glob)` → `SourceConnector` Protocol
   - Calls `IngestionPipeline.run(connector, spec)`:
     - For each document the connector yields:
       - `dedupe.decide(session, doc)` — new / changed / unchanged
       - If unchanged → `counts.skipped += 1`, continue
       - `chunker.chunk(doc)` → list of `Chunk` with deterministic IDs
       - `embedder.embed_documents([chunk.text for chunk in chunks])` — batches of 64
       - If changed → `vstore.delete_by_filter(physical, where={"doc_id": doc_id})` first
       - `vstore.upsert(physical, [UpsertItem(...)])` — atomic per-batch in Chroma
       - `docs_repo.upsert(doc_id, content_hash, chunk_count, ...)` — update dedupe row
   - Writes final `IngestionCounts` to the job row, marks SUCCEEDED.

**Crash safety:** if the worker dies between `vstore.upsert` and `docs_repo.upsert`, on restart `dedupe.decide` says "changed" (the recorded hash is stale) and we re-run, re-upserting with the **same** deterministic chunk IDs. No orphans, no double-counts. If it dies during `vstore.upsert` itself, deterministic IDs make the retry overwrite-in-place.

---

## Whiteboard exercise

Draw both flows from memory. Then for each, label:

1. Which storage tier touches each step (Chroma vs SQLite vs Azure)
2. Where tenant isolation gets enforced (two places in retrieval, two places in ingest)
3. Where the crash-recovery boundary is
4. Where the system would fall over at 100k documents

Time it: under 3 minutes total. If you need notes, do it again the next day.

---

## Anticipated Q&A

### Q1: "Walk me through what happens when a user asks the bot a policy question."

Use the 12-step flow above. Compress to 60 seconds — they want the *shape*, not every line. Key beats: orchestrator dispatches to skill by tool name, skill calls MCP, MCP calls REST, REST embeds the query, vector store does kNN, results come back formatted with source URIs the model cites.

### Q2: "Why 800-char chunks with 120-char overlap?"

**Strong answer:** It's a compromise between three pressures. Smaller chunks mean tighter relevance per hit but the model loses context — a chunk that says "this is the exception" without the rule above is useless. Larger chunks mean more context per hit but each embedding represents a less-focused topic, hurting retrieval precision. 800 chars (~150 words) is roughly one focused paragraph for the policy docs we have, which is the right granularity for question-answer fits. The 120-char overlap (~15% of chunk size) catches answers that straddle boundaries — a refund-window sentence that gets cut by the chunker is duplicated in the adjacent chunk so at least one of them retrieves cleanly. Both numbers are starting points; in production I'd tune them against an eval set of real questions.

**Follow-up they'll ask:** *"How would you actually pick those numbers for a new corpus?"* — Run an eval set of golden Q&A pairs at multiple (chunk_size, overlap) configurations and measure retrieval recall@k. Production teams typically plot the trade-off curve and pick the elbow. We don't have that harness yet — Session 7.

### Q3: "Why text-embedding-3-small? Why not -large or BGE?"

**Strong answer:** Three reasons. Latency: -small is ~3x faster than -large, and embedding latency hits the user on every query, not just ingestion. Cost: roughly 6x cheaper per token. Quality: for in-domain English policy text, the recall gap to -large is single-digit percent on most benchmarks — fine for a POC. We pinned the model on the collection so when the gap becomes the bottleneck we can launch a parallel collection with -large, A/B test, and switch atomically. BGE and other open models are a real option once the deployment is on infra you control — they often outperform OpenAI on domain-specific corpora when fine-tuned, but for a tenant we don't own data for, hosted is the right starting point.

### Q4: "How do you guarantee a tenant can't see another tenant's data?"

**Strong answer:** Two enforcement layers. Physical: every collection is named `{tenant_id}__{logical_name}` via the resolver, and that's the only name the engine ever passes to Chroma. A caller asking for `telecom_policies` for tenant `acme` *cannot* land on `telco_demo__telecom_policies` — the lookup path is `(tenant_id, logical_name) → spec → physical_name`, deterministic. Metadata: every chunk carries `tenant_id` in its metadata, and the retriever appends `where={"tenant_id": tenant_id}` to every query, with caller-supplied filters explicitly forbidden from overriding it. We have a unit test that asserts a caller passing `filters={"tenant_id": "evil"}` still gets their own tenant scope.

It's belt-and-suspenders on purpose: a config-management bug that points two tenants at the same physical collection still doesn't leak, because the metadata filter would still partition.

**Follow-up:** *"What if a row gets written with the wrong tenant_id in metadata?"* — That's the gap the physical name closes: a misconfigured ingestion can't write to another tenant's collection in the first place because the physical name is computed from the resolved spec, not from caller input.

### Q5: "What's the dedupe story? What happens when the same document is ingested twice?"

**Strong answer:** Two artifacts. First, the `documents` SQL table records `(doc_id, content_hash)` for every ingested doc. On the next ingest, we look up the existing row: if absent → new, embed and upsert; if present and hash matches → unchanged, skip entirely (no embedding API call, no vector store write); if hash differs → changed, `delete_by_filter(doc_id)` then re-upsert with new chunks. Second, chunk IDs are deterministic — `{doc_id}:{ordinal:04d}` — so a crash mid-upsert resolves cleanly: retrying overwrites the same IDs, no orphans, no duplicates.

The "delete before re-upsert on change" matters because a changed document might produce *fewer* chunks than before (e.g., a section was deleted). Without the explicit delete, stale chunks would still match queries.

### Q6: "Why a separate REST control plane and MCP data plane? Sounds like two APIs for one thing."

**Strong answer:** Different audiences and different security postures. REST is for operators, schedulers, and admin tools — it owns ingestion, jobs, collection CRUD, debug search. None of that should be visible to an LLM: there's no scenario where you want the model to enqueue an ingestion job or drop a collection. MCP is for the LLM, exposing exactly the two tools it needs: `search_knowledge_base` and `list_collections`. Every additional tool on the MCP surface spends prompt tokens on every turn, so the smaller it is, the better. Keeping them separate lets each evolve on its own schedule and authorize differently — REST gets admin auth, MCP gets bot-tenant auth.

### Q7: "What's the role of the `reranker` in your retriever?"

**Strong answer:** Right now it's a `NoOpReranker` — a passthrough. The seam exists because vector search alone is a coarse signal: cosine similarity in embedding space correlates with semantic relevance but isn't equal to it. For a known weakness — vector recall@5 of 80% might only be precision@5 of 50% — a cross-encoder reranker takes the top 20-50 hits from vector search and re-scores them against the query with a model that *jointly* encodes (query, passage). That trades latency for precision. The reason I haven't shipped one is the corpus is too small to need it; with 50 policy docs the LLM tolerates noisy retrieval. At 50,000 docs across many tenants, a reranker is the cheapest accuracy lift and the architecture is ready for it.

### Q8: "What if Chroma goes down?"

**Strong answer:** Two failure modes. During retrieval: `vstore.query` raises, the `Retriever.search` exception propagates up to the REST handler which 5xx's, the MCP tool returns `is_error=True` in `ToolResult`, the LLM sees the error and recovers via reasoning — usually apologizes and asks for the question to be rephrased or escalates. During ingestion: `vstore.upsert` raises inside the pipeline, `IngestionCounts.errors` increments, the doc is logged and skipped, the job continues with the next doc, the final job status is `SUCCEEDED` with `errors > 0`. The non-failure of the job there is deliberate — partial progress is better than rolling back an entire 500-doc upload because one chunk failed. Per-document errors surface in the job's error_messages for operator triage.

For production: I'd add a circuit breaker on `vstore.query` so during a Chroma outage the bot fails fast instead of timing out per request, and the chat layer falls back to "I can't search the knowledge base right now" instead of a generic 5xx.

### Q9: "Scale this for me. 10k tenants, 100 docs each, ~1000 QPS."

**Strong answer:** Walk through the bottlenecks in order.

- **Embeddings on the hot path** — every query is one embedding call. At 1000 QPS that's 1000 Azure embedding calls/sec, which busts the default Azure rate limit. Mitigations: (a) deploy your own embedding model behind a load balancer (BGE-small can do 5k+ QPS on a single GPU), (b) cache query embeddings — semantic-cache the top X% of repeated queries; (c) batch concurrent queries at the gateway.
- **Vector store fanout** — Chroma at 10k collections is going to struggle. Migration path: pgvector with a `(tenant_id, collection)` partition column and an HNSW index per partition. The `VectorStore` Protocol is the swap point — one new implementation file.
- **In-process job runner** — caps ingestion at one worker per `rag_api` instance. Lift to Redis-backed queue + N worker processes once you cross the throughput a single worker can handle.
- **One MCP server per tenant** — current model burns a process per tenant. At 10k tenants you'd centralize MCP and pass tenant via a signed claim in the tool call (the chatbot already knows its tenant_id), not via per-process env vars.
- **What I'd *not* worry about first:** the chatbot's tool-use loop, which is mostly Azure-bound; the SQLite metadata store, which is replaceable with Postgres in one file change. These are red-herring bottlenecks at this scale.

---

## What I'd improve (have these ready)

- **Hybrid retrieval** (BM25 + vector with rank fusion). Vector search misses exact-match keyword queries (model numbers, error codes). A BM25 pass merged via Reciprocal Rank Fusion typically lifts recall 10-20% on technical corpora and is cheap.
- **A real cross-encoder reranker** — Cohere Rerank or a self-hosted BGE-reranker. Top-50 retrieval → rerank to top-5 is the highest leverage accuracy improvement.
- **Eval harness** — golden Q&A pairs per collection, run weekly, track recall@k, precision@k, MRR. Plug into CI gates so retrieval regressions block deploys.
- **Query rewriting** — for multi-turn conversations the question is often "and what about that for postpaid?" — a query rewriter expands the user's literal question into a standalone query before embedding. Cheap (one LLM call) and meaningfully improves recall on conversational corpora.
- **Citation handling in the orchestrator** — today the model emits `[1]`, `[2]` strings but the platform doesn't structure them. A small post-processor could map `[N]` → `source_uri` so the UI can render clickable citations.
- **Chunk-level metadata for filtering** — section titles, doc dates, doc tags. The framework supports it via `metadata={...}` on each chunk; we just don't populate it richly today.

---

## Drill before Session 4 (TAG)

1. Out loud, walk through both flows (ingestion + retrieval) in 90 seconds each. Time it.
2. Answer Q4 (tenant isolation) and Q5 (dedupe) without looking — these are the strongest interview signals on this session.
3. Pick one "What I'd improve" item and explain *why that one first*. The ordering is the senior signal.
4. Run the live demo (next section). Read the rag_api logs while you do — see each stage fire.
