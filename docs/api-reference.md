# Chatbot Platform — API Reference

> **Note:** examples below reference the removed POC bots (Telecom Support, BI
> Assistant). The HTTP contract is unchanged and bot-agnostic — substitute
> `bot_id: am_marketplace` (the current live bot). `customer_id` is the generic
> end-user identity (any stable per-user string). See [README](../README.md).

## Overview

The Chatbot Platform exposes a single HTTP service that hosts multiple bots
(Telecom Support, BI Assistant) behind one uniform API. You send a user message
for a session; the platform runs the bot's skills (tool calls, knowledge-base
search, NL→SQL, clarification) and returns the reply plus structured metadata.

There are two groups of endpoints:

- **Conversation API** (`/chat*`) — the data plane your chat UI talks to.
- **Document API** (`/bots/{bot_id}/documents*`) — the RAG control plane for
  managing a bot's knowledge base (add / update / list / remove documents).

| Endpoint | Purpose |
|---|---|
| `POST /chat` | Send a user message, get the bot's reply. |
| `GET /chat/history` | Load the visible conversation for a session. |
| `POST /chat/reset` | Clear a session's conversation. |
| `POST` / `PUT /bots/{bot_id}/documents` | Add or update a knowledge-base document. |
| `GET /bots/{bot_id}/documents` | List a bot's knowledge-base documents. |
| `DELETE /bots/{bot_id}/documents/{id}` | Remove a document. |
| `GET /health` | Liveness check. |

---

## Conventions

| | |
|---|---|
| **Base URL** | `http://<host>:8000` (default `http://localhost:8000`) |
| **Content-Type** | `application/json` (requests and responses) |
| **Authentication** | None in this build (POC). `customer_id` is supplied by the caller in cleartext. **In production this must be derived from the caller's auth token, never trusted from the client.** The Document API must additionally sit behind operator/service auth (it mutates a tenant's corpus). |
| **Identity model** | A conversation is identified by `session_id`. The acting user is `customer_id`. The bot is selected by `bot_id`. |

### Available bots (`bot_id`)

| `bot_id` | Description | Skills |
|---|---|---|
| `telecom_support` *(default)* | Telecom customer support: account, billing, plans, SIM, network, plus policy Q&A. | Tool Call, RAG, Clarification |
| `bi_assistant` | Natural-language analytics over the e-commerce warehouse. | TAG (NL→SQL), Clarification |

---

# Conversation API

## `POST /chat`

Send one user message and receive the bot's reply for that turn.

### Request body

| Field | Type | Required | Description |
|---|---|---|---|
| `session_id` | string | yes | Stable ID per chat thread / browser tab. Reuse it across messages to keep context. |
| `customer_id` | string | yes | The user the bot acts as (e.g. `CUST001`). Used for tool calls and per-user budget. |
| `message` | string | yes | The user's message (min length 1). |
| `bot_id` | string | no | Which bot to talk to. Defaults to `telecom_support`. |

```json
{
  "session_id": "sess-abc-123",
  "customer_id": "CUST002",
  "message": "How much data do I have left?",
  "bot_id": "telecom_support"
}
```

### Response body

