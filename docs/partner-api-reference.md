# Chatbot Platform — API Reference (v1.0)

> **For:** partners integrating with the Chatbot Platform.
> **Stability:** v1. Paths and fields are stable; we only *add* fields — your
> client should ignore any it doesn't recognise.

The API has two parts:

- **Knowledge Base** — manage the documents a bot answers from (upload, list, download, delete).
- **Chat** — send an end‑user message and get the assistant's reply.

A **bot** is one assistant, identified by a `bot_id` we provision for you. Each
bot is fully isolated — a request for one `bot_id` can never touch another's data.

---

## Authentication

Send your API key on every request (except `GET /health`):

```
X-API-Key: <your-api-key>
```

Keys are issued **per bot** and work only for that bot. A missing or invalid key
returns `401`. Keep keys server‑side; never ship them in a browser or mobile app.

---

## Base URL

| Environment | Base URL |
| --- | --- |
| Production | `https://api.<your-host>` |
| Sandbox | `https://sandbox.<your-host>` |

HTTPS is required. Examples below use `{base_url}`.

---

## Conventions

- **JSON over HTTPS.** Send `Content-Type: application/json` on request bodies (file upload uses `multipart/form-data`).
- **You choose the IDs.** `session_id`, `customer_id`, and document `id` are opaque strings. Reusing an ID continues a conversation or updates a document in place.
- **Document IDs** may contain `/` (e.g. `policies/refund.md`).
- **Idempotent writes.** Re‑sending a document with the same `id` updates it; identical content is a no‑op (`status: "unchanged"`). Safe to retry.
- **Timestamps** are ISO‑8601 UTC.

### Errors

Errors return a JSON body `{ "detail": "..." }` (validation errors use a list of
`{ loc, msg, type }`).

| Status | Meaning |
| --- | --- |
| `400` | Input rejected (e.g. message too long). |
| `401` | Missing or invalid API key for this bot. |
| `404` | Unknown `bot_id`, or document not found. |
| `413` | Upload or text too large (25 MB file / 10 MB text). |
| `422` | Invalid body, or an uploaded file had no extractable text. |
| `429` | Rate limited — retry after the `Retry-After` header. |
| `500` / `503` | Temporary server/backend issue — retry with backoff. |
| `502` | Indexing hiccup — safe to retry the same request. |

---

## Knowledge Base API

All paths are scoped to `{bot_id}`. Supported files: **PDF, Markdown, TXT, HTML,
JSON**, and other UTF‑8 text. (Scanned/image‑only PDFs have no text — OCR them
first.) Each document's original file is stored and returned as a `download_url`.

### Upload a file — `POST /bots/{bot_id}/documents/upload`

`multipart/form-data`:

| Field | Required | Description |
| --- | --- | --- |
| `file` | yes | The document file. |
| `id` | no | Document identifier. Defaults to the filename. |
| `metadata` | no | A JSON object, encoded as a string. |

