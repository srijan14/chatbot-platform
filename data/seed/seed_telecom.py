"""Seed the telecom SQLite database with demo data.

Five demo customers, each engineered for a distinct demo scenario. See the plan
for the narrative of each customer (CUST001..CUST005).

Run:
    python -m data.seed.seed_telecom            # drops + recreates (idempotent)
    python -m data.seed.seed_telecom --reset    # explicit (same behavior)
"""
import argparse
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = os.getenv("TELECOM_DB_PATH", "data/telecom.db")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

DDL = """
CREATE TABLE customers (
    customer_id     TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    phone           TEXT NOT NULL UNIQUE,
    email           TEXT,
    account_type    TEXT CHECK(account_type IN ('prepaid','postpaid')) NOT NULL,
    status          TEXT CHECK(status IN ('active','suspended','blocked')) NOT NULL DEFAULT 'active',
    prepaid_balance REAL DEFAULT 0,
    area_code       TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE plans (
    plan_id         TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT,
    monthly_fee     REAL NOT NULL,
    data_quota_gb   REAL,
    voice_minutes   INTEGER,
    sms_quota       INTEGER,
    is_active       INTEGER DEFAULT 1
);

CREATE TABLE subscriptions (
    customer_id     TEXT PRIMARY KEY REFERENCES customers(customer_id),
    plan_id         TEXT NOT NULL REFERENCES plans(plan_id),
    start_date      TEXT NOT NULL,
    expiry_date     TEXT NOT NULL,
    auto_renew      INTEGER DEFAULT 1
);

CREATE TABLE usage_current (
    customer_id     TEXT PRIMARY KEY REFERENCES customers(customer_id),
    data_used_gb    REAL DEFAULT 0,
    voice_used_min  INTEGER DEFAULT 0,
    sms_used        INTEGER DEFAULT 0,
    cycle_start     TEXT NOT NULL,
    cycle_end       TEXT NOT NULL
);

CREATE TABLE bills (
    bill_id         TEXT PRIMARY KEY,
    customer_id     TEXT NOT NULL REFERENCES customers(customer_id),
    amount          REAL NOT NULL,
    issue_date      TEXT NOT NULL,
    due_date        TEXT NOT NULL,
    status          TEXT CHECK(status IN ('paid','pending','overdue')) NOT NULL,
    paid_at         TEXT
);

CREATE TABLE addons (
    addon_id        TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT,
    price           REAL NOT NULL,
    validity_days   INTEGER NOT NULL,
    description     TEXT
);

CREATE TABLE customer_addons (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     TEXT NOT NULL REFERENCES customers(customer_id),
    addon_id        TEXT NOT NULL REFERENCES addons(addon_id),
    purchased_at    TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    status          TEXT CHECK(status IN ('active','expired')) DEFAULT 'active'
);

CREATE TABLE sim_events (
    event_id        TEXT PRIMARY KEY,
    customer_id     TEXT NOT NULL REFERENCES customers(customer_id),
    event_type      TEXT,
    reason          TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE complaints (
    ticket_id       TEXT PRIMARY KEY,
    customer_id     TEXT NOT NULL REFERENCES customers(customer_id),
    category        TEXT,
    description     TEXT,
    status          TEXT CHECK(status IN ('open','in_progress','resolved')) DEFAULT 'open',
    sla_hours       INTEGER DEFAULT 48,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE network_outages (
    outage_id       TEXT PRIMARY KEY,
    area_code       TEXT NOT NULL,
    type            TEXT,
    start_time      TEXT NOT NULL,
    end_time        TEXT,
    description     TEXT
);

CREATE TABLE transactions (
    txn_id          TEXT PRIMARY KEY,
    customer_id     TEXT NOT NULL REFERENCES customers(customer_id),
    type            TEXT,
    amount          REAL,
    reference_id    TEXT,
    payment_method  TEXT,
    status          TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX idx_bills_customer_status ON bills(customer_id, status);
CREATE INDEX idx_complaints_customer_status ON complaints(customer_id, status);
CREATE INDEX idx_customer_addons_customer_status ON customer_addons(customer_id, status);
"""


