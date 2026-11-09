# Copilot Driver core

`agent-copilot-driver-core` owns the `agent_remote_driver` Python namespace:
the canonical Session Host, TCP protocol, ACP WebSocket adapter, retained
execution and compact receiving export. It does not import Bridge, Worktrees
or Manager. Install it as an ordinary first-party distribution and run
`python -m agent_remote_driver.session_host --support-json`; plugin enablement
is not required.

The [Driver application and extension connector](../../plugins/agent-remote-driver/README.md)
provides the ordinary application front door. Bridge consumes this same
library through the existing canonical-reference and wheel-build owners,
not a sibling plugin source dependency.

Worktrees' mux-creation adapter also consumes this core. The core owns its
short native check/create lease, neutral client working directory, nesting
scrub and uncertain-creation handling. Workspace allocation, pane argument
preparation and seed custody remain caller-owned; this seam alone does not
establish headed broker or all-caller convergence.

Manager's launchers select the finite boundary from their chosen
Worktrees interpreter with
`python -I -m agent_remote_driver.mux_creation --describe-contract`.
Contract `copilot-driver.mux-creation`, version 1, advertises
`windows-psmux-create-or-join` on Windows or `posix-tmux-create-or-join` on
POSIX and names the exact executing
interpreter and physical entrypoint. A selected older provider without this
declaration retains the documented legacy creation path; support is not
inferred from a package version. No retirement of that compatibility path
is scheduled by this change.

The selected entrypoint's `--create` operation reads at most 1 MiB of JSON
text from stdin. Its exact request properties are `version: 1`, `binary`,
`session` and literal `argv`; the latter starts with
`["new-session", "-d", "-s", session, "-c", work_dir]` and contains the pane
command. Duplicate/unknown properties and unknown CLI options are refused.
The response carries `contract`, `version` and `status`: `created` or `joined`
exits zero, `rejected` or `unknown` exits 3. Rejection precedes possible
dispatch; unknown retains handoffs and forbids automatic cleanup, replay or
legacy retry. The core does not echo pane argv or environment values.
POSIX creation checks the exact target before pane preparation. A failed
dispatched client is uncertain, including a concurrent tmux creation race;
it is never reported as created or retried through the old launcher.
The Bash consumer retains the selected physical interpreter/entrypoint,
uses bounded caller-owned processes and preserves AHP auth scrubbing.
Unknown creation retains any original token handoff; a confirmed join
settles only the unused local handoff and attaches without creating a pane.
Manager retains terminal attachment, options, UI, status and original native
admission. This finite operation does not wait for Copilot to exit.

### Finite PSMux observation

`agent_remote_driver.mux_observation` owns native PSMux reads for Driver
creation, Worktrees presence/listing and Manager discovery/client counts.
It reads the provider's port/key rendezvous and sends only bounded,
authenticated `list-panes`/`list-clients` queries and a final read-only
`display-message` identity fence over loopback. Each command uses its own
authenticated connection for providers that accept only one command per
connection; all three share one deadline and aggregate response-byte cap.
It never executes PSMux, repairs registrations or starts a background observer.
Pane and final identity responses must identify the requested session and
one incarnation; port/key hints must remain unchanged across the reads.
The final identity also names the current pane, so window-local active flags
from other windows cannot select the wrong pane.
Missing rendezvous hints alone never establish absence: Driver retains the
complete native server/PID-birth coverage reader introduced for prompted Resume.
Legacy PSMux 3.3.5 servers publish port/key/session-ID files but no PID sentinel.
Inventory therefore discovers port/key pairs, authenticates the live reply's
server PID against the native census, and rechecks process births and registration
contents afterward. A PID sentinel, when present, must still match.
Failed PID-less endpoint candidates may be stale; they do not block a complete
authenticated inventory when every live native server is covered elsewhere.
An unreachable or unregistered live server, unsupported process mode or changing
inventory remains unknown. Listings share one absence-coverage
snapshot within their finite budget; live positive rows use their own reads.
Selected-namespace absence uses qualified registration identities while still
covering every native server. The legacy name-only inventory API retains its
ambiguity refusal for duplicate logical names across namespaces.
Catalog discovery unions confirmed inventory identities with directory hints
before namespace filtering, including registrations published between the reads.
Registration basenames retain their authenticated logical names, so `__`
inside a default-namespace session is not mistaken for a namespace separator.
Unicode worktree identity components remain intact; path separators and
control characters are refused before reading rendezvous files.
Stale, partial, unreadable, changed, malformed or timed-out observations remain unknown.
Client counts come from client rows, not PSMux's boolean `session_attached`.

