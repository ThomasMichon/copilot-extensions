# S3a/b boundary decision

**Decision needed; no implementation admission.** #8143 stays draft/unmerged;
the existing task stays suspended. No new probe, benchmark, retry or agent was
run for this packet. Arithmetic uses the retained
[actual-callgraph counters](evidence/authority-manifest.json).

## Required versus redundant checks

The accepted [S3a/S3b contract](codespaces.md) requires source/account/config/
credential changes to invalidate **reuse**, bounded Available-proof age before
connection, and tenant generation checks at heartbeat/release. It does not
require recursively refreshing all authority while reading authority, parsing
a response, reading an immutable row, or closing an owned connection.

But a read-only *command* is not necessarily a read-only *effect*:
`CodespaceConfigSource._fetch_gh_config` can start a stopped venue; Windows
`ConnectionManager.exec_command` uses direct SSH/ProxyCommand and can reconnect.
Both need fresh admission. A retry is another admission. No supported borrowed
transport/no-reconnect guarantee is assumed; S3c remains excluded.

## One concrete, strict schedule

1. **Acquisition:** select due holds first; zero due means zero remote-observation
   work, without changing local push-debt handling. One fresh
   full authority read, then A account listings using that read's pinned explicit
   credentials. Preserve page/error/duplicate qualification and observation age.
   Listing is observational: no second full sweep merely to finish acquisition.
2. **Each actual wake-capable effect:** after queue/ownership waits, take one
   fresh full authority read, compare with the wave, and use its already-read
   selected credential for exactly that effect. Check owner/deadline at launch.
   Windows mux requires config-fetch + SSH-exec admissions; separate bridge
   proof adds another pair. Unknown/change/expiry suppresses the effect.
3. **Retries:** repeat step 2; never reuse the previous attempt's admission.
4. **Inert results and cleanup:** parse/store qualified results and drain owned
   resources without reauthenticating. They cannot renew/release/mirror yet.
5. **Publication:** drain only already-completed results, at most four venues /
   sixteen tenant verdicts per batch, with **no wait to fill a batch and no wait
   for an in-flight slow venue**. One fresh full authority read per batch;
   apply generation-fenced mutations through the existing owner lock with
   deadline/owner checks. A mirror launch is a separate wake-capable effect,
   not free publication work; transcripts are outside the measured fixture.

Step 5 needs an admitted owner batch helper: existing `heartbeat`/`release`
lock separately; caller-side checks are not atomic. Refreshing again after lock
waits would only increase the bounds. No global revocation transaction is assumed.

## Exact classified-work arithmetic

Identical retained fixture: A=3, K=4, existing per-venue account bindings, zero
adopted repos/active config providers, Windows-direct transport, no retries,
one page/account, no mirror. A fresh full sweep costs **2 discovery processes +
A fresh opaque credential reads**: five CLI processes, or two processes plus
three provider-keyring API calls. No hypothetical cheap revision is substituted.

For C>0 let E=2C (mux) or 4C (mux+bridge), and
`ceil(C/4) <= B <= C` completed-result batches. Full sweeps `S=1+E+B`.
Perfect same-read reuse gives:

```text
CLI-backed processes       = (A+2)S + A + E
provider-backed processes  = 2S + A + E
provider-keyring API calls = AS
```

These are **derived, not measured** schedule bounds. Cold=warm because opaque
reads remain fresh; B depends on completion timing, not assumed coalescing.

| Venues / consumers | Retained CLI baseline cold / warm | Strict schedule CLI cold = warm | Strict provider processes + API calls |
|---|---:|---:|---:|
| 0 / either | 0 / 0 | 0 | 0 + 0 |
| 1 / mux | 13 / 10 | **25** | 13 + 12 |
| 10 / mux | 103 / 100 | **143–178** | 71–85 + 72–93 |
| 1 / mux+bridge | 15 / 12 (derived) | **37** | 19 + 18 |
| 10 / mux+bridge | 123 / 120 (warm derived) | **263–298** | 131–145 + 132–153 |

Provider baselines: 10+4 / 100+40 for one/ten mux venues, 120+50 for ten
mux+bridge venues. These separate work units are not CPU. An SSH retry adds
six CLI requests, or three processes plus three provider APIs. Extra pages
remain counted/incomplete unless proven complete. GH-internal HTTP, native
children and CPU remain unmeasured.

**Impossibility lower bound:** ten mux venues already cost `8 + 12*10 = 128`
classified CLI requests *before any publication*, above baseline 103 cold /
100 warm. Thus pruning redundant checks alone cannot meet total-work
non-regression while retaining whole-candidate freshness at every true effect.
It can reduce the blocked draft's 596 requests, but that is not the baseline.

## Same-read reuse and exact ownership seam

Yes: passing **same-boundary** already-read values removes `capture_scope`'s
second config load/scan, A listing credential rereads and per-open credential
rereads. It does not remove the sweep's two discovery calls or fresh opaque
reads. No cross-boundary authority reuse, cache, revision provider or ledger.

The exact semantic collision is `account_candidates.candidates()`'s hidden
routing/config reads: exposing that read result crosses the retained #5761
namespace-discovery owner's seam. `load_merged_config(provider_reports=...)`
already accepts scan reuse. `owner_inventory`/`owner_mux_probe` consume the
result; a publication batch crosses the narrowly admitted `connection_owner`
mutation seam. No shared credential/account/config signature was edited.
Fresh task/forge-only check: task still suspended; all 31 open PRs inspected,
only #8143 touched the listed files. Canonical #5761 task/effort-key searches
returned no row, which is **not release evidence**; its existing owner remains.

## Smallest user decision

Clarify one authority rule: **must a read-only transport effect be invalidated
by changes to any candidate/config/credential, or may it use the bounded wave's
observation selection while live selected-account/source checks gate each
wake/retry and full live authority gates actionable publication?**

The latter is an explicit S3 semantics amendment: an unrelated candidate change
could allow an observational effect to finish before publication rejects it.
It is not permission to skip no-wake, explicit-account, age, cancellation or
tenant-generation checks, and it has no demonstrated total-work pass yet.
**Recommendation:** retain the strict rule pending that user decision; do not
build shared APIs or invent a framework to conceal the arithmetic. If strict
whole-set currentness is intended, accept the stated safety cost explicitly or
leave this optimization blocked rather than claiming aggregate improvement.
