# Offline synthetic screening demo

This example is fully synthetic. No row represents a real provider, beneficiary, claim, payment, or
allegation. Its identifiers deliberately cannot be NPIs, and every input row carries an explicit
`synthetic_record=true` marker.

The service identifier `TSTAA` is an invented, letters-only test token, registered alongside
`TSTAB` through `TSTAD` for the Part B tests. These are not assigned billing codes or officially
reserved test codes. Their labels, such as "Synthetic test service A", have no clinical meaning.
The examples exercise grouping, filtering, ranking and replay; they do not validate medical coding.

Every identity-like provider value used by the fixture or its deterministic shortlist is listed in
the [synthetic identity registry](../synthetic-identities.yaml). The registry is an inventory and a
set of structural validation rules, not proof that a value cannot identify a real person or
organization. Any value that cannot be confirmed within the registered synthetic design, or that is
found to resolve to a real identity, must fail closed and must not be used in the demo.

The demo exercises the maintained Part B shortlist logic. It shows how a latest-year screen can
prioritize temporally persistent and current-year signals, retain a provisional broader-peer route,
and exclude an unflagged row. Those routes are review-priority signals only; they do not establish
improper billing, medical necessity, intent, or fraud.

After Python is available, the demo itself needs no network access, Docker, PostgreSQL, or external
data. From the repository root, run the installed command:

```powershell
.\.venv\Scripts\drlf.exe onboarding-demo tmp/onboarding-demo
```

Or run the module directly from a clean source tree:

```powershell
$env:PYTHONPATH = "src"
python -m drlf.onboarding_demo tmp/onboarding-demo
```

By default, the command verifies that the input SHA-256 matches this committed fixture before running.
That pin makes the synthetic-data assurance part of the executable check rather than relying only on
column labels. The bounded input is captured once and the analysis runs against that immutable local
snapshot, so a concurrent edit cannot separate the analyzed bytes from the recorded input hash. The
command also refuses to overwrite either output. It writes:

- `synthetic-part-b-shortlist.csv`, a deterministic three-row review list; and
- `synthetic-part-b-demo-run.json`, an audit manifest containing the input and output hashes, byte
  counts, row counts, years, input path, fixture-assurance status, exact Python callable, and
  interpretation boundary.

Maintainers developing another entirely synthetic fixture may pass `--allow-unpinned-input` together
with `--input PATH`. That is an explicit safety opt-in: the run manifest says the input is
operator-asserted and unpinned because marker columns and a non-NPI identifier cannot independently
prove that every value was invented. Never use that option for claims or other real provider data.

Use a new output directory for a second run. Matching hashes demonstrate exact replay of the
committed fixture and method; they do not validate the method against real-world outcomes.

For this version of the example, the expected SHA-256 values are:

- input: `6680b16dbe10035eb153fd732eb62fcd41e187706ca7d45544555bde883b8f8e`;
- shortlist: `b6e93c1908334e31687cbf3934e2c65f82a718f470ba43d9429a5debd36998c4`; and
- run manifest: `4ccc0c422bcf49cc8e318707efef9d1b11bb2c16ccadf53ad4bdfe2c9ed2a31b`.
