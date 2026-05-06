"""Bot Router — picks a bot config and assembles its enabled skills.

For the POC there is one bot (telecom_support) and one skill (tool_call). The
router exists to prove the architectural slot.
"""
from src.chatbot.core.bot_config_store import BotConfig, load_bot_config
from src.chatbot.engines.tool_engine.mcp_client import MCPClient
from src.chatbot.skills.tool_call_skill import ToolCallSkill


class BotRouter:
    def __init__(self):
        self._configs: dict[str, BotConfig] = {}
        self._skills: dict[str, ToolCallSkill] = {}

    def get_config(self, bot_id: str) -> BotConfig:
        if bot_id not in self._configs:
            self._configs[bot_id] = load_bot_config(bot_id)
        return self._configs[bot_id]

    def get_tool_call_skill(self, bot_id: str) -> ToolCallSkill:
        if bot_id in self._skills:
            return self._skills[bot_id]
        cfg = self.get_config(bot_id)
        if "tool_call" not in cfg.enabled_skills:
            raise RuntimeError(f"Bot '{bot_id}' does not have tool_call enabled.")
        if not cfg.mcp_servers:
            raise RuntimeError(f"Bot '{bot_id}' tool_call has no mcp_servers configured.")
        client = MCPClient(cfg.mcp_servers[0].url)
        skill = ToolCallSkill(client, tool_allowlist=cfg.tool_allowlist)
        self._skills[bot_id] = skill
        return skill
