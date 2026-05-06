"""Guardrails — POC stub: input length cap + log redaction.

In production this would add prompt-injection detection, PII scrubbing, jailbreak
filters, output safety checks, etc. The class exists to prove the slot.
"""
import re

from src.chatbot.core.bot_config_store import BotConfig


_PHONE_RE = re.compile(r"\+?\d{10,13}")
_EMAIL_RE = re.compile(r"[\w.-]+@[\w.-]+\.\w+")


def check_input(message: str, config: BotConfig) -> str | None:
    """Return an error message if input violates a guardrail; None if OK."""
    if len(message) > config.max_input_chars:
        return f"Message too long (max {config.max_input_chars} characters)."
    return None


def redact_for_logs(text: str, config: BotConfig) -> str:
    if not config.pii_redaction_in_logs:
        return text
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    return text
