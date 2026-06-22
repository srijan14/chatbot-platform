# Core Concepts and Architecture

## Gateways and Traffic Separation

The solution uses two logical gateways:

- **Intranet Gateway** — handles internal APIs that are only accessible within
  the organization's internal network.
- **Internet Gateway** — handles external or public APIs that can be exposed to
  partners or external consumers.

This separation allows different security, rate limiting, and approval policies
for internal versus external audiences, while still using a unified marketplace
experience for onboarding and consumption.

## Workspaces

Workspaces are logical groupings of APIs and are also used as the **Host header**
during runtime API calls for routing through KONG. There are two main workspace
types:

- **Platform workspace** — primarily for internal APIs (intranet/platform
  services).
- **Product workspace** — primarily for external or product-facing APIs
  (internet/partner-facing).

Each workspace defines a boundary for API naming, governance, and routing. API
names must be unique within a workspace, and the workspace value is critical at
runtime because it maps to the Host header in API requests.

## Platforms (User-Facing Interfaces)

There are two main platforms where users interact with the marketplace:

- **Backoffice** — used for API onboarding (creation, editing) and approvals by
  publishers and approvers.
- **Public Portal** — used by application owners and consumers to register
  applications, configure IP whitelists and rate limits, request access to APIs,
  and obtain credentials.

Together these create a full lifecycle experience: publishers manage APIs in
Backoffice, while consumers work through the Public Portal without directly
touching underlying gateway configurations.
