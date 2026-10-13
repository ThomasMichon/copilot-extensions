# Baseline and first vertical-slice gate

Back to [the canonical effort](README.md). These are **agent-recommended**
implementation criteria under the reviewed architecture, not live adoption
permission or a claim that the full product exists.

## Actual baseline

Source baseline: `dev` commit `a27d57dc2ddc83fc1a8e92a0a026e5f64b5cb7b2`.
The installed fleet-manager test environment reported package version
`0.1.0.dev1`, on native Windows with Python 3.11.16. Each probe used an
isolated interpreter (`-I`), a working directory outside the source checkout,
captured headless subprocess output and a ten-second deadline. The imported
package was verified to be under that interpreter's installed site-packages.

| Actual installed invocation | Exit | Single observed elapsed time | Meaning |
| --- | --- | --- | --- |
| `python -I -m fleet_manager --version` | 0 | 728.26 ms | Installed client launches and identifies its version |
| `python -I -m fleet_manager --help` | 0 | 300.46 ms | Only `plan` and `inspect` are exposed |
| `python -I -m fleet_manager setup` | 1 | 283.02 ms | Setup is unsupported, explicitly refused |
| `python -I -m fleet_manager update` | 1 | 336.41 ms | Update is unsupported, explicitly refused |
| `python -I -m fleet_manager recover` | 1 | 266.82 ms | Recovery is unsupported, explicitly refused |

These are one observational sample per command, including Python/process startup.
They are **not** a benchmark, availability target or comparative performance
claim. No actual product Gateway/node daemon, application installer, signed
release or recovery/cutover command exists in this baseline. Their successful
launch/update/recovery latency and host-survival behavior therefore cannot be
measured yet. Do not turn package count or local journal tests into those numbers.
Client packaging/installed diagnostics and managed client lifecycle are separately
owned by #6424; its later delivered commands will need a new baseline.

### Existing V1 reuse evidence, not live cutover proof

| Existing implementation | Reusable seam | Unproven integrated-product behavior |
| --- | --- | --- |
| `libs/versioned-runtime/versioned_runtime.py` | Immutable version directories, completed-slot evidence, stable selected Python, atomic marker selection and last-known-good fallback | Product assembly, signed trust, OS registration, application/schema health and ownership transfer |
| `libs/zdd/src/zdd/cutover.py` and `routing.py` | Injected passive startup/health, atomic local routing publication, pre-commit route/drain rollback | Gateway journal-writer fencing, post-transfer state recovery, cross-host exclusion and accepted host survival |
| `libs/zdd/src/zdd/claims.py` | Generation-scoped conflict/reclaim decisions | Durable product ownership, live process proof or an external effect-owner fence |
| `libs/zdd/src/zdd/lifecycle.py` | Structured fail-open lifecycle event logging | A mandatory audit/admission transaction or proof activation succeeded |

This evidence is source inspection with existing unit-test references, not a
real V1 update, machine reboot, live service replacement or measured recovery.
No V1 service was activated, upgraded, retired or enrolled to manufacture a
baseline. The normal V1 standalone services keep their current owners.
The integrated product is not an eligible core-agent marketplace installation
cell. Generic route flipping also cannot substitute for excluding the old
Gateway journal writer.

## First assembly prerequisite

Use actual local role-selected artifact bytes, not supplied observation hashes,
for bounded integrity preflight. Preserve the existing release schema and thin
client dependency boundary. Do not modify the separately owned client installer,
package build resources, diagnostics or auth/routing prototypes.

The implementation must prove exact manifest-content binding, only selected
artifacts being opened, bounded streaming/aggregate bytes, explicit refusal of
missing/tampered/nonregular/linked/observably changing inputs, and installed
operation outside the checkout. Output must explicitly deny signature proof,
activation permission and readiness. This is a prerequisite, not completion of
release authentication, product assembly or Phase 1.

## First full local vertical slice

Before calling the product's lifecycle or coordination usable, prove:

1. **Owned, authenticated assembly:** the same selected product release pins
   client/Gateway/node roles and protocol/schema compatibility. Authenticate its
   artifacts using a reviewed explicit trust policy, stage immutable owned
   bytes, and reuse the shared version-slot/stable-launcher primitives.
   Client-only setup has no server, Docker, credential or provider effects.
2. **Real local operation:** separately started Gateway and node processes admit
   an identified objective, create an owned workspace, launch a real agent/mux
   host, deliver a content-bound prompt, retain the original outcome and settle
   obligations only with actual owner evidence. No fake host or direct journal
   callback alone satisfies this gate.
3. **Accepted-work survival:** replace the controller while the accepted host
   runs. Reattach by original host/resource/operation identity, including a
   lost-prompt acknowledgment and honest UNKNOWN. No automatic duplicate prompt,
   new objective identity or elapsed-time release.
4. **Recoverable lifecycle:** fail candidate preflight/health before ownership
   transfer and leave the old owner usable. Crash at transfer boundaries and
   recover to exactly one fenced writer without overwriting acknowledged work
   or reviving a stale generation. Stop admissions rather than overlap writers.
5. **Independent operation:** first-touch setup/update/doctor/recovery works
   without Copilot, marketplace/plugin caches or session hooks. Missing,
   incompatible and auth-needed failures remain distinct. Measure actual
   activation/admission/recovery durations here before setting a performance
   target; no target is inferred from this offline baseline.

The next multi-node gate is the same flow through one Gateway and two separate
nodes, with reconnect/result replay, revoked/stale/cross-domain denial, slow-node
isolation and partition plus blocked reassignment. It is not waived by a local
pass. All unit/E2E/first-touch/live tiers and platform limitations must be
recorded per slice.

## Ownership and admission gates

- #6240 owns product release verification and Gateway/node lifecycle assembly;
  #6424 owns the client packaging/diagnostics/managed installation lane.
- #5789 owns JWT/JWKS principal authentication and separate connector proof.
  Agree principal/grant attribution, connector identity and the separation of
  connector epoch from allocation generation before resident integration.
- #6030 retains deployment adapter adoption; #5861/#5862 retain reconciliation
  and observation ownership. No competing V1 migration or installer is proposed.
- Successful local proof does not authorize fleet enrollment, credential
  changes, capacity expansion, live deployment or destructive V1 retirement.
  The parent effort remains Active until every Plan/Validation item is resolved.
