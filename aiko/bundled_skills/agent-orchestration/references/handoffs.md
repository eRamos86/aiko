# Handoffs and retry boundaries

Give a worker the task, relevant current evidence, owned files or systems, permitted operations, dependency inputs, and expected artifact. Include the selected skill text when the worker cannot access the coordinator's library.

For shared checkouts, establish one writer for each file and inspect changes before integration. Do not overwrite another worker's edits because they differ from the starting snapshot. Parallelize independent ownership; sequence overlapping mutations.

Persist or return dispatch identifiers before retrying a transport failure. Query the original task when possible: an ambiguous timeout may mean work is already running. Avoid duplicate side effects by using the execution system's supported idempotency mechanism.

A handoff should distinguish changed files, verification, unresolved risks, and pending actions. Validate outputs against acceptance criteria; worker confidence is not evidence. See [Anthropic agent patterns](https://www.anthropic.com/engineering/building-effective-agents) for delegation tradeoffs.
