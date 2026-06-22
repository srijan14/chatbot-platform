# API Publishing via Backoffice

## Required Metadata Fields

When publishing an API through the Backoffice, a set of fields must be provided
to ensure the API can be discovered, governed, and called correctly. Required
fields include:

- **API Name** (must be unique within a given workspace)
- **Version** (e.g., v1, v2)
- **Base URL and Endpoint** (path to the service behind the gateway)
- **HTTP Method** (GET, POST, etc.)
- **Request and Response schemas** (for validation and documentation)
- **Workspace, Gateway, and Group** (for routing, environment, and logical
  grouping)

Collecting these fields makes the marketplace both a registry and a source of
documentation and provides a foundation for automated validations.

## Publishing Process Steps

The Backoffice publishing flow follows these steps:

1. Publisher creates the API, which starts in **Draft** state.
2. Publisher submits the API, moving it to **Pending** state.
3. Approver reviews the API definition, schema, and alignment with standards.
4. Upon approval, the API is deployed on KONG and becomes available according to
   its gateway (intranet or internet).

At this point, the API can be discovered and requested by applications via the
Public Portal, but consumption still requires application-level approval and
credentials.
