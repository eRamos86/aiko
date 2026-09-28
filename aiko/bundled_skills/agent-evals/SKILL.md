---
name: agent-evals
description: Evaluate agent behavior and regressions with realistic tasks, observable outcomes, and calibrated graders.
---

Define desired behavior and failure costs before cases. Include ordinary tasks, ambiguity, tool failures, permission boundaries, and long context. Separate development examples from held-out data.

Grade outcomes and artifacts, not repeated phrases. Combine deterministic objective checks with calibrated human judgment where needed. Record model/configuration, environment, and repeated stochastic trials.

Read [references/evaluation-design.md](references/evaluation-design.md) for tool-use evaluation. Isolate side effects; live actions retain ordinary authorization requirements. Inspect traces before prompt changes.

Consult [Anthropic evaluation guidance](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents). Report success, failure classes, cost, and latency together. Passing selected cases establishes only tested scope, not general safety or compliance certification.
