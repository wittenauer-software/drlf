# Getting started with DRLF

DRLF v0.1 is a repository-first, Windows 11 x64 release. Linux/POSIX environments are neither tested
nor supported in v0.1. Run it from a checked-out repository; no PyPI package, container image, hosted
service, or browser application is part of this release.

Read [AGENTS.md](../AGENTS.md) before using an agent. The public checkout is a distribution,
development, and synthetic-demonstration surface. It is not a place for names, NPIs, real candidate
rows, source observations, screenshots, allegations, or case records.

## Choose the correct workspace

| Workspace | Use it for | Never put here |
|---|---|---|
| Public DRLF checkout | Read the methods, improve generalized code, and run registered synthetic exercises. | An identifiable lead, retained real-data query, case evidence, or local research learning. |
| Separate private research workspace | Authorized public-source acquisition, named cases, local data, PostgreSQL rows, reports, and local learning. | Public remotes, shared Git history with the public checkout, or unauthorized data. |

An ignored folder or private branch inside a public checkout is still on a public publication
surface. Real research starts only after DRLF creates a separate directory with fresh Git metadata
and the operator verifies its filesystem, synchronization, backup, and remote audience.

## Windows prerequisites

- Windows 11 x64 with PowerShell 7;
- Git;
- Python 3.13;
- Docker Desktop with Docker Compose V2 for PostgreSQL-backed workflows; and
- internet access only when installing dependencies or acquiring an authorized public source.

Budget storage for the checkout, Python environment, container image, and database separately from
any source acquisition. Every download also needs explicit raw, extracted, database, and safety-margin
limits.

## Install from the checkout

From the DRLF repository root in PowerShell:

```powershell
py -3.13 -m pip install "uv==0.8.14"
py -3.13 -m uv sync --all-extras --locked --native-tls
.\.venv\Scripts\drlf.exe --help
```

`uv.lock` fixes the complete dependency graph; `--locked` must fail rather than rewrite it. The last
command is the interface inventory. DRLF's maintained commands and their repository assets ship
together in v0.1.
Documentation is not evidence that an omitted runtime asset exists: a release candidate missing
`src/drlf/` or an asset named by a command is incomplete and must not be represented as a working
harness.

## Run local checks

The supported validation path is local:

```powershell
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

DRLF intentionally ships no hosted automation or automatic security checks. Record the exact local
results in the pull request or review record.

## Run the offline synthetic workflow

The committed synthetic workflow is the safe first behavioral test:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m drlf.synthetic_workflows tmp\synthetic-workflow-suite
```

It must use only identities registered in `examples/synthetic-identities.yaml`, read inputs under
fixed byte caps, refuse to overwrite output, and write a deterministic JSON result. The exercise
covers bounded acquisition, Part D attribution, operational roles, DMEPOS interpretation, a complete
case lifecycle, the learning/skill-permission loop, and non-destructive upgrade planning. It does not
measure detector effectiveness or support a conclusion about real activity.

The smaller maintained Part B screen is also available:

```powershell
.\.venv\Scripts\drlf.exe onboarding-demo tmp\onboarding-demo
```

Use a new output directory for every run; both exercises refuse to replace existing results.

## Configure PostgreSQL for public development

PostgreSQL is optional for the offline synthetic exercise and required for database-backed research.
For public-checkout development or disposable database tests, create a local environment file and edit
every placeholder:

```powershell
Copy-Item -LiteralPath .env.example -Destination .env
```

Choose a unique `COMPOSE_PROJECT_NAME` for every checkout or worktree. If multiple stacks run at the
same time, also choose a distinct `POSTGRES_PORT` and use the same port in `DATABASE_URL`. Reusing a
Compose project name attaches the same named volume; a Git clone never contains an earlier database.
Replace the exact example value `drlf-change-this-workspace-id` even though it is syntactically valid;
`doctor` can validate its format but cannot prove that it is unique on the Docker host. Replace
`replace-with-a-local-password` in both `POSTGRES_PASSWORD` and `DATABASE_URL` with the same value.
Percent-encode URL-reserved password characters in `DATABASE_URL`, or use letters, digits, and hyphens
to avoid that ambiguity.

Start the service:

```powershell
docker compose up -d --wait postgres
```

Verify and initialize the database:

```powershell
.\.venv\Scripts\drlf.exe db-check
.\.venv\Scripts\drlf.exe db-migrate
```

Never point disposable tests at a research database. Keep downloaded bytes immutable under ignored
`data/raw/`; PostgreSQL stores normalized rows and load provenance, not source-file blobs.

