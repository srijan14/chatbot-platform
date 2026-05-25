"""Unit tests for the three LangChain v1 middlewares.

These exercise the middleware classes in isolation (no full agent loop):
  - dynamic_prompt: feed a fake ModelRequest with state, assert the system
    prompt picks up the customer_id auth line.
  - TokenUsageMiddleware: feed an AIMessage with usage_metadata, assert
    the state-update dict accumulates correctly.
  - BudgetGuardMiddleware: pre-populate the in-memory store above the
    cap; assert before_model short-circuits with jump_to="__end__".
"""
from __future__ import annotations

from langchain_core.messages import AIMessage

from src.chatbot.core.bot_config_store import BotConfig
from src.chatbot.core.middleware import (
    BudgetGuardMiddleware,
    TokenUsageMiddleware,
    build_dynamic_prompt,
)
from src.chatbot.skills.clarification_skill import ClarificationSkill


def _bot_config(persona: str = "You are a helpful assistant.") -> BotConfig:
    return BotConfig(
        bot_id="t",
        name="t",
        description="",
        llm_provider="azure_openai",
        llm_deployment="gpt-4o",
        llm_reasoning=False,
        max_tokens=128,
        temperature=0.2,
        max_tool_iterations=4,
        system_prompt=persona,
        enabled_skills=["clarification"],
        mcp_servers=[],
        tool_allowlist=[],
        max_input_chars=2000,
        pii_redaction_in_logs=True,
    )


# --- 1. Dynamic prompt middleware -------------------------------------------

def _invoke_dynamic_prompt(middleware, state: dict) -> str:
    """Call the middleware's wrap_model_call and intercept the system prompt
    it sets on the ModelRequest before the model would be invoked. Lets us
    unit-test the prompt-building logic without spinning a real agent graph.
    """
    from langchain.agents.middleware import ModelRequest
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableConfig
    from types import SimpleNamespace

    request = ModelRequest(
        model=GenericFakeChatModel(messages=iter([AIMessage(content="x")])),
        messages=[],
        system_prompt=None,
        tools=[],
        state=state,
        runtime=SimpleNamespace(context={}),
    )

    captured: dict[str, str | None] = {"system_prompt": None}

    def handler(req: ModelRequest):
        # The dynamic-prompt middleware sets `system_prompt` on the request
        # (visible via the system_message attribute the dataclass exposes).
        sm = getattr(req, "system_message", None)
        captured["system_prompt"] = sm.content if sm is not None else None
        return AIMessage(content="x")

    middleware.wrap_model_call(request, handler)
    if captured["system_prompt"] is None:
        raise AssertionError("dynamic_prompt did not set a system_message on the request")
    return captured["system_prompt"]


def test_dynamic_prompt_includes_persona_and_skill_addition():
    middleware = build_dynamic_prompt(
        _bot_config("You are the test bot."),
        [ClarificationSkill()],
    )
    text = _invoke_dynamic_prompt(middleware, state={"customer_id": None})
    assert "You are the test bot." in text
    # ClarificationSkill.system_prompt_addition() returns the calling-convention rule.
    assert "ask_clarification" in text


def test_dynamic_prompt_appends_customer_auth_when_signed_in():
    middleware = build_dynamic_prompt(_bot_config(), [ClarificationSkill()])
    text = _invoke_dynamic_prompt(middleware, state={"customer_id": "CUST001"})
    assert "CUST001" in text
    assert "do NOT ask the user for it" in text


def test_dynamic_prompt_omits_customer_line_when_anonymous():
    middleware = build_dynamic_prompt(_bot_config(), [ClarificationSkill()])
    text = _invoke_dynamic_prompt(middleware, state={"customer_id": None})
    assert "Authenticated customer" not in text


# --- 2. TokenUsageMiddleware ------------------------------------------------

def _ai_message_with_usage(input_tokens: int, output_tokens: int, cached: int = 0) -> AIMessage:
    return AIMessage(
        content="hello",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_token_details": {"cache_read": cached},
        },
    )


def test_token_usage_middleware_accumulates_into_state():
    m = TokenUsageMiddleware()
    state = {
        "messages": [_ai_message_with_usage(100, 50, cached=10)],
        "prompt_tokens_used": 5,         # prior totals
        "completion_tokens_used": 2,
        "cached_tokens_used": 1,
    }
    update = m.after_model(state, runtime=None)
    assert update is not None
    assert update["prompt_tokens_used"] == 105
    assert update["completion_tokens_used"] == 52
    assert update["cached_tokens_used"] == 11


def test_token_usage_middleware_skips_when_no_usage_metadata():
    m = TokenUsageMiddleware()
    state = {"messages": [AIMessage(content="x")]}
    assert m.after_model(state, runtime=None) is None


def test_token_usage_middleware_skips_when_last_is_not_ai():
    from langchain_core.messages import HumanMessage
    m = TokenUsageMiddleware()
    state = {"messages": [HumanMessage(content="x")]}
    assert m.after_model(state, runtime=None) is None


# --- 3. BudgetGuardMiddleware -----------------------------------------------

def test_budget_guard_under_cap_passes_through():
    store: dict[str, int] = {}
    m = BudgetGuardMiddleware(daily_cap=10_000, store=store)
    state = {"customer_id": "CUST_X", "prompt_tokens_used": 100, "completion_tokens_used": 50}
    update = m.before_model(state, runtime=None)
    assert update is None
    # Tally moved into the store.
    assert store["CUST_X"] == 150


def test_budget_guard_over_cap_short_circuits_with_jump_to_end():
    store = {"CUST_X": 9_999}
    m = BudgetGuardMiddleware(daily_cap=10_000, store=store)
    state = {"customer_id": "CUST_X", "prompt_tokens_used": 10_001, "completion_tokens_used": 0}
    update = m.before_model(state, runtime=None)
    assert update is not None
    assert update["jump_to"] == "__end__"
    msgs = update["messages"]
    assert len(msgs) == 1 and isinstance(msgs[0], AIMessage)
    assert "usage cap" in msgs[0].content.lower()


def test_budget_guard_ignores_anonymous_sessions():
    m = BudgetGuardMiddleware(daily_cap=10, store={})
    state = {"customer_id": None, "prompt_tokens_used": 10_000, "completion_tokens_used": 0}
    assert m.before_model(state, runtime=None) is None


def test_budget_guard_does_not_double_count_on_replay():
    """If a session is resumed, prior turn's tokens are already in state. We
    should only count the increment between state and our store, not the
    cumulative total."""
    store = {"CUST_X": 500}      # already recorded 500 from prior turn
    m = BudgetGuardMiddleware(daily_cap=10_000, store=store)
    state = {"customer_id": "CUST_X", "prompt_tokens_used": 500, "completion_tokens_used": 0}
    update = m.before_model(state, runtime=None)
    assert update is None
    # State and store agree → no increment.
    assert store["CUST_X"] == 500
