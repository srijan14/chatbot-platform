"""POST /chat handler — thin glue between HTTP and the orchestrator."""
from fastapi import APIRouter, HTTPException, Request

from src.chatbot.api.schemas import ChatRequest, ChatResponse, ToolCallTraceOut
from src.chatbot.core import guardrails

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> ChatResponse:
    state = request.app.state
    bot_config = state.router.get_config(req.bot_id)

    err = guardrails.check_input(req.message, bot_config)
    if err:
        raise HTTPException(400, err)

    skill = state.router.get_tool_call_skill(req.bot_id)
    session = state.conversations.get_or_create(req.session_id, customer_id=req.customer_id)

    result = await state.orchestrator.run_turn(session, req.message, bot_config, skill)

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
    )


@router.post("/chat/reset")
async def reset(req: ChatRequest, request: Request):
    request.app.state.conversations.reset(req.session_id)
    return {"ok": True}