Now run the complete environment check:

```powershell
.\.venv\Scripts\drlf.exe doctor
```

Every required check should report `PASS`, followed by `READY`. The check is diagnostic, not an
authorization to acquire data or begin an identifiable case.

## Create the private research workspace

Do not copy the public checkout by hand and do not add the public repository as a Git remote. Use the
shipped lifecycle commands and the root `baseline-manifest.json` inventory. Compute its SHA-256 in
PowerShell, compare it with the release's separately published value when one is provided, and retain
the lowercase digest for the bootstrap command:

```powershell
$publicRoot = (Resolve-Path -LiteralPath .).Path
$baselineManifest = (Resolve-Path -LiteralPath .\baseline-manifest.json).Path
$baselineHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $baselineManifest).Hash.ToLowerInvariant()
$baselineHash
```

Computing a digest proves which local bytes the command will use; by itself it does not establish who
published those bytes. Choose a new absolute destination outside the public checkout, then run:

```powershell
$privateRoot = Join-Path (Split-Path -Parent $publicRoot) "drlf-private-research"
.\.venv\Scripts\drlf.exe workspace-bootstrap `
  $publicRoot `
  $baselineManifest `
  $privateRoot `
  --expected-version "0.1.0" `
  --expected-manifest-sha256 $baselineHash `
  --git-author-name "DRLF Researcher" `
  --git-author-email "researcher@local.invalid" `
  --compose-project-name "drlf-research-001" `
  --postgres-port 55432 `
  --max-paths 5000 `
  --max-file-bytes 8388608 `
  --max-total-bytes 268435456
```

Bootstrap copies only inventory-listed baseline files, creates fresh local Git metadata with no
remote, and writes an ignored `.env` with a generated password plus the chosen Compose name and port.
It does not infer that the destination is private. Review the generated paths and the real filesystem,
synchronization, backup, and remote audience. Only then finalize:

The generated local-learning index uses namespace `LOCAL-KNOWLEDGE` and origin `private-workspace`.
Workspace doctor and finalization check this index envelope; card content and evidence lineage still
require the shipped knowledge validator. An older or malformed index is reported without rewriting
local learning. Review and reconcile its contents before finalizing or resuming research.

```powershell
.\.venv\Scripts\drlf.exe workspace-finalize `
  --root $privateRoot `
  --confirm-private-filesystem `
  --confirm-private-sync `
  --confirm-private-backups `
  --confirm-no-public-remote

.\.venv\Scripts\drlf.exe workspace-doctor --root $privateRoot
```

The doctor must report `WORKSPACE READY`. Next, install the locked environment inside that private
workspace, start its uniquely named PostgreSQL service, apply migrations, and run the full doctor:

```powershell
Set-Location -LiteralPath $privateRoot
py -3.13 -m uv sync --all-extras --locked --native-tls
docker compose up -d --wait postgres
.\.venv\Scripts\drlf.exe db-check
.\.venv\Scripts\drlf.exe db-migrate
.\.venv\Scripts\drlf.exe doctor
```

Before using a retained source release, inspect the private workspace's manifest records and then
check whether their referenced source bytes are actually present:

```powershell
.\.venv\Scripts\drlf.exe release-list
.\.venv\Scripts\drlf.exe source-status
git status --short
```

These are separate checks: `doctor` does not replace either source command, a manifest is not its
source bytes, and none of the three commands establishes that a manifest is committed. A new
workspace may correctly report no releases. Optional hashing in `source-status` requires an explicit
aggregate byte cap.

The root `baseline-manifest.json` is mandatory. Stop rather than approximating this boundary with a
copy, fork, branch, or nested directory.

## Start research safely

Inside a verified private workspace, begin with the [capability catalog](capabilities.md), the
[data-source catalog](data-sources/README.md), the [methods index](methods/README.md), and the
[skill catalog](../.agents/skills/README.md). Select the smallest source and resource envelope that can
answer a precise question. Create a case before attaching data or analyses to it, keep contrary
evidence, and stop when no bounded public action can change the disposition.

Every registered command has an explicit public-safe or real-data classification. Real acquisition,
manifest, load, and analysis commands fail closed unless the current directory is the private system
of record or a finalized, passing private workspace; case initialization also validates its explicit
root. This does not make DRLF a filesystem sandbox. Continue to review every explicit input and
output path and its audience before running a command.

For problems with the supported setup, see [SUPPORT.md](../SUPPORT.md). Sensitive security intake is
closed as described in [SECURITY.md](../SECURITY.md); never post vulnerability details, a research
subject, or a fraud tip in a public report.
