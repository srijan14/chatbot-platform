# Bulk API Onboarding (Excel-Based)

## When and Why to Use Bulk Onboarding

Bulk onboarding via Excel is intended for scenarios where multiple APIs need to
be onboarded at once, such as migration from an existing gateway, mass
registration of microservices, or initial platform setup. This avoids repetitive
manual data entry and provides a structured way to validate and import many APIs
together.

Bulk onboarding uses a standardized `.xlsx` template defined by the system and
supports files up to 5 MB in size. Using the system template ensures all
required columns are present and correctly named so the platform can parse and
validate the file automatically.

## Constraints, Columns, and Behavior

Key constraints include:

- File format must be `.xlsx`.
- Maximum file size: 5 MB.
- The system-provided template must be used without structural changes.

The template includes mandatory columns such as:

- API Name, Version, Gateway.
- Curl Command and Sample Response.

When the file is processed, APIs are created in **Draft** state so they still
undergo the normal review and approval workflow. Invalid rows are skipped,
meaning they do not create APIs, and duplicate APIs are rejected, ensuring
uniqueness and avoiding conflicts in the same workspace.
