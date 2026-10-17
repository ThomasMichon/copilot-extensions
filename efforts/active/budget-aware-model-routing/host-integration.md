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

- [x] Land this contract through normal review before extension implementation.
- [x] Implement bounded foreground-session acquisition and an allowlisted
  existing-schema capture without a new client or process.
- [x] Register the plugin-owned extension tool and update its on-demand skill,
  plugin entry, and canonical host integration reference.
- [x] Cover old and new SDK shapes, failures, session/account changes, bounds,
  and late results with isolated tests.
- [x] Publish the implementation through current-head review and required CI.
- [x] Leave live enablement, quota/reset verification, release, and rollout
  gated on their own explicit authorization and capabilities.

## Validation

- [x] An injected fixture session records only the intended public read RPCs;
  refresh, warning-drain, client construction, account changes, and lifecycle
  mutations cannot be invoked by the adapter.
- [x] Wrong invocation/session IDs and absent APIs return no counters; old SDK
  shapes remain supported as explicit unavailable states.
- [x] Login/origin mismatch or changes around the read withhold all values.
  Identity output states its non-atomic scope.
- [x] Each whole result is within its declared byte bound. Oversize nested
  evidence, opaque errors, and credential-shaped metadata cannot leak through
  either successful or failed responses.
- [x] A deadline covers all RPCs. A timed-out result cannot publish later,
  trigger retries, or allow a second acquisition to overlap an unresolved first.
- [x] The generated capture is accepted by the existing Python CLI while
  remaining unverified/cached and incapable of enabling live forecasting.
- [x] The actual extension entry point is tested with an isolated SDK module
  fixture. No live extension reload, session takeover, network account request,
  or production runtime update is required.
- [x] Existing budget, identity, guidance, and install/manifest contracts remain
  green; tests use bounded runners and preserve explicit platform skips.
- [x] Documented capability limits and no-rollout state match actual evidence.

## Implementation evidence

The plugin now ships `extensions/budget-guidance/extension.mjs` and its bounded
acquisition/projection modules. The entry fixture joins once without a startup
read, records only public status/quota/status calls when account matching is
requested, and feeds its actual generated capture to the Python CLI. Capture
attribution remains caller-declared and cannot enable live arithmetic.

The bounded Python devcontainer suite passes with the Node-required fixture
explicitly deselected (443 passed, 18 platform/environment skips); that fixture
passes separately through the bounded local runner on a Node-equipped host.
The pinned Python image lacks Node.js, and its initial focused run correctly
failed the explicit missing-tool assertion rather than silently skipping it.
No image, installed runtime, or live extension was modified. Install-contract,
documentation consistency, touched-Python lint, and the ordinary strict
customization scan pass; an independent source review found no blocking defect.

The settings-expanded customization scan separately detects a missing
`customizing-copilot` inline guidance floor in this checkout's existing
adoption state. The adapter does not change instruction adoption or installed
payloads; the cross-version reconciliation objective remains tracked in
[#6092](https://github.com/ThomasMichon/copilot-extensions/issues/6092),
separate from the plugin's source scan.
Implementation merged through
[#7065](https://github.com/ThomasMichon/copilot-extensions/pull/7065) as
`6ba614b9be86639011519d0fdecc4fdd4ce2dff1`, after actual current-head
approval and all three named managed checks. Live enablement, quota/raw-reset
qualification, release, and authorized rollout remain outside this receipt.
