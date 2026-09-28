---
name: debugging
description: Find causes of reproducible or intermittent failures through targeted evidence and experiments.
---

Record expected and actual behavior, input, environment, and last known good state. Reproduce at the smallest useful boundary. Find the first causal failure rather than only downstream symptoms.

Keep falsifiable hypotheses and choose checks that distinguish them. Update the explanation from results. Compare versions or bisect regressions without disturbing user changes.

Instrument only the needed boundary, excluding secrets and private payloads. For intermittent failures inspect timing, cancellation, shared state, pressure, and retries; a sleep that hides a race is not a causal repair.

Use [SRE troubleshooting methodology](https://sre.google/sre-book/effective-troubleshooting/) for distributed failures. Diagnose when asked to diagnose; implement when authorized. Verify repairs against the original trigger and nearby behavior.
