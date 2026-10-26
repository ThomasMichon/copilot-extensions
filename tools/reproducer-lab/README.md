# Reproducer lab: boundary observations and monitored storage

This directory implements the first boundary and storage observations from the
[reviewed persistent reproducer plan](../../efforts/active/linux-role-fleet/persistent-isolated-reproducer.md).
There is no provisioning CLI or qualified persistent lab. The opt-in reader can
inspect explicitly selected existing resources and run one fixed, non-root
measurement in an already running guest. It never creates, starts, stops or
deletes resources. Source publication does not authorize those observations
against an existing installation.

`boundary.py` provides:

- `parse_declaration(value)`: strict version-1 synthetic configuration, full
  image/Git object identities, separate experiment resource names and positive
  integer budgets. Malformed, unknown and out-of-scope inputs raise
  `DeclarationError` without echoing their payloads.
- `inspect_static_boundary(experiment, observed)`: pure checks on a Docker
  inspect-shaped observation. It identifies known unsafe/missing image,
  identity, namespace, privilege, exposure, CPU/memory/PID, persistent-mount
  and guest-environment fields without invoking Docker or reading host state.

The initial proposed image contract uses non-root `1000:1000`, the fixed HOME
`/home/lab`, PATH `/usr/local/bin:/usr/bin:/bin` and `PYTHONUNBUFFERED=1`.
It has four separately named volume destinations for home, state, workspace and
evidence. Config/runtime/registrar roots will live under the owned state path;
their actual service selection is not implemented or qualified here.
Supported declaration ceilings are 8 GiB memory (equal swap limit), four CPUs,
256 PIDs, 4 GiB persistent storage, 512 MiB `/tmp`, 64 MiB `/run` and 64 MiB
native output. These are tool-side input ceilings, **not enforced storage**.
Static inspection also requires exactly two `HostConfig.Tmpfs` destinations:
`/tmp` and `/run`, each with `rw,nosuid,nodev,uid=1000,gid=1000,mode=0700`
and exactly one `size=<declared bytes>` option. Missing, duplicate, conflicting,
extra or unbounded options refuse. This follows the existing restricted
provider's explicit tmpfs-flag/budget approach without changing its policy.
It is Docker configuration evidence, not a guest mount-table or runtime proof.

Every static result retains explicit unresolved requirements, even with zero
violations: fresh usage/backing-free-space measurements, actual internal
network membership/egress, exclusive resource/driver custody, guest source and
volatile/runtime configuration, and provisioning/strict pre-effect support.
There is deliberately no `ready` or admission result. A dictionary claiming
verified quota cannot supply a backend implementation or clear these gaps.
Synthetic inputs, expected names and image identities are not physical custody,
running-source or persistence proof.

## Finite host reader

`reader.DockerReader` takes an absolute caller-selected Docker executable and a
full physical container ID, never a guessed name or automatic replacement.
`inspect(experiment, container_id)` applies the static checks and reads only the
four declared volumes. Each must have the ordinary `local` driver, local scope
and no driver options. Full-volume mount declarations are required: subpath
mounts refuse, and each effective mount source must match its inspected volume
root so that a subtree cannot hide usage elsewhere in the same volume.
The returned `storage.Resources` binds the container ID
and volume names, creation timestamps and mountpoints. These observed Docker
identifiers are not exclusive-custody proof; retain and compare them on resume.
Docker exposes no separate immutable local-volume UUID.

`measure(experiment, retained_resources)` checks those same identities before
and after a fixed Python probe through `docker exec --user 1000:1000`.
The synthetic image must supply `/usr/bin/python3`. It does not start a stopped
container or retry an unknown result. The reader uses the existing
`agent-procutil` caller-owned runner, at most 10 seconds per command within a
30-second observation deadline, with 256 KiB stdout and 16 KiB stderr caps.
Settlement, JSON/metadata processing and the final sample timestamp must remain
inside that deadline; merely starting the last command before expiry is insufficient.
Failures raise `ReadError` with a bounded reason rather than echoing guest or
Docker diagnostic payloads. The caller's selected Docker context is used;
this tool does not discover endpoints, log in or borrow credentials.

