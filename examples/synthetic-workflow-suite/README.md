# Offline synthetic research-workflow suite

This suite is invented teaching material. It contains no real person, organization, provider,
beneficiary, claim, payment, enrollment record, license record, or allegation. Every identity-like
token is declared in the repository's synthetic identity registry.

The scenarios exercise seven boundaries that a complete public agent harness must preserve:

- bounded acquisition stops on page, byte, record, retry, duplicate, and pagination limits;
- Part D vaccine attribution begins with an aggregate-only unresolved-role state, keeps claims,
  beneficiaries, and fills distinct, uses a compatible unsuppressed national total rather than the
  sum of visible provider rows, and exercises exact-product peers plus a possible reassignment
  discontinuity before separately resolving protocol prescriber, dispenser, administrator, biller,
  and payment-recipient roles;
- operational context keeps source-specific affiliations, geography, and monetary meanings separate;
- DMEPOS interpretation resolves the billing unit, exact peer grain, component coverage, and
  point-in-time provider-status observations;
- a case records supporting and weakening evidence, completes two explicitly bounded actions that
  do not change its triage disposition, then reaches a research cutoff, receives a disposition, and
  completes a retrospective;
- a candidate learning retains evidence lineage without being silently promoted, and a proposed skill
  change stops until explicit user permission exists; and
- a baseline upgrade plans replacements, additions, local preservation, conflicts, and removals
  without mutating any local content.

The exercise is deterministic and offline. It does not need Docker, PostgreSQL, network access, or a
claims dataset. From the project root:

```powershell
$env:PYTHONPATH = "src"
python -m drlf.synthetic_workflows tmp/synthetic-workflow-suite
```

The command reads the scenario and registry under explicit byte caps, verifies that their canonical-LF
hashes match the committed fixtures, confirms that every uppercase synthetic identity token is
registered, refuses to overwrite its result, and writes `synthetic-workflow-results.json`. Its hashes
provide a canonical-LF format contract validated on Windows; they do not establish detector
effectiveness or support a conclusion about real activity.

Fixture maintainers may use `--allow-unpinned-input` only after independently confirming that modified
inputs contain invented values. The result then labels the inputs as operator-asserted because marker
fields and registry membership cannot independently prove that an identity is synthetic.
