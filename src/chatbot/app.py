"""Chatbot service entry point. FastAPI app exposing /chat + the demo web page."""
import logging
import os
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Load .env BEFORE importing modules that read env at import time.
load_dotenv()

from fastapi import FastAPI            # noqa: E402
from fastapi.responses import FileResponse   # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: E402

from src.chatbot.api import chat as chat_api  # noqa: E402
from src.chatbot.core.conversation_manager import ConversationManager  # noqa: E402
from src.chatbot.core.langgraph_orchestrator import LangGraphOrchestrator  # noqa: E402
from src.chatbot.persistence.db import create_engine_and_sessionmaker, init_schema  # noqa: E402
from src.chatbot.router.bot_router import BotRouter  # noqa: E402

STATIC_DIR = Path(__file__).parent / "static"
CHECKPOINT_DB = os.getenv("CHATBOT_CHECKPOINT_DB", "data/chatbot_checkpoints.db")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure the checkpoint DB's directory exists before SQLite opens it.
    Path(CHECKPOINT_DB).parent.mkdir(parents=True, exist_ok=True)

    async with AsyncExitStack() as stack:
        # LangGraph's AsyncSqliteSaver owns per-session conversation state
        # (the agent's `messages` list). It's an async context manager so we
        # let an AsyncExitStack drive its lifecycle alongside the SQLAlchemy
        # engine for our analytics tables.
        checkpointer = await stack.enter_async_context(
            AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB)
        )

        # Analytics DB (TurnLog rows + SessionRow metadata). Separate file
        # from the LangGraph checkpoint store so the two layers can evolve
        # independently.
        engine, sessionmaker = create_engine_and_sessionmaker()
        await init_schema(engine)
        stack.push_async_callback(engine.dispose)
        app.state.db_engine = engine
        app.state.db_sessionmaker = sessionmaker

        app.state.orchestrator = LangGraphOrchestrator(
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
            azure_api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
            azure_api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            checkpointer=checkpointer,
        )
        app.state.router = BotRouter()
        app.state.conversations = ConversationManager(sessionmaker)

        # Pre-warm the default bot's config and tool list — catches misconfig at
        # boot rather than at the first chat request. Tool fetch is best-effort:
        # if a downstream MCP server is down, log clearly and let the first chat
        # request retry, rather than refusing to start the chatbot at all.
        for bot_id in ("telecom_support", "bi_assistant"):
            try:
                bot_config = app.state.router.get_config(bot_id)
            except FileNotFoundError:
                # Bot config not on disk yet (bi_assistant lands in a later
                # commit). Skip pre-warm; first request will surface the error.
                continue
            skills = app.state.router.get_skills(bot_id)
            for skill in skills:
                try:
                    await skill.prepare_tools()
                except Exception as exc:
                    logging.getLogger("chatbot.startup").warning(
                        "skill %s prepare_tools failed at boot for %s (%s: %s); "
                        "will retry on first chat request.",
                        getattr(skill, "name", type(skill).__name__),
                        bot_id, type(exc).__name__, exc,
                    )
            try:
                await app.state.orchestrator.get_or_build_graph(bot_config, skills)
            except Exception as exc:
                logging.getLogger("chatbot.startup").warning(
                    "build_graph_for_bot failed for %s (%s: %s); "
                    "will retry on first chat request.",
                    bot_id, type(exc).__name__, exc,
                )

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
