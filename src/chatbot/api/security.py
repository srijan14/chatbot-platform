"""Per-bot API-key authentication for the HTTP surface.

Keys are configured **per bot in YAML** (`auth.api_keys` in
`configs/bots/{bot_id}.yaml`), so each bot / channel partner carries its own
credentials and can never be authenticated against another bot's key.

Policy (per bot):
  - No keys configured  → auth is **disabled** for that bot (open). Keeps local
    dev and internal bots frictionless; you opt in by adding keys to the YAML.
  - Keys configured      → the caller must send a matching `X-API-Key`, else 401.

Two entry points:
  - `require_bot_api_key` — a FastAPI dependency for routes that carry `{bot_id}`
    in the path (the document control plane).
  - `verify_api_key`      — a plain helper for handlers where the bot is in the
    body (e.g. `POST /chat`).
"""
from __future__ import annotations

from fastapi import Header, HTTPException, Request


def _bot_api_keys(state, bot_id: str) -> list[str]:
    """Configured keys for a bot. 404 if the bot is unknown (same as elsewhere)."""
    try:
        cfg = state.router.get_config(bot_id)
    except FileNotFoundError:
        raise HTTPException(404, f"Unknown bot '{bot_id}'.")
    return list(getattr(cfg, "api_keys", None) or [])


def verify_api_key(state, bot_id: str, provided: str | None) -> None:
    """Enforce the per-bot key policy. No-op when the bot configures no keys."""
    keys = _bot_api_keys(state, bot_id)
    if not keys:
        return  # auth disabled for this bot
    if not provided:
        raise HTTPException(
            401,
            "Missing API key. Provide it in the 'X-API-Key' header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    if provided not in keys:
        raise HTTPException(
            401,
            "Invalid API key for this bot.",
            headers={"WWW-Authenticate": "ApiKey"},
        )


async def require_bot_api_key(
    bot_id: str,
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """Dependency for `/bots/{bot_id}/...` routes: check `X-API-Key` against the
    keys configured for that bot."""
    verify_api_key(request.app.state, bot_id, x_api_key)
