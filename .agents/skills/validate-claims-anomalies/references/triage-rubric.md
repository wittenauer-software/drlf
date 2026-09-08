# Claims-anomaly triage rubric

Score each lane as `satisfied`, `partial`, `failed`, or `unknown`. Do not add the lanes into a pseudo-precise fraud probability.

## Lanes

1. **Identity** — The relevant provider, organization, role, service or drug, and payment recipient are resolved to the extent public data allow.
2. **Semantics** — Measures, dollars, population, aggregation, suppression, and exclusions are correctly interpreted.
3. **Magnitude** — The absolute scale is material and not driven solely by price or a tiny denominator.
4. **Peer robustness** — The anomaly persists under credible specialty, geography, entity, place-of-service, and temporal comparisons.
5. **Operational plausibility** — Capacity and utilization are tested without assuming that the named NPI personally performed or profited from every attributed event.
6. **Policy and clinical context** — Relevant coverage, reimbursement, code, product, and recommendation changes are incorporated.
7. **Ordinary explanations** — Material lawful alternatives were investigated and either supported, rejected with evidence, or left explicitly unresolved.
8. **Payment relevance** — The hypothesis identifies a potentially false representation or payment condition rather than unusual utilization alone.
9. **Reproducibility** — A second researcher can trace the result from an immutable source snapshot through code or query to the reported finding.
10. **Investigative next step** — The assessment identifies specific nonpublic evidence capable of confirming or refuting the hypothesis.

## Public continuation gate

Adopted process safeguard: **Stop when public actions cannot change disposition**. Resolve its current
local ID and confirm its scope through the active workspace learning catalog.

The investigative-next-step lane and the public continuation decision answer different questions. A
case may precisely identify decisive nonpublic evidence while having no worthwhile public research
left.

A proposed public action qualifies only when all five conditions are met:

1. it asks a precise unresolved question;
2. it names a lawful, specific, presently accessible source;
3. plausible results would distinguish competing explanations;
4. at least one plausible result would change a lane or disposition; and
5. time, queries, download size, and compute are explicitly capped.

Classify each proposal as `proceed`, `method-backlog`, or `do-not-pursue`. Conduct a cutoff review when
a supported ordinary explanation emerges, before a bulk deep dive, after two consecutive bounded
actions produce no lane change, and before retaining `active` at a material milestone. After two
no-change actions, continuation requires a genuinely different source and a documented path to a
disposition change.

## Disposition guidance

- `explained`: A supported ordinary explanation accounts for the narrowly stated observation; this
  does not certify every underlying transaction as valid.
- `dismissed`: A foundational lane such as identity, semantics, reproducibility, or payment relevance failed.
- `monitor`: The pattern remains unresolved, but no qualifying public action remains or decisive
  evidence is presently nonpublic.
- `active`: The anomaly survives initial challenges and at least one qualifying bounded public action
  remains.
- `counsel-review`: All foundational lanes are satisfied, material alternative explanations are addressed, and the user has reviewed the proposed internal dossier. This is not a finding of fraud and does not authorize contact.
