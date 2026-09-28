---
name: auth-sessions
description: Implement or debug authentication, OAuth redirects, session lifecycle, and resource authorization.
---

Trace the actual consumer URL through initiation, login, callback, token exchange, refresh, expiry, and logout. Separate identity proof from per-resource and tenant authorization. Exercise failed and retried paths.

Bind continuations to their initiating request. Validate redirects and state, use supported PKCE flows, and preserve client context across retries. Do not reconstruct continuations from untrusted input. Consult [OAuth security best practice](https://www.rfc-editor.org/info/rfc9700/) for the protocol in use.

Inspect session rotation, revocation, cookie flags, CSRF defenses, and concurrent refresh. Test cross-user access and replay without exposing credentials. Use [OWASP authentication guidance](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html) for recovery controls. Report demonstrated behavior and untested paths.
