# Credential-free foreground-session integration

[Canonical effort and completion gates](README.md).

## Intent and authorization

Implement the missing host entry point for the existing quota reader through
normal review. This is implementation authorization, not rollout authorization:
do not reload a live extension, update a CLI/plugin, switch an account, or restart
a host to qualify the change. Keep other authors' PRs and workspaces untouched.

This closes the harness-guidance attributable-budget-source and
unavailable-is-not-zero delta. It follows the payload-extension shape and the
independence, right-sized-surface, and single-posture-owner patterns. It does not
introduce another daemon, broker, ledger, queue, or authentication authority.

## Host contract

- Add an optional plugin-owned, on-demand extension tool. Join only the current
  foreground session through the supported SDK `joinSession` extension entry
  point; never construct a client, resume a conversation, or discover another
  session as a fallback.
- Compare the joined session ID with the actual tool invocation session ID
  before and after acquisition. An optional explicit GitHub login/HTTPS origin
  uses public session authentication status around the quota read, retaining
  the existing observed-not-atomic identity limit.
- Use only public read-only `session.rpc.quota.get` and authentication-status
  APIs when they exist. Never call quota refresh, take pending warnings, change
  credentials, inspect internal authentication unions, or access private
  connections to manufacture an unavailable RPC.
- Feature-detect the current host. A newer upstream SDK contract is not proof
  that the running host implements it. Missing APIs, missing session identity,
  wrong-session invocation, transport failures, and deadlines produce explicit
  unavailable/error results without budget values.
- Keep exactly one acquisition in flight and one total deadline, ten seconds
  by default and never above thirty seconds.
  SDK reads that cannot be cancelled must not abort the main session; ignore
  late results and do not admit overlapping replacement reads while the
  original request remains outstanding.
- Return a credential-filtered capture/summary of at most 64 KiB over existing quota
  facts. Preserve acquisition time, requesting-session identity, quantity kind,
  unknown provider observation freshness, and reset diagnostics. Whitelist
  fields; never render tokens, upgrade URLs, account descriptions, opaque error
  text, arbitrary provider metadata, or raw authentication responses.
- Preserve the incumbent capture schema for optional offline inspection by the
  Python normalizer. A serialized capture stays caller-declared evidence:
  neither matching IDs nor a capture flag may promote it to authenticated live
  arithmetic. Do not duplicate budget math or reinterpret currency as credits
  in the transport extension.
- No automatic file writes, startup account reads, injected output, subprocess
  launches, credential environment requests, worker mutation, or launch blocks.
  Session lifecycle remains owned by the host.

## Raw reset and evidence limits

The public quota projection and authentication-status APIs do not currently
expose raw legacy account reset metadata on every host. Report that dimension
unsupported independently from any valid category/provider reset facts.
Do not fabricate a raw-reset RPC, infer a recurring entitlement, or query
credential-bearing internal APIs. Retain the raw-reset acquisition obligation
in the canonical effort until a safe public host projection exists or a named,
reviewed transfer is agreed.

This slice supplies a real integration seam, not permission to claim live
qualification on a host without the required API. It also does not turn two
matching account-status observations into an atomic account/quota transaction
or prove the billing account of an arbitrary third-party provider.

## Plan

- [ ] Land this contract through normal review before extension implementation.
- [ ] Implement bounded foreground-session acquisition and an allowlisted
  existing-schema capture without a new client or process.
- [ ] Register the plugin-owned extension tool and update its on-demand skill,
  plugin entry, and canonical host integration reference.
- [ ] Cover old and new SDK shapes, failures, session/account changes, bounds,
  and late results with isolated tests.
- [ ] Publish the implementation through current-head review and required CI.
- [ ] Leave live enablement, quota/reset verification, release, and rollout
  gated on their own explicit authorization and capabilities.

## Validation

- [ ] An injected fixture session records only the intended public read RPCs;
  refresh, warning-drain, client construction, account changes, and lifecycle
  mutations cannot be invoked by the adapter.
- [ ] Wrong invocation/session IDs and absent APIs return no counters; old SDK
  shapes remain supported as explicit unavailable states.
- [ ] Login/origin mismatch or changes around the read withhold all values.
  Identity output states its non-atomic scope.
- [ ] Each whole result is within its declared byte bound. Oversize nested
  evidence, opaque errors, and credential-shaped metadata cannot leak through
  either successful or failed responses.
- [ ] A deadline covers all RPCs. A timed-out result cannot publish later,
  trigger retries, or allow a second acquisition to overlap an unresolved first.
- [ ] The generated capture is accepted by the existing Python CLI while
  remaining unverified/cached and incapable of enabling live forecasting.
- [ ] The actual extension entry point is tested with an isolated SDK module
  fixture. No live extension reload, session takeover, network account request,
  or production runtime update is required.
- [ ] Existing budget, identity, guidance, and install/manifest contracts remain
  green; tests use bounded runners and preserve explicit platform skips.
- [ ] Documented capability limits and no-rollout state match actual evidence.
