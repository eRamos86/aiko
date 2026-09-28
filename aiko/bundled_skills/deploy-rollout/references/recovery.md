# Data-aware recovery

Choose recovery by the failure's effects. A previous application image may be safe only while the new schema remains backward compatible. If new writes changed data semantics, image rollback alone may worsen the incident.

Before a risky migration, establish backup timestamp, restore destination, estimated recovery duration, and acceptable data loss with the authorized operator. Verify restore behavior on an isolated target when available.

Keep new code compatible with expanded schema during rollout. Delay removal of old fields until old readers and writers have stopped. Prefer a forward repair for data transformations that cannot be reversed faithfully.

During recovery, prevent uncontrolled retries and avoid directing full traffic at a cold dependency. Observe backlog and capacity before ramping up. [Google SRE overload guidance](https://sre.google/sre-book/handling-overload/) explains why a recovered process can still fail under accumulated load.

Report service restoration separately from data recovery and remaining reconciliation.
