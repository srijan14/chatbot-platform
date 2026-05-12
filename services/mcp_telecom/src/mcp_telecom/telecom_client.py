"""Thin httpx client wrapping the mock telecom REST API."""
import os
from typing import Optional
import httpx

TELECOM_API_URL = os.getenv("TELECOM_API_URL", "http://localhost:8001")
_client = httpx.Client(base_url=TELECOM_API_URL, timeout=10.0)


def _check(resp: httpx.Response) -> dict | list:
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise RuntimeError(f"Telecom API {resp.status_code}: {detail}")
    return resp.json()


# Accounts ------------------------------------------------------------------
def get_customer(customer_id: str) -> dict:
    return _check(_client.get(f"/customers/{customer_id}"))


# Plans ---------------------------------------------------------------------
def get_current_plan(customer_id: str) -> dict:
    return _check(_client.get(f"/customers/{customer_id}/plan"))


def list_plans(category: Optional[str] = None) -> list:
    params = {"category": category} if category else None
    return _check(_client.get("/plans", params=params))


def change_plan(customer_id: str, new_plan_id: str, confirm: bool) -> dict:
    return _check(_client.post(
        f"/customers/{customer_id}/plan",
        json={"new_plan_id": new_plan_id, "confirm": confirm},
    ))


# Usage ---------------------------------------------------------------------
def get_usage(customer_id: str) -> dict:
    return _check(_client.get(f"/customers/{customer_id}/usage"))


# Billing -------------------------------------------------------------------
def list_bills(customer_id: str, limit: int = 3) -> list:
    return _check(_client.get(f"/customers/{customer_id}/bills", params={"limit": limit}))


def pay_bill(customer_id: str, bill_id: str, payment_method: str) -> dict:
    return _check(_client.post(
        f"/customers/{customer_id}/bills/{bill_id}/pay",
        json={"payment_method": payment_method},
    ))


# Recharge ------------------------------------------------------------------
def recharge(customer_id: str, amount: float, payment_method: str) -> dict:
    return _check(_client.post(
        f"/customers/{customer_id}/recharge",
        json={"amount": amount, "payment_method": payment_method},
    ))


# Addons --------------------------------------------------------------------
def list_addons(category: Optional[str] = None) -> list:
    params = {"category": category} if category else None
    return _check(_client.get("/addons", params=params))


def purchase_addon(customer_id: str, addon_id: str, confirm: bool) -> dict:
    return _check(_client.post(
        f"/customers/{customer_id}/addons",
        json={"addon_id": addon_id, "confirm": confirm},
    ))


# SIM -----------------------------------------------------------------------
def block_sim(customer_id: str, reason: str) -> dict:
    return _check(_client.post(
        f"/customers/{customer_id}/sim/block",
        json={"reason": reason},
    ))


# Network -------------------------------------------------------------------
def network_status(area_code: Optional[str] = None,
                   customer_id: Optional[str] = None) -> dict:
    params: dict = {}
    if area_code:
        params["area_code"] = area_code
    if customer_id:
        params["customer_id"] = customer_id
    return _check(_client.get("/network/status", params=params))


# Complaints ----------------------------------------------------------------
def file_complaint(customer_id: str, category: str, description: str) -> dict:
    return _check(_client.post(
        f"/customers/{customer_id}/complaints",
        json={"category": category, "description": description},
    ))


def list_complaints(customer_id: str, ticket_id: Optional[str] = None) -> list:
    params = {"ticket_id": ticket_id} if ticket_id else None
    return _check(_client.get(f"/customers/{customer_id}/complaints", params=params))
