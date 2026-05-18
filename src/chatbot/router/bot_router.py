"""Bot Router — picks a bot config and assembles its enabled skills.

Returns an ordered list of `Skill` instances. The orchestrator iterates skills
to gather tool definitions and to dispatch tool calls by ownership.
"""
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

        self._skills[bot_id] = skills
        return skills
