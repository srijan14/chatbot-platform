# API Lifecycle and Governance Rules

## Standard Lifecycle States

Every API in the marketplace follows a defined lifecycle to ensure quality and
governance:

```
Create → Draft → Submit → Pending → Approve (Intranet) → Go Live → Approve (Internet)
```

Intranet approval is a prerequisite: an API must first be approved for intranet
use before it can be promoted or approved for internet exposure. This creates a
layered governance model: APIs are first internal, and only after validation and
stability are they considered for external exposure.

## Key Lifecycle Rules

Two important rules frame lifecycle behavior:

- An API must be approved on the **Intranet** side before any **Internet**
  (public) approval is possible.
- If an approver **rejects** an API at any stage, the API is moved back to the
  **Draft** state so the publisher can revise and resubmit.

These rules prevent partially reviewed APIs from entering production and provide
a clear, reversible path for correction.
