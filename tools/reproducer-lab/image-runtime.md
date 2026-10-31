# Image-built runtime input

Back to the [reproducer tool guide](README.md) and
[reviewed implementation plan](../../efforts/active/linux-role-fleet/persistent-isolated-reproducer.md).

This is source for a separately approved image build and first guest runtime
preparation, not a provisioning command or a qualified image. No build, image
pull, dependency installation, container, volume or service is started by
importing the tool or running its synthetic controls.

## Recipe and input

`Dockerfile` takes one complete `SourceReader` artifact as its build context:
`source/` and `source.json`. Its adjacent `Dockerfile.dockerignore` admits only
those paths and excludes the shallow checkout's Git metadata. Do not build
from a live checkout, add private context files or mount host configuration,
package credentials, Docker sockets, SSH agents or caches into the build.

The caller must separately select `PYTHON_IMAGE` and `UV_IMAGE` as full
digest-qualified public references. There are no default floating tags.
The initial recipe expects a Debian-based official Python image with Python
3.12 or later and the ordinary UV binary image. It installs Git in the build
stage, then uses the three plugins' existing UV project/dependency declarations.
Copied public source and metadata receive read/traverse permission independently
of the staging caller's umask, preserving executable versus non-executable files.
The same permission normalization makes the built seed readable to the guest.
The image-local `/usr/bin/python3` link explicitly replaces an existing file or
link without following a link to a directory; no host interpreter is modified.
Source packages are non-editable; external Python dependencies retain the
seven-day age restriction, no managed Python download and copy link mode.
Dependency resolution/build is not an offline phase and needs separate
authorization. No authenticated feed or host credential is supported here.

The venvs are built at their eventual fixed paths under
`/lab/state/runtime/{worktrees,bridge,dispatch}` and retained in the image at
`/opt/lab/runtime-seed`. This preserves console-script shebang paths when the
seed is later copied into the mounted state volume. There is no fake runtime
selector, install-complete marker or active installation receipt.
The immutable public source remains at `/opt/lab/source`.

A final scratch stage copies the built filesystem but not inherited image
metadata. It declares only the three baseline environment entries, UID/GID
`1000:1000`, the fixed workspace and a keepalive entrypoint. It declares no
volume, exposed port, healthcheck, service manager or automatic service launch.
Its keepalive is explicitly counted as a container-owned test venue in the
existing lifecycle census, matching the clean-room venue category; this is not
resident-service startup admission or permission to create the container.
The actual created container must still pass every effective boundary check;
the recipe is not evidence that a selected image or build succeeded.

The future build owner must bound the build, storage and output, retain the
actual image identity and verify executing interpreter/package/source origins.
Client exit or a reported build timeout does not prove a daemon-side build
settled. This directory supplies no Docker build/lifecycle driver, dependency
lock receipt, source-to-image attestation or performance guarantee.

## Explicit first guest preparation

`guest_runtime.prepare_runtime(custody)` requires the existing guest
`GuestOperation` and separately qualified resources, selected image/source,
fresh host storage monitoring and no competing writers. Run the entire
operation on one synchronous execution owner, outside an event loop.
The fixed seed must be a non-redirected root-owned directory on the read-only
image filesystem, with no group/other write permission.
The state root must contain only the existing fixed operation lock before
preparation. Any retained service, registrar or unknown state refuses; a missing
runtime is not permission to repair an existing installation. This limited
initial-state check is not proof of history retention or descendant settlement
after external state loss.

The function exclusively creates `/lab/state/runtime`, requiring the same
state device, `1000:1000` and mode `0700`. An existing directory, file, link,
partial or completed attempt refuses; no overwriting or automatic retry.
One fixed `/usr/bin/cp` call through the existing caller-owned process runner
copies the seed recursively without dereferencing links, preserves timestamps
and executable bits, and does not change the existing private root's mode.
The child has a 30-second execution budget and 16 KiB stream caps with a minimal
synthetic environment. Seed/destination identity and operation custody are
rechecked. Errors propagate and partial files remain; there is no success-shaped
fallback, cleanup or launch. Copy success is not a storage sync/power-loss proof.

`guest_cli.sh` dispatches only the three fixed persistent-runtime console
scripts; the dispatcher itself does not install or select a fallback interpreter.
The invoked commands retain their normal behavior, including any lazy-start or
backend-launch behavior. Their fixture configuration, explicit autostart controls
and authorization remain necessary; adding a launcher is not permission to call it.
The first configuration/harness source below supplies owner inputs, not an
actual installed service graph. Service/peer lifecycle and initial-task caller
wiring still need implementation and qualification before case execution.

## First synthetic harness configuration

`guest_fixture.prepare_fixture(custody)` explicitly prepares only the first
synthetic hosting repository and supported Worktrees/Bridge/Dispatch inputs.
It requires the same qualified resource/source/runtime and fresh monitoring
prerequisites as runtime preparation. It does not initialize databases, start
services, launch a peer, create a task or install/register a plugin.
Importing the module has no effect.

The state must contain only the original operation lock and prepared runtime;
the workspace must be empty. Private, non-redirected guest directories are
required. An exclusively created state-backed HOME retains any partial attempt.
Existing state/workspace refuses without overwriting, replay, repair or cleanup.
Configuration files use exclusive creation with mode `0600`; successful writes
are not a power-loss or mounted-volume durability qualification.

