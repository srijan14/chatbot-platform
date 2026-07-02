# Chatbot Platform — Integration API

> **Audience:** developers integrating an application with the Chatbot Platform.
> This page is the public HTTP contract. It documents only the fields a client
> needs; internal/diagnostic fields are called out where present.

---

## Overview

The platform exposes a small JSON/HTTP API with two groups of endpoints:

| Group | Purpose |
| --- | --- |
| **Conversations** | Send a user message and get the assistant's reply; read or reset a conversation. |
| **Knowledge Base** | Manage the documents a bot can answer from (add, list, download, delete). |

A **bot** is one configured assistant. Each bot is identified by a `bot_id`
(provisioned for you) and has its own isolated knowledge base — a request for
one `bot_id` can never read or write another bot's data.

---

## Conventions

- **Base URL:** `https://{your-host}` (local development: `http://localhost:8000`).
- **Transport:** JSON over HTTPS. Send `Content-Type: application/json` on all
  request bodies except file upload (`multipart/form-data`).
- **Identifiers:** all IDs are opaque strings you choose (`session_id`,
  `customer_id`, document `id`). Reusing the same ID is how you continue a
  conversation or update a document.
- **Versioning:** the API is currently unversioned; paths are stable. Additive
  fields may be introduced over time, so clients should ignore unknown fields.

### Authentication

The service does **not** enforce per-request authentication itself. Deploy it
behind your API gateway / reverse proxy and apply client authentication there
(API key, mTLS, etc.). All examples below assume requests already pass that edge.

### Errors

Errors use standard HTTP status codes with a JSON body:

```json
{ "detail": "Human-readable explanation of what went wrong." }
```

| Status | Meaning |
| --- | --- |
| `400` | Request rejected by an input guardrail (e.g. message too long). |
| `404` | Unknown `bot_id`, bot has no knowledge base, or document not found. |
| `422` | Request body failed validation, or an uploaded file could not be read. |
| `502` | Document indexing failed downstream; safe to retry. |
| `503` | Knowledge base temporarily unavailable. |

---

# Conversations

## Send a message — `POST /chat`

Send one user message and receive the assistant's reply. Continue a conversation
by reusing the same `session_id`.

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `session_id` | string | yes | Stable ID for one conversation thread (e.g. per chat window). |
| `customer_id` | string | yes | Your end-user's identity. Used for per-user usage accounting and audit. Opaque — any stable string. |
| `message` | string | yes | The user's message (non-empty). |
| `bot_id` | string | no | Which bot to talk to. Defaults to the platform's primary bot. |

**Response**