The default namespace uses the native `PSMUX_DATA_DIR` or native home.
An explicitly namespaced launcher can pair its existing `psmux -L <name>`
selector with `COPILOT_DRIVER_PSMUX_NAMESPACE=<name>` for observation.
An in-process caller can instead supply `observe_psmux(..., namespace=<name>)`
to select its exact creation transport without changing the environment shared
with other observers. An explicit empty namespace selects the default transport.
This Driver selector does not configure the PSMux CLI or isolate its startup
cleanup; the caller must keep both selectors consistent. No environment
setting makes native tests safe on a shared host.

The finite CLI advertises `copilot-driver.mux-observation`, version 1 and
operation `psmux-observe` with its actual interpreter/entrypoint through
`--describe-contract`. `--observe` consumes at most 4 KiB of JSON with exactly
`version: 1` and `session`. Its response has the contract/version and
`status` (`present`, `absent`, `unknown`, `unsupported`), with incarnation,
client count and pane observations when present. Confirmed present/absent
exits zero; unknown/unsupported exits 3. Duplicate/unknown properties and
unknown options are refused. Older independently installed providers without
the declaration stay unsupported; callers must not downgrade to passive
PSMux CLI reads. Foreground attach/create and explicit provider writes still
launch PSMux and retain its startup-cleanup risk.

The explicit headed coordinator now runs in the original caller-owned pane.
It stages the launch-bound SDK extension, retains the native child handle and
serves the same Session Host after native session/PID binding. The ordinary
application supports WS/TCP; `prepare_headed_command` retains a TCP-only compact
receiving executable under the original attempt, independent of the caller's
venv. Neither path allocates a workspace, mux or additional launch journal.
Original native startup admission and literal prompt argv are preserved.
Full caller migration and restoration qualification remain in progress.

`headed.caller.execute` retains that executable as the original Python
caller's foreground child. A live, PID/birth-bound admission channel rechecks
the original launch immediately before native creation. The native receipt
uses the original private nonce, session and coordinator identity to resolve a
distinct live Copilot PID/birth; creating the coordinator alone is not native
acceptance. Receipt failure retains the wait without replay or automatic seed
restoration. The API returns the actual coordinator exit code separately from
receipt certainty. `--support-json` advertises `headed_process_receipts` with
`native-child-v1`; older exports are refused before coordinator creation.

The retained extension assets also carry a bounded JavaScript client for
the same TCP Session Host. The native child receives its launch-bound
`COPILOT_DRIVER_HEADED_CLIENT` declaration; it is not a public endpoint or
permission grant. The client verifies the original state/native identity,
CLI lifetime, nonce and support before sending. It refuses a prior
controller's buffered RPCs and never reconnects after unknown admission.
Read-only native history remains available for original receipt recovery.

Headed `initialize` advertises `_meta.nativeSend` version 1 for the additive
`copilot-driver/native-send` method. This is not a standard ACP method. It
preserves explicit `enqueue`/`immediate` delivery, uses a generated
`agent-remote-driver-<uuid>` source and returns the actual SDK message ID
without treating acceptance as turn completion. Unknown SDK results,
concurrent effects or native input revoke control without replay. The
original argv-seed exception expires at the first controlled send, even if
its native event was not observed; later identical user text is not exempt.
The existing standard ACP prompt/turn-completion interface is unchanged.
Bridge's launch-selected retained inbox uses this client rather than a
second direct SDK sender; Bridge keeps its own durable handoff and
consumption authority. Restoration after a previous controller remains
unqualified, not an automatic re-hook.

See the [ACP WS boundary](../../plugins/agent-remote-driver/docs/session-host-websocket.md).
The compact dependency-free receiving archive supports TCP only. Tests stay
with the application and Bridge consumers; the canonical receiving-policy
units remain in the required actor-free packet.
