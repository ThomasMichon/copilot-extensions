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
Configuration, synthetic
hosting workspace/registrars, actual services, fake peers and initial-task
wiring still need implementation and qualification before case execution.

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
