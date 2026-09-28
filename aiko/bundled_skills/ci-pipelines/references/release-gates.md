# Release gates

Resolve the artifact produced by the successful build and bind it to the source revision. Deployment should consume that artifact, not a mutable tag that can change between approval and rollout.

Map the actual release event: branch push, tag, manual dispatch, or promotion. Confirm required checks run on that event; a skipped check is not a successful check. Exercise failure and cancellation so a later job cannot deploy after validation was skipped or failed.

Treat fork contributions and external artifacts as untrusted until validated. Publishing credentials belong only in the narrowly scoped release job. Serialize conflicting deployments to the same environment while allowing independent environments to proceed.

Return the revision, artifact identifier, required checks, target environment, and recovery route. Consult [GitHub secure-use guidance](https://docs.github.com/en/actions/reference/security/secure-use) when event context or artifact provenance crosses a trust boundary.
