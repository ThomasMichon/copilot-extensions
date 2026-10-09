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

Target-default inheritance requires a target-local attestor, not a guessed
path/name mapping. Unattested configured executables, shell
commands, `.cmd` wrappers and custom scripts report `unsupported`. Their
explicit request/profile selections still
work when ACP verifies them; target-settings inheritance itself is not claimed.
Without a resolvable model intent, a new target-mode session fails rather than
becoming ready on an unrelated agent default. No transport is
converted to another hosting architecture by this feature.

## Target-local container launcher

Wrapper-authority **v1** is an explicit option for an exact trusted/trusted
Linux container using the existing SSH Session Host route. Place
`{{target_preference_launcher}}` at the **final direct execution point** in
the operator-owned `acp_command`, before the Copilot program:

```yaml
acp_command: >-
  export PROXY_MODE=configured;
  {{target_preference_launcher}} copilot --acp --stdio
```

The token is a declaration that the prefix and final program are trusted
target-local code, and that the final program preserves the declared execution
context. It is not recognition of a basename, a shell parser, sandbox expansion,
or hardware attestation. Do not put it in a pipe, subshell, quoted argument, or
another identity-changing wrapper. Keep authentication/proxy/workspace setup
**before** the token. Appended caller flags cannot opt a command into authority.
Legacy commands and the public caller-settings default remain unchanged.
Restricted provider-exec, native Windows, and opaque launchers do not gain this
capability.

`session-host-prepare` advertises `preference_wrapper` with `version: 1` and
`launcher: "{{target_preference_launcher}}"`, together with its discovered
`execution_instance` and configured execution user. Missing, mismatched, or
unadvertised bindings refuse an attested launch. The bridge passes the selected
instance as `--expected-instance` before provider preparation. The provider
confirms it before credential projection and uses that immutable Docker ID for
SSH provisioning, environment/shim setup and launch-only file writes; a reused
container name cannot redirect those operations. The bridge stages its
content-addressed stdlib-only Host bundle through the existing transport;
there is no live installation or settings provisioning.

At the terminal token, an isolated `python -I -S` component reads the final
execution user's effective HOME/settings, UID, working directory, and Linux
namespace facts. The Host owns a private inherited socket and per-launch nonce.
It verifies component digest, selected instance/user, child PID/start identity,
namespace and workspace binding before consenting to PID-preserving exec.
The digest covers the complete staged Host-role source closure, including the
channel verifier/consent owner, dispatcher, receipt emitter and survival helpers,
not just the terminal settings reader.
Only the package's standalone release-version label is normalized, so release
promotion cannot invalidate authority while every executable source change
still changes the digest.
Binding variables and the socket are removed before the agent starts. A
timeout, malformed receipt, binding mismatch or missing inherited model/effort
defaults refuses startup; no caller settings are substituted.

The optional HELLO tail carries **receipt v2**: candidate status/values/sources,
intentional-provider selection, and `authority` (`kind: wrapper-v1`, component
`digest`, `mode`, hashed selected `target`, process `start`, and `space`).
`space` contains Linux platform/UID and hashes of namespace, effective HOME and
cwd, not raw paths, user names, credentials or the nonce. The 16-byte legacy
HELLO prefix/envelope is unchanged. The frontend requires the matching v2
capability for an attested launch and checks the selected instance binding.
HTTP protocol 27 alone does not prove wrapper availability.

Fresh inherited defaults require a readable settings file with model/effort
intent. Explicit model requests or declared local/native profiles can instead
provide their own intent; missing settings then remain visibly missing, not
claimed defaults. Caller-mode and confirmed resume/load use proof-only
`selection` authority, so deleted/changed settings cannot override a confirmed
selection. All required choices still need actual offered ACP current/readback
values; the wrapper receipt does not make ignored CLI flags effective.
Context has the same advertised-option limitation described below.

Execution namespaces are independent: Windows, WSL and containers never share
HOME, registrations, defaults or ownership merely because of a physical host.
The launcher uses its explicitly selected container instance and target-local
facts. It does not infer locality, allocate across registries, copy caller
settings, or remap home-directory paths.

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
Valid caller-settings selection behavior remains unchanged.
Unreadable, unparseable or non-mapping configuration refuses startup because
its authority cannot be established. Legacy fallback remains only when a
parsed caller-settings configuration is known.
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
