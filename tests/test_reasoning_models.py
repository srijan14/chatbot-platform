"""Verify LangGraphOrchestrator._build_llm builds the right AzureChatOpenAI
config for reasoning vs non-reasoning bots.

Reasoning models (o1/o3/o4-mini, gpt-5, etc.) reject any temperature other
than the server default (1.0). Non-reasoning models accept any temperature.
The orchestrator must omit `temperature` for reasoning bots and pass it
through for the rest — this test pins that behaviour.
"""
from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver

from src.chatbot.core.bot_config_store import BotConfig, is_reasoning_deployment
from src.chatbot.core.langgraph_orchestrator import LangGraphOrchestrator


def _orch() -> LangGraphOrchestrator:
    return LangGraphOrchestrator(
        azure_endpoint="https://fake.openai.azure.com",
        azure_api_key="dummy",
        azure_api_version="2024-10-21",
        checkpointer=InMemorySaver(),
    )


def _cfg(deployment: str, reasoning: bool, temperature: float = 0.2) -> BotConfig:
    return BotConfig(
        bot_id="t", name="t", description="",
        llm_provider="azure_openai", llm_deployment=deployment,
        llm_reasoning=reasoning, max_tokens=128, temperature=temperature,
        max_tool_iterations=4, system_prompt="x",
        enabled_skills=[], mcp_servers=[], tool_allowlist=[],
        max_input_chars=2000, pii_redaction_in_logs=True,
    )


def test_non_reasoning_model_passes_temperature():
    llm = _orch()._build_llm(_cfg("gpt-4o", reasoning=False, temperature=0.25))
    assert llm.temperature == 0.25
    assert llm.max_tokens == 128


def test_reasoning_model_omits_temperature():
    """o3-mini and gpt-5 reject custom temperature; the orchestrator must
    NOT pass it, so AzureChatOpenAI leaves it unset (Azure uses 1.0)."""
    llm = _orch()._build_llm(_cfg("o3-mini", reasoning=True, temperature=0.25))
    # When not passed, AzureChatOpenAI's `temperature` attribute is None,
    # which means it isn't sent on the request.
    assert llm.temperature is None
    assert llm.max_tokens == 128


def test_reasoning_autodetect_o_series():
    assert is_reasoning_deployment("o1")
    assert is_reasoning_deployment("o1-mini")
    assert is_reasoning_deployment("o3-mini")
    assert is_reasoning_deployment("o4-mini")
    assert is_reasoning_deployment("my-o3-mini-prod")


def test_reasoning_autodetect_gpt5():
    assert is_reasoning_deployment("gpt-5")
    assert is_reasoning_deployment("gpt-5-mini")
    assert is_reasoning_deployment("gpt-5o")
    assert is_reasoning_deployment("gpt-6-thinking")


def test_reasoning_autodetect_misses_classic_models():
    assert not is_reasoning_deployment("gpt-4o")
    assert not is_reasoning_deployment("gpt-4o-mini")
    assert not is_reasoning_deployment("gpt-4-turbo")
    # Opaque deployment names require explicit YAML `llm.reasoning: true`.
    assert not is_reasoning_deployment("production-bot")
