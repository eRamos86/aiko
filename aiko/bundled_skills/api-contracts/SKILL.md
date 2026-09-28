---
name: api-contracts
description: Design or change HTTP API payloads, pagination, errors, authorization, and retry contracts.
---

Read routes, schemas, clients, and tests together. Define field presence, nullability, identifier ownership, authorization, error shapes, and status semantics. Preserve deployed consumers or describe a versioned transition.

For writes, specify transaction boundaries, idempotency scope, duplicate handling, and safe retries after timeouts. Authorize object access before side effects. For lists, define deterministic ordering and behavior under concurrent changes.

Keep sensitive internals out of responses. Apply workload-appropriate request and timeout limits. Exercise malformed payloads, cross-user access, conflicts, and a real client integration. Consult the [OpenAPI specification](https://spec.openapis.org/oas/latest.html) using the project's chosen version; do not silently upgrade its format. Return examples matching actual behavior.
