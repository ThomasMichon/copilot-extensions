# S3a/b authority-work proposal — no functional admission

[Episode](s3ab.md) · [Reviewed S3 design](codespaces.md) · #7949

## Decision

Keep #8143 **draft and unmerged**. Existing APIs can supply the values for an
immutable wave-local handle, but **do not supply a cheap, complete
current-authority revision** for all supported account/configuration/credential
sources. In particular, no non-secret revision primitive was found for the
GitHub CLI's opaque keyring credentials. A fabricated revision, a token TTL,
or a selected-file watch list would weaken the requested guarantee.

The minimal next step is a **source-owned read-result contract**, not a new
credential provider, token cache, authority ledger, service or global framework.
The contract below is proposed only. No shared API or runtime behavior changes
are admitted or implemented by this document.

## What existing APIs actually provide

Paths below are under `plugins/agent-codespaces/src/agent_codespaces/` unless
qualified otherwise. Inspection is at `6bf5649a3a068c7e370bbae38009abfc02fe5a49`.

| Existing function/type | Reusable current information | Missing information |
|---|---|---|
| `account_candidates.candidates()` | Ordered, deduplicated candidate tuple from mapping, bindings, provider credentials, config and active account | Does not return the already-read routing/config inputs, their completeness or a current-revision check |
| `gh_account.account_inventory()` / `_account_inventory()` | Fresh account map and bulk resolution data; malformed/failed peer results raise | `AccountInventory` contains no revision or authoritative dependency closure |
| `config.scan_config_providers()` / `ConfigProviderReports` | Actual active-plugin/config.d reports, canonical targets and partial/indeterminate findings | No complete revision for adopted roots, grafted state-root resolution, activation/settings and all additions/removals |
| `config.load_merged_config(provider_reports=...)` | Can reuse the exact provider scan rather than scan again | A merged config value is not proof that all source reads were authoritative; retained last-known provider values must not become fresh authority |
| `picker_auth.authorized_configs()` / `credential_env()` | Accepted-login selection and provider configuration location | Marker presence does not certify current keyring contents |
| `picker_auth.credential_token()` → `credential_store.load()` | Fresh existing provider read; storage kind is explicit and errors propagate | Neither OS-keyring reads nor `store-kind` expose a credential generation |
| `gh_account.token_for_account()` / `_token_for_account()` | Provider precedence and legacy cached CLI lookup; the wrapped CLI implementation performs a fresh read | No public fresh-read option; no cheap current-keyring revision |
| `worktrees.validate_context()` | Existing source/installation validation; explicit refusal is distinct from legacy absence | Not an account-map or credential-store revision |

The peer call is implemented by
`plugins/agent-worktrees/src/agent_worktrees/repos_cli.py`'s
`repos account list --json --with-resolutions` branch and
`repo_accounts.resolution_snapshot()`. It reads the canonical registry once
and emits `resolution_version: 1`, account mappings and resolved targets.
It does **not** emit a revision/dependency receipt. A CodeSpaces consumer must
not infer the peer's storage paths or import its private registry implementation.

## Concrete counterexamples

1. **Opaque credential rotation:** capture a handle while
   `credential_store.load()` or `gh auth token --user <selected-login>` returns
   credential T1. Replace the OS-keyring entry with T2 while keeping the selected
   login, accepted-login marker, store-kind file and configuration unchanged.
   Reusing T1 or comparing only those files cannot distinguish that history from
   no change. A fresh existing provider read can distinguish it; no current
   source API offers a cheaper credential-generation observation. This is not
   fixed by persisting a token hash.
2. **Non-target candidate change:** checking only the venue's selected credential
   does not prove that the other candidates' authority is unchanged. Another
   candidate's selection/credential can change the inventory's visibility or
   ambiguity. The wave must retain its complete candidate-set qualification;
   target-only refresh is not a substitute for that invariant.
