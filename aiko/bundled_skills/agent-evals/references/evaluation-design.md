# Evaluating tool-using agents

Construct realistic tool fixtures with missing data, malformed responses, delays, expired authorization, and ambiguous completion. Grade both the resulting artifact and consequential side effects. A persuasive final answer cannot compensate for an unauthorized mutation.

Define task success independently from one particular tool sequence when multiple valid approaches exist. Add trajectory checks only for essential boundaries such as no secret disclosure or no write in a read-only task.

Keep graders blind to the proposed fix when feasible. Calibrate judgment against human-reviewed examples, inspect disagreements, and avoid using the same model's unsupported self-assessment as the sole grade.

Run repeat trials for stochastic behavior and report success rates with sample sizes. Track latency, usage, and rework alongside correctness. Use held-out cases to assess whether a prompt change generalizes.

Consult [Anthropic evaluation guidance](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) for outcome and trajectory evaluation.
