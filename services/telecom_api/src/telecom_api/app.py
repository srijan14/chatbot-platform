"""Mock telecom internal REST API. Stands in for the 'Internal APIs (Bot 4)' box."""
import os

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI  # noqa: E402

from telecom_api.routes import (  # noqa: E402
    accounts, plans, billing, usage, recharge, addons, sim, network, complaints,
)
from telecom_api.seed import ensure_seeded  # noqa: E402

app = FastAPI(title="Mock Telecom API", version="0.1.0")


@app.on_event("startup")
def _seed_if_empty() -> None:
    if ensure_seeded():
        print("[telecom_api] seeded empty database on startup")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "telecom_api"}


app.include_router(accounts.router)
app.include_router(plans.router)
app.include_router(billing.router)
app.include_router(usage.router)
app.include_router(recharge.router)
app.include_router(addons.router)
app.include_router(sim.router)
app.include_router(network.router)
app.include_router(complaints.router)


def run() -> None:
    import uvicorn
    uvicorn.run(
        "telecom_api.app:app",
        host=os.getenv("TELECOM_API_HOST", "127.0.0.1"),
        port=int(os.getenv("TELECOM_API_PORT", "8001")),
        reload=os.getenv("TELECOM_API_RELOAD", "0") == "1",
    )
