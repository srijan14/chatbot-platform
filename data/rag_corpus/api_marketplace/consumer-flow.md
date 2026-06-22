# Application and Consumer Flow (Public Portal)

## Application Registration Steps

Consumers interact with APIs through applications defined in the Public Portal.
Each application represents a client (for example, a web app, mobile app, or
system integration) that wants to use one or more APIs.

Typical steps for a consumer are:

1. Create an application in the Public Portal.
2. Configure IP whitelist and rate limits for that application.
3. Add desired APIs to the application and submit for approval.

This allows fine-grained control over which applications can access which APIs,
with security (IP whitelisting) and performance protection (rate limiting)
configured per application.

## Post-Approval Behavior

After an application and its API access requests are approved, the platform
automatically creates a corresponding KONG consumer and generates a **client ID**
and **client secret** for that application. These credentials are used in OAuth
flows to obtain access tokens for calling APIs through the gateway.

This design separates the application identity (client ID and secret and tokens)
from user identity, aligning with standard OAuth client-credentials patterns for
machine-to-machine or backend service integrations.
