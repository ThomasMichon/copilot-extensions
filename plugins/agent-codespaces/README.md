# agent-codespaces

See the [configuration reference](docs/configuration.md) for native files,
fields, precedence, source-relative provisioning and credential boundaries.

Wheel-application regular installation invokes the app-owned `__source-setup`
and `__source-start` receivers. Setup contributes the plain schema-0
`providers.d/agent-codespaces.standalone.json`, referencing the exact installed
version-neutral global command even if that command or Bridge is not installed
yet. The explicit `AGENT_BRIDGE_PROVIDERS_DIR` override wins; otherwise the
directory belongs to `AGENT_BRIDGE_CONFIG_DIR` or the default Bridge config root.
Without a config override, an explicit `AGENT_BRIDGE_INSTALL_DIR` precedes the
home-directory fallback, matching Bridge's own configuration-root resolution.
An override whose final component is exactly `providers.d` must have a parent
lexically matching that declared config root; otherwise setup refuses before
creating directories or publishing. Comparison expands home/relative paths,
normalizes path syntax and Windows case, but never resolves links or activates
Source/Bridge to infer another installation. Other override directory names
retain Bridge's explicit escape-hatch behavior.
Publication is atomic, create-only and idempotent through the canonical drop-in
writer. It never invokes a registration hook or backend.
An identical concurrent reader may wait up to one second for this target's
two-link publication window to settle. Reads retain the same file identity and
require an ordinary single-link declaration before success. Persistent hardlinks,
replacement or interruption refuse without rewriting either publisher's entry;
the producer acquires no lock during this bounded read-only wait.

The identity-bearing payload entry `agent-codespaces.json` stays separate and
untouched. An eligible attributed entry retains Bridge's existing filename-order
precedence only for the CodeSpaces plugin identity, regardless of marketplace;
foreign plugin namespace entries refuse. When the CodeSpaces plugin is disabled,
the installed app remains discoverable
without source trust, redirects or Copilot activation. Bridge's existing
unattributed-provider compatibility advisory still applies to the plain entry;
this is not schema-1 attribution or backend readiness. Collisions at the plain
destination or with other anonymous/foreign namespace entries refuse rather than
overwrite, including a prior plain entry at the payload filename. Those entries
require their owner's migration; application setup never silently downgrades one.
The own destination retains its 64 KiB read limit. Ordinary peer JSON is read
completely before namespace classification, matching Bridge; size alone never
hides a foreign namespace, and duplicate keys use their final value. Unrelated
or malformed JSON peers are isolated. Unsafe paths and storage failures,
including unreadable peer enumeration, still refuse publication.

Startup reads merged configuration without current-directory overlays and uses
the existing finite Connection Owner ensure only when enabled and executing
from the selected installed flat or nested source/profile slot. It does not
register tasks or force a disabled Owner. The explicit startup receiver requires
the selected completion's existing ownership proof and the same attributed
process binding before ensuring an Owner; it refuses missing or unreadable
attribution rather than falling back to legacy readiness. Ordinary legacy
on-demand ensure keeps its compatibility behavior. Stage-only installation invokes
neither callback; a staged environment is not installed/provider/serving readiness.

GitHub Codespaces lifecycle management, SSH transport, and credential relay
for Copilot CLI.

Default native `uninstall` removes only explicitly named runtime code and
selectors. Operator configuration, databases, adoption/account bindings, locks,
SSH configuration, sockets, logs and unknown entries remain in the install
root. Removal requires matching deployment ownership and an installer-engine
with entry-list support. Foreign roots and linked ancestry refuse before
removal; unowned binstubs remain untouched. Connection Owner registration and active/reconnect transport
receipts also refuse; uninstall does not stop the owner or close SSH masters.
The canonical uninstall engine holds runtime, snapshot and redirect-publication
custody throughout finite native cleanup. Persistent lease files alone are not
busy and retain their inodes; actual conflicting owners refuse admission.
Every native mutating lifecycle action shares the install transaction inode;
Bash uses an attached standard-library owner rather than depending on `flock`.
Existing native dry runs perform read-only admission and remove nothing.

## Overview

A copilot-extensions plugin that provides:

- **SSH transport** -- multiplexed SSH connections to CodeSpaces via
  ssh-manager, wrapping `gh codespace ssh --config`
- **Lifecycle management** -- list/pool, create/reuse, wait, stop, finalize,
  prune/delete, and status for CodeSpaces
- **Credential relay** -- contribute the CodeSpace relay profile to the
  agent-bridge-owned relay, then expose it to the CodeSpace over SSH reverse
  forwards (git credentials through host GCM; optional Azure tokens through
  `az-login`)
- **Agent-bridge provider** -- when agent-bridge is installed, a session-start
  hook drops a `providers.d` manifest so `codespace:<name>` agents resolve live
  over the agent-codespaces CLI boundary
