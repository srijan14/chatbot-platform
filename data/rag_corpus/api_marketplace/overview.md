# API Marketplace & Gateway — Project Overview

The API Marketplace and API Gateway is an internal platform that standardizes
how APIs are published, governed, discovered, and consumed across the
organization. The marketplace separates internal (intranet) and external
(internet) traffic, supports role-based governance, and uses KONG as the runtime
gateway with OAuth-based security for API consumption.

## Two user-facing areas

The platform has two key user-facing areas:

- **Backoffice** — for API publishing and approvals.
- **Public Portal** — for application registration and API consumption.

## Goal

The goal is to ensure that every API follows a consistent lifecycle — from draft
to approval to go-live — and that consumers use a unified, secure mechanism to
access APIs via KONG URLs rather than calling underlying services directly.
