"""Balance and usage endpoint."""
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException

from src.telecom_api.db import get_conn, row_to_dict

router = APIRouter(prefix="/customers/{customer_id}", tags=["usage"])


@router.get("/usage")
def get_balance_and_usage(customer_id: str) -> dict:
    with get_conn() as conn:
        cust = conn.execute(
            "SELECT account_type, prepaid_balance FROM customers WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
        if cust is None:
            raise HTTPException(404, f"Customer {customer_id} not found")

        usage = conn.execute(
            "SELECT * FROM usage_current WHERE customer_id = ?", (customer_id,)
        ).fetchone()

        plan = conn.execute(
            "SELECT p.plan_id, p.name, p.data_quota_gb, p.voice_minutes, p.sms_quota, "
            "       s.expiry_date "
            "FROM subscriptions s JOIN plans p ON p.plan_id = s.plan_id "
            "WHERE s.customer_id = ?",
            (customer_id,),
        ).fetchone()

    if usage is None or plan is None:
        raise HTTPException(404, f"No usage/plan data for {customer_id}")

    try:
        days_to_renewal = max(
            0, (datetime.fromisoformat(plan["expiry_date"]) - datetime.now(timezone.utc)).days
        )
    except Exception:
        days_to_renewal = None

    data_used = usage["data_used_gb"]
    data_quota = plan["data_quota_gb"] or 0
    data_remaining = max(0.0, data_quota - data_used)
    data_pct_used = round((data_used / data_quota) * 100, 1) if data_quota else 0

    return {
        "customer_id": customer_id,
        "account_type": cust["account_type"],
        "prepaid_balance": cust["prepaid_balance"] if cust["account_type"] == "prepaid" else None,
        "plan_id": plan["plan_id"],
        "plan_name": plan["name"],
        "data": {
            "quota_gb": data_quota,
            "used_gb": round(data_used, 2),
            "remaining_gb": round(data_remaining, 2),
            "pct_used": data_pct_used,
        },
        "voice": {
            "quota_minutes": plan["voice_minutes"],
            "used_minutes": usage["voice_used_min"],
            "remaining_minutes": max(0, (plan["voice_minutes"] or 0) - usage["voice_used_min"]),
        },
        "sms": {
            "quota": plan["sms_quota"],
            "used": usage["sms_used"],
            "remaining": max(0, (plan["sms_quota"] or 0) - usage["sms_used"]),
        },
        "cycle_start": usage["cycle_start"],
        "cycle_end": usage["cycle_end"],
        "days_to_renewal": days_to_renewal,
    }
