# Access, Governance, and Roles

## Identity and Access Requirements

Access to the marketplace is governed by the enterprise Identity and Access
Management (IAM) and Identity Provider (IDP) systems. Every user must have an
AUUID registered in IAM and IDP. Role-based access control is mandatory,
ensuring that only users with appropriate roles can publish or approve APIs or
manage applications.

This integration ensures traceability and accountability. Every action
(publishing, approval, configuration) is tied to an identified user with a
defined role rather than generic credentials.

## Key Roles

The project defines two primary functional roles for API governance:

- **Publisher** — responsible for creating and submitting APIs into the
  marketplace.
- **Approver** — responsible for reviewing and approving APIs before they become
  available on the gateway.

Each team can have only one designated approver, and that approver must be at
manager level or higher to ensure sufficient authority over APIs and their
exposure risk. Role assignments are handled through IAM in collaboration with
the team owner, not ad-hoc within the marketplace UI.
