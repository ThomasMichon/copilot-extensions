# In-Picker device authorization

## Problem and existing approach

A provider that cannot authenticate must not look like an authenticated empty
list. The optional Manager can present an explicit authorization action without
owning credentials or blocking its terminal UI. This pattern codifies the
existing CodeSpaces interaction; it introduces no new protocol or migration.
It serves the [Picker vision](../../visions/picker/README.md)'s render/derive,
graceful capability scaling and headless-testing intent.

The [Manager contribution contract](../../worktree-manager/docs/plugin-contribution-contract.md#manifest)
owns summary-conditioned `view_actions`, command provenance and refresh.
[`RegisteredPivotRuntime.run_action_stream`](../../worktree-manager/src/worktree_manager/production_picker/picker_tui/tasks.py)
owns the existing NDJSON process boundary.
[`PivotProgressScreen`](../../worktree-manager/src/worktree_manager/pivot_progress.py)
is shared by the production Picker and lightweight preview. Reuse these seams,
not a second auth modal, daemon, credential broker or progress protocol.

## Provider and presentation responsibilities

1. **Report, do not authorize, during reads.** Publish explicit summary state,
   selected-account context and a useful banner, including when there are no
   rows. The CodeSpaces exemplar uses `auth_required`, `scope_blocked`, `ready`
   and `error`; those names are provider data, not new mandatory schema keys.
   One-shot and subscribed summaries must agree and replace stale auth state.
   Probe/selection errors are not an authenticated empty list.
2. **Offer a summary-conditioned command action.** It must work without a
   selected row and bind its account context from that same summary. Only
   explicit activation permits acquisition. Resolve the command from its
   contributing payload; never replace an unavailable owned command with a
   same-named executable on PATH. The provider independently validates its
   explicit-action argument and target. Preview supports command actions only.
3. **Stream public instructions, not secrets.** A `progress: true` action runs
   off the UI loop and emits flushed NDJSON progress frames. Each message is
   the current multiline presentation, not an appended transcript: repeat the
   selected account alongside the verification URL and human device code.
   A terminal `done` frame reports success; `error` reports denial, expiry or
   another safe failure. Follow vendor polling intervals and `slow_down`
   backoff; do not turn pending approval into failure or retry the ceremony
   automatically. No token belongs in frames, errors, request metadata or
   session environments.
4. **Keep credentials provider-owned.** Verify the acquired identity against the
   selected account and verify required scopes before adoption. Preserve
   unrelated account selection and credential stores. The CodeSpaces
   [authorization owner](../../plugins/agent-codespaces/src/agent_codespaces/authorize.py),
   [device flow](../../plugins/agent-codespaces/src/agent_codespaces/oauth_device.py)
   and [credential store](../../plugins/agent-codespaces/src/agent_codespaces/credential_store.py)
   are exemplars, not generic storage APIs or permission to reuse their vendor
   registration/scopes for another service.
5. **Retain cancellation and visible outcomes.** The modal stays responsive.
   Escape or Cancel requests shutdown through the runtime's existing
   cancellation callback, including while stdout is silent; it becomes Close
   only after completion. Cancellation/error never refresh as success.
   Successful completion invalidates and reloads the originating provider.
   The modal treats provider messages as literal text, adds only validated
   HTTP(S) URL color/underline spans, and never interprets Rich markup or
   attaches executable/click-to-open actions. Text remains selectable for
   terminal copying; device-code spelling and line breaks are preserved.

Over SSH, the terminal and provider process remain on the consuming device.
The person copies the displayed URL/code and completes the ceremony in their
own browser, selecting the intended account there. This does not move the
credential into the browser's device or authorize an automatic browser launch.
Noninteractive listing never starts OAuth; use the provider's explicit
authorization surface when no interactive Picker is present.

## Synthetic example

These are illustrative provider-owned names, not an executable vendor login
recipe. Add the action to the provider's ordinary version-1 pivot manifest:

```json
{
  "view_actions": [{
    "key": "authorize",
    "label": "Authorize",
    "when": {"auth_status": "scope_blocked"},
    "run": ["agent-example", "authorize", "--account", "{auth_account}", "--user-initiated"],
    "progress": true
  }]
}
```

A read returns authentication state inside `summary`:

```json
{
  "entries": [],
  "summary": {
    "auth_status": "scope_blocked",
    "auth_account": "example-user",
    "banner_text": "Authorization required."
  }
}
```

The explicitly activated provider can then emit:

```json
{"type":"progress","msg":"Account: example-user\nOpen https://localhost/device\nEnter code: DEMO-CODE"}
{"type":"done","message":"Authorized example-user; refresh the view."}
```

`localhost` and `DEMO-CODE` are synthetic. Emit `done` only after verified
adoption; on failure use, for example,
`{"type":"error","message":"Authorization expired; try again explicitly."}`.
Exact action and frame semantics remain at the owners linked above.

## Bounded candidate inventory

This inventory is a read-only fit assessment, not enrollment or implementation
authorization. A later consumer still needs its own owner-approved scope,
vendor/account contract, tests and ordinary review.

| Surface | Evidence and present fit | Ownership decision before adoption |
|---------|--------------------------|------------------------------------|
| CodeSpaces pivot | [Manifest](../../plugins/agent-codespaces/pivots/agent-codespaces.json), authorization owner and modal above already implement this path. | Reuse as the exemplar; preserve its account-qualified storage and required scope. No migration needed. |
| Repository GitHub-account setup / account-aware preflight | [`resolve_registration_account`](../../plugins/agent-worktrees/src/agent_worktrees/repos.py) already reports resolved login, provenance, authentication and selection ambiguity. It does not implement this Picker acquisition action. | Plausible future explicit setup/preflight consumer. Worktrees owns account mapping and GitHub CLI consumption; decide credential ownership and verification first. CodeSpaces' private namespace does not automatically authenticate unrelated GitHub operations. Existing broader preflight work remains separately scoped. |
| MCP target authentication setup | [Injectors](../../plugins/agent-mcp/src/agent_mcp/auth/injectors.py) reuse existing credential sources; [routed identity selection](../../plugins/agent-mcp/src/agent_mcp/auth/identity.py) binds tenant/principal and rejects an unbindable relay. No device-acquisition Picker action is supplied there. | Conditional candidate for an explicit setup/configuration surface only, not automatic repair during tool calls. The credential-source owner must define vendor/tenant/resource/scope and adoption; Manager must not mint credentials or turn env/static/relay sources into device flows. |

## Validation and acceptance boundary

Use fake provider frames and isolated state for reproducible UI contracts:
actual entry-point activation in production and preview, literal hostile markup,
URL styling/contrast and copying, narrow/normal layouts, dark/light themes,
focus/hover, Escape/Cancel, denial/error and completion/Close, and successful
provider refresh. Existing coverage includes
[production clicks](../../worktree-manager/tests/production_picker/test_picker_tui.py),
[preview actions](../../worktree-manager/tests/test_pivot_auth_cta.py),
[modal presentation](../../worktree-manager/tests/test_pivot_progress_style.py),
[runtime streaming](../../worktree-manager/tests/production_picker/test_pivot_streaming.py)
and [vendor simulations](../../plugins/agent-codespaces/tests/test_oauth_device.py).

These simulations prove software contracts, not actual vendor approval,
native OS credential-store behavior, terminal focus/copy behavior over SSH, or
installed release acceptance. Native acceptance is a separate explicitly
authorized ceremony against the intended device/account, never a test side
effect or a claim inferred from a headless screenshot.
