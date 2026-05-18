"""RAG Skill — placeholder. Proves the slot exists; not implemented in POC."""
from src.chatbot.skills.base import Skill, ToolResult


class RagSkill(Skill):
    name = "rag"

    async def prepare_tools(self) -> list[dict]:
        raise NotImplementedError("RAG skill is not implemented in the POC.")

    async def execute_tool(self, name: str, arguments: dict) -> ToolResult:
        raise NotImplementedError("RAG skill is not implemented in the POC.")

    def owns_tool(self, name: str) -> bool:
        return False
