"""Complaint / ticket endpoints."""
import uuid
from typing import Optional
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException

from src.telecom_api.db import get_conn, rows_to_list, row_to_dict
from src.telecom_api.models import FileComplaintRequest, ActionResponse

router = APIRouter(prefix="/customers/{customer_id}/complaints", tags=["complaints"])


@router.post("")
def file_complaint(customer_id: str, req: FileComplaintRequest):
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    sla_by_category = {"billing": 24, "network": 48, "service": 48, "other": 72}
    sla = sla_by_category.get(req.category, 48)

    with get_conn() as conn:
        cust = conn.execute(
            "SELECT 1 FROM customers WHERE customer_id = ?", (customer_id,)
        ).fetchone()
        if cust is None:
            raise HTTPException(404, f"Customer {customer_id} not found")

        ticket_id = "TKT-" + datetime.now(timezone.utc).strftime("%Y") + "-" + uuid.uuid4().hex[:6].upper()
        conn.execute(
            "INSERT INTO complaints (ticket_id,customer_id,category,description,status,sla_hours,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (ticket_id, customer_id, req.category, req.description, "open", sla, now_iso, now_iso),
        )

    return ActionResponse(
        ok=True,
        message=f"Complaint filed. Ticket {ticket_id}, SLA {sla}h.",
        details={"ticket_id": ticket_id, "sla_hours": sla, "status": "open"},
    ).model_dump()


@router.get("")
def list_complaints(customer_id: str, ticket_id: Optional[str] = None) -> list[dict]:
    with get_conn() as conn:
        if ticket_id:
            rows = conn.execute(
                "SELECT * FROM complaints WHERE customer_id = ? AND ticket_id = ?",
                (customer_id, ticket_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM complaints WHERE customer_id = ? ORDER BY created_at DESC LIMIT 5",
                (customer_id,),
            ).fetchall()
    return rows_to_list(rows)
