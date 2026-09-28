---
name: agent-usage
description: Measure and control agent token, cost, quota, and latency budgets during routing and execution.
---

Separate provider-reported usage from estimates. Track model, request/task identity, tokens, cache treatment, and tool time without storing sensitive prompts. Do not infer remaining quota from local token estimates.

Budget the whole task including subagents, retries, and tool loops. Choose cheaper workers only when capabilities and data boundaries fit. Respect rate-limit retry signals and expose exhausted capacity rather than causing retry storms.

Load skill metadata and references progressively. Compare savings against success, latency, and rework. Avoid double-counting resumed sessions.

Consult the provider's current usage contract and [OpenTelemetry GenAI conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/), checking stability before adopting fields. Report measured and estimated totals separately, including unavailable information.
