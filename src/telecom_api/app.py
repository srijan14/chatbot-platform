"""Mock telecom internal REST API. Stands in for the 'Internal APIs (Bot 4)' box."""
from fastapi import FastAPI

from src.telecom_api.routes import accounts, plans, billing, usage, recharge, addons, sim, network, complaints

app = FastAPI(title="Mock Telecom API", version="0.1.0")


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