3. **Partial config read:** provider reconciliation can retain a last-known
   `ConfigDropin` during an indeterminate scan. `repr(merged_config)` may stay
   identical. That equality does not turn the new partial observation into an
   authoritative unchanged scope.
4. **Wave-only revalidation:** a selection change after the start barrier but
   before a queued wake-capable config fetch can cause an effect under old
   authority. Discovering the change at the final publication barrier cannot
   undo a wake. Waiting for the slowest venue before publishing all results also
   violates the existing slow-venue independence requirement.

Consequently, pinning an explicit account is necessary but not sufficient.
There is no demonstrated semantics-preserving shortcut for opaque credentials
under the current APIs. An optimized path must not silently call them unchanged.

## Exact minimal proposed contract

### 1. Compose one snapshot, do not invent an authority owner

Proposed additive entry point in `account_candidates.py`:

```text
snapshot() -> CandidateSnapshot
candidates(...) -> unchanged compatibility projection
```

`CandidateSnapshot` contains:

- `source_context`: validated installation/source identity, existing namespace
  and installation generations where supplied;
- `candidate_accounts`: ordered explicit logins and selection provenance;
  an unresolved ambient identity is UNKNOWN for the owner path, never fallback;
- `routing`: the existing `AccountInventory`;
- `configuration`: the effective value plus the actual `ConfigProviderReports`
  and adopted/grafted source completeness;
- `observed_at`: monotonic observation time;
- `status`: complete / partial / unavailable, with bounded reason codes;
- `current_check`: an owner-supplied read operation, **not** a cached boolean;
- `revision_capability`: an existing authoritative revision, or explicitly
  `opaque`. No credential bytes or credential hashes are diagnostic fields.

The producer should return inputs it already read, allowing
`load_merged_config(provider_reports=...)` and candidate selection to share one
scan. This is one per-wave value object, not a persistent repository or daemon
registry. `candidates()` keeps its current return type and precedence.

### 2. Read-through revalidation with honest capability

Proposed result shape:

```text
check_current(snapshot, boundary) ->
    {state: SAME | CHANGED | UNKNOWN,
     coverage: selection/configuration/credentials,
     reason: bounded-code}
```

Boundaries are wave acquisition, wake-capable effect admission, retry admission,
and verdict publication. They are not tenant/mux item callbacks.

- `SAME` requires fresh coverage of **every relevant source**, using a real
  source-owned revision or the source's existing read-through operation.
- `CHANGED` invalidates the entire handle; stop admitting effects, suppress its
  actionable results and reacquire only on a counted later wave.
- `UNKNOWN` is never cached or reused. Error, unsupported revision, incomplete
  dependency coverage, missing provider and opaque credential state remain
  explicit; no guessed "unchanged" result.
- A revision capability can be exposed only by the owner of the actual
  source/selection. A self-generated hash or marker is not such a capability.
- For opaque credentials, use the existing fresh provider read if needed.
  Its API/CLI start remains in the cost ledger. **This fallback is not yet
  performance-qualified**, and the optimized path must remain blocked if it
  cannot meet the whole-work bound.

Minimum credential API candidate: additive
`gh_account.token_for_account(login, *, fresh=False)`, preserving existing
callers and provider precedence, with `fresh=True` selecting the existing
uncached CLI read. This removes the private `.__wrapped__` consumer dependency;
it does **not** create cheap credential revision or solve the fanout alone.
`picker_auth.credential_token()` and `credential_store.load()` must retain their
existing authority and failures. No new revision writer/provider is proposed.

### 3. Lifetime and publication

The owner-local handle binds wave identity and existing owner generation. It
expires no later than the existing inventory admission deadline; there is no
cross-wave cache or timer-based extension. Credentials flow only through the
existing short-lived effect environment/source; never into inventory rows,
logs or durable evidence.