`environment()` supplies a fresh minimal environment, not an overlay on the
host or caller environment. HOME, Copilot, XDG, coupled Worktrees registries,
Bridge configuration/providers and Dispatch state all select experiment-local
paths. It uses the immutable Worktrees source payload for ordinary helper assets.
The installed packages and three persistent runtime commands remain those from
the image recipe. Bridge/Dispatch select the ordinary legacy roots beneath
that synthetic HOME, with their database paths explicitly set under state.
This avoids requesting an installation-cell context from an unrelated custom
install root; no installation selector or completion receipt is invented.
Existing status-monitor, Dispatch lazy-start/fallback/GC and Bridge self-retire
switches are explicit. **Bridge has no general lazy-start kill switch here**:
this module neither adds one nor claims startup/fault qualification. Ordinary
commands retain their normal behavior and need separately authorized lifecycle.

The subject is a new hosting-permitted synthetic repository, not the source
checkout. Three fixed bounded local Git calls initialize and commit only its
generic configuration, without hooks, global/system Git config, inherited templates,
remotes, credentials or network. A failure propagates its bounded literal
diagnostic and leaves the attempt intact.
The explicit agent registry selects the existing test-only stdio ACP peer;
an exclusively created private executable wrapper places its Python script
before the ACP flags that Bridge appends. Python itself is not registered as
the Copilot executable, since those flags would be interpreted by Python.
Its transcript contains only synthetic data. This is not a native Copilot or
model and does not complete a Dispatch task. Credential relay listening is
disabled and the private provider registry starts empty; the ordinary Bridge
startup still invokes its HTTP-source registry machinery.

Actual service startup, guest runtime-origin qualification, bounded peer ownership,
the initial-task caller and original-owner recovery/fault lifecycle remain open.
Preparation is not permission to execute commands or new work after monitor loss.

## Explicit runtime-origin observation

`guest_origins.inspect_runtimes(custody)` uses the existing operation and finite
process owners to observe the three prepared interpreters. It requires separately
authorized, qualified resources/image/source, fresh host monitoring and no
competing runtime writers. Each fixed `bin/python3 -I -B` invocation has ten
seconds and 16 KiB stream caps. The isolated flag ignores ambient Python paths;
bytecode writing is disabled. A failure propagates its bounded literal cause,
without retries, partial-success results, repairs or persisted readiness markers.

The stdlib-only `runtime_origin_probe.py` checks the executing virtualenv prefix
and executable, distribution location/version and noneditable local-project
metadata. It compares all main-package `.py` files with the selected source,
refusing missing, extra, changed or redirected Python paths. Cached `.pyc`/`.pyo`
files, including those beneath `__pycache__`, refuse before importing; `-B`
prevents writes, not stale-cache reads. Previously imported main-package modules
also refuse. No cache is removed or repaired, even if it might be consistent.
Each inventory has
4,096 entries, 1 MiB per file and 64 MiB total ceilings. It then imports only the
verified top-level package and checks its actual origin, never a service/CLI
entry point. The returned observations name the interpreter, package, version,
source and compared Python-file/byte counts; they are not installation receipts.

This checks main-package Python bytes, not dependencies, native extensions,
package data, interpreter authenticity, source-to-image provenance or build
closure. It is not an atomic snapshot against a competing writer and does not
qualify commands, services, native START or readiness. The complete image/guest
still needs actual qualification; source/synthetic results cannot substitute.

## Controls

`tools/test_reproducer_lab_runtime.py` checks the recipe/dispatcher contract and
synthetic preparation, retained partial attempts, lost custody, changed source/
destination, root/private posture and event-loop refusal. It does not build.
The explicitly selected `runtime_local_tests.py` uses real local `cp`, the
existing finite process owner and an actual operation lock on synthetic
temporary trees. It checks executable bits, hidden files, non-followed links,
private root mode and refusal of a second preparation. It is outside default
test discovery. Separate actual local command cases cover restrictive source
permissions and absent/file/symlink Python destinations, preserving prior link
targets. Neither lane qualifies a real venv, image, mount or service.

`tools/test_reproducer_lab_fixture.py` supplies synthetic preparation/failure,
minimal-environment, configuration and no-launch controls. Explicitly selected
`fixture_owner_tests.py` exercises real local Git, the peer wrapper's argument
parser and actual owner config loaders on temporary synthetic paths; its guest
ownership check is substituted. The parser control uses `--help` and never
enters the peer loop or writes a transcript. It first obtains the exact argv
from the real Bridge `resolve_local_launch()` with `explicit_cwd=True`,
including ordinary hosting admission. This lane needs the owning projects'
declared Python dependencies in its isolated test environment.
It starts no service or backend and is outside default test discovery. Neither
lane qualifies the actual image, guest mounts, installed runtimes or ordinary
Dispatch-to-Bridge execution.

`tools/test_reproducer_lab_origins.py` checks probe rejection and finite caller
behavior on synthetic paths/metadata. Explicitly selected `origin_local_tests.py`
runs a real isolated Python child in a temporary empty stdlib venv with only a
synthetic package and metadata. No actual plugin, build or dependency is installed.
It checks origin/version/byte/editable rejection and ambient-path exclusion,
plus rejection of actual timestamp-valid stale initializer/helper bytecode.
It does not qualify the actual guest recipe, dependency closure or service lifecycle.
