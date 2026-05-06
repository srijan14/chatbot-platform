"""Prepaid recharge endpoint."""
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException

from src.telecom_api.db import get_conn
from src.telecom_api.models import RechargeRequest, ActionResponse

router = APIRouter(prefix="/customers/{customer_id}", tags=["recharge"])


@router.post("/recharge")
def recharge(customer_id: str, req: RechargeRequest):
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with get_conn() as conn:
        cust = conn.execute(
            "SELECT account_type, prepaid_balance FROM customers WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
        if cust is None:
            raise HTTPException(404, f"Customer {customer_id} not found")
        if cust["account_type"] != "prepaid":
            raise HTTPException(400, "Recharge is only available for prepaid accounts")

        new_balance = round(cust["prepaid_balance"] + req.amount, 2)
        txn_id = "TXN-" + uuid.uuid4().hex[:10].upper()
        conn.execute(
            "UPDATE customers SET prepaid_balance = ? WHERE customer_id = ?",
            (new_balance, customer_id),
        )
        conn.execute(
            "INSERT INTO transactions (txn_id,customer_id,type,amount,reference_id,payment_method,status,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (txn_id, customer_id, "recharge", req.amount, None, req.payment_method, "success", now_iso),
        )

    return ActionResponse(
        ok=True,
        message=f"Recharged ₹{req.amount} via {req.payment_method}.",
        details={"transaction_id": txn_id, "new_balance": new_balance},
    ).model_dump()