`guest_usage.py` traverses the four fixed roots without following symlinks,
deduplicates hardlinks per volume and counts the larger of logical and allocated
bytes (including directories and hidden files). It refuses missing/unreadable
paths, changed directory identities, nested filesystems, excessive depth,
more than 256 pending directories or more than 100,000 entries.
A guest-local five-second timer bounds the probe even
if the Docker client disconnects; host-client cleanup is not proof that a
daemon-side exec settled. Backing free space uses guest-visible `f_bavail`;
the minimum across the four roots is conservative for shared filesystems.
Evidence-root usage is the output threshold's observation, not an assertion
that native streams have been wired to that root.

## Storage-only monitoring

`storage.StorageMonitor` requires an explicit `MonitoringPolicy`: observation
cadence (1-60 seconds), active-case lifetime (at most one hour) and backing free
reserve (positive, at most 4 GiB). Its monotonic clock belongs to one host
process. Construct a new unmeasured monitor on resume; never persist/reuse its
monotonic timestamps across processes. The future case owner must independently
authorize that lifetime, cadence, resource selection and actions.

`reader.refresh(monitor)` invalidates the previous observation before reading.
`monitor.refusals(now=...)` refuses NEW work when closed, missing, stale,
expired, at/above either usage threshold, or at/below the backing reserve.
Freshness includes scan duration and expires exactly at the cadence boundary.
The active caller must refresh on that bounded cadence; no scheduler, daemon or
automatic stream of new cases is supplied. Closing the monitor, losing it or
failing a refresh never kills accepted work, fabricates a successful drain or
deletes evidence. No empty storage-refusal tuple is overall lab admission.

Ordinary volumes have **monitored thresholds, not hard quotas**. Concurrent
writes, open-unlinked files, delayed observations and accepted work can overshoot
them. A traversal is not an atomic filesystem snapshot. Free-space observation
can also change immediately after a sample. Missing/partial/error outcomes
refuse further work; no automatic deletion, truncation, growth or replacement
is supplied. CPU/memory/PID hard limits are unchanged.

## Finite network inventory

`reader.network_inventory(experiment, subject_id, peers=...)` inspects the full
selected subject ID and at most three explicitly selected, distinct full peer
IDs. It does not discover peers or accept names as physical identity.
Each container must be running and attached only to the experiment's exact
physical internal, local bridge network. The network's endpoint inventory must
contain exactly those containers; endpoint IDs must agree in both directions.
Custom bridge options, attachable/ingress networks, shared network namespaces,
external attachments and missing observations refuse.

The reader repeats the container/network inspections and compares the network
ID, creation timestamp and each endpoint identity within its existing
30-second deadline (four calls, at most 10 seconds each). Pass `expected` on
resume to compare a retained observation; a missing or replaced selection
refuses, without automatic creation or repair. All calls use the same bounded
process owner as storage observations.

These are point-in-time, non-atomic observations, not exclusive-resource
custody or effective-egress proof. An attachment can change between/after
observations, and a peer ID does not establish its synthetic image, environment,
service or privilege posture. Actual peer qualification, exclusive driver and
resource ownership, and a controlled real egress-refusal experiment remain
separate prerequisites. This API never clears the static unresolved list or
admits a case.

Effective egress, exclusive driver/resource custody, guest/runtime/source
qualification, native output ownership and strict pre-effect support remain
unavailable. Actual provisioning and case execution remain blocked until those
requirements and separate approval are satisfied.
Restricted-provider policy and ephemeral tests remain unchanged. Real resource
creation, guest lifecycle/fault actions and deletion need separate authorization
through the normal owner. No production credentials, state or network are used.

## Immutable local source input

`source.SourceReader(absolute_git_path).materialize(repository, commit,
private_results_root, fresh_name)` prepares one local build input from the
experiment's full `source_sha`. It does not build an image, transfer into a
guest, install dependencies or run a service. The results root must already
exist, be private and caller-owned, and be outside repositories and managed
session state. This initial source path supports POSIX hosts only.
The fresh artifact directory is exclusive; an existing or incomplete attempt
is never overwritten, deleted, resumed or retried automatically.

