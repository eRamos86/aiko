---
name: ci-pipelines
description: Build or diagnose CI workflows, job permissions, dependency caches, and required checks.
---

Identify the failing workflow, event, revision, and earliest failing job. Reproduce using the declared runtime and dependency lock. Separate test failures from runner setup, permission, or service failures.

Keep untrusted pull-request execution separate from credential-bearing jobs. Minimize token permissions, pin third-party actions to immutable revisions, and keep untrusted event fields out of executable shell text. Consult [GitHub secure-use guidance](https://docs.github.com/en/actions/reference/security/secure-use) when changing trust boundaries.

Cache keys must track dependencies and platform; cache hits must not skip validation. Verify event filters, matrix behavior, cancellation, and required check names. Report hosted and local results separately. For release jobs, read [references/release-gates.md](references/release-gates.md).
