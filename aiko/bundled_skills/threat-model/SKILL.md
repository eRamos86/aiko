---
name: threat-model
description: Analyze security threats and trust boundaries for designs or data-flow changes; not a certification audit.
---

Map assets, actors, entry points, stores, and trust boundaries from actual diagrams and code. Separate assumptions from verified controls. Read [references/abuse-cases.md](references/abuse-cases.md) when enumerating cross-boundary threats.

For each credible abuse path, identify starting access, crossed boundary, affected asset, and control gap. Rank by plausible impact and exposure. Consider tenant isolation, privileged actions, and untrusted content where relevant.

Propose targeted mitigations and falsifiable checks. Do not silently change identity architecture or perform active probes outside authorized scope. Deliver evidence, residual risks, and unknowns. Use [OWASP threat modeling](https://cheatsheetseries.owasp.org/cheatsheets/Threat_Modeling_Cheat_Sheet.html) as a reasoning aid; this review does not establish compliance or certification.
