---
name: investigate-pharmacy-protocol-attribution
description: Resolve pharmacy-administered drug or vaccine outliers where a prescriber NPI may represent protocol, supervision, or default attribution. Use before clinical interpretation; do not infer dispenser or payee identity without evidence.
---

# Investigate pharmacy protocol attribution

Determine which operational roles a Part D prescriber aggregate can and cannot establish before
interpreting its scale.

1. Verify source year, Part D population, drug identity, aggregation grain, redaction, and the exact
   meaning of claims, beneficiaries, fills, days, and total drug cost. Total drug cost is not
   automatically Medicare payment or prescriber revenue.
2. Compare the attributed NPI/drug series with the compatible CMS national geography/drug total. Use
   a CMS total based on the full underlying data; do not sum suppressed provider rows.
3. Calculate share, growth, concentration, entry/exit, and attribution reassignment without assuming
   beneficiary geography or personal encounters.
4. Build a role matrix for patient-specific prescriber, protocol or standing-order provider,
   supervising provider, administrator, dispenser, pharmacy location, transaction submitter, biller,
   plan, and payment recipient. Mark every unsupported role unknown.
5. Build separate effective-dated lanes for enrollment, licensing, employment, directories,
   contracts, pharmacy networks, administrators, PBMs, and corporate relationships. A current profile
   does not prove a historical relationship.
6. Test whether a discontinuity aligns with source methodology, product or policy change, network
   scale, a protocol/default reassignment, or another ordinary explanation. Keep an
   infrastructure-screen route experimental.
7. Identify the nonpublic fields needed to resolve dispensing pharmacy, service/administration site,
   transaction submitter, prescriber-qualifier use, BIN/PCN/group, plan/PBM, reimbursement, reversal,
   and payment-recipient questions.
8. Apply `validate-claims-anomalies` and the decision-relevant cutoff. Do not keep researching a case
   when the decisive roles require nonpublic transaction records and no bounded public action can
   change disposition.

Use only the registered synthetic Part D protocol exercise in the public checkout. Do not contact a
provider, pharmacy, network, or other subject without explicit authorization.
