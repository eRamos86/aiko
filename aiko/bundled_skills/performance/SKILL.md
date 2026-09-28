---
name: performance
description: Measure and improve latency, throughput, memory, or web responsiveness using reproducible workloads.
---

Define the target operation, workload, environment, and success metric. Capture distributions and resource use; averages can hide tail latency. Profile before choosing an optimization.

Separate rendering, network, application, database, queueing, and dependency costs. For browser work consult [Web Vitals guidance](https://web.dev/articles/vitals); distinguish lab measurements from real-user field data.

Change the measured bottleneck and compare equivalent workloads. Check correctness, memory, saturation, and failure behavior alongside speed. Bound retries and concurrency using [SRE overload guidance](https://sre.google/sre-book/handling-overload/).

Report baseline, result, sample conditions, and uncertainty. A one-off timing is not universal improvement. Live load tests must remain within authorized traffic and environment limits.
