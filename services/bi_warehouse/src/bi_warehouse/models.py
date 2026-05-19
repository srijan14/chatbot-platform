"""SQLAlchemy 2.x ORM models for the demo BI warehouse.

Six tables, deliberately shaped so multi-table joins are required for the
interesting analytical questions:

  customers ─┐
             ├─ orders ─┬─ order_items ── products
             │          └─ campaign_attributions ── marketing_campaigns
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    signup_date: Mapped[date] = mapped_column(Date)
    country: Mapped[str] = mapped_column(String, index=True)
    segment: Mapped[str] = mapped_column(String, index=True)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String, index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    cost: Mapped[Decimal] = mapped_column(Numeric(10, 2))


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    order_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String, index=True)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2))


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))


class MarketingCampaign(Base):
    __tablename__ = "marketing_campaigns"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    channel: Mapped[str] = mapped_column(String, index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    spend: Mapped[Decimal] = mapped_column(Numeric(12, 2))


class CampaignAttribution(Base):
    __tablename__ = "campaign_attributions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("marketing_campaigns.id"), index=True)
    attributed_revenue: Mapped[Decimal] = mapped_column(Numeric(12, 2))
