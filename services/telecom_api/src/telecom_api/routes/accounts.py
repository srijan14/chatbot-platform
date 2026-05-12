"""Customer account endpoints."""
from fastapi import APIRouter, HTTPException

from telecom_api.db import get_conn, row_to_dict

router = APIRouter(prefix="/customers", tags=["accounts"])


@router.get("/{customer_id}")
def get_customer(customer_id: str) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT customer_id, name, phone, email, account_type, status, "
            "prepaid_balance, area_code, created_at FROM customers WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(404, f"Customer {customer_id} not found")
    return row_to_dict(row)
