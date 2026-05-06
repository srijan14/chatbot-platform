"""Bot Config Store — loads YAML bot configs into typed BotConfig objects.

Maps to the 'Bot Config Store (YAML/JSON per bot type)' box in the architecture.
"""
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class MCPServerRef:
    name: str
    transport: str
    url: str


@dataclass
class BotConfig:
    bot_id: str
    name: str
    description: str
    # llm
    llm_provider: str          # 'azure_openai'
    llm_deployment: str        # Azure deployment name (passed as `model=` to the SDK)
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
    # guardrails
    max_input_chars: int
    pii_redaction_in_logs: bool
    # observability
    log_level: str = "info"
    log_format: str = "json"

    @classmethod
    def from_yaml(cls, path: str | Path) -> "BotConfig":
        data = yaml.safe_load(Path(path).read_text())
        llm = data["llm"]
        # Accept both `deployment` (preferred for Azure OpenAI) and legacy `model`.
        deployment = llm.get("deployment") or llm.get("model")
        if not deployment:
            raise ValueError(f"{path}: llm.deployment is required")
        tool_call = data.get("tool_call") or {}
        guardrails = data.get("guardrails") or {}
        observability = data.get("observability") or {}
        servers = [MCPServerRef(**s) for s in tool_call.get("mcp_servers", [])]
        return cls(
            bot_id=data["bot_id"],
            name=data["name"],
            description=data.get("description", ""),
            llm_provider=llm["provider"],
            llm_deployment=deployment,
            max_tokens=llm.get("max_tokens", 1024),
            temperature=llm.get("temperature", 0.2),
            max_tool_iterations=llm.get("max_tool_iterations", 6),
            system_prompt=data["persona"]["system_prompt"],
            enabled_skills=list(data.get("skills", {}).get("enabled", [])),
            mcp_servers=servers,
            tool_allowlist=list(tool_call.get("tool_allowlist") or []),
            max_input_chars=guardrails.get("max_input_chars", 2000),
            pii_redaction_in_logs=guardrails.get("pii_redaction_in_logs", True),
            log_level=observability.get("log_level", "info"),
            log_format=observability.get("log_format", "json"),
        )


_cache: dict[str, BotConfig] = {}


def load_bot_config(bot_id: str, configs_dir: str | Path = "configs/bots") -> BotConfig:
    if bot_id in _cache:
        return _cache[bot_id]
    path = Path(configs_dir) / f"{bot_id}.yaml"
    cfg = BotConfig.from_yaml(path)
    _cache[bot_id] = cfg
    return cfg
