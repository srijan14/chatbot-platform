"""Response Formatter — extracts the user-visible text from an Anthropic Message."""
from typing import Any


def format_assistant_text(content_blocks: list[Any]) -> str:
    parts: list[str] = []
    for b in content_blocks:
        btype = b.get("type") if isinstance(b, dict) else getattr(b, "type", None)
        if btype == "text":
            text = b.get("text") if isinstance(b, dict) else getattr(b, "text", "")
            if text:
                parts.append(text)
    return "\n\n".join(parts).strip()
