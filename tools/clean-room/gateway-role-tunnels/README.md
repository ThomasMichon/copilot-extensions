# Isolated Gateway role-tunnel witness

This explicit host-driven Tier-P lane composes ordinary unprivileged containers
on the existing Docker engine. It follows the
[clean-room report and jam vocabulary](../ARCHITECTURE.md), not the privileged
Komodo nested-Docker path. There is no scheduler, CI job, Docker socket mount,
host port, cloud credential, provider account, Copilot or live adopter change.

## Prerequisites and invocation

Use Linux/WSL, Python 3.10+, Git, and an existing ordinary Linux Docker engine
reporting native x86_64/amd64 or aarch64/arm64. Select the architecture explicitly
with `--architecture amd64` or `--architecture arm64`; omission retains the
existing amd64 default. Engine access must already work through its default
local endpoint. The runner deliberately supplies an empty private Docker
configuration and does not inherit `DOCKER_HOST`, credential variables or host
configuration. Remote contexts, implicit dependencies, host Python crypto
packages, QEMU and automatic Buildx installation are not supported.

The owner must supply an **existing absolute private directory**, owned by the
invoking non-root user with no group/other access, outside Git repositories
and managed session state, with no symlink component. Do not use a checkout,
session folder, drive root or home directory as the results root.

After the parent has reviewed, staged and committed all candidate files, invoke
the runner from that source:

```text
python tools/clean-room/gateway-role-tunnels/run.py --source-commit <full-Git-SHA> --results-root <existing-absolute-private-directory>
```

For an explicitly admitted native ARM64 venue, add `--architecture arm64`.
This does not select a remote machine or grant execution permission. Run the
same committed source on that venue's own native local engine. Unknown choices
and cross-architecture engines refuse; the runner never installs emulation.
The engine's architecture aliases may be `x86_64`/`amd64` or `aarch64`/`arm64`,
but CLI selections and inspected image architectures are canonical `amd64` or
`arm64`.

`--source-ref <available-local-ref>` is an alternative; it resolves once to an
exact full commit. `--repository <absolute-checkout>` selects a local Git object
database, not a live-source overlay. There is no fetch, clone or worktree
creation. The default build timeout is 900 seconds (`--build-timeout`, bounded
30..1800); the subsequent witness has a 300-second global budget. Cleanup has
its own finite per-command deadlines. Every Git/Docker child is synchronous,
headless on the supported Linux host, contained by its caller, and timed out.

**Uncommitted candidates cannot qualify:** the selected commit must contain
the Dockerfile, fixed package/resource closure and every witness module.
The executing runner/helper bytes must match that same commit. Missing,
linked, unknown package source, or unavailable commits fail closed. This
prevents accidentally exercising the current uncommitted runtime or substituting
a host fixture. The allowlist intentionally requires review when the package
source closure changes.

## Composition and custody

One unique timestamp/random private directory beneath the supplied root contains
the allowlisted Git-only build context, separate read-only proof scripts,
offline-generated synthetic issuer/TLS/proof keys and tokens, source manifest,
creation receipts and logs. Fixture preparation runs in the built image with
network `none`; no host packages are installed. Signing occurs offline, with no
issuer daemon. Production Gateway containers never receive the synthetic issuer
private key, service keys or caller tokens.

One Gateway, two independently isolated services and one bounded observer share
one fresh test-owned **internal** bridge network. Only the Gateway has the DNS
alias `gateway`. Backend HTTP listeners bind service-container loopback; only
verified direct TLS and authenticated real SDK channels cross containers.
Fixtures and credentials are mounted read-only; only offline preparation can
write its explicit fixture output. Atomic host replacement of the selected
non-secret policy file exercises the supported reload seam.

Each container is non-root, capped at 256 MiB/1 CPU/64 PIDs, has all capabilities
dropped and `no-new-privileges`, and uses a read-only image filesystem plus
32 MiB `/tmp` and 16 MiB `/state` tmpfs. There is no host network/PID namespace,
device, socket or published port. The production image uses the installed
`fleet-manager ... --service-policy ... --tls-config ...` CLI; services import
the installed SDK, not Gateway source via `PYTHONPATH`.

