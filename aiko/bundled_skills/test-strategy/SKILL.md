---
name: test-strategy
description: Design behavioral tests for changed contracts, regressions, concurrency, and failure paths.
---

Start from an observable requirement or reproduced defect. Use the lowest layer that can falsify the claim, adding integration coverage for actual boundaries. Avoid assertions that repeat implementation constants, private call ordering, or generated wording.

Test meaningful boundaries: missing versus empty values, retries, duplicate delivery, cancellation, and ownership. Assert persistent effects and externally visible responses. Coordinate concurrency deterministically rather than adding arbitrary sleeps.

Control time, randomness, network, home directories, and shared state. Fixtures must clean up their own resources; consult [pytest fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html) where applicable. Verify the regression fails on broken behavior. Report checks and limits; coverage percentage alone does not establish correctness.
