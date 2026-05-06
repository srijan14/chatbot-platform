"""SIM management — block lost/stolen SIM."""
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException

from src.telecom_api.db import get_conn
from src.telecom_api.models import BlockSimRequest, ActionResponse

router = APIRouter(prefix="/customers/{customer_id}/sim", tags=["sim"])


@router.post("/block")
def block_sim(customer_id: str, req: BlockSimRequest):
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with get_conn() as conn:
        cust = conn.execute(
            "SELECT status FROM customers WHERE customer_id = ?", (customer_id,)
        ).fetchone()
        if cust is None:
            raise HTTPException(404, f"Customer {customer_id} not found")
        if cust["status"] == "blocked":
            raise HTTPException(400, "SIM is already blocked")

        block_ref = "BLK-" + uuid.uuid4().hex[:10].upper()
        conn.execute(
            "UPDATE customers SET status = 'blocked' WHERE customer_id = ?", (customer_id,)
        )
        conn.execute(
            "INSERT INTO sim_events (event_id,customer_id,event_type,reason,created_at) "
            "VALUES (?,?,?,?,?)",
            (block_ref, customer_id, "block", req.reason, now_iso),
        )

    return ActionResponse(
        ok=True,
        message=f"SIM blocked ({req.reason}). For SIM replacement, visit any store with photo ID.",
        details={"block_reference": block_ref, "blocked_at": now_iso},
    ).model_dump()
