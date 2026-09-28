---
name: agent-orchestration
description: Design agent decomposition, worker selection, handoffs, and verification for multi-step work.
---

Use one capable agent when delegation adds no value. Split independent work by ownership and concrete outputs; sequence dependencies. Give workers objectives, evidence, permissions, owned files, and acceptance criteria.

Discover skill metadata first and attach only relevant guidance. Avoid dumping repositories, histories, or credentials into prompts. Select workers by capabilities, tool access, locality, and usage availability rather than a fixed host.

Track dispatch IDs, cancellation, completion, and failures. Dispatch success is not task success. Inspect outputs and verify acceptance before integration. Read [references/handoffs.md](references/handoffs.md) for shared workspaces and retries.

Consult [Anthropic agent patterns](https://www.anthropic.com/engineering/building-effective-agents). Keep budgets and stopping conditions explicit. Delegation does not grant additional permissions.
