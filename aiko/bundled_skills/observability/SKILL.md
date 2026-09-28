---
name: observability
description: Improve traces, metrics, logs, and alerts for a specific service question or failure mode.
---

Begin with the operational question: which request failed, where time went, or whether users are affected. Define the signal's units, ownership, sampling, retention, and volume.

Correlate logs and traces without recording secrets or sensitive payloads. Avoid unbounded identifiers in metric labels. Capture errors at the responsible boundary with causal context.

Use existing instrumentation libraries and [OpenTelemetry signal concepts](https://opentelemetry.io/docs/concepts/signals/). Verify propagation across asynchronous and service boundaries.

Alert on actionable user impact with a defined operator response. Exercise controlled failure, verify delivery, and check alert recovery. Report end-to-end observations; installing an exporter does not establish telemetry delivery.
