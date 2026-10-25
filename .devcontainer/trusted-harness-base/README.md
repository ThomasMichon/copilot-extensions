# Trusted harness base: initial Linux amd64 contract

Generic image construction for [the reviewed image slice](../../efforts/active/linux-role-fleet/trusted-harness-containers.md),
#8352 under #6030. This is not the restricted test-isolation image, a deployer,
publisher, signer, credential producer or preconfigured operator harness.

## Fixed consuming contract

| Property | Initial supported value |
|----------|-------------------------|
| Platform | `linux/amd64`, Debian bookworm / glibc |
| Agent login / exec user | `vscode`, UID 1000, GID 1000 |
| Home | `/home/vscode`, entire directory persisted per logical container |
| Workspace volume | `/workspaces`; consuming harness chooses concrete `/workspaces/<repository>` cwd |
| PID 1 | root `/lib/systemd/systemd`, through `trusted-harness-init` |
| Tools | Python 3.12.11, Node 22.15.0 / npm 10.9.2, Copilot 1.0.98, uv 0.12.6; base Git 2.50.1; exact added OS packages in lock |
| Identity | `/home/vscode/.local/state/trusted-harness/identity`, root-only; canonical machine-id and SSH host key survive recreation |
| Authentication | none at build; runtime authorized keys and existing refreshing user-session auth supplied by their owners |

The downstream thin devcontainer uses `remoteUser: vscode`,
`containerUser: root`, `overrideCommand: false`, `updateRemoteUserUID: false`,
an explicitly selected workspace folder, and distinct per-container persistent
mounts at **both** `/home/vscode` and `/workspaces`. Do not bind an unrelated host
home or share one home between containers. Do not replace the entrypoint with
`sleep infinity`, change the fixed UID, or assume another platform works.

The existing agent-containers trusted systemd launch contract supplies
`SYS_ADMIN`, writable executable tmpfs at `/run` and `/run/lock`, `container=docker`
and a TTY. The entrypoint remounts the container cgroup filesystem writable.
That explicitly elevated trusted profile is required; no blanket `--privileged`,
Docker socket or host cgroup bind is prescribed. Host policy must admit the
profile. Ordinary default Docker confinement may reject it; such a rejection
is unsupported host configuration, not permission to loosen host security.
Native cgroup/systemd operation is not yet proved by offline tests.

The entrypoint requires real, non-symlink persistent mount directories. Existing
roots must belong to UID/GID 1000; only empty root:root mount roots can be
initialized by changing their owner. Foreign owners and nonempty root-owned
mounts are refused without silent adoption or recursive ownership changes.
Existing home subdirectories must also have the expected owner and not be links.

Only an absent identity directory is initialized. A persisted directory must
already contain the complete, regular, root-owned machine-id/private/public key
set with expected permissions; symlinks, partial sets, corrupt IDs, mismatched
key pairs and foreign ownership are refused, never repaired by generating
replacement keys. Valid persisted key bytes and home permissions stay intact.
The canonical ID stays in the private home, but startup projects it into a
**regular root-owned, mode 0444 `/etc/machine-id`** before PID 1. The DBus
machine-id reference points there, not into home. Thus messagebus and the user
manager need not traverse a 0700 home or root-only identity directory.
SSH accepts public keys for `vscode` only, never password or root login.
The runtime owner installs authorized keys under the persistent `.ssh` directory;
no key or token is supplied to a build. SSH listens inside the container;
host exposure and verified transport/lease binding remain deployment/adapter
decisions, not an implicit published port.

Tools and recipe metadata live in `/opt/trusted-harness`; system packages,
systemd and SSH configuration use their standard image-owned system paths.
Linger enables the ordinary `vscode` systemd user manager. Image startup does
**not** install, activate or report readiness for the suite's `services/*`,
plugin runtimes, control repositories, Gateway, relay or auth pump. Those use
their existing supported installers and enrollment owners. An installer which
currently stores runtime code in home still does so; no unsupported code/data
split or source-over-active-slot update is introduced here.

Home-installed launchers may be newer than image tools and remain intact.
Login profile prepends `$HOME/.local/bin`; image tools otherwise precede the
base's inherited Node tooling. Existing home shell customizations can alter
effective tools: record actual runtime versions during integration. Replacement
preserves data/identity, **not live processes**; drain before Komodo replacement.

Selected folders persist with ordinary named volumes at `/home/vscode` and
`/workspaces`. Recreate the container; keep those volume names. Systemd is
disposable image PID 1; `/run` is tmpfs and is not persisted. Initialization
refuses a non-PID-1 launch or an initially non-private root mount. Systemd's
later namespace-local propagation changes do not authorize host-shared mounts.
`/run` must be a mountpoint. This image is for operational tidiness, control
and scale-out, not a host-security product. Do not add mount-pin or LSM work
here.
Image-root APT update services/timers are masked: root package changes
come from the admitted image build/update path, while explicit compatible
home-runtime updates retain their own owners.
Build-time unit enable/mask operations use Debian's absolute systemctl binary
with `--root=/`, not the inherited devcontainer PATH shim that reports a no-op
before systemd is running.

