"""Bot Router — picks a bot config and assembles its enabled skills.

Returns an ordered list of `Skill` instances. The orchestrator iterates
skills to gather tool definitions and to dispatch tool calls by ownership.

Skills are opt-in via `skills.enabled` in the bot YAML — the platform makes
no assumption about which skills any given bot wants. This keeps clarification,
tool_call, RAG, TAG, etc. on the same footing.
"""
import logging
import os

from src.chatbot.core.bot_config_store import (
    BotConfig,
    is_reasoning_deployment,
    load_bot_config,
)
from src.chatbot.engines.tool_engine.mcp_client import MCPClient
from src.chatbot.skills.base import Skill
from src.chatbot.skills.clarification_skill import ClarificationSkill
from src.chatbot.skills.rag_skill import RagSkill
from src.chatbot.skills.tool_call_skill import ToolCallSkill

_log = logging.getLogger("chatbot.router")


class BotRouter:
    def __init__(self, rag_engine=None):
        # `rag_engine` is the in-process RagEngine (rag_engine.RagEngine), built
        # once in the app lifespan and shared across all bots. Optional so tests
        # that don't exercise RAG can construct a bare router.
        self._rag_engine = rag_engine
        self._configs: dict[str, BotConfig] = {}
        self._skills: dict[str, list[Skill]] = {}

    def get_config(self, bot_id: str) -> BotConfig:
        if bot_id not in self._configs:
            self._configs[bot_id] = load_bot_config(bot_id)
        return self._configs[bot_id]

    def get_skills(self, bot_id: str) -> list[Skill]:
        if bot_id in self._skills:
            return self._skills[bot_id]

        cfg = self.get_config(bot_id)
        skills: list[Skill] = []

        # Clarification is always available — it's how the bot signals it needs
        # more info regardless of which domain skills are enabled. Schema (the
        # expected-reply enum, description, suggested-reply cap) comes from the
        # bot's `clarification:` YAML block so no domain leaks into the skill.
        clar_cfg = cfg.clarification
        skills.append(ClarificationSkill(
            expected_types=clar_cfg.expected_types or None,
            description=clar_cfg.description,
            max_suggested_replies=clar_cfg.max_suggested_replies,
        ))

        if "tool_call" in cfg.enabled_skills:
            if not cfg.mcp_servers:
                raise RuntimeError(
                    f"Bot '{bot_id}' tool_call has no mcp_servers configured."
                )
            client = MCPClient(cfg.mcp_servers[0].url)
            skills.append(ToolCallSkill(client, tool_allowlist=cfg.tool_allowlist))

        if "rag" in cfg.enabled_skills:
            # Config error (developer mistake) — fail loud.
            if not cfg.rag.collection:
                raise RuntimeError(
                    f"Bot '{bot_id}' rag requires `rag.collection`."
                )
            # Infra unavailable (e.g. RAG engine failed to build at boot) —
            # degrade gracefully: keep tool_call/clarification working, just
            # don't offer RAG, rather than bricking the whole bot.
            if self._rag_engine is None:
                _log.warning(
                    "Bot '%s' enables rag but no rag_engine is available; "
                    "skipping the RAG skill (other skills still load).", bot_id,
                )
            else:
                # tenant == bot_id: each bot is isolated to {bot_id}__{collection}.
                skills.append(
                    RagSkill(
                        self._rag_engine,
                        tenant_id=bot_id,
                        collection=cfg.rag.collection,
                        top_k=cfg.rag.top_k,
                        search_instructions=cfg.rag.search_instructions,
                    )
                )
        if "tag" in cfg.enabled_skills:
            if cfg.tag is None:
                raise RuntimeError(
                    f"Bot '{bot_id}' enables 'tag' but has no `tag:` config block."
                )
            skills.append(_build_tag_skill(cfg))

        self._skills[bot_id] = skills
        return skills


