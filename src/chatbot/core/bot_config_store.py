"""Bot Config Store — loads YAML bot configs into typed BotConfig objects.

Maps to the 'Bot Config Store (YAML/JSON per bot type)' box in the architecture.
"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


# Matches o1, o1-mini, o3-mini-prod, my-o4-mini, etc. Misses opaque names like
# 'production-bot' — for those, set llm.reasoning: true in the YAML.
_REASONING_PATTERN = re.compile(r"(?:^|[-_/])o[1-9](?:[-_/]|$)")


def is_reasoning_deployment(name: str) -> bool:
    """Heuristic: detect OpenAI 'o-series' reasoning models from deployment name.

    Reasoning models (o1, o3, o4-mini, etc.) use `max_completion_tokens` instead
    of `max_tokens` and reject custom `temperature` values.
    """
    return bool(_REASONING_PATTERN.search(name.lower()))


@dataclass
class MCPServerRef:
    name: str
    transport: str
    url: str


@dataclass
class ClarificationConfig:
    """Per-bot config for the synthetic `ask_clarification` tool.

    `expected_types` constrains the `expected` parameter to an enum (e.g.
    telecom uses plan_id|bill_id|...; a BI bot might use metric|dimension|...).
    When empty the field is left unconstrained so generic bots can fill it
    however they like.
    """
    expected_types: list[str] = field(default_factory=list)
    description: str | None = None
    max_suggested_replies: int = 4


@dataclass
class BotConfig:
    bot_id: str
    name: str
    description: str
    # llm
    llm_provider: str          # 'azure_openai'
    llm_deployment: str        # Azure deployment name (passed as `model=` to the SDK)
    llm_reasoning: bool        # True for o-series (o1/o3/o4-mini): swaps param names
    max_tokens: int
    temperature: float
    max_tool_iterations: int
    # persona
    system_prompt: str
    # skills
    enabled_skills: list[str]
    # tool_call
    mcp_servers: list[MCPServerRef]
    tool_allowlist: list[str]
    # clarification (optional; only used if 'clarification' is in enabled_skills)
    clarification_expected_values: list[str] | None
    # guardrails
    max_input_chars: int
    pii_redaction_in_logs: bool
    # observability
    log_level: str = "info"
    log_format: str = "json"
    # clarification skill (always wired by the router; config is optional)
    clarification: ClarificationConfig = field(default_factory=ClarificationConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "BotConfig":
        data = yaml.safe_load(Path(path).read_text())
        llm = data["llm"]
        # Resolution order: AZURE_OPENAI_DEPLOYMENT env var > yaml `deployment` > legacy `model`.
        # The env override lets dev/staging/prod use different Azure deployments
        # without editing the YAML. Empty env values are ignored.
        deployment = (
            os.getenv("AZURE_OPENAI_DEPLOYMENT")
            or llm.get("deployment")
            or llm.get("model")
        )
        if not deployment:
            raise ValueError(f"{path}: llm.deployment is required")

        # Reasoning model detection: explicit YAML wins; otherwise auto-detect
        # from deployment name (catches o1/o3/o4-mini variants).
        explicit_reasoning = llm.get("reasoning")
        if explicit_reasoning is None:
            is_reasoning = is_reasoning_deployment(deployment)
        else:
            is_reasoning = bool(explicit_reasoning)

        tool_call = data.get("tool_call") or {}
        clarification = data.get("clarification") or {}
        guardrails = data.get("guardrails") or {}
        observability = data.get("observability") or {}
        clarification_raw = data.get("clarification") or {}
        servers = [MCPServerRef(**s) for s in tool_call.get("mcp_servers", [])]
        clarification = ClarificationConfig(
            expected_types=list(clarification_raw.get("expected_types") or []),
            description=clarification_raw.get("description"),
            max_suggested_replies=int(clarification_raw.get("max_suggested_replies", 4)),
        )
        return cls(
            bot_id=data["bot_id"],
            name=data["name"],
            description=data.get("description", ""),
            llm_provider=llm["provider"],
            llm_deployment=deployment,
            llm_reasoning=is_reasoning,
            max_tokens=llm.get("max_tokens", 1024),
            temperature=llm.get("temperature", 0.2),
            max_tool_iterations=llm.get("max_tool_iterations", 6),
            system_prompt=data["persona"]["system_prompt"],
            enabled_skills=list(data.get("skills", {}).get("enabled", [])),
            mcp_servers=servers,
            tool_allowlist=list(tool_call.get("tool_allowlist") or []),
            clarification_expected_values=expected_values,
            max_input_chars=guardrails.get("max_input_chars", 2000),
            pii_redaction_in_logs=guardrails.get("pii_redaction_in_logs", True),
            log_level=observability.get("log_level", "info"),
            log_format=observability.get("log_format", "json"),
            clarification=clarification,
        )


_cache: dict[str, BotConfig] = {}


def load_bot_config(bot_id: str, configs_dir: str | Path = "configs/bots") -> BotConfig:
    if bot_id in _cache:
        return _cache[bot_id]
    path = Path(configs_dir) / f"{bot_id}.yaml"
    cfg = BotConfig.from_yaml(path)
    _cache[bot_id] = cfg
    return cfg