Git reads only the selected immutable objects, not the index or dirty/untracked
working-tree bytes. Preflight refuses symlinks, submodules, malformed paths,
case-folded or Unicode-normalization path aliases (including directory prefixes
and reserved Git metadata components), even on case-sensitive hosts,
more than 16,000 files, any blob over 8 MiB or logical source content over
256 MiB. Canonical caseless comparison normalizes before and after folding.
The listing is at most 4 MiB; the prefix inventory admits at most 32,000 entries
and 8 MiB of combined original/key UTF-8 bytes, checked before retaining a new
entry. Deep zero-byte trees cannot grow that inventory without bound; these
are input/retention ceilings, not a hard process-memory quota.
A shallow local-file fetch produces one detached commit and its tree
without copying the source's config, hooks, refs, reflogs, index or object store.
No remote URL is configured and no fetch-head source-path record is written.
The subprocess environment is a fixed synthetic baseline: ambient credentials,
Git overrides, proxies and service-discovery settings are not inherited.
Global/system Git config, templates, hooks, lazy fetches and automatic
maintenance are excluded. `GIT_ATTR_NOSYSTEM=1` and an explicit null
`core.attributesFile` also exclude host system/user attribute policy;
repository-tracked attributes remain part of the selected source.
Only the explicit local-file transport is permitted. Destination initialization
explicitly matches the verified commit's SHA-1 or SHA-256 object format.

Every command uses `process.py`'s existing caller-owned runner with at most
15 seconds, 4 MiB stdout and 16 KiB stderr within one 60-second preparation
deadline. Final commit/tree observations and metadata writing are inside that
deadline. `source.json` contains selected object identities and input counts,
not completion, running-source, custody or lab-admission authority. Any raised
error leaves the attempt unavailable even if partial files or metadata exist.
The tool does not interpret a lock/PID, old directory or artifact as permission
to continue an interrupted guest effect.

The input-size ceilings are **not a hard disk quota** on Git metadata or
materialization. Partial writes and filesystem overhead remain possible and
are retained on failure. Source staging, image build and guest copy still need
their own bounded storage/approval and installed-source qualification; an
observed Git commit is not a proof of an executing guest's code.

## Tests

`tools/test_reproducer_lab_boundary.py` is an in-process, synthetic control set:
declaration/budget rejection, static unsafe or incomplete Docker observations,
mount/environment isolation and retained unavailable prerequisites.
`tools/test_reproducer_lab_monitoring.py` covers mocked finite-reader seams,
physical-resource mismatch, failure invalidation, exact threshold/cadence/
lifetime edges, monitor loss/resume and local synthetic traversal fixtures.
`tools/test_reproducer_lab_network.py` covers exact subject/peer membership,
foreign attachments, physical endpoint replacement, retained selections,
malformed/missing observations and final-settlement deadline refusal.
`tools/test_reproducer_lab_source.py` covers pure/synthetic immutable-source
inputs, exact file/byte ceilings, environment/command selection, retained
partial failures and the last command's deadline. It invokes no Git.
Run it with the repository's selected Python/pytest environment through the
caller's bounded test supervisor:

```text
<bounded-test-supervisor> -- <python> -m pytest -q tools/test_reproducer_lab_boundary.py tools/test_reproducer_lab_monitoring.py tools/test_reproducer_lab_network.py tools/test_reproducer_lab_source.py
```

These tests do not run Docker, open a socket, install dependencies, exercise native
service lifetimes or join the unconditional promotion matrix. See
[`TESTING.md`](../../TESTING.md) for the separate real integration portfolio.

The explicit local-Git lane is separate from those synthetic selectors:

```text
<bounded-test-supervisor> -- <python> -m pytest -q tools/reproducer-lab/source_native_tests.py
```

Its fresh SHA-1 and SHA-256 synthetic repositories prove the selected committed bytes survive
dirty/skip-worktree changes, excludes untracked content, source configuration,
hooks and older history, and leaves the source checkout untouched. It does not
use Docker, a remote forge, production credentials or service processes.
The filename is intentionally outside default pytest discovery; no required
PR/promotion matrix is expanded.
