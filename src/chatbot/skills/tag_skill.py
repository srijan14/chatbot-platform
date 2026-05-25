"""TAG Skill — Text-to-Analytics-Generation.

Exposes two tools to the bot LLM:

  * `list_business_metrics()` — returns the semantic-layer summary (metrics,
    dimensions) so the bot can discover what's askable without us dumping the
    full schema into the system prompt every turn.
  * `query_business_data(question, time_range?)` — runs the full NL→SQL→exec
    →summarize pipeline (LlamaIndex schema RAG + sqlglot validation + repair
    loop + dedicated summarizer LLM) and returns the analyst answer.

The pipeline itself lives in `src/chatbot/engines/tag_engine/`; the skill is
the thin platform-shim that adapts those internals to the `Skill` contract.
"""
from __future__ import annotations

from src.chatbot.engines.tag_engine.pipeline import TagPipeline
from src.chatbot.observability.logger import get_logger, truncate
from src.chatbot.skills.base import Skill, ToolResult

_log = get_logger("tag")

QUERY_TOOL = "query_business_data"
LIST_METRICS_TOOL = "list_business_metrics"

_SYSTEM_PROMPT_RULE = (
    "When the user asks a business question about the analytics warehouse, "
    "call `query_business_data` with a clear `question` (and a `time_range` "
    "if specified). For exploratory 'what can you tell me' questions, call "
    "`list_business_metrics` first to discover what's available. Do NOT "
    "invent numbers — always cite values from the tool result."
)


class TagSkill(Skill):
    name = "tag"

    def __init__(self, pipeline: TagPipeline):
        self._pipeline = pipeline
        self._tool_names = {QUERY_TOOL, LIST_METRICS_TOOL}

    async def prepare_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": LIST_METRICS_TOOL,
                    "description": (
                        "List the metrics and dimensions available in the "
                        "analytics warehouse. Call this first when the user "
                        "asks an open-ended discovery question."
                    ),
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": QUERY_TOOL,
                    "description": (
                        "Answer a business question by translating it to "
                        "SQL, running it against the analytics warehouse, "
                        "and returning an analyst-grade summary with a "
                        "markdown data table beneath."
                    ),
                    "parameters": {
                        "type": "object",
                        "required": ["question"],
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": (
                                    "The user's business question in plain "
                                    "English. Preserve specifics (segment, "
                                    "channel, currency)."
                                ),
                            },
                            "time_range": {
                                "type": "string",
                                "description": (
                                    "Optional time range hint, e.g. "
                                    "'last 30 days', 'last month', 'YTD'. "
                                    "If the question already includes one, "
                                    "omit this."
                                ),
                            },
                        },
                    },
                },
            },
        ]

    def owns_tool(self, name: str) -> bool:
        return name in self._tool_names

    def system_prompt_addition(self) -> str:
        return _SYSTEM_PROMPT_RULE

    async def execute_tool(self, name: str, arguments: dict) -> ToolResult:
        if name == LIST_METRICS_TOOL:
            text = await self._pipeline.list_metrics_text()
            _log.info("[tag] LIST-METRICS-CALL chars=%d", len(text))
            return ToolResult(text=text)

        if name == QUERY_TOOL:
            question = (arguments.get("question") or "").strip()
            time_range = arguments.get("time_range")
            _log.info(
                "[tag] QUERY-CALL question=%r time_range=%s",
                truncate(question, 200), time_range,
            )
            if not question:
                return ToolResult(
                    text="No question provided.",
                    is_error=True,
                )
            try:
                result = await self._pipeline.answer(question, time_range=time_range)
            except RuntimeError as exc:
                _log.warning("[tag] QUERY-FAILED reason=%s", exc)
                return ToolResult(
                    text=f"I couldn't answer that against the data: {exc}",
                    is_error=True,
                )
            return ToolResult(text=result.summary)

        return ToolResult(text=f"Unknown tool: {name}", is_error=True)
