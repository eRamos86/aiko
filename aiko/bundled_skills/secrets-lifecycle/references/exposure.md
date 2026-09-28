# Suspected credential exposure

Identify credential type, issuer, privileges, exposed locations, and potential access interval without redisplaying the value. A token fingerprint or provider identifier can support correlation.

When revocation is authorized, revoke or rotate at the issuer and verify consumers use the replacement. If abrupt revocation would interrupt critical service, coordinate the shortest safe overlap with its owner. Exposure response may require faster revocation than routine rotation.

Inspect narrowly scoped access/audit records for evidence of misuse. Do not infer no misuse from missing telemetry. Report the limits of observation.

Remove exposed values from current surfaces within scope. History rewriting, cache invalidation, and contacting third parties are separate coordinated actions; deleting source text does not invalidate a credential. Preserve incident evidence securely.

Consult [OWASP secrets management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html) for revocation and lifecycle controls.