TABLES_IN_DROP_ORDER = [
    "transactions", "network_outages", "complaints", "sim_events",
    "customer_addons", "addons", "bills", "usage_current",
    "subscriptions", "plans", "customers",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _days(n: int) -> str:
    return _iso(_now() + timedelta(days=n))


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

PLANS = [
    # plan_id, name, category, fee, data_gb, voice_min, sms
    ("LITE_299",      "Lite 299",       "postpaid",  299.0,  10.0,   500,  100),
    ("SMART_199",     "Smart 199",      "prepaid",   199.0,   1.5,   200,   50),  # ~1.5GB/day for ~28 days; we model as 45GB cycle
    ("PRO_599",       "Pro 599",        "postpaid",  599.0,  50.0,  2000,  500),
    ("MAX_999",       "Max 999",        "postpaid",  999.0, 150.0,  5000, 1000),
    ("DATA_ONLY_399", "Data Only 399",  "data-only", 399.0,  75.0,     0,    0),
]

# For Smart 199 we set data_quota_gb to the cycle total (45GB) to make 92% used realistic.
# Override here:
PLANS[1] = ("SMART_199", "Smart 199", "prepaid", 199.0, 45.0, 200, 50)


ADDONS = [
    # addon_id, name, category, price, validity_days, description
    ("DATA_5GB_99",          "Data Pack 5GB",         "data",          99.0,  28, "Extra 5GB high-speed data, valid 28 days."),
    ("DATA_20GB_249",        "Data Pack 20GB",        "data",         249.0,  28, "Extra 20GB high-speed data, valid 28 days."),
    ("ROAM_INTL_7D_499",     "Intl Roaming 7-day",    "roaming",      499.0,   7, "International roaming with 1GB data + 100 mins, 7 days."),
    ("INTL_CALL_100MIN_199", "Intl Calling 100 min",  "international",199.0,  30, "100 minutes of outgoing international calls, 30 days."),
    ("VOICE_UNL_99",         "Unlimited Voice 1-day", "voice",         99.0,   1, "Unlimited local + STD voice for 24 hours."),
]


CUSTOMERS = [
    # customer_id, name, phone, email, account_type, status, prepaid_balance, area_code
    ("CUST001", "Aarav Mehta",   "+919900000001", "aarav@example.com",  "postpaid", "active",   0.0,   "BLR-01"),
    ("CUST002", "Priya Iyer",    "+919900000002", "priya@example.com",  "prepaid",  "active",  35.0,   "BLR-02"),
    ("CUST003", "Rohan Kapoor",  "+919900000003", "rohan@example.com",  "postpaid", "suspended",0.0,   "DEL-01"),
    ("CUST004", "Sneha Reddy",   "+919900000004", "sneha@example.com",  "prepaid",  "active", 120.0,   "HYD-03"),
    ("CUST005", "Vikram Singh",  "+919900000005", "vikram@example.com", "postpaid", "active",   0.0,   "BLR-04"),
]


SUBSCRIPTIONS = [
    # customer_id, plan_id, start_offset_days, expiry_offset_days, auto_renew
    ("CUST001", "PRO_599",       -35,  25, 1),
    ("CUST002", "SMART_199",     -25,   3, 1),   # plan expires in 3 days
    ("CUST003", "LITE_299",      -40,  20, 1),
    ("CUST004", "SMART_199",      -5,  23, 0),
    ("CUST005", "PRO_599",       -20,  40, 1),
]


# customer_id, data_used_gb, voice_used_min, sms_used, cycle_start_offset, cycle_end_offset
USAGE = [
    ("CUST001", 30.0,  900, 120, -25, 5),
    ("CUST002", 41.4,  120,  30, -25, 3),    # 41.4/45 = 92%
    ("CUST003",  4.2,  150,  10, -25, 5),
    ("CUST004", 12.0,   80,  20,  -5, 23),
    ("CUST005", 28.5,  600, 200, -20, 10),
]


# bill_id, customer_id, amount, issue_offset, due_offset, status, paid_offset(or None)
BILLS = [
    ("BILL001A", "CUST001", 599.0, -35, -5,  "paid",    -7),
    ("BILL001B", "CUST001", 599.0,  -5, 25,  "pending", None),

    ("BILL003A", "CUST003", 299.0, -42, -12, "overdue", None),
    ("BILL003B", "CUST003", 299.0, -12,  18, "pending", None),

    ("BILL005A", "CUST005", 599.0, -50, -20, "paid",   -22),
    ("BILL005B", "CUST005", 599.0, -20, 10,  "paid",   -15),
]


# outage_id, area_code, type, start_offset_h, end_offset_h(or None), description
OUTAGES = [
    ("OUT_BLR04_1", "BLR-04", "unplanned", -6,  None, "Fiber cut affecting voice and data in BLR-04 sectors."),
    ("OUT_DEL02_1", "DEL-02", "planned",   -72, -48,  "Scheduled tower maintenance (resolved)."),
]


# Sample existing customer addon: CUST001 has an active VOICE_UNL_99
CUSTOMER_ADDONS = [
    # customer_id, addon_id, purchased_offset_h, expires_offset_h, status
    ("CUST001", "VOICE_UNL_99", -2, 22, "active"),
]


# Pre-existing complaint for CUST005
COMPLAINTS = [
    # ticket_id, customer_id, category, description, status, sla_hours, created_offset_h, updated_offset_h
    ("TKT-2026-0042", "CUST005", "network", "Calls dropping repeatedly in evening hours.", "in_progress", 48, -30, -2),
]


# ---------------------------------------------------------------------------
# Seed routine
# ---------------------------------------------------------------------------

def seed(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    for t in TABLES_IN_DROP_ORDER:
        cur.execute(f"DROP TABLE IF EXISTS {t}")

    for stmt in DDL.split(";"):
        if stmt.strip():
            cur.execute(stmt)

    now_iso = _iso(_now())

    # plans
    cur.executemany(
        "INSERT INTO plans (plan_id,name,category,monthly_fee,data_quota_gb,voice_minutes,sms_quota,is_active) "
        "VALUES (?,?,?,?,?,?,?,1)",
        PLANS,
    )

    # addons
    cur.executemany(
        "INSERT INTO addons (addon_id,name,category,price,validity_days,description) VALUES (?,?,?,?,?,?)",
        ADDONS,
    )

    # customers
    cur.executemany(
        "INSERT INTO customers (customer_id,name,phone,email,account_type,status,prepaid_balance,area_code,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [(*c, now_iso) for c in CUSTOMERS],
    )

    # subscriptions
    cur.executemany(
        "INSERT INTO subscriptions (customer_id,plan_id,start_date,expiry_date,auto_renew) VALUES (?,?,?,?,?)",
        [(cid, pid, _days(s), _days(e), ar) for (cid, pid, s, e, ar) in SUBSCRIPTIONS],
    )

    # usage
    cur.executemany(
        "INSERT INTO usage_current (customer_id,data_used_gb,voice_used_min,sms_used,cycle_start,cycle_end) "
        "VALUES (?,?,?,?,?,?)",
        [(cid, d, v, s, _days(cs), _days(ce)) for (cid, d, v, s, cs, ce) in USAGE],
    )

    # bills
    bill_rows = []
    for (bid, cid, amt, iss, due, status, paid) in BILLS:
        paid_at = _days(paid) if paid is not None else None
        bill_rows.append((bid, cid, amt, _days(iss), _days(due), status, paid_at))
    cur.executemany(
        "INSERT INTO bills (bill_id,customer_id,amount,issue_date,due_date,status,paid_at) "
        "VALUES (?,?,?,?,?,?,?)",
        bill_rows,
    )

    # outages
    def _hours(h: int) -> str:
        return _iso(_now() + timedelta(hours=h))
    out_rows = [
        (oid, area, typ, _hours(s), _hours(e) if e is not None else None, desc)
        for (oid, area, typ, s, e, desc) in OUTAGES
    ]
    cur.executemany(
        "INSERT INTO network_outages (outage_id,area_code,type,start_time,end_time,description) "
        "VALUES (?,?,?,?,?,?)",
        out_rows,
    )

    # customer_addons
    cur.executemany(
        "INSERT INTO customer_addons (customer_id,addon_id,purchased_at,expires_at,status) "
        "VALUES (?,?,?,?,?)",
        [(cid, aid, _hours(p), _hours(e), st) for (cid, aid, p, e, st) in CUSTOMER_ADDONS],
    )

    # complaints
    cur.executemany(
        "INSERT INTO complaints (ticket_id,customer_id,category,description,status,sla_hours,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [(tid, cid, cat, desc, st, sla, _hours(c), _hours(u))
         for (tid, cid, cat, desc, st, sla, c, u) in COMPLAINTS],
    )

    conn.commit()
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed telecom.db with demo data.")
    parser.add_argument("--reset", action="store_true",
                        help="Drop and recreate (default behavior even without flag).")
    args = parser.parse_args()
    _ = args.reset  # behavior is the same; flag is documentation
    seed(DB_PATH)
    print(f"Seeded {DB_PATH} with {len(CUSTOMERS)} customers, {len(PLANS)} plans, {len(ADDONS)} addons.")


if __name__ == "__main__":
    main()
