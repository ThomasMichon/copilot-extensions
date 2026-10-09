# Session preference authority

The default remains **caller-settings**: the bridge applies its caller user's
persisted model/effort with the existing environment and request precedence.
**target-settings** is an opt-in policy, not a change to that public default.

## Selecting policy

Set `preference_source: target-settings` in the service config, or send
`"preference_source": "target-settings"` to `POST /api/v1/sessions`.
The Python `BridgeClient.start_session` accepts the same keyword and rejects
old daemons before sending it (HTTP protocol 27). Raw HTTP clients must check
the daemon's advertised protocol first; older servers ignore unknown fields.
The create response reports the selected policy, not a claim that every desired
preference was enforced.

A registered profile/repository can also opt in through its existing
`default_env` / target environment:

```yaml
default_env:
  AGENT_BRIDGE_PREFERENCE_SOURCE: target-settings
```

The request-owned policy survives target serialization and provider refresh.
An explicit request policy wins over environment/config policy. Reusing a
session preserves its policy; an explicit conflicting policy returns 409
rather than claiming to change the existing session.
Target-mode reuse also rejects explicit preference/provider changes that cannot
be verified against the existing session; use `force_new` to apply them.

The optional request `context` field requires target-settings mode. The
existing explicit `model`, `effort`, and declared Copilot arguments retain
their meanings.

## Execution-side receipt and capability boundary

The local settings reader handles only `model`, `effortLevel`, and `contextTier`
from its executing user's `.copilot/settings.json` (including line comments),
with candidate states `resolved`, `missing`, or `error`. Reading the Host's
settings does not attest the eventual child's execution context.

Version-1 Host receipts therefore report `unsupported` for configured launchers
whose executable provenance and identity-preserving behavior are unverified.
A basename such as `copilot`/`copilot.exe` is not proof: a script or configured
executable can change HOME or identity. Candidate settings cannot be accepted
as a trusted receipt. Neither whole settings files nor home-directory paths
travel in the receipt.

The optional JSON receipt follows the existing 16-byte HELLO cursor/PID
prefix. Legacy clients still read that prefix. New clients validate version,
shape, size and child binding; a legacy host or invalid receipt is an
unavailable capability, **never permission to use caller settings**.

Target-default inheritance remains held until a target-local attestor supplies
verified execution-context authority; this is a separate wrapper-authority
follow-up, not a guessed path/name mapping. Configured executables, shell
commands, `.cmd` wrappers and custom scripts report `unsupported`. Their
explicit request/profile selections still
work when ACP verifies them; target-settings inheritance itself is not claimed.
Without a resolvable model intent, a new target-mode session fails rather than
becoming ready on an unrelated agent default. No transport is
converted to another hosting architecture by this feature.

An intentionally selected provider (`COPILOT_PROVIDER_BASE_URL` or
`COPILOT_OFFLINE`) does not inherit an unrelated settings-file model.
`COPILOT_MODEL` and explicit launch model/effort/context arguments are profile
selections and precede settings defaults.

## Applying and preserving selections

For a fresh session, precedence per field is:

1. Explicit request choice.
2. Declared target arguments/provider model or execution-side launch profile.
3. Explicit compatibility environment override.
4. Execution-user settings.
5. No unrelated default: unresolved model intent prevents a ready session.

An execution receipt proving intentional provider selection may use that
provider's native advertised model. It is not overwritten by a cloud default
or lower-precedence compatibility model environment variable.

`AGENT_BRIDGE_ACP_MODEL` / `_EFFORT` / `_CONTEXT` and historical
`AGENT_CODESPACES_ACP_*` aliases are intentional overrides, not inferred caller
defaults. Bridge-native names win. `AGENT_BRIDGE_MODEL_PROPAGATE=0` (or its
historical alias) disables inherited defaults, not explicit requests/profiles.
Caller-settings behavior remains unchanged.
Compatibility variables are bridge-process globals; use the request
`model`/`effort`/`context` fields for per-session choices, not those variables
inside a request's child environment.

Target-mode selections are confirmed from advertised current values or the
actual `set_config_option` response, never from desired snapshots. The final
readback must still match required model/effort after all options are applied.
Missing/unoffered required options, failed RPCs, absent readback or mismatched
readback produce `preference_application` status `unsupported`/`error` and fail
startup before the session becomes ready. Failed application does not replace
the prior confirmed selection snapshot with a fallback model.

Confirmed selections are recorded in `preference_selected` events,
including later ACP configuration notifications. Reattach does not apply
defaults. Load/recreation reasserts a confirmed selection snapshot instead of
new settings or the original launch request. A session with no snapshot reports
an `unconfirmed` resolution and fails if model intent cannot be established;
it does not guess what the previous selection was.

Preferences are applied only through advertised ACP select options and offered
values. The bridge recognizes `model`, `reasoning_effort`, and context options
named `context` or `context_tier`. If the server exposes no compatible context
option, the requested tier is **unsupported**, not effective. CLI flags ignored
by ACP do not establish long context.

The event stream exposes `preference_resolution`, `preference_application`
(status, requested values, actual effective values and failures), `model_applied`,
`model_fallback` (not-advertised/not-offered/RPC failure), and
`preference_selected`. A create response can have `status: failed`; its selected
policy field alone is
not evidence of inheritance or long-context enforcement.

## Detached CLI launch helpers

The shared `venue-copilot` helper honors
`AGENT_BRIDGE_PREFERENCE_SOURCE=target-settings` by not reading/injecting caller
settings. Explicit flags and compatibility environment choices remain.
The target's ordinary CLI/worktree launcher owns its native settings
translation; this helper never invents remote settings or ACP capability.

See [Agent Bridge](../README.md) and [venue-copilot](../../../libs/venue-copilot/README.md).
