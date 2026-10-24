# POSIX marker-only bootstrap readiness

Parent: [Update Flow Observability](README.md).
Coordination: #7743. Existing #4195 admission, stale-process and lifetime work,
the #6812 repair pipeline, #8370 logging and held #6859 / #7833 remain separate.

## Request and bounded ownership

The operator requested a source-installed development loop:

> Work the problem to get the dispatch system allocating running tasks. Adjust variables and redeploy as needed in a tight loop.

`tinkling-capacitor` owns only the completed-runtime POSIX bootstrap no-op and
its directly dependent family coverage. This does not transfer another owner's
installer, broker, PID-reaping or publication authority.

## Established discrepancy

The ordinary POSIX bootstrap fast path still requires a legacy `.venv` or
`venv` link in addition to matching deployed and payload versions. Normal
marker-only installations intentionally have neither link. A completed,
unchanged runtime therefore enters reconciliation instead of the intended
no-op. A source-installed full-plugin worker exposed repeated bootstrap hook
timeouts while otherwise reaching real task execution.

This is a readiness predicate defect, not evidence that every timeout has the
same cause. Missing runtimes, changed content or versions, malformed completion,
and uncertain publication still require their existing handling.

## Plan

Implementation choices below are _(agent-recommended)_:

- Reuse the already-vendored canonical versioned-runtime resolver in its
  read-only mode. Do not add another interpreter resolver, repair authority,
  readiness cache or legacy runtime link.
- Permit the cheap no-op only for the expected selected completed generation
  and matching payload/deployment identity. A complete fallback generation
  alone must not make an invalid or different current selection look healthy.
- Preserve valid legacy behavior and all missing, changed, malformed,
  first-provision, refusal and reconciliation paths. Keep guidance writers and
  structured hook output intact.
- Update directly affected hook-family consumers through their existing
  synchronization conventions, with only necessary docs and changefiles.
  Review the PowerShell counterparts for parity rather than inventing a
  second platform's defect.
- Keep atomic admission and safe stale-owner recovery with #4195. Do not
  introduce or relax age-based process termination, publication consent,
  service lifetime, budgets or exception handling.

## Validation plan

- Exercise the actual hook and canonical resolver on owned temporary runtime
  files. A complete marker-only current runtime with matching identity invokes
  no installer or reconciliation child and creates no legacy links.
- Cover valid legacy installation, missing/current/fallback selection,
  incomplete or corrupt completion, mismatched expected generation and version,
  changed deployment identity and existing refusal paths.
- Retain exact hook output and guidance behavior, and measure actual before/after
  child count and elapsed cost. Do not call a source-shaped mock an installed
  or native startup proof.
- Run the focused contained tests and bootstrap synchronization/install-contract
  guards without increasing timeouts or growing required unit-only CI into an
  effectful matrix.
- Redeploy the source candidate into the disposable full-plugin integration
  fixture and execute real task allocation, submission and independent
  evaluation. Record process readiness separately from first-turn work.
- Obtain required review, CI and the normal containing release. Drive authorized
  installed rollout and running-state proof separately; source merge is not
  deployment or parent-effort completion.

## Journal

- Claimed the narrow marker-only no-op slice on #7743 after checking current
  source and existing bootstrap coordination. No implementation or host
  deployment has been performed by this slice.
