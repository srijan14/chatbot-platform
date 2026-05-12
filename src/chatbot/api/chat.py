"""POST /chat handler — thin glue between HTTP and the orchestrator."""
from fastapi import APIRouter, HTTPException, Request

from src.chatbot.api.schemas import (
    ChatRequest,
    ChatResponse,
    ClarificationOut,
    ToolCallTraceOut,
)
from src.chatbot.core import guardrails
from src.chatbot.observability.logger import log_turn

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> ChatResponse:
    state = request.app.state
    bot_config = state.router.get_config(req.bot_id)

    err = guardrails.check_input(req.message, bot_config)
    if err:
        raise HTTPException(400, err)

    skills = state.router.get_skills(req.bot_id)
    session = await state.conversations.get_or_create(
        req.session_id, customer_id=req.customer_id, bot_id=req.bot_id
    )

    result = await state.orchestrator.run_turn(session, req.message, bot_config, skills)

    await state.conversations.persist_turn(
        session,
        result.new_messages,
        awaiting_clarification=result.awaiting_clarification,
    )
    await log_turn(state.db_sessionmaker, result.log_payload)

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
        awaiting_clarification=result.awaiting_clarification,
        clarification=clarification_out,
    )


@router.post("/chat/reset")
async def reset(req: ChatRequest, request: Request):
    await request.app.state.conversations.reset(req.session_id)
    return {"ok": True}
