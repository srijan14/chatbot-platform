"""Addon catalog + purchase endpoints."""
import uuid
from typing import Optional
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException

from telecom_api.db import get_conn, rows_to_list, row_to_dict
from telecom_api.models import PurchaseAddonRequest, PreviewResponse, ActionResponse

router = APIRouter(tags=["addons"])


@router.get("/addons")
def list_addons(category: Optional[str] = None) -> list[dict]:
    sql = "SELECT * FROM addons"
    params: tuple = ()
    if category:
        sql += " WHERE category = ?"
        params = (category,)
    sql += " ORDER BY price"
    with get_conn() as conn:
        return rows_to_list(conn.execute(sql, params).fetchall())


@router.post("/customers/{customer_id}/addons")
def purchase_addon(customer_id: str, req: PurchaseAddonRequest):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with get_conn() as conn:
        cust = conn.execute(
            "SELECT 1 FROM customers WHERE customer_id = ?", (customer_id,)
        ).fetchone()
        if cust is None:
            raise HTTPException(404, f"Customer {customer_id} not found")

        addon = conn.execute(
            "SELECT * FROM addons WHERE addon_id = ?", (req.addon_id,)
        ).fetchone()
        if addon is None:
            raise HTTPException(404, f"Addon {req.addon_id} not found")

        if not req.confirm:
            return PreviewResponse(
                summary=(
                    f"Purchase '{addon['name']}' (₹{addon['price']}, valid {addon['validity_days']} days). "
                    f"Confirm to proceed."
                ),
                details={
                    "addon_id": addon["addon_id"],
                    "name": addon["name"],
                    "price": addon["price"],
                    "validity_days": addon["validity_days"],
                    "description": addon["description"],
                },
            ).model_dump()

        expires = (now + timedelta(days=addon["validity_days"])).isoformat()
        cur = conn.execute(
            "INSERT INTO customer_addons (customer_id,addon_id,purchased_at,expires_at,status) "
            "VALUES (?,?,?,?,'active')",
            (customer_id, req.addon_id, now.isoformat(), expires),
        )
        receipt = "RCPT-" + uuid.uuid4().hex[:10].upper()
        conn.execute(
            "INSERT INTO transactions (txn_id,customer_id,type,amount,reference_id,payment_method,status,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (receipt, customer_id, "addon_purchase", addon["price"], addon["addon_id"], "wallet", "success", now.isoformat()),
        )

    return ActionResponse(
        ok=True,
        message=f"Purchased '{addon['name']}' for ₹{addon['price']}.",
        details={"customer_addon_id": cur.lastrowid, "expires_at": expires, "receipt_id": receipt},
    ).model_dump()


@router.get("/customers/{customer_id}/addons")
def list_customer_addons(customer_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT ca.id, ca.addon_id, a.name, a.category, a.price, ca.purchased_at,
                   ca.expires_at, ca.status
            FROM customer_addons ca
            JOIN addons a ON a.addon_id = ca.addon_id
            WHERE ca.customer_id = ?
            ORDER BY ca.purchased_at DESC
            """,
            (customer_id,),
        ).fetchall()
    return rows_to_list(rows)
