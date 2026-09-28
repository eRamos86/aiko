---
name: secrets-lifecycle
description: Handle credential storage, delivery, rotation, revocation, and accidental exposure without revealing values.
---

Inventory credential identifiers, owners, consumers, scopes, expiry, and storage without copying values into reports. Use the configured secret manager or runtime binding; avoid secrets in prompts, logs, artifacts, command history, and images.

Determine whether consumers support overlapping credentials. Create the replacement, update authorized consumers, verify new use, then revoke the old credential. For suspected compromise, read [references/exposure.md](references/exposure.md).

Prefer narrow scopes and short lifetimes where supported. Verify injection and failure behavior with synthetic values. State which consumers were checked. Removing a leaked string does not revoke access; rewriting shared history requires separate coordination. Consult [OWASP secrets management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html) for lifecycle design.
