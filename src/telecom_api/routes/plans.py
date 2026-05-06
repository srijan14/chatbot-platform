"""Plan catalog + plan change endpoints."""
from typing import Optional
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException

from src.telecom_api.db import get_conn, row_to_dict, rows_to_list
from src.telecom_api.models import ChangePlanRequest, PreviewResponse, ActionResponse

router = APIRouter(tags=["plans"])


@router.get("/plans")
def list_plans(category: Optional[str] = None) -> list[dict]:
    sql = "SELECT * FROM plans WHERE is_active = 1"
    params: tuple = ()
    if category:
        sql += " AND category = ?"
        params = (category,)
    sql += " ORDER BY monthly_fee"
    with get_conn() as conn:
        return rows_to_list(conn.execute(sql, params).fetchall())


@router.get("/customers/{customer_id}/plan")
def get_current_plan(customer_id: str) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT p.plan_id, p.name, p.category, p.monthly_fee, p.data_quota_gb,
                   p.voice_minutes, p.sms_quota,
                   s.start_date, s.expiry_date, s.auto_renew
            FROM subscriptions s
            JOIN plans p ON p.plan_id = s.plan_id
            WHERE s.customer_id = ?
            """,
            (customer_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(404, f"No active subscription for {customer_id}")
    return row_to_dict(row)


@router.post("/customers/{customer_id}/plan")
def change_plan(customer_id: str, req: ChangePlanRequest):
    with get_conn() as conn:
        new_plan = conn.execute(
            "SELECT * FROM plans WHERE plan_id = ? AND is_active = 1", (req.new_plan_id,)
        ).fetchone()
        if new_plan is None:
            raise HTTPException(404, f"Plan {req.new_plan_id} not found")

        current_sub = conn.execute(
            "SELECT s.plan_id, s.expiry_date, p.monthly_fee, p.name "
            "FROM subscriptions s JOIN plans p ON p.plan_id = s.plan_id "
            "WHERE s.customer_id = ?",
            (customer_id,),
        ).fetchone()
        if current_sub is None:
            raise HTTPException(404, f"Customer {customer_id} has no subscription")

        if current_sub["plan_id"] == req.new_plan_id:
            raise HTTPException(400, "Customer is already on this plan")

        # naive proration: difference in monthly fee, prorated by days remaining
        try:
            expiry = datetime.fromisoformat(current_sub["expiry_date"])
            days_remaining = max(0, (expiry - datetime.now(timezone.utc)).days)
        except Exception:
            days_remaining = 30
        proration = round((new_plan["monthly_fee"] - current_sub["monthly_fee"]) * days_remaining / 30, 2)

        if not req.confirm:
            return PreviewResponse(
                summary=(
                    f"Switch from '{current_sub['name']}' (₹{current_sub['monthly_fee']}/mo) "
                    f"to '{new_plan['name']}' (₹{new_plan['monthly_fee']}/mo). "
                    f"Estimated proration: ₹{proration} for {days_remaining} day(s) remaining."
                ),
                details={
                    "current_plan_id": current_sub["plan_id"],
                    "new_plan_id": new_plan["plan_id"],
                    "current_fee": current_sub["monthly_fee"],
                    "new_fee": new_plan["monthly_fee"],
                    "proration_amount": proration,
                    "days_remaining": days_remaining,
                },
            ).model_dump()

        new_expiry = (datetime.now(timezone.utc) + timedelta(days=30)).replace(microsecond=0).isoformat()
        conn.execute(
            "UPDATE subscriptions SET plan_id = ?, expiry_date = ? WHERE customer_id = ?",
            (req.new_plan_id, new_expiry, customer_id),
        )

    return ActionResponse(
        ok=True,
        message=f"Plan changed to '{new_plan['name']}'.",
        details={"new_plan_id": req.new_plan_id, "new_expiry": new_expiry, "proration_amount": proration},
    ).model_dump()