Cache-free acquisition is skipped for zero due venues. One selected account
inventory is acquired per due wave; pages and retries remain separate requests.
Duplicate ownership, an errored account or a limit-sized page prevents complete
admission. Complete absence alone still does not authorize release.

Collect verdicts without authenticating once per tenant. Publication should be
a bounded completed-result batch, revalidated once at its actual effect
boundary, with existing compare-under-lock tenant generations. It must not
wait behind a stuck venue. If batching cannot preserve that property with the
existing owner API, propose the exact private batch-mutation helper separately;
do not replace atomic heartbeat with caller-side check-then-heartbeat or add a
second lease/registry. No batch helper is admitted here.

### 4. What prevents immediate implementation

The proposed composition is small, but the **cheap revision prerequisite is
not satisfied** for all sources. In particular, neither of these is acceptable:

- moving the same full `capture_scope()` into a differently named handle and
  calling it before every effect or tenant;
- enabling bounded reuse for opaque credentials without a current read while
  describing it as the existing current-auth guarantee.

Before further functional admission, the relevant owners must identify the
actual supported revision/read-through boundary and its cost. If opaque GH
credentials cannot satisfy both safety and the measured total-work budget,
report that limitation and retain the blocker. Do not invent a new credential
revision service or weaken semantics to obtain the two headline counts.

## Affected files and current claims

Read-only claim check on 2026-10-23 inspected all 33 open PRs returned with a
500-result bound. No open PR touched the shared account/config/credential files
below. This is collision evidence, not permission to take their ownership.

| Potential edit | Exact seam / ownership |
|---|---|
| `account_candidates.py` | `candidates`, proposed additive `snapshot`; namespace-discovery provenance #7998, active #5761 effort names `rattling-circuit` as discovery repair owner |
| `gh_account.py` | `AccountInventory`, `account_inventory`, `_account_inventory`, `token_for_account`; preserve #5761 bulk-resolution semantics and existing multi-account behavior |
| `config.py` | `scan_config_providers`, `load_merged_config`, adopted/grafted-source completeness; do not infer truth from retained config values |
| `picker_auth.py` | `authorized_configs`, `credential_env`, `credential_token`; provider-selection provenance only, not a new credential store |
| `credential_store.py` | `load` is the current provider authority; inspect/read-through contract only unless its owner identifies an existing revision. No proposed ledger or credential migration |
| Worktrees `repo_accounts.py` / `repos_cli.py` | Only if the routing owner supplies a genuine optional current-revision/read-result contract; outside this slice and not admitted |
| Existing S3a/b owner helpers | Consume the admitted handle; retire repeated full discovery; keep legacy probe/flattened APIs |

The #5761 issue has no structured claim markers in the fetched comments, but its
active effort's explicit owner is retained. Provider-auth provenance is #7797;
no open conflicting PR was visible, and no active ownership is inferred from
age or lack of a marker. #8143's own claim remains reserved.

## Measured realistic counter baseline

`tests/test_owner_authority_work.py` now executes the actual
`capture_scope → candidates → account_inventory/config/provider/credential`
call graph, not a constant authority value. External process, installation and
keyring boundaries are synthetic, fail closed on unclassified calls, and count
every reached request by category. Registry backing and Windows-direct
transport are synthetic and identical across baseline/draft. No real account,
secret, CodeSpace, keyring or service was contacted.

For ten venues, three accounts and four tenants/muxes, the complete cold frames:
The principal comparison enables mux observation only, with zero adopted
repositories/active config providers and existing per-venue account bindings.
Config/provider mutation controls exercise actual changed config and selection
inputs separately. This is a deliberately explicit workload, not a fleet census.

