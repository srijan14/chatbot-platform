"""Bot Config Store — loads YAML bot configs into typed BotConfig objects.

Maps to the 'Bot Config Store (YAML/JSON per bot type)' box in the architecture.
"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


# Matches o-series (o1, o3-mini, my-o4-mini, …) and gpt-5+ family
# (gpt-5, gpt-5-mini, gpt-5o, gpt-6-thinking, …) — both reject custom
# temperature and use max_completion_tokens. Misses opaque names like
# 'production-bot'; set llm.reasoning: true in the YAML for those.
_REASONING_PATTERN = re.compile(
    r"(?:^|[-_/])(o[1-9]|gpt-[5-9])(?:[-_/o]|$)",
    re.IGNORECASE,
)


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
class TagConfigSpec:
    """Per-bot config for the TAG skill (parsed; the engine reads it).

    Deployment fields default to None — meaning "use the bot's main
    `llm.deployment` for this stage". The router applies that fallback
    at skill-build time. This lets a bot reuse a single Azure deployment
    for everything (common when only one model is provisioned) while
    still allowing per-stage overrides for cost optimisation.
    """
    semantic_layer_path: str
    sql_gen_deployment: str | None = None       # falls back to bot.llm_deployment
    sql_gen_temperature: float = 0.0
    sql_gen_max_tokens: int = 512
    summarizer_deployment: str | None = None    # falls back to bot.llm_deployment
    summarizer_temperature: float = 0.2
    summarizer_max_tokens: int = 400
    embed_deployment: str | None = None         # falls back to env / sensible default
    schema_top_k: int = 4
    row_limit: int = 100
    repair_max_attempts: int = 3
    query_timeout_seconds: float = 2.0


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
class RagConfig:
    """Per-bot config for the in-process RAG skill.

    The platform owns RAG in-process (no MCP/REST hop): each bot gets its own
    vector-DB collection, scoped by tenant_id == bot_id (physical name
    `{bot_id}__{collection}`).

    `collection`  logical collection name (required when rag is enabled).
    `sources`     list of ingestion sources, each `{connector, config, metadata?}`
                  — e.g. {"connector": "file_loader",
                          "config": {"path": "...", "glob": "**/*.md"}}.
                  Ingested on startup (idempotent) and via the `rag-ingest` CLI.
    `embedding_model` / `dimensions`  pin the embedding space for the collection.
    `top_k`       default passages returned when the model omits it.
    """
    collection: str = ""
    sources: list[dict] = field(default_factory=list)
    embedding_model: str = "text-embedding-3-small"
    dimensions: int = 1536
    top_k: int = 5
    search_instructions: str | None = None


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
    # guardrails
    max_input_chars: int
    pii_redaction_in_logs: bool
    # observability
    log_level: str = "info"
    log_format: str = "json"
    # clarification skill (always wired by the router; config is optional)
    clarification: ClarificationConfig = field(default_factory=ClarificationConfig)
    # rag skill (wired only when "rag" is in enabled_skills)
    rag: RagConfig = field(default_factory=RagConfig)
    # tag skill (only required when 'tag' is in enabled_skills)
    tag: TagConfigSpec | None = None

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
        guardrails = data.get("guardrails") or {}
        observability = data.get("observability") or {}
        clarification_raw = data.get("clarification") or {}
        servers = [MCPServerRef(**s) for s in tool_call.get("mcp_servers", [])]
        # Accept both `expected_types` (current) and the legacy `expected_values`
        # so existing YAMLs keep working. Either spelling produces the same
        # enum on the tool schema.
        expected = (
            clarification_raw.get("expected_types")
            or clarification_raw.get("expected_values")
            or []
        )
        clarification = ClarificationConfig(
            expected_types=list(expected),
            description=clarification_raw.get("description"),
            max_suggested_replies=int(clarification_raw.get("max_suggested_replies", 4)),
        )
        rag_raw = data.get("rag") or {}
        rag_embedding = rag_raw.get("embedding") or {}
        rag = RagConfig(
            collection=rag_raw.get("collection", ""),
            sources=list(rag_raw.get("sources") or []),
            embedding_model=rag_embedding.get("model", "text-embedding-3-small"),
            dimensions=int(rag_embedding.get("dimensions", 1536)),
            top_k=int(rag_raw.get("top_k", 5)),
            search_instructions=rag_raw.get("search_instructions"),
        )

        tag_raw = data.get("tag") or {}
        tag_spec: TagConfigSpec | None = None
        if tag_raw:
            sql_gen = tag_raw.get("sql_generator") or {}
            summ = tag_raw.get("summarizer") or {}
            executor = tag_raw.get("executor") or {}
            tag_spec = TagConfigSpec(
                semantic_layer_path=tag_raw["semantic_layer_path"],
                sql_gen_deployment=sql_gen.get("deployment"),
                sql_gen_temperature=float(sql_gen.get("temperature", 0.0)),
                sql_gen_max_tokens=int(sql_gen.get("max_tokens", 512)),
                summarizer_deployment=summ.get("deployment"),
                summarizer_temperature=float(summ.get("temperature", 0.2)),
                summarizer_max_tokens=int(summ.get("max_tokens", 400)),
                embed_deployment=tag_raw.get("embed_deployment"),
                schema_top_k=int(tag_raw.get("schema_top_k", 4)),
                row_limit=int(executor.get("row_limit", 100)),
                repair_max_attempts=int(tag_raw.get("repair_max_attempts", 3)),
                query_timeout_seconds=float(executor.get("timeout_seconds", 2.0)),
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
            max_input_chars=guardrails.get("max_input_chars", 2000),
            pii_redaction_in_logs=guardrails.get("pii_redaction_in_logs", True),
            log_level=observability.get("log_level", "info"),
            log_format=observability.get("log_format", "json"),
            clarification=clarification,
            rag=rag,
            tag=tag_spec,
        )


_cache: dict[str, BotConfig] = {}


def load_bot_config(bot_id: str, configs_dir: str | Path = "configs/bots") -> BotConfig:
    if bot_id in _cache:
        return _cache[bot_id]
    path = Path(configs_dir) / f"{bot_id}.yaml"
    cfg = BotConfig.from_yaml(path)
    _cache[bot_id] = cfg
    return cfg
