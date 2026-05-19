"""Bot Router — picks a bot config and assembles its enabled skills.

Returns an ordered list of `Skill` instances. The orchestrator iterates
skills to gather tool definitions and to dispatch tool calls by ownership.

Skills are opt-in via `skills.enabled` in the bot YAML — the platform makes
no assumption about which skills any given bot wants. This keeps clarification,
tool_call, RAG, TAG, etc. on the same footing.
"""
import os

from src.chatbot.core.bot_config_store import BotConfig, load_bot_config
from src.chatbot.engines.tool_engine.mcp_client import MCPClient
from src.chatbot.skills.base import Skill
from src.chatbot.skills.clarification_skill import ClarificationSkill
from src.chatbot.skills.tool_call_skill import ToolCallSkill


class BotRouter:
    def __init__(self):
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
    don't load for bots that don't enable TAG."""
    from llama_index.embeddings.azure_openai import AzureOpenAIEmbedding

    from src.chatbot.engines.tag_engine.index_builder import build_tag_index
    from src.chatbot.engines.tag_engine.pipeline import TagConfig, TagPipeline
    from src.chatbot.engines.tag_engine.semantic_layer import SemanticLayer
    from src.chatbot.engines.tag_engine.summarizer import make_summarizer
    from src.chatbot.skills.tag_skill import TagSkill
    from langchain_openai import AzureChatOpenAI

    spec = cfg.tag
    assert spec is not None  # guarded by caller

    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    azure_api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
    azure_api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
    embed_deployment = os.getenv("AZURE_OPENAI_EMBED_DEPLOYMENT", "text-embedding-3-small")

    semantic_layer = SemanticLayer.from_yaml(spec.semantic_layer_path)

    # Azure embeddings for the ObjectIndex (per-table summaries).
    embed_model = AzureOpenAIEmbedding(
        model=embed_deployment,
        deployment_name=embed_deployment,
        azure_endpoint=azure_endpoint,
        api_key=azure_api_key,
        api_version=azure_api_version,
    )
    index = build_tag_index(semantic_layer, embed_model=embed_model)

    sql_gen_llm = AzureChatOpenAI(
        azure_endpoint=azure_endpoint,
        api_key=azure_api_key,
        api_version=azure_api_version,
        azure_deployment=spec.sql_gen_deployment,
        temperature=spec.sql_gen_temperature,
        max_tokens=spec.sql_gen_max_tokens,
    )
    summarizer_llm = make_summarizer(
        azure_endpoint=azure_endpoint,
        azure_api_key=azure_api_key,
        azure_api_version=azure_api_version,
        deployment=spec.summarizer_deployment,
        max_tokens=spec.summarizer_max_tokens,
        temperature=spec.summarizer_temperature,
    )

    pipeline = TagPipeline(
        index,
        sql_gen_llm=sql_gen_llm,
        summarizer_llm=summarizer_llm,
        config=TagConfig(
            semantic_layer_path=spec.semantic_layer_path,
            sql_gen_deployment=spec.sql_gen_deployment,
            sql_gen_temperature=spec.sql_gen_temperature,
            sql_gen_max_tokens=spec.sql_gen_max_tokens,
            summarizer_deployment=spec.summarizer_deployment,
            summarizer_temperature=spec.summarizer_temperature,
            summarizer_max_tokens=spec.summarizer_max_tokens,
            schema_top_k=spec.schema_top_k,
            row_limit=spec.row_limit,
            repair_max_attempts=spec.repair_max_attempts,
            query_timeout_seconds=spec.query_timeout_seconds,
        ),
    )
    return TagSkill(pipeline)