| Field | Type | Description |
|---|---|---|
| `session_id` | string | Echoes the request. |
| `trace_id` | string | Unique ID for this turn; appears in all server logs for the turn. |
| `text` | string | The bot's reply to show the user. |
| `iterations` | integer | How many model steps the turn took. |
| `capped` | boolean | True if the turn hit the internal tool-iteration cap. |
| `tool_calls` | array | Tools the bot invoked this turn (see [ToolCall](#object-toolcall)). |
| `latency_ms` | integer | Server-side turn latency. |
| `tokens` | object | `{ "prompt": int, "completion": int, "cached": int }`. |
| `signals` | array | Structured events the bot raised (see [Signal](#object-signal)). |
| `awaiting_clarification` | boolean | True if the bot is pausing to ask the user a question. |
| `clarification` | object \| null | Present when `awaiting_clarification` is true (see [Clarification](#object-clarification)). |

```json
{
  "session_id": "sess-abc-123",
  "trace_id": "trace_9f2a1c4b7e10",
  "text": "You've used 4.6 GB of 5 GB this cycle (92%), with 3 days to renewal.",
  "iterations": 2,
  "capped": false,
  "tool_calls": [
    { "name": "get_balance_and_usage", "input": { "customer_id": "CUST002" }, "duration_ms": 41, "ok": true }
  ],
  "latency_ms": 1840,
  "tokens": { "prompt": 1320, "completion": 96, "cached": 1024 },
  "signals": [],
  "awaiting_clarification": false,
  "clarification": null
}
```

### The clarification round-trip

When the bot needs missing information it pauses and returns a clarification
instead of a final answer. **You handle this in two steps over the same
`session_id`:**

**Step 1 — bot asks.** Request:
```json
{ "session_id": "sess-xyz", "customer_id": "CUST007", "message": "Change my plan", "bot_id": "telecom_support" }
```
Response (note `awaiting_clarification: true`):
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

**Step 2 — user answers.** Send the user's reply as a normal `/chat` call with the **same `session_id`**:
```json
{ "session_id": "sess-xyz", "customer_id": "CUST007", "message": "PRO_599", "bot_id": "telecom_support" }
```
The bot resumes exactly where it left off. No special endpoint or flag is needed
on your side — just reuse the session.

> **Mutating actions** (change plan, pay bill, etc.) ask for explicit
> confirmation first. Your integration just relays the user's "yes"/"no" as the
> next message; there's no separate confirm API.

---

## `GET /chat/history`

Return the visible chat bubbles for a session — internal tool plumbing is
stripped. Use this to rehydrate the UI on page load.

### Query parameters

| Param | Type | Required | Description |
|---|---|---|---|
| `session_id` | string | yes | The session to load. |

`GET /chat/history?session_id=sess-abc-123`

### Response body

| Field | Type | Description |
|---|---|---|
| `session_id` | string | Echoes the request. |
| `customer_id` | string \| null | The session's user, if known. |
| `bot_id` | string \| null | The session's bot, if known. |
| `awaiting_clarification` | boolean | Whether the session is waiting on a clarification answer. |
| `messages` | array | Ordered bubbles: `[{ "role": "user" \| "assistant", "text": "..." }]`. |

If the session is unknown, returns `200` with an empty `messages` array.

---

## `POST /chat/reset`

Clear a session's conversation (both the message history and the pending
clarification state).

Send a `ChatRequest` body; **only `session_id` is used** (other fields are
required by the shared schema — pass placeholders).

```json
{ "session_id": "sess-abc-123", "customer_id": "CUST002", "message": "reset" }
```

Response: `{ "ok": true }`

---

# Document API (RAG knowledge base)

Manage the documents a RAG-enabled bot can retrieve. These endpoints are the
**control plane** — only operators/integrations should call them; the LLM never
does. Every endpoint is scoped to one bot: the tenant is the `bot_id` and the
collection is taken from the bot's config, so a caller can never reach another
bot's knowledge base.

> Available only for bots with the `rag` skill enabled (e.g. `telecom_support`).
> For non-RAG bots these endpoints return `404`.

### Document identity

A document is identified by a caller-chosen **`id`** — a stable, URL-safe string
such as `refund-policy` or `policies/roaming.md`. The `id` is also stored as the
document's source URI. Re-using the same `id` **updates** the document in place
(no duplicates). Internally the platform derives a deterministic `doc_id`
(returned in responses) but you only ever need the `id` you chose.

---

## `POST /bots/{bot_id}/documents`  ·  `PUT /bots/{bot_id}/documents`

Add a new document or update an existing one. Idempotent by `id`. The call is
**synchronous**: on success the document is chunked, embedded, indexed, and
immediately searchable. `POST` and `PUT` are equivalent (both upsert).

### Path parameters

| Param | Type | Description |
|---|---|---|
| `bot_id` | string | The RAG-enabled bot, e.g. `telecom_support`. |

### Request body

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | Stable, URL-safe document identifier (also the source URI). Re-using it updates in place. |
| `content` | string | yes | The full document text. |
| `mime_type` | string | no | Defaults to inference from the `id`'s extension (`.md` → markdown chunking with headings; otherwise plain text). |
| `metadata` | object | no | Arbitrary metadata stored with the document's chunks. |

```json
{
  "id": "refund-policy.md",
  "content": "# Refunds\n\nPostpaid refunds are issued within 7 business days...",
  "metadata": { "owner": "support_ops", "version": "2026-06" }
}
```

### Response body

| Field | Type | Description |
|---|---|---|
| `bot_id` | string | The bot. |
| `collection` | string | Logical collection the document landed in. |
| `document_id` | string | The `id` you supplied. |
| `doc_id` | string | Internal deterministic id. |
| `status` | string | `created`, `updated`, or `unchanged`. |
| `chunks` | integer | Chunks produced from the document. |
| `embedded` | integer | Chunks embedded this call (0 when `unchanged`). |
| `upserted` | integer | Chunks written to the vector store (0 when `unchanged`). |

```json
{
  "bot_id": "telecom_support",
  "collection": "telecom_policies",
  "document_id": "refund-policy.md",
  "doc_id": "0a1b2c3d4e5f60718293a4b5c6d7e8f9",
  "status": "created",
  "chunks": 3,
  "embedded": 3,
  "upserted": 3
}
```

`status` semantics:
- **created** — new document, indexed.
- **updated** — content changed; old chunks dropped, new ones indexed.
- **unchanged** — identical content already present; nothing re-embedded (cheap, idempotent).

---

## `GET /bots/{bot_id}/documents`

List the documents currently in the bot's knowledge base.

### Response body

| Field | Type | Description |
|---|---|---|
| `bot_id` | string | The bot. |
| `collection` | string | The logical collection. |
| `count` | integer | Number of documents. |
| `documents` | array | List of [DocumentInfo](#object-documentinfo). |

```json
{
  "bot_id": "telecom_support",
  "collection": "telecom_policies",
  "count": 2,
  "documents": [
    {
      "document_id": "refund-policy.md",
      "doc_id": "0a1b2c3d4e5f60718293a4b5c6d7e8f9",
      "chunk_count": 3,
      "ingested_at": "2026-06-16T16:30:00",
      "metadata": { "owner": "support_ops" }
    }
  ]
}
```

---

## `DELETE /bots/{bot_id}/documents/{id}`

Remove a document — its chunks from the vector store **and** its bookkeeping row.
The `{id}` is the same identifier you used to create it.

`DELETE /bots/telecom_support/documents/refund-policy.md`

### Response body

| Field | Type | Description |
|---|---|---|
| `bot_id` | string | The bot. |
| `collection` | string | The logical collection. |
| `document_id` | string | The `id` you supplied. |
| `doc_id` | string | Internal deterministic id. |
| `deleted` | boolean | True if the document existed and was removed. |
| `chunks_removed` | integer | Number of vector chunks deleted. |

```json
{
  "bot_id": "telecom_support",
  "collection": "telecom_policies",
  "document_id": "refund-policy.md",
  "doc_id": "0a1b2c3d4e5f60718293a4b5c6d7e8f9",
  "deleted": true,
  "chunks_removed": 3
}
```

Returns `404` if the document (or the bot's knowledge base) doesn't exist.

---

## `GET /health`

Liveness probe. Returns `{ "ok": true, "service": "chatbot" }`.

---

## Object reference

### Object: ToolCall
Appears in `ChatResponse.tool_calls`.

| Field | Type | Description |
|---|---|---|
| `name` | string | Tool name (e.g. `get_current_plan`, `search_knowledge_base`, `query_business_data`). |
| `input` | object | Arguments the bot passed to the tool. |
| `duration_ms` | integer | Tool execution time. |
| `ok` | boolean | Whether the tool succeeded. |

### Object: Clarification
Appears in `ChatResponse.clarification`.

| Field | Type | Description |
|---|---|---|
| `question` | string | The question to show the user. |
| `expected` | string | Hint about the expected reply type (e.g. `plan_id`, `yes_no`, `free_text`). |
| `suggested_replies` | string[] | Optional quick-reply options. |

### Object: Signal
Appears in `ChatResponse.signals`. A generic structured event; `payload` shape
depends on `type`.

| `type` | `payload` shape |
|---|---|
| `clarification` | `{ question, expected, suggested_replies }` |
| `confirmation_required` | `{ summary, action, options }` |
| `handoff` | `{ reason, queue }` |
| `end_conversation` | `{ reason }` |

New signal types can be added without breaking clients — ignore types you don't handle.

### Object: DocumentInfo
Appears in `DocumentListResponse.documents`.

| Field | Type | Description |
|---|---|---|
| `document_id` | string | The caller's `id` (source URI). |
| `doc_id` | string | Internal deterministic id. |
| `chunk_count` | integer | Chunks stored for this document. |
| `ingested_at` | string \| null | ISO timestamp of last (re)ingest. |
| `metadata` | object | Metadata stored with the document. |

---

## Error handling

| Status | When |
|---|---|
| `400` | A guardrail rejected the input (e.g. chat message exceeds the bot's max length). |
| `404` | Unknown bot, a non-RAG bot's document endpoint, or a document/knowledge base that doesn't exist. |
| `422` | Request body failed validation (missing/invalid fields). |
| `502` | A document upsert failed during embedding/indexing (e.g. the embedding backend was down). |
| `503` | The RAG engine is unavailable (failed to initialise at boot). |
| `200` | **Chat runtime/model errors are returned as a normal turn**, not a 5xx — the reply is an apology and `trace_id` identifies the turn for debugging. |

> For `/chat`, runtime failures surface inside a `200` response (apologetic
> `text`). Always render `text` and key support flows off `trace_id` rather than
> HTTP status alone. The Document API uses conventional status codes.

---

## cURL quickstart

```bash
# Talk to the telecom bot
curl -s http://localhost:8000/chat -H 'Content-Type: application/json' -d '{
  "session_id":"s1","customer_id":"CUST002","message":"how much data is left?"
}'

# Add a knowledge-base document
curl -s -X POST http://localhost:8000/bots/telecom_support/documents \
  -H 'Content-Type: application/json' -d '{
    "id":"refund-policy.md",
    "content":"# Refunds\n\nPostpaid refunds are issued within 7 business days."
  }'

# List documents
curl -s http://localhost:8000/bots/telecom_support/documents

# Update (same id) — re-indexes in place
curl -s -X PUT http://localhost:8000/bots/telecom_support/documents \
  -H 'Content-Type: application/json' -d '{
    "id":"refund-policy.md",
    "content":"# Refunds\n\nPostpaid refunds are issued within 14 business days."
  }'

# Delete
curl -s -X DELETE http://localhost:8000/bots/telecom_support/documents/refund-policy.md
```

---

## Minimal integration flow

```
Conversation:
  1. Generate a session_id when a chat thread starts (one per thread).
  2. POST /chat with { session_id, customer_id, message, bot_id }.
  3. If awaiting_clarification == true → show clarification.question
     (+ suggested_replies), then POST /chat again with the user's answer and
     the SAME session_id. Else → show response.text.
  4. On reload, GET /chat/history?session_id=... to repaint the thread.
  5. On "New chat", POST /chat/reset with the session_id.

Knowledge base (RAG bots):
  - POST/PUT /bots/{bot_id}/documents to add or update content (idempotent by id).
  - GET to audit what's indexed.
  - DELETE to remove. Changes are live on the next /chat search.
```
