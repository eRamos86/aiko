---
name: deploy-rollout
description: Plan or execute application releases with readiness checks, staged rollout, and recovery verification.
---

Resolve the authorized environment, service, tested artifact, and deployment mechanism. Define observable success and a recovery route before rollout. Inspect schema compatibility between old and new application versions.

Use the existing release mechanism. Observe readiness, errors, latency, and a representative user operation. Stop rollout expansion when the agreed failure signal appears. For persistent data or incompatible versions, read [references/recovery.md](references/recovery.md).

Verify the public route as well as process health. Record the deployed revision and distinguish build, publish, deploy, and live verification. Consult [SRE overload guidance](https://sre.google/sre-book/handling-overload/) when retry amplification or capacity threatens recovery; choose thresholds from this service's behavior.
