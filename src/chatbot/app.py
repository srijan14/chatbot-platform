"""Chatbot service entry point. FastAPI app exposing /chat + the demo web page."""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Load .env BEFORE importing modules that read env at import time.
load_dotenv()

from openai import AsyncAzureOpenAI  # noqa: E402
from fastapi import FastAPI            # noqa: E402
from fastapi.responses import FileResponse   # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from src.chatbot.api import chat as chat_api  # noqa: E402
from src.chatbot.core.conversation_manager import ConversationManager  # noqa: E402
from src.chatbot.core.llm_orchestrator import LLMOrchestrator  # noqa: E402
from src.chatbot.router.bot_router import BotRouter  # noqa: E402

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # AsyncAzureOpenAI auto-reads AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT
    # from the environment. We pass api_version explicitly because our .env names
    # it AZURE_OPENAI_API_VERSION (the SDK's auto-pickup name is OPENAI_API_VERSION).
    app.state.llm_client = AsyncAzureOpenAI(
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
    )
    app.state.orchestrator = LLMOrchestrator(app.state.llm_client)
    app.state.router = BotRouter()
    app.state.conversations = ConversationManager()

    # Pre-warm the default bot's config and tool list — catches misconfig at boot
    # rather than at the first chat request.
    app.state.router.get_config("telecom_support")
    skill = app.state.router.get_tool_call_skill("telecom_support")
    await skill.prepare_tools()
    yield


app = FastAPI(title="Chatbot Platform — Telecom POC", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "chatbot"}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(chat_api.router)
