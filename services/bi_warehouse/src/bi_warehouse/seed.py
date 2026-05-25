"""Seed the BI warehouse with synthetic e-commerce data.

Generates ~500 orders spread across 12 months, four customer segments, three
countries, four marketing channels — enough variance that MoM / segment /
channel-ROAS questions return interesting answers.

Run with:  bi-seed             # idempotent, skips if data exists
Reset:     bi-seed --reset     # drops + recreates schema, reseeds
"""
from __future__ import annotations

import argparse
import random
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from .db import create_writable_engine, warehouse_path
from .models import (
    Base,
    CampaignAttribution,
    Customer,
    MarketingCampaign,
    Order,
    OrderItem,
    Product,
)

# Determinism: same seed → same warehouse → reproducible bot demos.
random.seed(42)

COUNTRIES = ["IN", "US", "UK"]
SEGMENTS = ["free", "premium", "enterprise", "trial"]
CATEGORIES = ["electronics", "apparel", "home", "books"]
CHANNELS = ["search", "social", "email", "display"]


def _date_range(start: date, days: int) -> date:
    return start + timedelta(days=days)


def _seed(session) -> None:
    today = date.today()
    year_ago = _date_range(today, -365)

    # Customers --------------------------------------------------------------
    customers: list[Customer] = []
    for i in range(120):
        signup = _date_range(year_ago, random.randint(0, 365))
        customers.append(
            Customer(
                id=f"CUST{1000 + i}",
                signup_date=signup,
                country=random.choice(COUNTRIES),
                segment=random.choice(SEGMENTS),
            )
        )
    session.add_all(customers)

    # Products ---------------------------------------------------------------
    products: list[Product] = []
    for i in range(40):
        cat = random.choice(CATEGORIES)
        price = Decimal(random.randint(199, 4999))
        cost = price * Decimal("0.55")  # 45% gross margin
        products.append(
            Product(
                id=f"P{100 + i}",
                name=f"{cat.title()} item {i}",
                category=cat,
                price=price.quantize(Decimal("0.01")),
                cost=cost.quantize(Decimal("0.01")),
            )
        )
    session.add_all(products)

    # Marketing campaigns ----------------------------------------------------
    campaigns: list[MarketingCampaign] = []
    for i in range(12):
        start = _date_range(year_ago, i * 30)
        end = _date_range(start, 30)
        campaigns.append(
            MarketingCampaign(
                id=f"C{200 + i}",
                name=f"Campaign {i}",
                channel=random.choice(CHANNELS),
                start_date=start,
                end_date=end,
                spend=Decimal(random.randint(10_000, 100_000)).quantize(Decimal("0.01")),
            )
        )
    session.add_all(campaigns)

    # Orders, items, attributions -------------------------------------------
    orders: list[Order] = []
    items: list[OrderItem] = []
    attributions: list[CampaignAttribution] = []
    for i in range(500):
        cust = random.choice(customers)
        order_date = datetime.combine(
            _date_range(year_ago, random.randint(0, 365)),
            datetime.min.time(),
        ) + timedelta(hours=random.randint(0, 23))
        order_id = f"O{10000 + i}"
        order_total = Decimal("0.00")
        n_items = random.randint(1, 4)
        sampled_products = random.sample(products, k=n_items)
        for p in sampled_products:
            qty = random.randint(1, 3)
            line_total = p.price * qty
            order_total += line_total
            items.append(
                OrderItem(
                    order_id=order_id,
                    product_id=p.id,
                    quantity=qty,
                    unit_price=p.price,
                )
            )
        status = random.choices(
            ["completed", "refunded", "cancelled"],
            weights=[85, 7, 8],
        )[0]
        orders.append(
            Order(
                id=order_id,
                customer_id=cust.id,
                order_date=order_date,
                status=status,
                total=order_total.quantize(Decimal("0.01")),
            )
        )
        # Attribute ~60% of orders to a campaign whose date range covers the order.
        if random.random() < 0.6:
            eligible = [
                c for c in campaigns
                if c.start_date <= order_date.date() <= c.end_date
            ]
            if eligible:
                campaign = random.choice(eligible)
                attributions.append(
                    CampaignAttribution(
                        order_id=order_id,
                        campaign_id=campaign.id,
                        attributed_revenue=(order_total * Decimal("0.5")).quantize(
                            Decimal("0.01")
                        ),
                    )
                )

    session.add_all(orders)
    session.add_all(items)
    session.add_all(attributions)
    session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the BI warehouse.")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate schema before seeding.")
    args = parser.parse_args()

    path = warehouse_path()
    print(f"BI warehouse path: {path}")
    engine, Session = create_writable_engine(path)

    if args.reset:
        print("Resetting schema…")
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    with Session() as session:
        existing = session.query(Customer).count() if not args.reset else 0
        if existing and not args.reset:
            print(f"Already seeded ({existing} customers); pass --reset to repopulate.")
            return
        _seed(session)

    # Quick sanity counts.
    with Session() as session:
        n_orders = session.query(Order).count()
        n_items = session.query(OrderItem).count()
        n_attrib = session.query(CampaignAttribution).count()
    print(f"Seeded: orders={n_orders} items={n_items} attributions={n_attrib}")


if __name__ == "__main__":
    main()
