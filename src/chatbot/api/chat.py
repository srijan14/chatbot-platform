"""POST /chat handler — thin glue between HTTP and the orchestrator."""
from fastapi import APIRouter, HTTPException, Request
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from src.chatbot.api.schemas import (
    ChatRequest,
    ChatResponse,
    ClarificationOut,
    HistoryMessage,
    HistoryResponse,
    ToolCallTraceOut,
    TurnSignalOut,
)
from src.chatbot.core import guardrails
from src.chatbot.observability.logger import get_logger, log_turn, truncate
from src.chatbot.skills.clarification_skill import TOOL_NAME as CLARIFY_TOOL_NAME

router = APIRouter()
_log = get_logger("chat")


def _messages_to_visible(messages: list[BaseMessage]) -> list[HistoryMessage]:
    """Filter LangChain messages down to user/assistant chat bubbles.

    Rules:
      - HumanMessage → user bubble
      - AIMessage with non-empty string content → assistant bubble
      - AIMessage with content=None/"" but a tool_call to ask_clarification
        → render the call's `question` arg as an assistant bubble (the
        clarify short-circuit stores the question only in tool args)
      - ToolMessage / SystemMessage → skipped (internal)
    """
    out: list[HistoryMessage] = []
    for m in messages:
        if isinstance(m, HumanMessage) and m.content:
            out.append(HistoryMessage(role="user", text=str(m.content)))
        elif isinstance(m, AIMessage):
            content = m.content
            if isinstance(content, str) and content:
                out.append(HistoryMessage(role="assistant", text=content))
                continue
            # No text content — look for ask_clarification tool call.
            for tc in m.tool_calls or []:
                if tc.get("name") != CLARIFY_TOOL_NAME:
                    continue
                args = tc.get("args") or {}
                question = args.get("question")
                if question:
                    out.append(HistoryMessage(role="assistant", text=str(question)))
                    break
    return out


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> ChatResponse:
    state = request.app.state

    _log.info(
        "[chat] REQUEST  session=%s customer=%s bot=%s message=%r",
        req.session_id, req.customer_id, req.bot_id, truncate(req.message, 120),
    )

    bot_config = state.router.get_config(req.bot_id)

    err = guardrails.check_input(req.message, bot_config)
    if err:
        _log.warning("[chat] guardrail rejected session=%s reason=%s", req.session_id, err)
        raise HTTPException(400, err)

    skills = state.router.get_skills(req.bot_id)
    session = await state.conversations.get_or_create(
        req.session_id, customer_id=req.customer_id, bot_id=req.bot_id
    )

    result = await state.orchestrator.run_turn(session, req.message, bot_config, skills)

    # LangGraph owns conversation history now — we no longer write MessageRow.
    # ConversationManager.persist_turn with new_messages=[] still updates the
    # SessionRow.awaiting_clarification flag, which we read on the next request
    # to decide between fresh-turn and Command(resume=...).
    await state.conversations.persist_turn(
        session,
        new_messages=[],
        awaiting_clarification=result.awaiting_clarification,
    )
    # Set response_chars now that we know the final text length.
    result.log_payload["response_chars"] = len(result.text or "")
    await log_turn(state.db_sessionmaker, result.log_payload)

    _log.info(
        "[chat] RESPONSE session=%s trace=%s iter=%d latency=%dms tool_calls=%d "
        "awaiting_clarification=%s tokens=in:%d/out:%d/cached:%d",
        req.session_id, result.trace_id, result.iterations, result.latency_ms,
        len(result.tool_calls), result.awaiting_clarification,
        result.prompt_tokens, result.completion_tokens, result.cached_tokens,
    )

    clarification_out = None
    if result.clarification is not None:
        clarification_out = ClarificationOut(
            question=result.clarification.question,
            expected=result.clarification.expected,
            suggested_replies=result.clarification.suggested_replies,
        )

    return ChatResponse(
        session_id=req.session_id,
        trace_id=result.trace_id,
        text=result.text,
        iterations=result.iterations,
        capped=result.capped,
        tool_calls=[
            ToolCallTraceOut(name=tc.name, input=tc.input, duration_ms=tc.duration_ms, ok=tc.ok)
            for tc in result.tool_calls
        ],
        latency_ms=result.latency_ms,
        tokens={
            "prompt": result.prompt_tokens,
            "completion": result.completion_tokens,
            "cached": result.cached_tokens,
        },
        signals=[
            TurnSignalOut(type=s.type, payload=s.payload) for s in result.signals
        ],
        awaiting_clarification=result.awaiting_clarification,
        clarification=clarification_out,
    )


@router.get("/chat/history", response_model=HistoryResponse)
async def get_history(session_id: str, request: Request) -> HistoryResponse:
    """Return user/assistant bubbles for a session, used by the UI on page load.

    Pulls metadata (customer_id, bot_id, awaiting_clarification) from the
    relational `SessionRow` and the actual message list from the LangGraph
    checkpointer. Returns an empty response if neither knows the session.
    """
    state = request.app.state
    session = await state.conversations.load_session(session_id)
    if session is None:
        _log.info("[chat] HISTORY session=%s → no row, returning empty", session_id)
        return HistoryResponse(session_id=session_id)

    bot_config = state.router.get_config(session.bot_id)
    skills = state.router.get_skills(session.bot_id)
    messages = await state.orchestrator.get_state_messages(session_id, bot_config, skills)
    visible = _messages_to_visible(messages)

    _log.info(
        "[chat] HISTORY session=%s customer=%s visible=%d raw=%d awaiting=%s",
        session_id, session.customer_id, len(visible), len(messages),
        session.awaiting_clarification,
    )
    return HistoryResponse(
        session_id=session_id,
        customer_id=session.customer_id,
        bot_id=session.bot_id,
        awaiting_clarification=session.awaiting_clarification,
        messages=visible,
    )


@router.post("/chat/reset")
async def reset(req: ChatRequest, request: Request):
    await request.app.state.conversations.reset(req.session_id)
    return {"ok": True}