## Approved local build interface

Python 3.11+ and an already-available Docker CLI with Buildx are required.
No dependency installation or registry selection occurs in the host wrapper.
Gateway is the initial builder; future CI uses exactly this interface.

The initial client-metadata contract is Buildx 0.30.1's static single-node
`docker-container` shape, with the digest-pinned `BUILDKIT_IMAGE` in `build.py`.
Driver options must explicitly supply that image, memory, equal memory-swap,
CPU period and quota within the approved ceilings. Only the default
`--allow-insecure-entitlement=network.host` daemon flag (or no flag), empty
platform overrides and no embedded files are accepted. This does not add a
client-side insecure entitlement or allow a host-network build. Other metadata
versions/options fail closed rather than inheriting an unqualified setup.

The builder owner must first admit an isolated, running, single-node
`docker-container` Buildx builder for this job, with verifiable hard memory and
CPU limits and no additional swap. The wrapper checks the selected Buildx node
against its exact Docker container ID and inspects the actual resource limits
and the running container's digest-pinned `Config.Image` before and after
building. Matching declared driver options alone do not qualify that worker.
The default daemon worker is **not** bounded by
these inputs and is refused. The wrapper does not create, bootstrap, stop,
reconfigure or prune builders or existing service containers. Preparing this
admitted worker is an explicit owner prerequisite, not delivered by the image.

Only the explicit `default` Docker context at `unix:///var/run/docker.sock` is
supported initially. Every context lookup, Buildx lookup, container inspection
and build carries `--context default`; the selected node must name either that
context or the exact local Unix endpoint. Foreign contexts, sockets, TCP/SSH
endpoints and missing node endpoint evidence are refused. Effective context,
node binding and actual limits are checked before and after building; an
ambient local container cannot supply bounds for a worker on another daemon.

Every CLI call uses a private job Docker configuration and filtered environment.
Only the admitted builder's bounded metadata is snapshotted; registry auth,
credential helpers, proxy settings and the caller's remaining Docker/Buildx
environment are not inherited. The job configuration is outside the allowlisted
image context. A refusal can leave preflight artifacts, but never launches
Buildx's image build or grants permission to retry an unknown operation.

1. Start from the approved source candidate. Inspect its allowlisted context:

   ```text
   python .devcontainer/trusted-harness-base/build.py --describe
   ```

2. Supply a JSON document conforming to `build-inputs.schema.json`:

   ```json
   {
     "schema_version": 1,
     "source_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
     "context_sha256": "<64 lowercase hexadecimal characters from --describe>",
     "timeout_seconds": 3600,
     "builder": {
       "name": "image-proof",
       "container_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
       "max_memory_bytes": 8589934592,
       "max_cpus": 4
     }
   }
   ```

   The source revision is a caller-supplied provenance label, **not independently
   verified authorization**. The source/publication owner approves that revision
   and context digest. The wrapper refuses a changed context, unknown fields or
   version/platform overrides. Only the fixed context files enter BuildKit;
   no home, repository `.git`, build credential or unrelated source is copied.

3. Build into a **new** caller-selected artifact directory outside this recipe:

   ```text
   python .devcontainer/trusted-harness-base/build.py --inputs <approved-inputs.json> --output <new-artifact-directory>
   ```

   `--docker` selects an existing Docker executable, not a remote shell command.
   No push, load, run, auth, secret, SSH-agent or build-argument flag is emitted.
   The caller retains engine access and resource authorization.

   Limits are admission ceilings, not a request to change the worker. The
   selected worker's `Memory` must be positive and within the ceiling,
   `MemorySwap` must equal `Memory`, and `NanoCpus` or `CpuQuota/CpuPeriod` must
   establish a positive CPU bound within the ceiling.

Outputs are `image.oci.tar`, an approved `context/` snapshot,
`dependencies.lock.json` and `build-receipt.json`. The wrapper checks OCI
manifest/config/layer hashes, records immutable identities and the whole archive
hash, and reports `built-not-runtime-validated`. Failures retain a failure
receipt without a success identity. Existing output directories are never
overwritten. Before launching Buildx, the wrapper flushes an atomic receipt
containing the exact opaque builder-container ID, canonical input approval
digest, context digest and `build-started-outcome-unconfirmed` status.
Failure/cancellation receipts retain those recovery bindings after caller loss.
Receipts contain no private endpoint, host path or environment dump.
No output is published or considered signed. Komodo's owner separately agrees
artifact transfer/import and activation; a local archive is not a remotely
pullable tag. A failed/offline manager never authorizes direct worker mutation.

For native import, verify the archive hash and its manifest/config chain first.
Do not assume an engine's image ID is the config digest: Docker 29's containerd
store can address the manifest digest instead. Verify its imported descriptor,
configuration and rootfs against the receipt/archive before running it or
assigning a convenience alias. A downstream build can consume the verified
archive as an extracted OCI layout through Buildx's digest-qualified
`--build-context name=oci-layout:///path@sha256:<manifest-digest>`, without
inventing a registry or treating a local tag as integrity proof.