| Field | Type | Description |
| --- | --- | --- |
| `session_id` | string | Echoes the request. |
| `text` | string | The assistant's reply to show the user. |
| `trace_id` | string | Correlation ID for this turn — quote it in support requests. |
| `awaiting_clarification` | boolean | `true` if the bot needs more info before it can answer. |
| `clarification` | object \| null | Present when `awaiting_clarification` is true. See [Clarifications](#clarifications). |
| `signals` | array | Structured events the bot raised this turn. See [Turn Signals](#turn-signals). |
| `latency_ms` | integer | Server-side processing time. |
| `tokens` | object | Usage for this turn: `{ "prompt", "completion", "cached" }`. |

> **Diagnostic fields (optional):** the response also includes `iterations`,
> `capped`, and `tool_calls` (an internal execution trace). These are for
> debugging and are not needed for a normal integration.

**Example**

```bash
curl -X POST https://{your-host}/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "sess-9f2a",
    "customer_id": "user-12345",
    "message": "What is your refund window?",
    "bot_id": "am_marketplace"
  }'
```

```json
{
  "session_id": "sess-9f2a",
  "trace_id": "trace-7b10c4",
  "text": "Refunds can be requested within 14 days of purchase.",
  "awaiting_clarification": false,
  "clarification": null,
  "signals": [],
  "latency_ms": 842,
  "tokens": { "prompt": 1203, "completion": 48, "cached": 1024 }
}
```

---

## Get conversation history — `GET /chat/history`

Return the visible user/assistant messages for a session (e.g. to rehydrate a
chat window on page load). Internal tool/system messages are not included.

**Query parameters**

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `session_id` | string | yes | The conversation to load. |

**Response**

| Field | Type | Description |
| --- | --- | --- |
| `session_id` | string | Echoes the request. |
| `customer_id` | string \| null | The user this session belongs to. |
| `bot_id` | string \| null | The bot for this session. |
| `awaiting_clarification` | boolean | Whether the bot is currently waiting on the user. |
| `messages` | array | Ordered list of `{ "role": "user"\|"assistant", "text": string }`. |

An unknown `session_id` returns `200` with an empty `messages` array.

```bash
curl "https://{your-host}/chat/history?session_id=sess-9f2a"
```

---

## Reset a conversation — `POST /chat/reset`

Clear all history for a session so the next message starts fresh.

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `session_id` | string | yes | The conversation to clear. |
| `customer_id` | string | yes | Required by request validation; not otherwise used here. |
| `message` | string | yes | Required by request validation (send any non-empty placeholder, e.g. `"reset"`). |

**Response:** `{ "ok": true }`

```bash
curl -X POST https://{your-host}/chat/reset \
  -H "Content-Type: application/json" \
  -d '{ "session_id": "sess-9f2a", "customer_id": "user-12345", "message": "reset" }'
```

---

# Knowledge Base

These endpoints manage the documents a bot answers from. All are scoped to a
single bot via the `{bot_id}` path segment. The bot must have a knowledge base
configured, otherwise these return `404`.

`document_id` is **your** stable identifier for a document. Re-using it updates
the document in place (and replaces its stored file). It may contain `/`
(e.g. `policies/refund.md`).

## Add or update a text document — `PUT` / `POST /bots/{bot_id}/documents`

Insert or update a document from text you already have. Idempotent by `id`.

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Stable document identifier. Reusing it updates in place. |
| `content` | string | yes | The full document text. |
| `mime_type` | string | no | E.g. `text/markdown`. Inferred from the `id`'s extension if omitted. |
| `metadata` | object | no | Arbitrary key/values stored and returned alongside the document. |

**Response** — see [Document result](#document-result).

```bash
curl -X PUT https://{your-host}/bots/am_marketplace/documents \
  -H "Content-Type: application/json" \
  -d '{
    "id": "policies/refund.md",
    "content": "# Refund Policy\nRefunds within 14 days...",
    "metadata": { "owner": "legal" }
  }'
```

## Upload a file — `POST /bots/{bot_id}/documents/upload`

Insert or update a document from a raw file (PDF, txt, md, html, json, …). The
original file is stored for download; its text is extracted and indexed.

**Form fields** (`multipart/form-data`)

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `file` | file | yes | The document file. |
| `id` | string | no | Document identifier. Defaults to the uploaded filename. |
| `metadata` | string | no | A JSON object, encoded as a string. |

**Response** — see [Document result](#document-result).

Returns `422` if the file is empty, or if no text can be extracted (e.g. a
scanned PDF — run OCR first or upload the text directly).

```bash
curl -X POST https://{your-host}/bots/am_marketplace/documents/upload \
  -F "file=@refund-policy.pdf" \
  -F 'metadata={"owner":"legal"}'
```

### Document result

Returned by the text-upsert and upload endpoints.

| Field | Type | Description |
| --- | --- | --- |
| `document_id` | string | Your document identifier. |
| `status` | string | `created`, `updated`, or `unchanged`. |
| `chunks` | integer | Number of searchable chunks the document was split into. |
| `filename` | string | Stored file name. |
| `content_type` | string | Stored file MIME type. |
| `size_bytes` | integer | Stored file size. |
| `download_url` | string | URL to fetch the original file back (see below). |

> **Diagnostic fields (optional):** also includes `bot_id`, `collection`,
> `doc_id` (internal id), `embedded`, and `upserted`.

```json
{
  "document_id": "policies/refund.md",
  "status": "created",
  "chunks": 3,
  "filename": "refund.md",
  "content_type": "text/markdown",
  "size_bytes": 412,
  "download_url": "https://{your-host}/bots/am_marketplace/documents/policies/refund.md/content"
}
```

## List documents — `GET /bots/{bot_id}/documents`

List the documents currently in the bot's knowledge base.

**Response**

| Field | Type | Description |
| --- | --- | --- |
| `bot_id` | string | The bot. |
| `count` | integer | Number of documents. |
| `documents` | array | List of document entries (below). |

Each entry:

| Field | Type | Description |
| --- | --- | --- |
| `document_id` | string | Your document identifier. |
| `chunk_count` | integer | Searchable chunks for this document. |
| `ingested_at` | string \| null | ISO-8601 timestamp of last index. |
| `metadata` | object | The metadata you supplied. |
| `filename` | string \| null | Stored file name. |
| `content_type` | string \| null | Stored file MIME type. |
| `size_bytes` | integer \| null | Stored file size. |
| `download_url` | string \| null | URL to download the original file (null if none stored). |

```bash
curl https://{your-host}/bots/am_marketplace/documents
```

## Download a document's file — `GET /bots/{bot_id}/documents/{document_id}/content`

Return the original stored file. The response is the raw bytes with the stored
`Content-Type` and a `Content-Disposition` filename. Returns `404` if no file is
stored for that document. This is the URL returned as `download_url`.

```bash
curl -OJ https://{your-host}/bots/am_marketplace/documents/policies/refund.md/content
```

## Delete a document — `DELETE /bots/{bot_id}/documents/{document_id}`

Remove a document: its searchable chunks and its stored file. Returns `404` if
the document does not exist.

**Response**

| Field | Type | Description |
| --- | --- | --- |
| `document_id` | string | The document removed. |
| `deleted` | boolean | Whether anything was removed. |
| `chunks_removed` | integer | Number of searchable chunks deleted. |
| `blob_deleted` | boolean | Whether the stored file was deleted. |

```bash
curl -X DELETE https://{your-host}/bots/am_marketplace/documents/policies/refund.md
```

---

# Service

## Health check — `GET /health`

Liveness probe. Returns `200` with `{ "ok": true, "service": "chatbot" }`.

---

# Turn Signals

`POST /chat` may return a `signals` array — structured events the bot wants your
application to act on. Each signal is `{ "type": string, "payload": object }`.
Iterate `signals` and handle the types you support; ignore unknown types.

| Type | Payload | What it means |
| --- | --- | --- |
| `clarification` | `{ question, expected, suggested_replies }` | The bot needs more info. Show `question`; optionally render `suggested_replies` as quick choices. |
| `confirmation_required` | `{ summary, action, options }` | The bot wants the user to confirm before proceeding. Show `summary` and `options`. |
| `handoff` | `{ reason, queue }` | The bot is escalating to a human. Route the conversation to `queue`. |
| `end_conversation` | `{ reason }` | The bot considers the conversation finished. |

### Clarifications

When the bot asks a question instead of answering, `awaiting_clarification` is
`true` and `clarification` is populated:

| Field | Type | Description |
| --- | --- | --- |
| `question` | string | The question to show the user. |
| `expected` | string | Hint about the expected reply (e.g. `free_text`). |
| `suggested_replies` | array | Optional quick-reply suggestions. |

Send the user's answer back as a normal `POST /chat` with the **same**
`session_id` to continue.

```json
{
  "session_id": "sess-9f2a",
  "text": "",
  "awaiting_clarification": true,
  "clarification": {
    "question": "Which order are you asking about?",
    "expected": "free_text",
    "suggested_replies": ["My latest order", "A previous order"]
  },
  "signals": [
    { "type": "clarification",
      "payload": { "question": "Which order are you asking about?",
                   "expected": "free_text",
                   "suggested_replies": ["My latest order", "A previous order"] } }
  ]
}
```

---

## Quick reference

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/chat` | Send a message, get a reply. |
| `GET` | `/chat/history?session_id=` | Read a conversation. |
| `POST` | `/chat/reset` | Clear a conversation. |
| `PUT` / `POST` | `/bots/{bot_id}/documents` | Add/update a text document. |
| `POST` | `/bots/{bot_id}/documents/upload` | Add/update from a file. |
| `GET` | `/bots/{bot_id}/documents` | List documents. |
| `GET` | `/bots/{bot_id}/documents/{document_id}/content` | Download a document's file. |
| `DELETE` | `/bots/{bot_id}/documents/{document_id}` | Delete a document. |
| `GET` | `/health` | Health check. |
