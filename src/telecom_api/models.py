"""Pydantic models for the telecom REST API."""
from typing import Literal, Optional
from pydantic import BaseModel, Field


class ChangePlanRequest(BaseModel):
    new_plan_id: str
    confirm: bool = False


class PayBillRequest(BaseModel):
    payment_method: str = Field(..., description="card | upi | netbanking | wallet")


class RechargeRequest(BaseModel):
    amount: float = Field(..., gt=0)
    payment_method: str


class PurchaseAddonRequest(BaseModel):
    addon_id: str
    confirm: bool = False


class BlockSimRequest(BaseModel):
    reason: str = Field(..., description="lost | stolen | damaged | other")


class FileComplaintRequest(BaseModel):
    category: Literal["billing", "network", "service", "other"]
    description: str


class PreviewResponse(BaseModel):
    """Returned when a mutating endpoint is called with confirm=False."""
    requires_confirmation: bool = True
    summary: str
    details: dict


class ActionResponse(BaseModel):
    ok: bool
    message: str
    details: Optional[dict] = None