Timeout or interruption terminates only the invocation-owned CLI/plugin process
group or Windows Job, with bounded final pipe drain and child wait. On POSIX the
leader remains unreaped until group cleanup; missing exclusive custody refuses
numeric-PID signaling and retains the unconfirmed outcome. This requests BuildKit cancellation; it
does not prove that remote work has quiesced. The receipt explicitly retains
that unknown outcome. The builder owner checks the selected job/worker before
reuse and cleans up only this job's artifact directory and owned builder
resources. No global Docker prune or shared service restart is authorized.

## Dependency evidence and updates

`dependencies.lock.json` carries exact URLs, digests and source timestamps.
The base is the existing repository-adopted immutable Python image, narrowed
to its amd64 manifest. Its configuration/labels identify the July 2025 build.
Node's official release index and checksums identify the April 2025 archive.
Copilot uses the official standalone glibc release archive, **not an npm latest
install**; its SHA matches both release-asset metadata and `SHA256SUMS.txt`.
uv reuses the existing repository pin and verified official release digest.

Added OS packages and their dependency closure resolve only from the fixed
October 1 Debian snapshot. Its Release identifies the July 11 bookworm package
set. The recipe hashes Release and the compressed package index before APT;
APT still verifies Debian signatures and package hashes. Disabling snapshot
expiry checks does not disable signature verification. The inherited package
set remains bound to the immutable base digest; installed OS package versions
are recorded in `/opt/trusted-harness/build/os-packages.txt`.

The wrapper **and image installer** independently enforce the seven-day age
minimum on all selected artifacts and the snapshot. Recorded upstream evidence
is reviewed source, not a new signing/attestation system. Refresh pins and
evidence together through review; unknown timestamps never count as seasoned.
No Python packages are installed by this recipe. Future Python/plugin installs
must retain the existing canonical installer dependency policy rather than
adding unrestricted pip/uv commands. Do not copy runtime source over active slots.

## Validation and remaining integration

Offline, without Docker or package installation:

```text
python -B -m unittest discover -s .devcontainer/trusted-harness-base/tests -v
bash -n .devcontainer/trusted-harness-base/entrypoint.sh
bash -n .devcontainer/trusted-harness-base/profile.sh
```

The tests include real wrapper/child-process invocation, approval/refusal,
deterministic context, age boundaries, pre-launch/failure receipts, same-daemon
worker binding and OCI integrity. Persistence tests exercise actual file/link
operations with simulated UID/GID/mode and key-generation boundaries, including
partial/foreign/linked state refusal and byte preservation.
They do not claim an image build or native service readiness.

The qualified Gateway Docker/Buildx lane must build the published source
candidate and prove actual tool versions, empty-home and existing-home boot,
SSH/tmux, system/user systemd, identity preservation after recreation, and
failed-candidate retention. Existing adapter/auth owners then prove current
instance/lease binding, refreshing auth with either of two signed-in sources,
all-source signout/expiry, and the real Picker/tmux interactive/reconnect plus
headless bridge journey. No initiating client needs to remain connected while
another valid refreshing source exists. Expiry stops auth-dependent work
without deleting state. These gates remain open in the canonical child plan.

In that native lane, specifically boot with an existing 0700 home and root-only
identity directory. Both `vscode` and `messagebus` must independently read the
same projected ID through `/etc/machine-id` and `/var/lib/dbus/machine-id`;
the canonical home ID and key bytes must remain unchanged across reboot.
Prove the real `vscode` user manager and DBus connection work, not just that
the files exist. Also exercise foreign mount ownership, missing key halves,
invalid IDs and symlinked persistent paths: each must refuse startup without
adoption, identity rotation or recursive permission changes.

Native candidate validation built and verified the OCI archive, then confirmed
the pinned tools, public machine-ID reads by both unprivileged users with a
0700 home, and unchanged canonical identity bytes across two disposable
containers using the same mounts. These are tool/file-preservation proofs only.
Docker 29.1.3 with cgroup v2 and its default AppArmor/seccomp confinement refused
both the entrypoint's read/write cgroup remount and a fresh cgroup mount under
the declared capability profile. Systemd/user-DBus service readiness is therefore
**not qualified on that host profile**. The deployment owner must admit and
prove a suitable confined cgroup profile; do not silently add privileged mode,
unconfined security profiles or host cgroup binds to turn this into a pass.

The explicit `fleet_manager.host_profile` bootstrap is the owning candidate
for the fixed `fleet-manager-trusted-systemd-v1` enforcing profile. A consuming
controller must independently admit that host operation, inspect its status on
the selected engine host, and launch with
`--security-opt apparmor=fleet-manager-trusted-systemd-v1` and the private
cgroup namespace. Image startup never installs host policy. The initial policy
grants the existing cgroup remount and pinned systemd namespace/credential
operations at fixed runtime paths, not arbitrary filesystems or bind mappings;
parser-only validation is not native service qualification. See the
[host bootstrap contract](../../fleet-manager/docs/host-profiles.md).