def _build_tag_skill(cfg: BotConfig):
    """Wire the TAG engine. Lazy-imported so the (heavier) LlamaIndex deps
    don't load for bots that don't enable TAG.

    Deployment resolution order (per stage):
      1. Stage-specific YAML override (`tag.sql_generator.deployment`,
         `tag.summarizer.deployment`, `tag.embed_deployment`)
      2. Stage-specific env var (AZURE_OPENAI_SQL_GEN_DEPLOYMENT,
         AZURE_OPENAI_SUMMARIZER_DEPLOYMENT, AZURE_OPENAI_EMBED_DEPLOYMENT)
      3. The bot's main `llm.deployment` (i.e. the same model the bot LLM uses)

    Reasoning models (o-series, gpt-5+) reject custom temperature; we honor
    `bot_config.llm_reasoning` (and re-check per-stage deployment name) and
    omit the temperature kwarg when the stage's deployment is reasoning.
    """
    from llama_index.core import Settings
    from llama_index.embeddings.azure_openai import AzureOpenAIEmbedding
    from llama_index.llms.azure_openai import AzureOpenAI as LIAzureOpenAI

    from src.chatbot.engines.tag_engine.index_builder import build_tag_index
    from src.chatbot.engines.tag_engine.pipeline import TagConfig, TagPipeline
    from src.chatbot.engines.tag_engine.semantic_layer import SemanticLayer
    from src.chatbot.engines.tag_engine.summarizer import make_summarizer
    from src.chatbot.skills.tag_skill import TagSkill

    spec = cfg.tag
    assert spec is not None  # guarded by caller

    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    azure_api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
    azure_api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")

    sql_gen_deployment = (
        os.getenv("AZURE_OPENAI_SQL_GEN_DEPLOYMENT")
        or spec.sql_gen_deployment
        or cfg.llm_deployment
    )
    summarizer_deployment = (
        os.getenv("AZURE_OPENAI_SUMMARIZER_DEPLOYMENT")
        or spec.summarizer_deployment
        or cfg.llm_deployment
    )
    # Embeddings deployment has NO chat-model fallback — chat and embedding
    # are different model families. If neither YAML nor env supplies one,
    # we skip ObjectIndex entirely and pass all tables to NLSQLRetriever
    # directly (no schema RAG). Fine for the demo warehouse; suboptimal
    # for huge schemas.
    embed_deployment = (
        os.getenv("AZURE_OPENAI_EMBED_DEPLOYMENT")
        or spec.embed_deployment
        # No 'or cfg.llm_deployment' — chat models can't do embeddings.
    )

    # Reasoning models reject custom temperature. We resolve per-stage —
    # the SQL gen and summarizer can in principle use different deployments.
    sql_gen_is_reasoning = is_reasoning_deployment(sql_gen_deployment)
    summarizer_is_reasoning = is_reasoning_deployment(summarizer_deployment)

    semantic_layer = SemanticLayer.from_yaml(spec.semantic_layer_path)

    embed_model = None
    if embed_deployment:
        embed_model = AzureOpenAIEmbedding(
            model=embed_deployment,
            deployment_name=embed_deployment,
            azure_endpoint=azure_endpoint,
            api_key=azure_api_key,
            api_version=azure_api_version,
        )

    # LlamaIndex AzureOpenAI for NLSQLRetriever's SQL generation.
    # LangChain's AzureChatOpenAI auto-converts `max_tokens` →
    # `max_completion_tokens` for o-series deployments, but LlamaIndex does
    # not — so we route the token cap through `additional_kwargs` when the
    # deployment is reasoning-class so the right parameter goes on the wire.
    sql_gen_kwargs: dict = {
        "engine": sql_gen_deployment,
        "model": sql_gen_deployment,
        "azure_endpoint": azure_endpoint,
        "api_key": azure_api_key,
        "api_version": azure_api_version,
    }
    if sql_gen_is_reasoning:
        sql_gen_kwargs["additional_kwargs"] = {
            "max_completion_tokens": spec.sql_gen_max_tokens
        }
        # LlamaIndex AzureOpenAI's default temperature is 0.1 — Azure rejects
        # any non-default temperature for o-series. Set to 1.0 explicitly so
        # the request matches what o-series accepts.
        sql_gen_kwargs["temperature"] = 1.0
    else:
        sql_gen_kwargs["max_tokens"] = spec.sql_gen_max_tokens
        sql_gen_kwargs["temperature"] = spec.sql_gen_temperature
    li_sql_gen_llm = LIAzureOpenAI(**sql_gen_kwargs)

    # Set the global LlamaIndex Settings so any auxiliary call inside the
    # retriever / object index that consults `Settings.llm` /
    # `Settings.embed_model` uses our explicit Azure clients instead of
    # falling back to the OpenAI default (which would fail without
    # OPENAI_API_KEY).
    Settings.llm = li_sql_gen_llm
    if embed_model is not None:
        Settings.embed_model = embed_model
    else:
        # Even in tables=[...] mode, parts of NLSQLRetriever/SQLDatabase init
        # touch Settings.embed_model and trigger LlamaIndex's lazy OpenAI
        # default, which 500s without OPENAI_API_KEY. Plug a no-network
        # MockEmbedding so the global is satisfied without phoning OpenAI.
        # Real embeddings aren't used in this code path (no ObjectIndex).
        from llama_index.core.embeddings.mock_embed_model import MockEmbedding
        Settings.embed_model = MockEmbedding(embed_dim=8)

    index = build_tag_index(
        semantic_layer,
        llm=li_sql_gen_llm,
        embed_model=embed_model,
        schema_top_k=spec.schema_top_k,
    )

    # LangChain AzureChatOpenAI for the prose summarizer.
    summarizer_llm = make_summarizer(
        azure_endpoint=azure_endpoint,
        azure_api_key=azure_api_key,
        azure_api_version=azure_api_version,
        deployment=summarizer_deployment,
        max_tokens=spec.summarizer_max_tokens,
        # None signals "skip temperature" in make_summarizer; the wrapper
        # below omits the kwarg when the deployment is reasoning-class.
        temperature=None if summarizer_is_reasoning else spec.summarizer_temperature,
    )

    pipeline = TagPipeline(
        index,
        summarizer_llm=summarizer_llm,
        config=TagConfig(
            semantic_layer_path=spec.semantic_layer_path,
            sql_gen_deployment=sql_gen_deployment,
            sql_gen_temperature=spec.sql_gen_temperature,
            sql_gen_max_tokens=spec.sql_gen_max_tokens,
            summarizer_deployment=summarizer_deployment,
            summarizer_temperature=spec.summarizer_temperature,
            summarizer_max_tokens=spec.summarizer_max_tokens,
            schema_top_k=spec.schema_top_k,
            row_limit=spec.row_limit,
            repair_max_attempts=spec.repair_max_attempts,
            query_timeout_seconds=spec.query_timeout_seconds,
        ),
    )
    return TagSkill(pipeline)
