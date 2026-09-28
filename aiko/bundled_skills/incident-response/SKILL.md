---
name: incident-response
description: Coordinate investigation and mitigation during live incidents with clear ownership and preserved evidence.
---

Establish impact, affected environment, start time, and incident owner. Keep a time-stamped record of observations and actions. Separate confirmed impact from hypotheses; identify the next discriminating check.

Prioritize reversible mitigation within authorized scope. Avoid competing simultaneous changes. Define success signals and observe each action. Read [references/mitigation.md](references/mitigation.md) for rollback, traffic shifts, and dependency failures.

Preserve relevant evidence without sensitive payloads. Communicate through authorized channels. Distinguish recovery from confirmed root cause. Use [Google SRE incident-response guidance](https://sre.google/workbook/incident-response/) for coordination. After stabilization, capture the causal chain, contributing conditions, detection gaps, and specific prevention work without unsupported certainty or blame.
