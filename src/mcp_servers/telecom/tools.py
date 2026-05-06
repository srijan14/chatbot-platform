"""MCP tool definitions for the telecom domain.

Each function is registered as an MCP tool via @mcp.tool() and is a thin wrapper
over the telecom REST client. Docstrings become tool descriptions seen by Claude —
keep them clear and action-oriented.
"""
from typing import Optional

from src.mcp_servers.telecom import telecom_client as tc


def register(mcp):
    """Register all telecom tools on the given FastMCP instance."""

    @mcp.tool()
    def get_customer_profile(customer_id: str) -> dict:
        """Get a customer's profile: name, phone, email, account type (prepaid/postpaid),
        account status (active/suspended/blocked), prepaid balance, and area code.
        Use this when the user asks about their account or you need to identify them."""
        return tc.get_customer(customer_id)

    @mcp.tool()
    def get_current_plan(customer_id: str) -> dict:
        """Get the customer's current active plan: plan name, monthly fee, data quota (GB),
        voice minutes, SMS quota, plan start date, expiry date, and auto-renew setting.
        Use when the user asks "what plan am I on" or before recommending plan changes."""
        return tc.get_current_plan(customer_id)

    @mcp.tool()
    def list_available_plans(category: Optional[str] = None) -> list:
        """List plans the customer could switch to. Optional category filter:
        'prepaid', 'postpaid', or 'data-only'. Returns plan_id, name, monthly_fee,
        data quota, voice minutes, and SMS quota for each."""
        return tc.list_plans(category)

    @mcp.tool()
    def change_plan(customer_id: str, new_plan_id: str, confirm: bool = False) -> dict:
        """Switch a customer's plan. TWO-STEP: call first with confirm=False to get a
        proration preview (price difference, days remaining); only call with
        confirm=True AFTER the user explicitly confirms. Mutating action."""
        return tc.change_plan(customer_id, new_plan_id, confirm)

    @mcp.tool()
    def get_balance_and_usage(customer_id: str) -> dict:
        """Get prepaid balance (if prepaid) and current cycle usage:
        data used vs quota (with % used), voice minutes used, SMS used, days to renewal.
        Use when the user asks "how much data do I have left" or reports slow internet."""
        return tc.get_usage(customer_id)

    @mcp.tool()
    def get_recent_bills(customer_id: str, limit: int = 3) -> list:
        """Get the customer's recent bills with bill_id, amount, issue date, due date,
        status (paid/pending/overdue) and paid_at timestamp. Default limit=3.
        Use for billing questions or to detect overdue bills causing service issues."""
        return tc.list_bills(customer_id, limit)

    @mcp.tool()
    def pay_bill(customer_id: str, bill_id: str, payment_method: str) -> dict:
        """Pay a specific bill. payment_method must be one of: 'card', 'upi',
        'netbanking', 'wallet'. Mutating action — confirm with the user first."""
        return tc.pay_bill(customer_id, bill_id, payment_method)

    @mcp.tool()
    def recharge_prepaid(customer_id: str, amount: float, payment_method: str) -> dict:
        """Top up a prepaid customer's balance. payment_method one of: 'card', 'upi',
        'netbanking', 'wallet'. Mutating action — confirm with the user first."""
        return tc.recharge(customer_id, amount, payment_method)

    @mcp.tool()
    def list_addons(category: Optional[str] = None) -> list:
        """List available addons. Optional category: 'data', 'roaming', 'international',
        'voice'. Returns addon_id, name, price, validity in days, and description."""
        return tc.list_addons(category)

    @mcp.tool()
    def purchase_addon(customer_id: str, addon_id: str, confirm: bool = False) -> dict:
        """Purchase an addon for a customer. TWO-STEP: call first with confirm=False
        to get a price preview; only call with confirm=True AFTER the user confirms.
        Mutating action."""
        return tc.purchase_addon(customer_id, addon_id, confirm)

    @mcp.tool()
    def block_sim(customer_id: str, reason: str) -> dict:
        """Block a customer's SIM (lost/stolen/damaged). reason should be one of:
        'lost', 'stolen', 'damaged', 'other'. This suspends the account immediately.
        Mutating action — always confirm with the user before calling. Returns
        a block reference and SIM-replacement instructions."""
        return tc.block_sim(customer_id, reason)

    @mcp.tool()
    def check_network_status(area_code: Optional[str] = None,
                             customer_id: Optional[str] = None) -> dict:
        """Check for network outages. Provide either area_code (e.g., 'BLR-04') or
        customer_id (the customer's area is looked up). Returns active and recent
        outages with type (planned/unplanned), start time, and description."""
        return tc.network_status(area_code=area_code, customer_id=customer_id)

    @mcp.tool()
    def file_complaint(customer_id: str, category: str, description: str) -> dict:
        """File a customer complaint. category must be: 'billing', 'network', 'service',
        or 'other'. Returns ticket_id and SLA hours. Use after diagnosing an issue
        the customer cannot resolve themselves."""
        return tc.file_complaint(customer_id, category, description)

    @mcp.tool()
    def get_complaint_status(customer_id: str, ticket_id: Optional[str] = None) -> list:
        """Get the status of complaints for a customer. If ticket_id is given,
        returns just that ticket; otherwise returns the 5 most recent."""
        return tc.list_complaints(customer_id, ticket_id)
