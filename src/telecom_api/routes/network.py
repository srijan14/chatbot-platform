"""Network status / outage check."""
from typing import Optional
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException

from src.telecom_api.db import get_conn, rows_to_list

router = APIRouter(tags=["network"])


@router.get("/network/status")
def check_network_status(area_code: Optional[str] = None,
                         customer_id: Optional[str] = None) -> dict:
    if not area_code and not customer_id:
        raise HTTPException(400, "Provide area_code or customer_id")

    with get_conn() as conn:
        if not area_code and customer_id:
            row = conn.execute(
                "SELECT area_code FROM customers WHERE customer_id = ?", (customer_id,)
            ).fetchone()
            if row is None:
                raise HTTPException(404, f"Customer {customer_id} not found")
            area_code = row["area_code"]

        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        rows = conn.execute(
            """
            SELECT * FROM network_outages
            WHERE area_code = ?
              AND (end_time IS NULL OR end_time >= ?)
            ORDER BY start_time DESC
            """,
            (area_code, now),
        ).fetchall()

    outages = rows_to_list(rows)
    return {
        "area_code": area_code,
        "any_active_outage": any(o["end_time"] is None for o in outages),
        "outages": outages,
    }
