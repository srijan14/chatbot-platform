"""TAG/SQL Skill — placeholder. Proves the slot exists; not implemented in POC."""
from src.chatbot.skills.base import Skill


class TagSkill(Skill):
    name = "tag"

    async def prepare_tools(self) -> list[dict]:
        raise NotImplementedError("TAG skill is not implemented in the POC.")

    async def execute_tool(self, name: str, arguments: dict) -> tuple[str, bool]:
        raise NotImplementedError("TAG skill is not implemented in the POC.")

    def owns_tool(self, name: str) -> bool:
        return False