- **CLI-mode sessions** -- `copilot <name>` puts a real interactive Copilot
  CLI session (tmux, inside the CodeSpace) into your terminal; `copilot <name>
  --detach` starts it for an orchestrating agent instead (JSON handle; the
  Connection Owner keeps its relay + host-bridge forwards alive), observable and
  steerable through agent-bridge; if a new session registers but the seed
  cannot be typed into the TTY prompt, the seed is delivered over that same
  bridge message lane instead of stopping the session; `--ref-file <path>`
  hands the worker a reference file (HAR trace, transcript, logs) without the
  orchestrator reading it; `--reverse-forward VENUE:HOST` keeps a host port (e.g. a
  browser's DevTools) reachable inside the venue; `--forward PORT[:VENUE]`
  keeps a venue port (e.g. the worker's dev server) reachable on this host;
  `--stop` ends it. See the
  `codespaces-lifecycle` skill.
- **Resource obligations** -- a borrowed CodeSpace is an accountable
  **obligation** on the borrowing worktree: `ssh` journals an `active`
  `codespace` claim onto its ledger, a clean disconnect settles it to `at-rest`
  and **mirrors that disposition onto the shared exclusion lease** (cross-machine
  visible), so the worktree's `agent-worktrees finalize` is gated until the box
  is safe. See [`docs/resource-obligations.md`](docs/resource-obligations.md) and
  the `borrowing-codespaces` skill.
- **Session context map** -- a `sessionStart` hook injects a brief
  `additionalContext` map of the repos delegated to CodeSpaces (derived from
  `agent-worktrees related list`), so every session knows which repos have no
  local checkout and must be worked via a CodeSpace

### Relay-token file transactions

Relay secrets remain stable per Codespace in the existing `relay-tokens.json`
format. Mint/re-scope and revoke hold one OS-backed transaction lock across the
entire read-modify-write, with a five-second contention budget. Publication uses
an attempt-owned temporary file and atomic replacement. Transient Windows
sharing/access failures receive bounded retries; persistent failure preserves
the old store and refuses the operation instead of inventing an empty store.
The lock file stays in place and is released by descriptor close/process exit,
not deleted or reclaimed by age.

Bridge's file-backed authorization readers use the same bounded read behavior;
they remain lock-free and reject requests if the store cannot be read. Legacy
string entries and structured resource allowlists remain compatible. Older
writers do not participate in this transaction lock: serialize mixed-version
writers during rollout. This repair neither rotates existing secrets nor
establishes listener/tunnel readiness or token-refresh recovery.

The opt-in native regression packet is
`python tools/run-plugin-tests.py agent-codespaces --test-file tests/test_relay_token_concurrency.py --allow-explicit-tiers`.
It uses synthetic stores and caller-contained children, not installed credentials.

### Transport identity boundary

`codespace:<name>` addresses a dynamic GitHub Codespace, not a static named
machine from `machines.yaml`. The provider discovers its current name, state,
repository and SSH connection through GitHub; it must not reinterpret a
Codespace address as a machine-registry key, hostname or SSH environment.

The shared `machine-transport` library owns static named-machine identity and
local-versus-SSH decisions. Codespaces owns its separate venue namespace and
lifecycle. Detached launches' `_ssh_namespace()` helper only assembles options
for the existing Codespace SSH session path, preserving provisioning, credential
relay, forwarding, claim ownership and disconnect settlement. It is not an
independent machine resolver and does not need a `machine-transport` dependency.

### Target-local plugin preparation

Direct SSH refuses preparation and requested command delivery when the Connection
Owner's existing relay wait times out or its own supervised relay cannot start.
The refusal retains normal tenant, target-lock and transport cleanup without
restarting the Owner or replacing its relay. Successful admission and explicit
`--no-relay` operation retain their existing behavior. These signals do not prove
end-to-end credential delivery; host credential checks remain a separate boundary.

After positive Owner/forward admission, direct SSH also requires the exact
exported remote listener to answer the shared relay ping/pong command before
preparation or command delivery. The check uses the existing account-qualified
SSH manager and selected operation budget, with a finite default when none is
selected. Missing, wrong-service or inconclusive results refuse without replacing
the Owner; cancellation retains normal owned cleanup. Explicit `--no-relay`
skips the check. Ping never fetches credentials or proves real Git authentication.

Fresh ACP and attached CLI launches prepare selected plugins before starting the
actual Copilot child. Credential relay readiness precedes authenticated checkout
provisioning and plugin installation; checkout-creating `on_connect` hooks run
before Git-root-dependent preparation. Cross-repo targets carry a separate,
provider-generated clone-if-missing command, used by both fresh and replacement
Session Hosts rather than evaluating an ACP command prefix.
For fresh and replacement children, a dynamic environment-selected workspace
overrides conventional metadata: the validated probe resolves one concrete path
for preparation and the child command. Live Host reattachment retains its
persisted workspace instead of redirecting a running child.
Path resolution before provisioning determines intent without requiring an
existing directory. After relay readiness, authenticated hooks and any explicit
clone run before strict directory/Git validation, preparation, and child launch.
Fresh-start ownership cleanup covers preflight as well as connect failures;
it releases newly acquired claims/locks, restores a failed replacement's lock
transfer, and retains ownership when remote launch cleanup is still pending.
An unconfirmed claim release retains the failed session's existing ownership
and TargetLock and reports cleanup failure without claiming a remote Host is
running. Parity retention begins at connect, never at early claim denial or
workspace preflight.
The existing `provision-command --phase helpers|hooks` interface separates
helper deployment from authenticated hooks: Session Hosts deploy helpers first,
verify the real relay, then run hooks in the login-shell launch environment
before plugin preparation.

`namespace-resolve` additionally declares `portable_receiving_policy` version
`1`, with the provider's raw `acp_command` for Bridge's enforcing Session Host.
Its `activation: start` field names the provider-owned operation for an
explicitly admitted stopped-target launch; ordinary `ensure_ready` only
reports connectability and does not wake the venue.
`agent-codespaces start <exact-name>` checks the registered launch policy,
uses the authoritative per-name account binding, requests activation and waits
for `Available` under that same account. A missing or failed bound lookup never
selects another account's same-named venue. A known owner's unavailable credentials refuse rather than
fall back to ambient authentication. This operation does not provision guest
tools, project credentials, stage plugins or launch a worker.
On first use without an account map or stored binding, it identifies the
authenticated login and revalidates the exact venue with that account's explicit
credentials before activation. This does not change global authentication and
never follows a failed authoritative binding with ambient fallback.
The ordinary ACP/spawn command retains its guest-owner policy guard for prior
callers and direct launches. Bridge selects the additive declaration only for
its host-owned portable receiver; it never strips a shell guard or treats a
preflight allow as launch authority. See the
[receiving contract](../agent-bridge/docs/repository-hosting.md#host-selected-portable-policy-contract).

Pre-plugin relay readiness has a 30-second total budget: up to 20 seconds for
SSH startup and transport retries, with 10 seconds reserved for owned SSH
cancellation cleanup. Windows process-tree termination is bounded to 3 seconds
before killing and reaping the SSH root; cancellation does not detach its reaper.
Each remote ping remains bounded to 3 seconds; a relay that never answers still
blocks launch.

Attached CLI launches retain their foreground-owned relay fallback when the
Connection Owner is unavailable. Its event loop supervises the forward until the
interactive child exits, while SSH retains the main thread and terminal signals.
Rejoining an already-live CLI session does not reinstall plugins.
Interrupting before preparation is ready cancels the in-flight work and waits
for its relay/connection cleanup; normal interactive exit releases the retained
forward without canceling already-completed preparation.
Attached checkout-creating hooks receive the same relay launch prelude inside
their login shell, including on a cold foreground fallback without cached auth.
Full SSH provisioning and fresh `create` provisioning carry that context too;
fresh creation also supplies it to authenticated control-repository bootstrap.

Connection Owner retirement waits for its already-admitted native work: late
factory results remain in channel custody, mirror append/push work keeps its
slot and OS lock until settled, and channel teardown waits for native process
and registered proxy cleanup rather than relying on forward health. Repeated
cancellation does not abandon teardown. The beacon's bounded `stop()` remains
unchanged; daemon finalization additionally joins its thread before withdrawing
liveness. Shutdown never starts an unstarted transport or replays an unknown
transcript push, and dirty/prune markers retain any owed work for a later Owner.
The liveness beacon also records the running process's frozen build `version`,
alongside its PID and process-birth identity. Changing the selected runtime does
not relabel an incumbent process. Legacy beacons without this additive field
remain readable, but do not establish which build is running.

On Windows, install/update preserve scheduling by default: no missing login task
is created and existing/custom registrations are not rewritten. Explicit
installation-local scheduling opt-in remains supported. The existing on-demand
`ensure_owner_running` launcher remains independent of Task Scheduler; capability
activation (`connection_owner.enabled`) is not scheduling consent.
Normal install/update with desired task absence still reconciles an independently
enabled selected Owner; only the explicit `task-retire` command is strictly
registration-only. Unknown capability status cannot report complete readiness.
An observed disabled task holds installer startup; without the ScheduledTasks
module, install remains usable and defers startup to on-demand use instead of
guessing the task's enablement. Query errors are not successful absence.
Disabled capability configuration creates/starts nothing and leaves existing
registrations alone unless task-only retirement is deliberately requested.
Code application does not depend on old beacon compatibility or channel cleanup.
See [Windows scheduling](docs/configuration.md#windows-scheduling) for opt-in,
registration-only retirement, independent holds and state-preservation limits.

An immutable-slot Owner freezes its principal-owned completion identity at
birth, using the slot's retained runtime helper. A verified selection inequality
(including rollback or repair, not package-version ordering) ends admission of
new reconcile cycles, but does not alone end process lifetime. Retirement and
cleanup wait for a fresh, principal- and process-birth-verified passive or active
beacon matching the different selected full identity. Missing, stale, foreign
or unprovable successor readiness does not authorize retirement.
Its isolated startup explicitly selects the
[external Python bytecode cache](../../libs/agent-procutil/README.md#managed-python-bytecode-cache)
under the existing durable owner HOME, without moving existing bytecode or state.
The selected process publishes additive `runtime_identity`, `state` and
`work_lease_version: 1` beacon fields. `passive` readiness has empty serving sets:
it is process readiness, not proof of relay service. One OS-held work lease in
the existing durable HOME excludes channel mutations across generations until
predecessor cleanup settles. Legacy/unknown peers do not prevent incoming
startup; without a compatible work lease, their observed native process birth
is conservatively retained before activating incoming work. An unknown birth
can leave that process passive, never license forced shutdown. Predecessors
cannot withdraw a successor's beacon. Source/legacy layouts without immutable
slot attribution retain the previous singleton discovery behavior.
Lease-less process-birth exclusions are persisted separately in
`connection-owner.predecessors.json`, under the existing registry lock, before
replacing a legacy beacon. Passive restarts, supersession and beacon cleanup
retain this evidence. Only positive native exit or birth mismatch retires an
entry; corrupt, foreign, linked or unknown evidence conservatively excludes
work without preventing passive startup or code application. Barrier updates
are principal-validated, bounded and atomically published; an empty record is
retained rather than deleting the last durable exclusion path.
This record is preserved state, outside the uninstall profile's disposable
`root_entries`. The existing uninstall custody guard refuses unresolved or
uncertain exclusions; settled empty evidence survives ordinary runtime removal.

`agent-codespaces owner --stop` requests cooperative shutdown of the verified
local PID and process-birth identity. The running beacon advertises
`owner_control_version: 1` for the `agent-codespaces.owner-control` contract;
an older or unknown provider is refused before writing a request. Version 1
uses the existing owner registry lock and an atomic, bounded
`connection-owner.stop.json` request/acknowledgement. The owner stops its loop
and acknowledges only after normal channel cleanup returns; cleanup failure
and the 30-second timeout are non-success outcomes, never process termination
or permission to target a replacement. Holds and pending work remain intact.
This explicit shutdown is not called by installation or replacement activation.

`ssh`, `copilot`, and `namespace-resolve` accept repeatable
`--stage-plugin SOURCE` and `--stage-plugin-disabled SOURCE`. The latter preserves
an explicit disabled selection; paths, spaces, and `=false` in a source remain
literal. Provider selections take precedence over related-source fallbacks.
Payload lookup prefers explicit controller roots, then current configuration
roots before adopted anchors; settings retain their existing merge order.
The existing `plugin-plan` command accepts repeatable `--plugin-root PATH`
arguments in highest-priority-first order to carry validated controller roots
across the provider boundary.

## Runtime health and activation

Both installers verify `python -I -B -m agent_codespaces --help` before publishing
completion or activating a numbered or claimed development slot. Isolated
module execution checks the actual CLI entrypoint and its imports; a successful
package import alone can accept a partial namespace package. Checkout files,
`PYTHONPATH`, and user-site packages cannot mask missing runtime files.

On Windows, a host with `uv` but no Python on `PATH` can bootstrap construction:
the installer first asks `uv` for Python 3.11, then uses its normal managed-Python
run path if necessary. It requires a successful concrete interpreter result
before construction; failure does not authorize reuse of an incomplete slot.

Normal install, update and first-use provisioning reuse a matching completed
generation only after that launch check. An occupied but incomplete, malformed,
content-conflicting or unhealthy target instead constructs a distinct
`<version>+repair.<generation>` slot (or `.repair.<generation>` after a
source-qualified identity), including under `--force`/`-Force`. The original
slot and user configuration are not rebuilt or removed. Failed construction or
incoming health leaves the existing selection intact; a healthy candidate is
completed before activation and recorded in the deployment manifest. Repeating
the same request reuses its healthy selected repair without dependency writes.

PowerShell reuse also verifies the retained bootstrap helpers required by its
completion step; stale helpers select a fresh generation rather than modifying
the old slot. Undecodable legacy selector contents do not block construction
or publication of a healthy incoming selection. Filesystem safety and storage
failures retain their existing handling.

Source receipt validation remains bound to the original source generation,
independently of the repair suffix. This native CLI recovery does not establish
central-service cutover, shared admission/coalescing or installed-host rollout.

### First-use deployment admission

The stamped global command and payload-local runtime gate use the canonical
resolver's ready mode before considering provisioning. A healthy selected
runtime remains callable under managed deployment. Cold commands require the
selected installation's publication decision before writing boot traces,
provision status or orchestration locks; context validation alone is not
deployment permission. Missing or older admission support produces an explicit
readiness error rather than an alternate installer or completion parser.

On Windows, initial installation `status` and selected-context `validate`
reads may use the payload's canonical SDK in a fresh isolated, no-site Python
process. The [PATH Python bootstrap selector](../../docs/install-contract.md#path-python-evaluator-bootstrap)
admits a registered signed CPython base before it executes; PATH and the
addressed runtime do not establish admission. Missing optional support or an
unavailable base selects native PowerShell before evaluation. Once selected,
interpreter/SDK failures are terminal, not a reason to try another evaluator.
The file guard is released before ordinary package-site/runtime resolution;
publication admission and provisioning retain their existing ordering.

Windows stamping captures the private snapshot candidate's canonical source
before publishing the command and replacing the previous selection. Failed
resolver or command publication leaves that previous snapshot and its selectors
intact. Resolver destinations must be files, not directories.
Checkout-stamped Windows snapshots retain the declared local dependency graph,
including the bootstrap driver, for provisioning without the original checkout.
The global command binds configuration and provisioning to its installed root.
The generated PowerShell command uses UTF-8 with a BOM so Windows PowerShell
5.1 preserves non-ASCII installation paths.
PowerShell's legacy native argument mode stringifies and encodes forwarded
arguments before module execution to preserve embedded quotes and native
argument conversion; newer native argument passing retains direct module
invocation.
Cold `provision` builds and activates the addressed runtime without publishing
user-global commands; ordinary install/update/stamp retain their command
publication responsibilities. An incomplete selected slot remains a cold
candidate rather than a silent POSIX dispatcher exit.

### Session bootstrap execution

The legacy/default session bootstrap submits cold `stamp` and installed-payload
version-drift `provision` actions to the same durable installation driver. Its
self-contained foundation ships through canonical dependency materialization;
it does not require Manager, another plugin or an existing Python runtime for
the first stamp. POSIX drift detection retains its existing Python metadata
reader. The hook uses its own canonical installed payload and default legacy
root, not the deployment manifest's potentially different source.

Direct POSIX installer mutation requires either native `flock` or an already
available bootstrap Python for the same install-inode transaction. Native
`flock` permits Python-free cold stamping; the Python fallback supports Bash
3.2. If neither capability exists, the installer refuses without provisioning
one. Read-only `status` requires neither capability.

The existing 15-second hook returns `{}` after bounded native launch admission.
Its diagnostic acknowledgement identifies a supervisor and log, **not** durable
queue acceptance or installation success. Read the ordinary request receipt
and final result in that log/queue. The driver owns construction and native
installer execution independently of the caller; the hook never runs an
installer in the foreground or reaps an age/PID marker.

Healthy runtime detection remains a no-op. Managed publication, pinned reads,
no-self-provisioning, nested custody, missing/skewed driver support and
unsupported source/installation selection retain explicit refusal or defer
outcomes without another installer fallback. First stamp preserves native
Windows snapshot versus POSIX payload-pointer behavior. Drift preserves the
canonical config-derived Connection Owner reconciliation, including disabled
and unknown configuration; no client-only service flag is introduced.
Provider registration and exact-session guidance remain independent hooks.
This migration does not cover primary/payload command publishers, selected
snapshots, explicit installers or namespaced/custom-root bootstrap publication.

## Configuration

Runtime-home overrides are anchored to the selecting process's cwd and
normalized lexically, including `..`, before daemon reservation or admission.
The owner child receives that same canonical home even when launched from
HOME. Filesystem links are not resolved away; instance custody checks still
refuse redirected runtime roots.

**Most repos need no config at all.** agent-codespaces works out of the box on
standard GitHub CodeSpaces by **convention**:

- machine `largePremiumLinux`, location `EastUs`
- in-CodeSpace checkout at `/workspaces/<repo-basename>`
- credential relay serving `github.com` **and** Azure DevOps (via the host Git
  Credential Manager) when the agent-bridge relay is running

So `agent-codespaces create <your-org>/<standard-repo>` just works -- no file to
author.

Add a **supplementary** config only when a repo deviates from convention (a
split CodeSpaces-vs-product repo, a pinned devcontainer, an ADO host, a
provision hook). It lives in the **adopting repo**, in the canonical
`.copilot-extensions/<plugin>/` namespace:

```
<repo>/.copilot-extensions/agent-codespaces/config.yaml
```

Scaffold and adopt it in one step from inside the repo:

```bash
agent-codespaces config init      # writes .copilot-extensions/agent-codespaces/config.yaml (+ auto-adopts)
```

On Windows, the noninteractive workspace-discovery command used by `config init`
runs with console-window suppression.

Running a command inside a repo that carries the file **auto-discovers** it (no
manual `config adopt`); adoption persists it for the detached daemon and for
extra/multi-repo setups. Legacy `.agent-codespaces/config.yaml` and repo-root
`codespaces.yaml` are still read as fallbacks -- relocate them with
`agent-codespaces config migrate`.

```yaml
# .copilot-extensions/agent-codespaces/config.yaml -- SUPPLEMENTARY, in-repo. Add ONLY what
# deviates from convention; everything omitted is derived.
repos:
  org/my-app-codespaces:
    workspace_repo: my-app          # split repo -> agents land in /workspaces/my-app
    machine_type: largePremiumLinux256gb
    devcontainer_path: .devcontainer/devcontainer.json   # pin if repo ships >1

credentials:
  ado_host: my-org.visualstudio.com   # only for bare ADO get-access-token
  identity_env: [GITHUB_USER]         # optional launch-time host identity alias
```

> The service reads config live from the repo -- no generated intermediate
> config. All org/account/URL values live in **your** repo, never in the plugin.

For launch-time credential extras, `credentials.feed_token_env` exports fresh
relay-minted Azure bearer tokens into named env vars, and
`credentials.identity_env` exports the host Azure-login identity string those
tokens represent. Ordinary user principals export the short alias (UPN local
part); other principal types keep the reported identity string.

### Installer reuse

On both platforms, a healthy completed runtime with matching canonical payload
admission skips venv construction, package installation and UV acquisition.
Health failures, conflicting payloads and malformed completion evidence still
refuse reuse. Fresh construction acquires UV only after canonical admission;
an acquisition failure remains an error and the incomplete candidate is reclaimed.

### Repo provenance & the active-plugin config seam

A repo's venue policy does **not** have to live in an adopted control-plane repo.
A plugin can ship the venue's **repo provenance** with itself and make it
discoverable with **no control-plane repo**. Two convention-discovered seams are
honored here:

- **Config declaration (`codespaceConfig`).** The active plugin's `plugin.json`
  names one string path relative to its payload root, for example
  `"codespaceConfig": "references/agent-codespaces/config.yaml"`.
  agent-codespaces resolves effectively active plugins, reads the declaration
  from the identity-verified root, rejects path escapes and non-regular or
  malformed targets, and parses the file as the normal supplementary config
  shape. No `sessionStart` hook or user-level pointer is required.
- **Hygiene and compatibility.** Legacy and operator-owned `config.d` inputs
  remain supported and independently diagnosed. A pointer for an identity with
  a valid active declaration is reported as superseded and cannot override or
  reject the declaration. Invalid, disabled, missing, duplicate, or transient
  contributions are isolated from peers; indeterminate reads retain only their
  own last-known contribution. Runtime warnings are bounded and deduplicated;
  `agent-codespaces doctor` (or `doctor --json`) reports exhaustive findings and
  precise remediation without deleting any entry. A combined provider scan
  shares one fresh activation snapshot across declarations and compatibility
  pointers; the next scan resolves activation again rather than caching it
  across reloads.
- **Precedence.** `load_merged_config` consumes provider configs at the **lowest
  precedence** — adopted-repo/cwd config always wins. Active plugin declarations
  precede compatibility `config.d` inputs.
- **Repo provenance (`workspace_repo`).** The provider config's
  `repos.<vessel>.workspace_repo: <product>` is what makes
  `effective_acp_command_for(<vessel>)` launch the agent in `/workspaces/<product>`
  (the product checkout) rather than the vessel folder, and what
  `resolved_workspace_folder_for` publishes as the dispatched agent's ACP
  `session/new` cwd.
- **In-venue plugins (`codespacePlugins`).** The harness plugin's `plugin.json`
  also declares which plugins to inject **into** the CodeSpace on connect (the
  `<product>-agent`), scoped by `forWorkspaceRepo` (see `codespace_plugins.py`).
  Remote-marketplace plugins are installed using an isolated `COPILOT_HOME`;
  locally sourced payloads are reflected from their declaring checkout.
  Both operations must finish before the shared preparation barrier publishes
  local marketplace paths and enablement in the execution repository's
  `.github/copilot/settings.local.json`. CLI loads those settings directly;
  ACP additionally receives `--plugin-dir` for every effective plugin.
  Bridge-owned Session Hosts use the same barrier over their existing transport,
  rather than relying on the legacy SSH launch command to stage the payloads.
  Missing required payloads block launch. Attached and detached native CLI
  attempts always complete the idempotent plugin barrier before the remote
  reuse/create decision: a host live-session registration cannot guarantee that
  its CLI child still exists. A genuine remote rejoin still reuses that child
  without replacing its active bindings or replaying its seed. Guaranteed
  surviving Session Host reattachment does not rewrite registration.
  Attached CLI launch carries the exact prepared execution checkout into the
  child login shell; its quoted `cd` runs after login profiles, including when
  explicit worktree resolution selected a checkout other than the default.
  See [`plugin-resolve`](../../libs/plugin-resolve/README.md#trusted-venue-preparation)
  for preservation, selected-asset topology, companion diagnostics, and
  installation-cache details. Dependency declarations never expand the
  eligible provider/related or target-native set.

Authoring a `<repo>-harness` plugin that uses these seams is the
`authoring-harness-plugins` skill (`customizing-copilot`) and the pattern
[`docs/patterns/codespace-repo-provenance.md`](../../docs/patterns/codespace-repo-provenance.md).
The reference implementation is `example-web-harness` (example-marketplace).

## CLI

agent-codespaces is a standalone CLI/binstub. Listing, creating, deleting,
waiting, stopping, and diagnostic SSH do not require registering the current
repo as an agent-worktrees harness. The bridge namespace and shared credential
relay are optional sibling composition: if agent-bridge is absent or stopped,
`codespace:` dispatch and relay-backed auth stay dark, but the CLI remains
usable (use `--no-relay` for relay-free diagnostics).

```bash
agent-codespaces ssh <name>           # SSH into a CodeSpace
agent-codespaces ssh --stdio <name>   # Structured SSH for agent-bridge
agent-codespaces list                 # List active CodeSpaces
agent-codespaces pool                 # Pool view: disposition + core budget
agent-codespaces allocate <owner/repo> # Reuse/create/recycle/pressure decision
agent-codespaces create <owner/repo>  # Create, guarded by reuse/budget checks
agent-codespaces wait <name>          # Patiently wait for Available
agent-codespaces stop <name>          # Recover sessions, then stop (preserve)
agent-codespaces sync-sessions <name> # Non-destructive session capture (stays leased/running; never boots/stops/deletes; defers if held/unbound/mid-write)
agent-codespaces finalize <name>      # Recover, stop, mark recovered/reusable
agent-codespaces finalize <name> --delete  # Recover, verify off-box safety, delete
agent-codespaces verify <name>        # Publish git-cleanliness safety verdict
agent-codespaces delete <name>        # Delete a CodeSpace (--force to skip prompt)
agent-codespaces config init          # Scaffold .copilot-extensions/agent-codespaces/config.yaml (+ auto-adopt)
agent-codespaces config adopt         # Register a repo's config for the daemon
agent-codespaces config migrate       # Relocate legacy config -> .copilot-extensions/agent-codespaces/config.yaml
agent-codespaces config show          # Show resolved config
agent-codespaces config validate      # Validate resolved config
agent-codespaces cleanup              # Remove stale local state (SSH configs, sockets)
agent-codespaces doctor               # Check gh auth + config-provider hygiene
agent-codespaces doctor --json        # Exhaustive structured auth/config report
agent-codespaces status               # Runtime/config/gh/ssh overview
agent-codespaces version              # Show version
```

There are also bridge-facing seams (`namespace-list`, `namespace-resolve`,
`namespace-target-repo`, `namespace-ensure-ready`, `relay-profile`,
`relay-launch-env`, `provision-command`, `acp-model-flags`). They are invoked by
agent-bridge and are not the normal human/operator surface.

### Periodic session capture (`sync-sessions`)

This repo ships only the on-demand `sync-sessions` verb and its liveness gate
-- it never schedules anything itself, exactly like `agent-containers`'
`rescue-capture` (session-rescue-parity Phase 1's recorded decision: no
existing repo-owned loop -- e.g. the Connection Owner daemon's
`run_owner_daemon` -- covers every leased CodeSpace unconditionally, so
scheduling stays a consumer concern). A downstream consumer that wants
periodic evidence preservation for a long-lived, never-recycled CodeSpace
wires its own external timer (cron, a systemd unit, a scheduled task) that
periodically invokes:

```bash
agent-codespaces sync-sessions <name> --account <account> --json
```

The verb is safe to invoke on any schedule: it never boots a non-`Available`
CodeSpace, defers (exit code `75`) whenever the box is held/unbound/mid-write
or its own preflight fails (not found, account unauthenticatable, etc.), and
is a no-op success when there are no sessions to capture. `75` covers every
`deferred` case, not only transient contention -- **a consumer's timer must
not blindly retry forever on `75`; always read the JSON `detail` field**, since
a permanent configuration/identity problem (e.g. a missing account binding, an
unmintable `gh` token) also returns `75` and will never resolve itself on a
retry. Because account resolution is fail-closed (an explicit `--account` or
an exact per-name binding only), a scheduled invocation should pass
`--account` explicitly rather than rely on binding lookup succeeding
unattended.

`sync-sessions --json`'s result is `{ok, deferred, session_count, detail}` --
`deferred` is the busy/held/not-ready/misconfigured case (exit `75`, `detail`
explains which), `ok` is the capture/no-op-success signal otherwise, and
`detail` always carries a human-readable reason. This is deliberately the
same shape family as `rescue-capture`'s per-member result (`captured`/
`rescues`/`deferred`, same busy exit code) scaled down to one target instead
of a fleet, so a consumer already handling one provider's capture verb needs
no new mental model for the other's.

### `create` options

```bash
agent-codespaces create <owner/repo> \
  --branch <branch> \           # branch to create on (default: repo default)
  --display-name <name> \       # CodeSpace display name
  --devcontainer-path <path> \  # only needed to override multi-devcontainer resolution
  --timeout 300 \               # seconds to wait for Available (default 300)
  --force-create \              # bypass reuse-before-create / core-budget guard
  --no-wait                     # don't wait / skip provisioning
```

Machine type and location default by convention (`largePremiumLinux` / `EastUs`)
and can be overridden per-repo in `.copilot-extensions/agent-codespaces/config.yaml`. After the
CodeSpace is Available, any `on_create` provisioning hooks from that config run
automatically. Without `--force-create`, `create` first consults the pool
planner: it returns this workstream's valid retained venue, or reserves a
compatible explicitly returned stopped venue, instead of creating a new one.
It refuses when local capacity is unknown or under pressure. A running idle
appearance, expired lease or missing local process is not a return.

Sequential reuse is currently limited to stopped venues on the selected
account and matching repository, with the requested branch (when specified)
and at least the configured machine's core capacity. Known wake headroom is
required independently of owned slots. It requires an explicit shared release
tombstone, no local holder (including an expired record), known absence of local users,
and the selected lease receiver's `lease-acquire-released-only-v1` capability.
Missing support leaves the legacy workstream/create path intact; it does not
silently allow stale takeover. `allocate` remains advice, not a reservation.
Ordinary `create` records the native obligation and exclusive shared/local
claim before reporting a reserved name, rechecking stopped state and headroom.
Reservation checks existing provider holds, including retained uncertain or
unreadable hold state, before account binding, journaling or shared acquisition,
and retains its final admission recheck.
It neither wakes nor provisions that retained venue, changes its branch, nor
discards files or configuration. Read-only and no-build review tasks can use
the same exclusive sequential reservation; concurrent sharing is not enabled.
An uncertain reservation retains native responsibility and reports pressure
instead of blindly retrying acquisition or creating a duplicate. Inspect
`claims show` and `leases` before reconciliation. Normal Bridge connection is
re-entrant for the reserving worktree; return through the existing
`release-claim`/owning Worktrees lifecycle, not advisory `release` or force.
An outbound obligation alone proves responsibility, not acquisition. Unconfirmed
workstream ownership refuses even with spare slots. Confirmed local ownership
retains legacy own-venue reuse; an explicitly returned venue must be reserved
again. The selected account is retained before an uncertain attempt, and strict
binding writes preserve additive fields, other records and corrupt-state bytes.
Qualified ownership is read through the selected Worktrees runtime, not an
installed sibling Python package or a directory-basename identity guess.
Native anchor IDs may differ from their repository directory names.
Cross-caller return matches the stored native worktree ID or native-recorded
path together with its complete qualified reference; it does not resolve a
relative owner against the returning caller's directory.
An older noncanonical owner label can also be linked by its exact active
fencing token and unique qualified native owner. An unmatched epoch or an
already-released lease without an identifiable original owner remains unknown.
Failed or malformed ledger reads refuse;
an omitted inventory row does not hide a matching bound obligation.
Admission classifies every matching obligation; one returned venue cannot hide
another uncertain acquisition. Holder equality includes machine, project and
worktree, ignoring only the session suffix. Unmapped obligations spanning
accounts require an explicit repository-account selection.
Caller-owned local claims and pending returns also participate independently of
the native ledger, including after its entry is released or removed. Pending
returns remain unresolved even with spare owned slots; unreadable local custody
refuses admission rather than becoming an empty snapshot. The same retained
custody preserves account selection across an active-account switch.
Each plan resolves the caller path and qualified reference once and shares that
pair with obligation classification and candidate selection. Known missing
identity is retained within that plan, not probed again per venue. The next
plan resolves fresh; reservation independently revalidates identity before effects.
Tokened return confirms shared release and settles the original qualified
owner's ledger before removing the unchanged local claim. Unknown return or
deferred settlement reports failure and retains local custody, including through
generic and bulk release. A quota retry that discovers a returned venue uses
the same reservation flow rather than creating again.
Tokened own-workstream reuse also requires shared evidence; only tokenless
legacy local claims retain the optional-peer fallback. Ordinary `claim` journals
responsibility like the connection path. A reopened owner's valid earlier
finalization history is diagnostic data, not a failed active-claim ACK or
restoration of those old resources. Return accepts a missing legacy ledger
entry only after a complete qualified native read confirms absence or prior
release, never after a failed lookup.
Shared fencing tokens must be raw JSON strings with the supported object-ID
shape; numeric values are never coerced into apparently valid tokens.
Claim, advisory borrow, heartbeat, sequential reservation and return share a
per-CodeSpace local OS-held exclusion. Return holds it through native settlement
and local cleanup; ordinary claim journaling stays inside it too, outside the
short lease-file transaction. Concurrent operations report busy, and a process
exit releases the guard without deleting or age-stealing its lock identity.
Busy, unsettled or malformed return coordination is a blocking rejection for
SSH and attached/detached Copilot admission, not optional bookkeeping.
Provider-hold publication briefly joins this same exclusion before its body;
it does not retain the guard during provider work or block normal return
inside that hold. Claim checks provider holds and known local conflicts before
shared coordination, then rechecks before committing local ownership.
Force, renewal and adoption do not bypass an existing hold. A rejected attempt
returns only a shared acquisition it created, never an original lease it
renewed or adopted. A defensive late-hold check preserves a rotated original
token only when its local snapshot is unchanged; replacement records remain
untouched. Unconfirmed rollback is reported explicitly, not called cleanup
success. Provider-hold, corrupt-admission and store-lock failures block
owner-associated connection admission rather than becoming bookkeeping warnings.
Generic and bulk return also pin their preselected local snapshots; a delayed
selection cannot release a replacement claim from the same owner.
The existing `leases.json` retains pending returns in the reserved
`__agent_codespaces_returns_v1__` entry: original local snapshot, qualified
owner, an available original `lease_id`, and prepared/confirmed-release phase. Ordinary lease records keep their
legacy shape. After confirmed release, a settlement retry uses that saved
owner without rereleasing or inspecting a later borrower's lease. Unknown
progress or settlement retains custody; unrelated current writes preserve it.
Progress rows must be JSON objects; arrays with matching key names are malformed
state and receive the same blocking admission diagnostic, not a traceback.
If publishing the released phase fails after a positive shared-release ACK,
the same invocation may still finish through confirmed native settlement and
durable local cleanup. Native ledger retirement is bookkeeping, not proof of
shared release: finalization can mark it released without returning the venue.
Prepared progress, including an older optional active-obligation fact, cannot
use a later ledger transition as shared-return evidence.
If the actual ACK is no longer available and no released phase was persisted,
retain original custody when the shared generation cannot be confirmed.
Return captures the original raw generation ID before effects, while the
shared fencing token and qualified holder still match. When the exact selected
receiver advertises `lease-return-proof-v1`, it requests bounded return evidence
through `lease release --retain-return-proof`; absent support keeps the ordinary
acknowledged-return path without trying new syntax. A failed new invocation is
never replayed with older arguments.

Retry may confirm that saved generation from its own current release tombstone,
or from the receiver's strict schema-2 `returned_generations` evidence after
another host acquires or returns the venue. Proof must match the resource,
original generation and complete saved holder.
Other successor or historical holders follow the receiver's bounded opaque-string
contract; they need not resolve as Worktrees references. Only the matching
original proof must equal the exact saved holder.
Missing, evicted, malformed, foreign or rollback-unreadable evidence remains
unknown. A same-holder tombstone without a previously captured generation is
no longer enough: it can describe
a later borrow. A persisted released phase or an ACK still known to this
invocation retains its existing meaning. Recovery settles only the original
owner's ledger and unchanged local custody; it never returns a successor.
Do not settle the native ledger as a workaround, remove its entry, hand-edit
progress, release a successor or infer success from absence/expiry.
Release and selected installed/POSIX/live qualification of this recovery remain
tracked in [#11343](https://github.com/ThomasMichon/copilot-extensions/issues/11343);
source tests are not installed or live reconciliation.
Older tolerant readers still see ordinary holders; older strict admission
readers refuse the extra entry. Older writers do not join the new exclusion
and may discard progress: serialize mixed-version writers during rollout or
rollback. Lost progress is unknown, not proof another borrower's return
settled the original claim. No automatic replay or cross-version concurrency
guarantee is implied.
The existing per-target lifecycle lock covers final inventory/user checks,
local reservation, binding confirmation and eligibility-marker removal, so a
managed destructive operation cannot interleave with that commit sequence.
Explicit creation-only display-name/devcontainer options and a repository's
declared or custom global devcontainer/location preferences do not silently become cross-effort reuse:
their compatibility is not established by this pool inventory. Existing own
venue reuse remains unchanged; `--force-create` retains its explicit meaning.

Allocation and ordinary creation also enforce `defaults.max_owned_codespaces`
(conservative policy default 30) under the target repository's selected account.
Stopped venues still consume owned slots; stopping one only frees running
cores. Failed, malformed or truncated selected-account inventory refuses
new compute with pressure (exit 4), rather than falling through to a
blind provider call. Unknown machine costs also refuse additional compute;
a stopped claimed venue needs known wake headroom.
`--force-create` explicitly bypasses local capacity/reuse policy, not provider
limits or authentication. Any quota retry retains existing rescue, liveness
and cleanup gates and is restricted to that account. Automatic reclaim requires
a matching persisted account binding; absent, conflicting or unreadable binding
is not permission to reclaim. The same validated credential reaches rescue,
deletion and the account-specific stop-state lookup. Creation's headless REST
fallback retains the admitted environment rather than rediscovering credentials.
Ordinary quota recovery checks fresh known core headroom before a rescue can
wake a stopped venue, then revalidates the same creation policy after reclaim.
It reports completed recovery even if a concurrent capacity change blocks the
retry. Explicit force-create still overrides only local policy, not the
account, rescue, liveness or cleanup gates.
For unmapped repositories, a workstream's own matching claim and persisted
account binding take precedence over a newly active account, so an account
switch does not hide its retained venue. Explicit repository mappings still
win; unrelated bindings do not select an account. Binding contention or an
unavailable selected credential refuses admission rather than switching users.
Admission opts into content-strict reads of the existing binding store: corrupt,
malformed or unreadable state refuses before account switching, without changing
legacy readers or replacing the stored bytes. A genuinely absent store stays
empty.
An expired or malformed ambient identity may fall back only to that bound
account's qualified credential. Creation-scope checks run after reuse admission
and use the admitted environment, not an unrelated ambient token.
Bound status and activation prefer saved account credentials, or verify that an
environment-only credential belongs to that same account before using it. A
different environment identity never satisfies a bound-account lookup.
Fully qualified repository requests preserve the owner when matching claims
and bindings; logical short names retain their host-repository aliases. The
selected creation-scope check excludes unrelated saved logins and hosts.
Malformed allocation metadata or account-binding fields produce an actionable
refusal, not an uncaught parser failure. Devcontainer discovery and creation
share the admitted account environment rather than rediscovering credentials.
GitHub.com saved-account resolution, identity verification, qualified inventory,
creation, bound status/activation and token-pinned recovery SSH/deletion pin
their host independently of an ambient enterprise `GH_HOST`.
Allocation metadata checks include display names and branch text before pool
parsing.
Default creation also retains the selected account during availability polling
and the admitted token through post-create provisioning SSH.
A recycle recommendation is advisory, not freed capacity: ordinary creation
refuses until safe recycling has actually occurred and inventory is refreshed,
rather than relying on a provider's higher quota to enforce the local core
budget. A partial selected-account view can still advise already-running own
venue reuse without additional compute, but cannot admit creation or a stopped
venue's wake; its headroom is unqualified. Invalid native configuration reports
a diagnostic error and can be repaired before retrying, without silent defaults.
Picker snapshots retain their JSON error envelope for invalid configuration,
and active subscriptions recover after configuration repair without a restart.

For unmapped repositories, a valid `GH_TOKEN` or `GITHUB_TOKEN` needs no saved
login: admission verifies its account and retains that credential through
inventory, creation and recovery. Explicit account mappings remain strict and
never fall back to another ambient identity.

The merged `pool` and Picker views expose `owned_by_account`,
`max_owned_codespaces` and `owned_inventory_complete`; their merged discovery
counts are observations, not complete selected-account admission evidence.
`owned_headroom` is null when completeness is unqualified. Selected-account
admission uses the stricter inventory read without changing the ordinary
discovery contract or integrating the private owner-wave foundation.

### Agent-bridge integration (automatic)

Once agent-codespaces is installed, its sessionStart hook drops a
namespace-provider manifest into `~/.agent-bridge/providers.d/`. agent-bridge
discovers it there and registers the live `codespace:` namespace resolver, so
CodeSpaces are addressable as `codespace:<name>` (raw or friendly) — listed and
resolved live, with no expiry, including newly-created ones. There is **no
`bridge register` step**; installing the plugin is all that's needed.
The manifest is versioned and attributes its plugin source/root. If its binstub
or payload disappears, bridge leaves the namespace inactive, warns without
breaking other providers, and reports exact cleanup through
`agent-bridge doctor`.

The current bridge integration is process-boundary first, not PATH/import
coupled: the manifest carries the absolute agent-codespaces binstub, and
agent-bridge invokes `namespace-*` commands to list/resolve targets. The
credential-relay and Session Host helper paths similarly prefer CLI seams
(`relay-profile`, `relay-launch-env`, `provision-command`) with in-process import
fallbacks only when the bridge venv happens to vendor the package. This follows
the repo's à-la-carte independence pattern: the agent-codespaces CLI owns its
runtime; agent-bridge only lights up optional dispatch/relay features.

## Multi-account gh (per-repo identity)

Host-side `gh` operations (`gh codespace list/create/delete/stop/ssh`, `gh api`,
and the `gh codespace ssh --config` fetch) run under the `gh` account that can
access the **target repo's org** — not whatever account is active in the `gh`
keyring. With two accounts backing different repositories (for example
`operator-public` for selected public repos and `operator-work` for work repos), the active-account
default would hide or `403`/`404` the other org's CodeSpaces entirely.

- The owner→login mapping is owned by **agent-worktrees** (its `repos.yaml`
  `account_map` + `accounts.yaml` catalog). Its preferred identity applies after
  exact repo overrides and explicit owner routes; namespaces never identify the
  operator. agent-codespaces shells out to
  `agent-worktrees repos account-for <owner/name>` (loose coupling — separate
  venvs) and mints a per-account `GH_TOKEN` for each `gh` subprocess.
- **Selected credential isolation:** named GitHub CLI store lookups remove
  inherited token overrides, so a work token cannot shadow an explicit public
  account choice. If the named store is unavailable, the existing `/user`
  verifier may reuse an inherited token only when its login matches the selected
  identity, using the existing helper's remaining verification allowance. Otherwise the
  operation refuses; it does not switch accounts or start login. Fresh owner
  observations bypass the process token cache to detect store rotation.
- **Cross-account discovery:** `gh codespace list` only returns the active
  account's CodeSpaces, so `list` (and status/resolve) enumerate under **every**
  mapped account plus the ambient one and merge, tagging each CodeSpace with its
  owning account. Per-CodeSpace ops (stop/delete/ssh) then pin `gh` to that
  account.
- **Bounded namespace-discovery overlap:** namespace enumeration opts into
  overlapping independent account-source reads, and known accounts begin listing
  before slower configuration reads finish.
  At most four native account listings run concurrently; all are joined before
  the command returns. The final merge retains source/account precedence rather
  than depending on completion order. A compatible Worktrees peer supplies
  strict bulk account-resolution metadata in the existing account-list call,
  avoiding a second process for each configured repo. Each discovery call reads
  fresh routing and shares it only within that call. Older peers retain the
  individual lookup path. No persistent roster cache or larger Bridge
  foreground deadline is introduced. A failed process, refused or malformed account snapshot
  produces a non-zero `namespace-list` result so Bridge can retain that
  namespace as incomplete instead of confirming an empty roster.
  Ordinary listing, authentication and bounded credential-owner lookup retain
  serial account/configuration reads; those callers do not acquire executor
  shutdown hooks that can prolong a short-lived command after its caller returns.
- **Auth preflight** verifies only the accounts that serve a CodeSpace -- bound
  to a live CodeSpace, owning one, or configured for a repo -- plus the active
  account when a CodeSpace uses ambient ownership. Each must be logged in with
  the `codespace` scope; the remedy is the account's recorded `accounts.yaml`
  login flow.
- **Private observation foundations:** `owner_inventory` and `owner_mux_probe`
  expose explicit qualified inventory and versioned mux-protocol operations.
  They are not wired into the Connection Owner's default factories or session
  consumer. Production retains its existing listing, probing and cleanup paths.
  A qualified listing treats errors and the 50-row limit as incomplete; it is
  not a complete inventory or fresh selected-authority API. Repeated full scope
  resolution remains unqualified for production cost. Consumer integration,
  selected-effect/full-publication scheduling and provider admission remain
  tracked separately in #7949 and #5761.
  The optional `heartbeat(..., generation=...)` fences renewal under the existing
  lock; omitting it preserves the existing behavior and does not activate a wave.
  Explicit probe cleanup errors produce unknown observations and retain caller
  cancellation; they do not establish successful channel teardown.
- **Unpinned baseline:** without a resolved preference, ambient authentication
  remains available. A resolved identity requires its own credential or a
  verified same-identity inherited token, never an unverified substitute.

### Authenticating an account over SSH (device-code flows)

Setting up a second account on a remote box — `gh auth login` / `gh auth refresh
-s codespace`, and likewise `az login` / `devtunnel user login` — runs an
**interactive device-code flow** that polls for a minute-plus while a human
authorizes in a browser. **Do not run it as a foreground command over SSH.** A
Windows SSH session is a **network logon** whose session (and its entire child
process tree) is torn down the moment the connection drops — and a
`Start-Process … -WindowStyle Hidden` child launched from that SSH shell is
*still* parented to it, so it dies too. Any tunnel blip (acute on dtssh, and on
hibernate-prone cloud dev boxes) kills the poller and the code silently expires
(`context deadline exceeded`).

Run the auth under **Task Scheduler**, which owns the process in a session that
outlives the SSH connection:

```powershell
# over ssh: write a runner, register+run a one-shot task, redirect output to a file
Set-Content $env:USERPROFILE\ghauth.ps1 'gh auth refresh -h github.com -s codespace *> "$env:USERPROFILE\ghauth.out"'
schtasks /Create /TN ghauth /TR "pwsh -NoProfile -File $env:USERPROFILE\ghauth.ps1" /SC ONCE /ST 00:00 /F
schtasks /Run /TN ghauth
# then, over FRESH ssh connections, poll the file for the device code + completion:
#   Get-Content $env:USERPROFILE\ghauth.out
# clean up: schtasks /Delete /TN ghauth /F ; Remove-Item $env:USERPROFILE\ghauth.ps1,$env:USERPROFILE\ghauth.out
```

Surface the device code from the output file, have the human authorize it (in an
**incognito** window signed in as the **target** account — otherwise the code
authorizes whatever account the browser is already on), then poll the same file
for `✓ Authentication complete`. Note `gh auth refresh` targets the **active**
account (no `-u/--user` on many `gh` builds), so `gh auth switch --user <login>`
first and restore afterward.

## CodeSpaces pivot authentication

The canonical `pool --picker-json` contribution reports authentication in its
summary: `auth_status` is `ready`, `auth_required`, `scope_blocked`, or `error`;
`auth_account` names the selected login, and `auth` includes per-account results.
These fields accompany both JSON snapshots and streaming summary frames. A
failed probe or inventory is visible as an error, never an authenticated empty
pool. Subscriptions recheck authentication rather than retaining a stale banner.

The Manager displays **Sign in** or **Authorize** above a blocked list, even
when there are no rows. Clicking starts a cancellable progress modal containing
the vendor browser/device-code instructions. Only that explicit action (or the
explicit CLI below) starts authentication; listing and rendering never do.

```bash
agent-codespaces authorize --account <login> --user-initiated
```

Omit `--account` only when no login has been selected yet. The command uses
GitHub's device OAuth endpoints and the GitHub CLI's public OAuth application
identifier. It never runs `gh auth login`: that command can modify the shared OS
keyring even with an isolated `GH_CONFIG_DIR`, and even its file-storage option
can delete the global active credential.

Identity and granted scope are verified before storing a credential in the
provider's own installation-qualified OS keyring namespace. No GitHub CLI keyring
slot is written or deleted. On POSIX without an available keyring, the provider
logs that it is using a private vendor-compatible `hosts.yml`: directories must
be owner-only and files are published with mode `0600`. The modal discloses this
fallback before authorization. Windows requires the OS keyring and reports an
explicit storage failure rather than silently writing an unprotected token file.
No token is exported into the session environment, and no global `gh` file or
keyring slot is changed. A wrong login, denied scope, failed ceremony or
cancellation is not success. Verified provider credentials take precedence over
an older named keyring token for CodeSpace operations. The Manager refreshes the
provider after success.

Authentication and inventory use one shared set of mapped, bound, configured,
provider-authorized and active account candidates. A mapped-only account
missing scope therefore offers Authorize even when another account has rows.

The internal `account_candidates.read_candidates(*, on_account=None,
concurrent=False)` seam returns a frozen `CandidateRead` containing that
invocation's ordered accounts, routing inventory, effective configuration,
actual configuration-provider reports and bound/provider/active selection
observations. `candidates()` remains the ordered accounts tuple. The result
contains no credentials and is not a cache or a currentness authority; nested
source-owned configuration/routing values must not be mutated. Consumers make
their own immutable projection. The unwired inventory foundation uses the actual
reports to withhold authority when a provider is indeterminate, including when
retained configuration would otherwise produce the same merged value.

`inventory_store` is an **unwired internal JSON/storage prototype**, not an
enabled inventory cache. Its caller supplies an absolute existing data parent,
source identity, explicit limits and policy. It reuses existing operation
custody/atomic-replace primitives and compares source, creation epoch and
revision before replacement. Read failures remain unknown; snapshots contain
no credentials or credential digests. Pure freshness results label status only
and cannot authorize connection or tenant mutation. No runtime path, production
defaults, timer, provider read, push transport or consumer is activated.

No private broker, persistent authentication daemon, PAT or repository opt-out
is required. A remote terminal without an available browser can still use the
displayed vendor verification URL/code in a browser. Native authorization and
operating-system credential-store behavior require an operator-run ceremony;
headless regression tests mock that boundary and never authorize an account.
No browser or authentication child process is spawned; opening the displayed
verification URL remains a deliberate user action, and cancelling cannot
terminate an operator's browser or leave an authentication child behind.

## Credential relay: fail-fast & auth verification

The relay forwards git-credential requests from a CodeSpace back to the host
over the SSH tunnel, resolving them through the host's Git Credential Manager
(GCM) — which serves **both** GitHub (`github.com`) and Azure DevOps
(`*.visualstudio.com`, `dev.azure.com`) credentials. The relay server is owned
by agent-bridge; agent-codespaces contributes the CodeSpace policy/profile and
sets up the SSH reverse-forward on connect.

For `github.com`, each CodeSpace connection can pass its GitHub account as
`username=<account>` before the request reaches non-interactive GCM. Bound
CodeSpaces use their persisted account; ambient-owned CodeSpaces use the active
`gh` account. That avoids GCM's account picker (`Cannot prompt because user
interactivity has been disabled`) when several GitHub accounts are stored. The
relay profile itself stays account-free, and a missing/ambiguous GitHub
credential warns during launch rather than blocking the session; `doctor`
continues to report it. If GCM still cannot serve the selected account, sign in
to GitHub in GCM for that account; the relay never substitutes a `gh auth token`
for git `get`/`fill`. The CodeSpace helper acknowledges git `store`/`erase`
locally without contacting the relay, so they never change host GCM. The
account is named only when the running relay advertises the
`git-credential-username-cache` capability. An older bridge relay caches git
credentials per host, so against one the helper sends the request without an
account, as it did before.

Provisioning installs the relay-first wrapper only as `~/ado-auth-helper`.
It deliberately leaves `~/azure-auth-helper` to the native Azure tooling so
interactive `az login` keeps working. Reconnecting with a newer
agent-codespaces version repairs older installations that shadowed the Azure
helper, restoring a preserved native helper when one exists and otherwise
removing the stale relay wrapper.

To avoid the failure mode where a missing/expired credential causes a CodeSpace
`git fetch` to hang indefinitely on `git credential fill`:

- **Host GCM runs non-interactively** (`GIT_TERMINAL_PROMPT=0`,
  `GCM_INTERACTIVE=never`), so it errors fast instead of blocking on a prompt.
- **The relay replies `quit=1`** when a git `get`/`fill` request can't be
  resolved, which makes git in the CodeSpace abort immediately
  (`fatal: credential helper ... told us to quit`) rather than dropping to an
  interactive prompt. CodeSpace SSH sessions also export `GIT_TERMINAL_PROMPT=0`.
- **On connect, remote-domain auth is verified up front:** the workspace's
  `git remote -v` domains are probed against the host credential store, and any
  domain lacking local auth is reported as a `[WARN]` so it can be fixed
  (`az login` / GCM sign-in) before work begins, rather than discovered
  mid-fetch.

## Local identifier guard

This is a **public** repo, so internal org/account/repo names and personal
aliases must never land in it. The generated
`.copilot-extensions/agent-codespaces/config.yaml`
scaffold is checked for such leaks by `tests/test_config_init.py`, and the whole
working tree by [`tools/check-no-internal-identifiers.py`](../../tools/check-no-internal-identifiers.py)
(wire it up as a git `pre-push` hook).

A denylist that *named* those identifiers would itself leak them, so it is
**never stored in the repo**. Both guards read it privately from:

1. env `COPILOT_EXTENSIONS_FORBIDDEN_IDS` (comma-separated), and
2. `~/.agent-codespaces/forbidden-identifiers.txt` (one per line; blank lines
   and `#` comments ignored).

With neither configured (a fresh clone / CI) the identifier check is a no-op, so
the guards are safe to ship. Populate one of the sources on your own machine —
e.g.:

```text
# ~/.agent-codespaces/forbidden-identifiers.txt
my-internal-org
my-internal-repo
my-alias
```

Matching is case-insensitive (substring). The host file lives in `$HOME`, outside
any repo, so it is never committed.

## Development

```bash
cd plugins\agent-codespaces
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -e ".[dev]"
python ..\..\tools\run-plugin-tests.py agent-codespaces --guards
```
