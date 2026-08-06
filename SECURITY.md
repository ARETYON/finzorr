# Security policy

## Reporting

Open a private GitHub security advisory on this repository (Security →
Advisories → Report a vulnerability). Please do not open public issues for
suspected vulnerabilities.

## Dependency-audit triage (CI is enforcing)

`security-scans.yml` runs `pip-audit` and `npm audit --audit-level=high`
WITHOUT `|| true` — a new advisory fails CI on every PR. When that happens:

1. **Fixable** (patched version exists): bump the dependency. Prefer the
   patched line over a downgrade (e.g. react-router GHSA-qwww-vcr4-c8h2 was
   fixed by moving to `react-router@8.3.0`).
2. **Not applicable** (the vulnerable code path can't be reached — document
   WHY in the PR):
   - Python: add `--ignore-vuln <ID>` to the pip-audit step with an inline
     YAML comment naming the advisory and the reason.
   - npm: use an `overrides` entry in `package.json` to force the patched
     transitive version; if none exists, document the accepted risk the same
     way.
3. **No fix exists yet**: an ignore entry is acceptable ONLY with a linked
   tracking issue and a revisit date in the comment. Never leave an ignore
   without an expiry note.

The weekly scheduled run exists to catch advisories published between PRs.

## Hard security invariants (regression-tested in `backend/tests/`)

- NL2SQL executes on a Postgres role that can only SELECT from
  `fundamentals` (DB-enforced, migration `fb685f49d3ca`); the validator
  additionally rejects writes, `SELECT INTO`, table-free queries, and
  multi-statements.
- `read_url` re-validates every redirect hop against the private-address
  guard.
- All retrieved/extracted content (web pages, RAG excerpts, recalled
  memories, connector data) is fence-wrapped as untrusted data with fence
  tokens neutralized.
- Uploads are magic-byte validated, size-capped before buffering, and rate
  limited.
- The code sandbox runs unprivileged (`--user 65534 --cap-drop=ALL
  --security-opt=no-new-privileges --network=none`) and is killed on every
  exit path.
