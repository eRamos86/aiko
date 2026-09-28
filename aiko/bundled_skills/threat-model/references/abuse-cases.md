# Boundary-focused abuse cases

For each boundary, ask what an actor on the less trusted side can supply, replay, replace, or observe. Example cases include using another tenant's object ID, substituting a callback destination, replaying a signed event, or making an agent treat retrieved text as authorization.

Write a concrete chain: precondition -> entry point -> missing check -> impact. Record the existing guard and evidence for whether it is effective. A hypothetical vulnerability without a reachable path belongs under uncertainty.

Consider asynchronous boundaries as well as HTTP routes: queue consumers, cache keys, exports, and background jobs may lose tenant or authorization context. Test mitigations using controlled identities and synthetic data.

Use [OWASP threat modeling](https://cheatsheetseries.owasp.org/cheatsheets/Threat_Modeling_Cheat_Sheet.html) for decomposition. The result should prioritize actionable risks and residual assumptions, not imply that an exhaustive list or certification was produced.
