---
name: container-ops
description: Diagnose or change container service networking, volumes, readiness, shutdown, and resource limits.
---

Inspect rendered configuration, image identity, mounts, ports, and dependencies. Confirm the environment and identify persistent volumes before recreating services.

Trace failures across process exit, readiness, DNS, connectivity, and resource pressure. A listening port does not prove dependency readiness. Prefer narrow service replacement when dependencies need no changes.

Keep credentials outside images and command arguments. Distinguish ephemeral layers from durable storage; a volume reset is not routine repair. Check privileges, resource limits, and graceful shutdown against the workload. Verify production does not accidentally mount development code or expose internal databases.

Use [Docker production Compose guidance](https://docs.docker.com/compose/how-tos/production/) for environment overrides and targeted replacement. Verify the running artifact and a real request afterward.
