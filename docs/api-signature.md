# Chatbot Platform — API Signature

> **Audience:** end-user developers integrating with the Chatbot Platform.
> This is the typed HTTP contract. Field types use a TypeScript-like notation
> (`string`, `integer`, `boolean`, `T[]`, `T | null`, `= default`). Fields marked
> *(diagnostic)* are returned for debugging and can be ignored by clients.

---

## Conventions

| | |
| --- | --- |
| **Base URL** | `https://{your-host}` (local dev: `http://localhost:8000`) |
| **Format** | JSON over HTTPS; `Content-Type: application/json` (except file upload: `multipart/form-data`) |
| **Auth** | Not enforced by the service — apply client auth at your API gateway / proxy |
| **Tenancy** | A `bot_id` scopes all knowledge-base operations; bots are fully isolated |
| **IDs** | `session_id`, `customer_id`, document `id` are opaque strings you choose; reuse = continue/update |
| **Versioning** | Unversioned, stable paths. Clients must ignore unknown response fields |

**Error response** (any non-2xx):

```
ErrorResponse {
  detail: string          // human-readable reason
}
```

| Status | Meaning |
| --- | --- |
| `400` | Input rejected by a guardrail (e.g. message too long) |
| `404` | Unknown `bot_id`, no knowledge base, or document not found |
| `422` | Body failed validation, or an uploaded file could not be read |
| `502` | Document indexing failed downstream (retryable) |
| `503` | Knowledge base temporarily unavailable |

---

## Conversations

### `POST /chat` — send a message, get a reply

```
Request  (application/json):
ChatRequest {
  session_id:  string                       // required — one conversation thread
  customer_id: string                       // required — your end-user identity
  message:     string                       // required — non-empty
  bot_id:      string = "am_marketplace"    // which bot to address
}

Response (200):
ChatResponse {
  session_id:             string
  trace_id:               string            // correlation id for support
  text:                   string            // assistant reply to display
  awaiting_clarification: boolean
  clarification:          Clarification | null
  signals:                Signal[]
  latency_ms:             integer
  tokens:                 TokenUsage
  // (diagnostic) iterations: integer; capped: boolean; tool_calls: ToolCall[]
}
```

### `GET /chat/history?session_id={id}` — read a conversation

```
Query: session_id: string                   // required

Response (200):
HistoryResponse {
  session_id:             string
  customer_id:            string | null
  bot_id:                 string | null
  awaiting_clarification: boolean
  messages:               ChatMessage[]      // empty if session unknown
}
```

### `POST /chat/reset` — clear a conversation

```
Request  (application/json):
ChatRequest {                               // full shape required by validation
  session_id:  string                       // required — the only field used
  customer_id: string                       // required (validation)
  message:     string                       // required, non-empty (validation) — e.g. "reset"
  bot_id:      string = "am_marketplace"
}

Response (200):
{ ok: boolean }
```

---

## Knowledge Base

All paths are scoped to `{bot_id}`. `document_id` is your stable identifier and
may contain `/` (e.g. `policies/refund.md`); reusing it updates in place.

### `PUT` / `POST /bots/{bot_id}/documents` — add/update a text document

```
Request  (application/json):
DocumentUpsertRequest {
  id:        string                         // required — stable document id
  content:   string                         // required — full document text
  mime_type: string | null                  // inferred from id extension if omitted
  metadata:  object = {}                    // arbitrary key/values
}

Response (200): DocumentResult
```

### `POST /bots/{bot_id}/documents/upload` — add/update from a file

```
Request  (multipart/form-data):
  file:     binary                          // required — pdf | txt | md | html | json | ...
  id:       string | null                   // defaults to the uploaded filename
  metadata: string | null                   // a JSON object, encoded as a string

Response (200): DocumentResult
  // 422 if the file is empty or no text can be extracted (e.g. scanned PDF)
```

### `GET /bots/{bot_id}/documents` — list documents

```
Response (200):
DocumentListResponse {
  bot_id:    string
  count:     integer
  documents: DocumentInfo[]
}
```

### `GET /bots/{bot_id}/documents/{document_id}/content` — download the file

```
Response (200): raw file bytes
  Content-Type:        <stored mime type>
  Content-Disposition: inline; filename="<stored filename>"
  // 404 if no file is stored for that document
```

### `DELETE /bots/{bot_id}/documents/{document_id}` — delete a document

```
Response (200):
DocumentDeleteResponse {
  document_id:    string
  deleted:        boolean
  chunks_removed: integer
  blob_deleted:   boolean
  // (diagnostic) bot_id, collection, doc_id
}
```

---

## Service

### `GET /health` — liveness probe

```
Response (200):
{ ok: boolean, service: string }
```

---

## Shared models

```
TokenUsage {                                // per-turn usage
  prompt:     integer
  completion: integer
  cached:     integer
}

Clarification {                             // present when awaiting_clarification = true
  question:          string
  expected:          string = "free_text"   // hint about the expected reply
  suggested_replies: string[]               // optional quick-reply options
}

Signal {                                    // a structured event raised during a turn
  type:    string                           // see "Signal types" below
  payload: object                           // shape depends on type
}

ChatMessage {
  role: string                              // "user" | "assistant"
  text: string
}

DocumentResult {                            // returned by upsert + upload
  document_id:  string
  status:       string                      // "created" | "updated" | "unchanged"
  chunks:       integer                     // searchable chunks produced
  filename:     string | null
  content_type: string | null
  size_bytes:   integer | null
  download_url: string | null               // URL of the file-download endpoint
  // (diagnostic) bot_id, collection, doc_id, embedded, upserted
}

DocumentInfo {                              // one entry in a document list
  document_id:  string
  chunk_count:  integer
  ingested_at:  string | null               // ISO-8601
  metadata:     object
  filename:     string | null
  content_type: string | null
  size_bytes:   integer | null
  download_url: string | null
  // (diagnostic) doc_id
}

ToolCall {                                  // diagnostic only
  name:        string
  input:       object
  duration_ms: integer
  ok:          boolean
}
```

### Signal types

`POST /chat` returns a `signals` array. Handle the types you support; ignore the rest.

| `type` | `payload` | Meaning |
| --- | --- | --- |
| `clarification` | `{ question, expected, suggested_replies }` | Bot needs more info before answering |
| `confirmation_required` | `{ summary, action, options }` | Bot wants the user to confirm an action |
| `handoff` | `{ reason, queue }` | Bot is escalating to a human |
| `end_conversation` | `{ reason }` | Bot considers the conversation finished |

To answer a clarification, send the user's reply as a normal `POST /chat` with the
**same** `session_id`.

---

## Endpoint index

| Method | Path | Request | Response |
| --- | --- | --- | --- |
| `POST` | `/chat` | `ChatRequest` | `ChatResponse` |
| `GET` | `/chat/history` | `?session_id` | `HistoryResponse` |
| `POST` | `/chat/reset` | `ChatRequest` | `{ ok }` |
| `PUT`/`POST` | `/bots/{bot_id}/documents` | `DocumentUpsertRequest` | `DocumentResult` |
| `POST` | `/bots/{bot_id}/documents/upload` | multipart | `DocumentResult` |
| `GET` | `/bots/{bot_id}/documents` | — | `DocumentListResponse` |
| `GET` | `/bots/{bot_id}/documents/{document_id}/content` | — | file bytes |
| `DELETE` | `/bots/{bot_id}/documents/{document_id}` | — | `DocumentDeleteResponse` |
| `GET` | `/health` | — | `{ ok, service }` |
