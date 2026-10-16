# budget-guidance architecture

The plugin is an independently installable runtime CLI with shared posture
layers:

1. `models.py` parses strict, versioned, inert configuration and reading data.
2. `resolve.py` performs deterministic field-level resolution and preserves
   source, capture time, freshness, authority, and contradictions.
3. `math.py` contains pure balance, horizon, sustainable-rate, ceiling,
   projection, and warning-band calculations.
4. `posture.py` builds the single machine-readable posture used by `cli.py` for
   both JSON and human output.
5. `session_guidance.py` projects that posture into a bounded launch-time data
   cue and atomically writes it to the requesting session's instruction folder.
   Its [delivery contract](session-guidance.md) owns the pointer, freshness,
   redaction, byte budget, and hook behavior.
6. `routing_advice.py` issues optional point-in-time advice with an expiry
   bounded by the selected fields and reset. The [contract](configuration.md#optional-routing-advice)
   lets independent routing consumers use it without importing this runtime or
   reimplementing its arithmetic.

The static adapter is intentionally complete enough for offline operation and
partial enough to exercise multi-source composition. It performs no I/O beyond
reading the selected JSON file.

`external_reader.py` acquires inert external references using invocation-owned
operator grants, then passes ordinary readings to the same pure resolution.
Repository/configuration data cannot grant execution authority, and the plugin
does not acquire credentials. An explicit operator grant may authorize a
repository executable; its location is not itself a trust guarantee.
The [external-reader contract](external-readers.md) owns bounds and failures.

`github_usage.py` reads personal billing API reports through a caller-owned
authenticated async transport. It verifies the selected account and reporting
period before exposing allowlisted quantities. The [provider report contract](github-usage.md)
keeps those facts separate from a current budget: the API does not establish
entitlement, reset, provider observation time, or the requesting session's
account. Its inert budget reading withholds numeric values rather than turning
an empty or historical billing report into a fresh forecast.

The fast installer stamp copies the exact owning payload into an immutable
versioned snapshot. Both compatibility and payload-local wrappers provision
only from that recorded snapshot, serialize through the runtime root's
`.provision.lock`, and re-resolve the runtime after acquiring the lock. They do
not search other marketplace installations for a same-named plugin.
Session bootstrap also reads deployed and payload versions without ambient
Python, so an older valid runtime cannot suppress reconciliation to a newer
owning payload on a Python-less host.
Snapshots also materialize the owning `agent-procutil` dependency; both installer
paths pass its local source explicitly rather than resolving an index package.
Both package build roots clear setuptools `build/` and root/src `*.egg-info`
artifacts before and after every uv or pip attempt, including the fallback, so
partial build residue cannot shadow current source.
The bootstrap and exact-session writer hooks are declared output-free and emit
only `{}`. The writer reads inert configuration without reader grants or
provider refresh and loads the owning payload's pure source, not a stale runtime.
Command-catalog
rendering remains a plugin-owned emitter for explicit use and a future native
host-composition seam; no custom cross-plugin authority invokes it.

Reset horizon and projection are anchored to the posture's `evaluated_at`
instant, while each selected field retains its own source `captured_at`. A
reading whose reset has already elapsed is stale until an explicit future
rollover contract supplies the next budget period. Projection uses the selected
trailing-rate field and the shortest supported trailing window in that reading.

Warning precedence is: stale, contradictory, overspent, reset due, no trailing
rate, projected overspend, rate above the effective daily limit, on track.
Missing required fields produce no calculated block.

The incumbent-session quota helper is separate from static adapter resolution:
`provider_quota.py` qualifies one explicitly selected provider observation, and
`provider_pacing.py` qualifies compatible monthly usage before reusing the
same posture arithmetic. Rate provenance explicitly labels cycle averages as
estimates; the advice block never grants worker-control or model-eligibility
authority. See [provider evidence](provider-quota.md) and
[provider pacing](provider-pacing.md) for the contracts.

The optional `extensions/budget-guidance/extension.mjs` entry uses the host's
`joinSession` API and registers only `budget_guidance_session_quota`.
`session-quota.mjs` owns the on-demand public RPC deadline, single-flight slot,
and observed session/account checks; `quota-projection.mjs` owns the bounded
field allowlist. Neither module imports Python, launches processes, writes
files, reads credentials, or calculates a budget. The serialized result remains
an offline capture for the existing Python normalization path. Missing host
capabilities are explicit, not synthesized through private RPCs.
