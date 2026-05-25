"""Dedicated Azure OpenAI call that turns (question, sql, rows) → prose.

Separate from the bot LLM so:
  • it can use a smaller/cheaper deployment (per-bot YAML override)
  • its system prompt is tuned for data-presentation rules ("never invent
    numbers, cite the row count, round currency") without polluting the
    bot's persona
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI


SYSTEM_PROMPT = """You are a BI analyst summarising the result of one SQL query.

RULES:
  - Never invent or extrapolate numbers. Only cite values present in the rows.
  - State the exact row count when relevant.
  - Use compact prose: one short paragraph, then optionally up to 4 bullets.
  - For currency, use the unit visible in the data; round to 2 decimal places.
  - If the result set is empty, say so plainly — do not guess.
"""


def make_summarizer(
    *,
    azure_endpoint: str,
    azure_api_key: str,
    azure_api_version: str,
    deployment: str,
    max_tokens: int = 400,
    temperature: float | None = 0.2,
) -> AzureChatOpenAI:
    """Build the dedicated summarizer LLM.

    Pass `temperature=None` to omit the kwarg entirely — required when the
    deployment is a reasoning-class model (o-series, gpt-5+) which rejects
    any non-default temperature. AzureChatOpenAI then leaves the field
    unset and Azure uses its server default of 1.0.
    """
    kwargs: dict = {
        "azure_endpoint": azure_endpoint,
        "api_key": azure_api_key,
        "api_version": azure_api_version,
        "azure_deployment": deployment,
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    return AzureChatOpenAI(**kwargs)


def render_rows_as_markdown(columns: list[str], rows: list[tuple], max_rows: int = 30) -> str:
    """Return a markdown table; truncate with a `(+N more)` footer."""
    if not columns:
        return "(no columns)"
    if not rows:
        return "_(no rows)_"
    head = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join(["---"] * len(columns)) + " |"
    body_rows = []
    for r in rows[:max_rows]:
        body_rows.append("| " + " | ".join(str(c) for c in r) + " |")
    table = "\n".join([head, sep, *body_rows])
    if len(rows) > max_rows:
        table += f"\n\n_(+{len(rows) - max_rows} more rows)_"
    return table


async def summarize(
    llm: AzureChatOpenAI,
    *,
    question: str,
    sql: str,
    columns: list[str],
    rows: list[tuple],
) -> str:
    markdown = render_rows_as_markdown(columns, rows)
    user_payload = (
        f"User question:\n{question}\n\n"
        f"SQL executed:\n```sql\n{sql}\n```\n\n"
        f"Result ({len(rows)} row{'' if len(rows) == 1 else 's'}):\n{markdown}\n\n"
        f"Write the analyst answer."
    )
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_payload),
    ]
    response = await llm.ainvoke(messages)
    content = response.content if isinstance(response.content, str) else str(response.content)
    # Embed the data table beneath the prose so the bot has both the
    # human summary and the cited rows in one tool result.
    return f"{content.strip()}\n\n{markdown}"