| Boundary requests | Baseline | Blocked draft |
|---|---:|---:|
| Account-map discovery process | 10 | 112 |
| Active-account discovery process | 10 | 112 |
| CLI credential process, CLI-backed variant | 3 | 349 |
| Inventory process / pages | 30 / 30 | 3 / 3 |
| SSH config process | 10 | 10 |
| Mux/SSH execution process | 40 | 10 |
| **All classified process requests, CLI-backed** | **103** | **596** |
| Provider-keyring API reads, provider-backed variant | 40 | 349 |
| **Process requests + provider API reads, provider-backed** | **100 + 40** | **247 + 349** |
| Expected/completed tenant observations | 40 / 40 | 40 / 40 |

The CLI-backed warm due baseline is **100**, not 103 (its legacy token cache is
already warm); the draft remains **596** in a complete frame. One venue is
13 cold / 10 warm versus 74. Zero due venues and the unchanged not-due poll
produce zero requests. These counters disprove a total-work improvement.

An exploratory warm provider-backed frame completed only 38/40 tenant
observations with a scope error. It is **partial**, not a cheaper successful
wave; its raw frame is retained. Earlier exploratory frames lacked completion
accounting and must not be used to claim complete qualification.
Later runs also retained 36/40 partial frames with a `PermissionError` classified
inside resolution; the exception class alone does not identify its root cause.
The initial recorder asserted full mux execution before emitting a partial
frame, causing one evidence-run failure. That failed run is preserved. The
recorder now distinguishes accounting correctness from observation completion;
no product health or test deadline was weakened.

An additional cold ten-venue comparison enables the actual default bridge probe
as well as mux observation, still with separate managers and no transcript
consumer. Complete frames show **123 -> 926** process-boundary requests with
CLI-backed credentials, and **120 + 50 -> 387 + 539** process + keyring-API
requests with provider credentials. Baseline/draft retain 20 config fetches and
20 balanced manager opens/closes. Mux calls remain 40 -> 10, with ten additional
bridge commands in each. Partial bridge-enabled frames are separately marked
and are not used as successful comparisons.

These are **intercepted production-boundary requests**, not native process
measurements. GH's internal HTTP requests and SSH/ProxyCommand descendants were
not executed and are **opaque/unmeasured**, not zero. New native qualification
must instrument those boundaries too; these data cannot close that gate.
The prior real-child 30→3 / 40→10 fixture remains separate subwork evidence in
[`s3ab-subwork-evidence.json`](s3ab-subwork-evidence.json).
All six new runs, including the failed and partial ones, are indexed by
[`evidence/authority-manifest.json`](evidence/authority-manifest.json); the
adjacent JSONL files retain their original counter frames. The final evidence
recorder run passed 27 accounting/control tests, not a production acceptance
gate.

## Validation and evidence gate for any admitted fix

- Retain cold, warm-due and unchanged-not-due frames; 0/1/10 venues; explicit
  backend, account/provider/config cardinality, expected/completed observations,
  errors, pages, retry starts, auth/discovery/config/SSH starts and API calls.
- Exercise real resolution with synthetic source mutations during the wave:
  account selection, installation/namespace generation, credential rotation,
  config changes and provider errors; retain expiry, partial/50-row inventory,
  retry, stale heartbeat and cancellation controls.
- The new evidence lane covers those mutations without replacing authority
  with a constant. Its tests characterize current behavior, not approve the
  blocked implementation. Required unit-only CI is not expanded.
- Reject any candidate whose total classified work substantially regresses the
  identical baseline. Counts must include authority checks and retries; an
  omitted/opaque child or API makes the result incomplete, not a pass.
- Do not substitute larger deadlines, suppressed health, or absent observations
  for reduced work. Keep per-venue cancellation and slow-venue progress.
- Preserve raw counter frames before cleanup. The full local runner logs are
  retained in owned ignored evidence storage; public artifacts contain only
  synthetic counts and source identities, never credentials, hashes of
  credentials, machine paths or private coordination.

**Outcome:** exact source boundaries and the causal regression are established;
no cheap opaque-credential revision has been demonstrated. Shared-source
implementation, review, merge, release and installed qualification remain open.