→ `200` [`DocumentResult`](#documentresult)

```bash
curl -X POST {base_url}/bots/am_marketplace/documents/upload \
  -H "X-API-Key: $API_KEY" \
  -F "file=@refund-policy.pdf" \
  -F 'id=policies/refund.pdf' \
  -F 'metadata={"owner":"legal"}'
```

### Add or update text — `PUT` / `POST /bots/{bot_id}/documents`

`application/json`:

| Field | Required | Description |
| --- | --- | --- |
| `id` | yes | Stable document identifier. |
| `content` | yes | The full document text. |
| `mime_type` | no | E.g. `text/markdown`. Inferred from `id` if omitted. |
| `metadata` | no | Arbitrary key/values stored with the document. |

→ `200` [`DocumentResult`](#documentresult)

```bash
curl -X PUT {base_url}/bots/am_marketplace/documents \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{ "id":"policies/refund.md", "content":"# Refund Policy\n..." }'
```

### List documents — `GET /bots/{bot_id}/documents`

→ `200` [`DocumentListResponse`](#documentlistresponse)

```bash
curl {base_url}/bots/am_marketplace/documents -H "X-API-Key: $API_KEY"
```

### Download the original file — `GET /bots/{bot_id}/documents/{document_id}/content`

Returns the raw bytes (this is the `download_url`). `404` if no file is stored.

### Delete a document — `DELETE /bots/{bot_id}/documents/{document_id}`

Removes the document and its stored file. → `200` [`DocumentDeleteResponse`](#documentdeleteresponse)

---

## Chat API

### Send a message — `POST /chat`

`application/json`:

| Field | Required | Description |
| --- | --- | --- |
| `session_id` | yes | Stable ID for one conversation thread. |
| `customer_id` | yes | Your end‑user's identity (opaque). |
| `message` | yes | The user's message. |
| `bot_id` | no | Which bot to address. Defaults to the primary bot. |

→ `200` [`ChatResponse`](#chatresponse). The response includes **`sources`** — the
documents the answer drew on, each with a link — so you can render clickable
references alongside the reply.

```bash
curl -X POST {base_url}/chat \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{ "session_id":"sess-1", "customer_id":"user-1",
        "message":"What is your refund window?" }'
```

```json
{
  "session_id": "sess-1",
  "trace_id": "trace-7b10c4",
  "text": "Refunds can be requested within 14 days of purchase. [1]",
  "sources": [
    { "document_id": "policies/refund.md",
      "title": "Refunds",
      "url": "https://.../policies/refund.md?..." }
  ],
  "awaiting_clarification": false,
  "clarification": null,
  "latency_ms": 842,
  "tokens": { "prompt": 1203, "completion": 48, "cached": 1024 }
}
```

**Clarifications.** If the bot needs more information it replies with
`awaiting_clarification: true` and a `clarification` object (the question to
show). Send the user's answer back as a normal `POST /chat` with the **same**
`session_id`.

### Get history — `GET /chat/history?session_id={id}`

Returns the visible messages for a session. → `200` [`HistoryResponse`](#historyresponse).
An unknown `session_id` returns an empty `messages` list.

### Reset — `POST /chat/reset`

Body is a `ChatRequest` (only `session_id` is used). Clears the conversation.
→ `200` `{ "ok": true }`.

### Health — `GET /health`

Liveness probe (no auth). → `200` `{ "ok": true, "service": "chatbot" }`.

---

## Data models

Fields marked *(optional)* may be `null`.

#### DocumentResult
`document_id` · `status` (`created` | `updated` | `unchanged`) · `chunks` (int) ·
`filename` *(optional)* · `content_type` *(optional)* · `size_bytes` *(optional)* ·
`download_url` *(optional)* — link to fetch the original file.

#### DocumentInfo
`document_id` · `chunk_count` (int) · `ingested_at` (ISO‑8601, *optional*) ·
`metadata` (object) · `filename` *(optional)* · `content_type` *(optional)* ·
`size_bytes` *(optional)* · `download_url` *(optional)*.

#### DocumentListResponse
`bot_id` · `count` (int) · `documents` (`DocumentInfo[]`).

#### DocumentDeleteResponse
`document_id` · `deleted` (bool) · `chunks_removed` (int).

#### ChatResponse
`session_id` · `trace_id` · `text` — the reply to display ·
`sources` (`SourceRef[]`) — documents the answer used ·
`awaiting_clarification` (bool) · `clarification` (`Clarification` | null) ·
`latency_ms` (int) · `tokens` (`{ prompt, completion, cached }`).

#### SourceRef
`document_id` — the document's id · `title` *(optional)* — heading or filename ·
`url` *(optional)* — link to the source document.

#### Clarification
`question` · `expected` (e.g. `free_text`) · `suggested_replies` (string[]).

#### HistoryResponse
`session_id` · `customer_id` *(optional)* · `bot_id` *(optional)* ·
`awaiting_clarification` (bool) · `messages` (`{ role, text }[]`).

---

## Quick reference

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/bots/{bot_id}/documents/upload` | Add/update a document from a file. |
| `PUT` / `POST` | `/bots/{bot_id}/documents` | Add/update a document from text. |
| `GET` | `/bots/{bot_id}/documents` | List documents. |
| `GET` | `/bots/{bot_id}/documents/{document_id}/content` | Download the original file. |
| `DELETE` | `/bots/{bot_id}/documents/{document_id}` | Delete a document. |
| `POST` | `/chat` | Send a message; reply includes source references. |
| `GET` | `/chat/history?session_id=` | Read a conversation. |
| `POST` | `/chat/reset` | Clear a conversation. |
| `GET` | `/health` | Health check (no auth). |
