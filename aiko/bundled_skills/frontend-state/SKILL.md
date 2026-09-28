---
name: frontend-state
description: Implement frontend behavior across loading, empty, error, success, and concurrent request states.
---

Inspect the component system, routing, fetching model, and server contract. Identify state ownership and derived values. Do not duplicate server facts into local state without synchronization rules.

Cover initial load, empty and partial data, validation failure, retry, success, and navigation away. Prevent stale responses from overwriting newer requests. Define rollback and duplicate-submission handling for optimistic writes.

Use semantic controls and labels; retain input and useful focus after errors. Keep authorization authoritative on the server. Consult [MDN accessibility](https://developer.mozilla.org/en-US/docs/Learn_web_development/Core/Accessibility) before creating custom controls.

Verify actual response handling at narrow/wide viewports with keyboard input, interrupted requests, and repeated actions. A static screenshot does not establish working interactions.
