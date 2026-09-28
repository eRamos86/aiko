# Choosing a mitigation

Compare rollback, disabling a feature, shifting traffic, throttling, and dependency isolation by time to impact reduction, reversibility, and data effects. Prefer the smallest action with a clear causal connection to observed impact.

Before rollback, check schema compatibility and any writes produced by the new version. Before traffic shifts, confirm spare capacity and avoid overwhelming a cold target. Before retrying a dependency, inspect backlog and retry amplification.

Assign one owner per action and record timestamp, intended result, observed result, and stop condition. Avoid overlapping changes that make evidence ambiguous. If the expected signal does not improve, reassess the hypothesis rather than repeating the same action indefinitely.

[Google SRE incident response](https://sre.google/workbook/incident-response/) provides coordination guidance. Confirm recovery using user-visible behavior, not only process uptime, and retain follow-up ownership for unresolved causes.