The witness build selects the invoking user's nonzero UID/GID and prepares
that account's passwd home and state directory. Fixture services run under
separate lease supervisors, not their own renewal loops. Each supervisor
observes a private FIFO whose only writer belongs to the host proof runner;
runner loss ends renewal and stops the exact service child. The service also
retains the unattended lease and hard lifetime bound. This does not require a
shared PID namespace or a mounted Docker socket.

Container IDs come only from per-creation cidfiles; the network ID comes only
from its creation response. Receipts are written before starting containers.
`finally` captures bounded owned-container logs, stops/removes only those exact
IDs, and removes only that network ID. No force-remove, image removal,
enumeration or prune is used. If creation has an uncertain outcome without an
ID, it reports incomplete cleanup rather than guessing an ownership target.
Log-write failures become classified failures without stopping subsequent
teardown attempts. SIGTERM and SIGINT enter failure reporting and cleanup;
both signals are ignored during teardown, and their previous handlers are
restored afterward. SIGKILL or host loss cannot run cleanup: retain the exact-ID receipts
for explicit owner recovery rather than enumerating or pruning other resources.
The image is retained and its immutable ID reported for explicit owner reuse.
Results, logs, policies and synthetic credential references remain private in
the run directory for investigation; normal owner retention policy applies.

## Evidence and limits

`cr-report.json` preserves `scenario`, `copilot_version` (`not-used`),
`ran_until_phase`, `passed`, `failed`, `env`, `jams` and `results`, with
stage PASS/FAIL and classified failure evidence. `receipt.json` records source
SHA, canonical-source manifest hash, image ID/architecture, candidate policy
revision/hash, exact resource IDs, Gateway PID/start samples and cleanup status.
The receipt records `requested_architecture`, observed engine facts and
`architecture.selected`/`image`/`process`/`execution`. Image build/inspection
leaves process and execution `UNRUN`. Only successful installed-wheel fixture
execution with matching Linux machine architecture sets `native-amd64` or
`native-arm64`; a build-only stage PASS is not native execution evidence.
Engine or image mismatch refuses before fixture/service composition, and
process mismatch refuses before creating the service network.
`source-manifest.json` hashes every admitted blob; `cr-logs/` retains bounded
command evidence. No ambient environment dump or credential values are collected.
Missing Docker/source is a reported failure, not a skipped successful proof.

The actual assembled run must observe both expected-but-absent services,
API/HTML routes, scoped caller/connector-principal/slot hijack denial, invalid
candidate refusal with old routes intact, higher role/namespace publication,
caller revocation, connector replacement/predecessor retirement, and a service
container restart establishing a new outbound channel. Gateway container ID,
PID 1 birth ticks, boot ID, engine PID and start timestamp must remain identical
across those updates. Candidate revisions are submitted-source evidence;
the witness does not invent an unauthenticated runtime status API.

The SSE backend writes its first chunk and then waits for an explicit request
to release completion. The observer must receive that chunk, read unfinished
backend state, and only then release/read the final chunk. A buffered whole
response cannot satisfy this handshake. The fixture stream has a finite
12-second release deadline.

The focused `tools/tests/test_gateway_role_tunnels.py` and
`tools/tests/test_gateway_role_architecture.py` suites cover mechanical argv,
ownership, source/results-root, architecture/refusal, report and cleanup boundaries only.
Run it through the repository's bounded tools runner; mocked Docker tests are
**not** a substitute for executing this lane.

This lane does not independently qualify every late-frame, slow-consumer,
cancellation or uncertain-effect case from the in-process runtime portfolio.
It does not prove a live identity provider, protected proxy/Cloudflare origin,
production TLS/key custody, restart-safe accepted revision authority, managed
install/update/rollback or live adoption. Every architecture needs its own
actual native run; success on one never qualifies another. Native architecture
and live vendor ingress evidence remain separately recorded in the readiness
document; the separate generic proxy tests do not
change this lane's boundary. Container proof is a prerequisite, never deployment
consent. See the [base image](../../../fleet-manager/containers/gateway/README.md),
[service profile](../../../fleet-manager/docs/gateway-service-profile.md) and
[adoption-readiness record](../../../fleet-manager/docs/gateway-adoption-readiness.md).
