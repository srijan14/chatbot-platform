"""Billing endpoints — list bills, pay bill."""
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException

from src.telecom_api.db import get_conn, rows_to_list, row_to_dict
from src.telecom_api.models import PayBillRequest, ActionResponse

router = APIRouter(prefix="/customers/{customer_id}", tags=["billing"])


@router.get("/bills")
def list_bills(customer_id: str, limit: int = 3) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT bill_id, amount, issue_date, due_date, status, paid_at "
            "FROM bills WHERE customer_id = ? ORDER BY issue_date DESC LIMIT ?",
            (customer_id, max(1, min(limit, 24))),
        ).fetchall()
    return rows_to_list(rows)


@router.post("/bills/{bill_id}/pay")
def pay_bill(customer_id: str, bill_id: str, req: PayBillRequest):
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with get_conn() as conn:
        bill = conn.execute(
            "SELECT * FROM bills WHERE bill_id = ? AND customer_id = ?",
            (bill_id, customer_id),
        ).fetchone()
        if bill is None:
            raise HTTPException(404, f"Bill {bill_id} not found for {customer_id}")
        if bill["status"] == "paid":
            raise HTTPException(400, "Bill is already paid")

        receipt_id = "RCPT-" + uuid.uuid4().hex[:10].upper()
        conn.execute(
            "UPDATE bills SET status = 'paid', paid_at = ? WHERE bill_id = ?",
            (now_iso, bill_id),
        )
        conn.execute(
            "INSERT INTO transactions (txn_id,customer_id,type,amount,reference_id,payment_method,status,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (receipt_id, customer_id, "bill_payment", bill["amount"], bill_id, req.payment_method, "success", now_iso),
        )

        # if customer was suspended due to overdue, reactivate
        any_overdue = conn.execute(
            "SELECT 1 FROM bills WHERE customer_id = ? AND status = 'overdue'", (customer_id,)
        ).fetchone()
        if not any_overdue:
            conn.execute(
                "UPDATE customers SET status = 'active' WHERE customer_id = ? AND status = 'suspended'",
                (customer_id,),
            )

    return ActionResponse(
        ok=True,
        message=f"Bill {bill_id} paid via {req.payment_method}.",
        details={"receipt_id": receipt_id, "amount": bill["amount"], "paid_at": now_iso},
    ).model_dump()
