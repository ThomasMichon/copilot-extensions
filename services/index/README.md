# agent-index-service

For independently owned fresh state, the explicit host option
`store: {layout: source-aware-v1}` enables the core's
[cold source-aware layout](../../plugins/agent-index/docs/source-aware-store.md).
This is not legacy store adoption, data transfer or deployment/migration proof.

Optional source-authorized fleet hosting is documented in
[the core backend contract](../../plugins/agent-index/docs/fleet-backend.md).
An explicit `fleet.policy` host reference and `[fleet]` distribution extra
enable only the two signed-grant TLS routes; ordinary native hosting is
unchanged. Gateway CLI routing and live fleet enrollment remain separate gates.
Worker-local credential consumption is selected explicitly as described below.

## Generic offline Linux image context

`agent-index-service container prepare` creates a runnable, explicit Docker build
context. It does not build, pull, publish, enroll, activate or deploy anything:

```console
agent-index-service container prepare --descriptor <absolute-release.json> --expected-source-commit <trusted-40-character-revision> --third-party-lock <absolute-platform-lock.txt> --third-party-wheels <absolute-wheel-directory> --output <absent-absolute-context-directory> --base-image <approved-python-reference@sha256:digest> --python-version <exact-3.x.y> --platform <linux/amd64-or-linux/arm64> --glibc-version <exact-2.x>
```

The caller supplies the approved Python base **by immutable digest**, its exact
CPython/glibc versions and platform, the independently trusted release revision,
and a complete platform-specific third-party wheel closure acquired through the
operator's governed feed. No registry, package version or image is selected by
the preparer. The base must already provide `python`, stdlib venv and pip.
It must also provide the operating-system tools required by the selected source
connectors (for example Git for an actual Git-corpus ingestion proof). This
adapter does not install OS tools or silently replace a deficient approved base.
The build checks the asserted target against the base before installing wheels.
Only glibc Linux CPython images are admitted by this initial adapter.
Library callers use `container.ContainerTarget(python_version, platform,
glibc_version)` and `container.prepare_container_context(descriptor_path, *,
expected_source_commit, third_party_lock, third_party_wheels, output, base_image,
target, uid=10001, gid=10001)`. The returned schema-1
`agent-index-service.container-context` result names the context, selected
revision/service version, contract digest, build argv and `prepared` versus
`already_prepared` status.

The first-party descriptor must name exactly `agent-index-service`, `agent-index`,
`agent-zdd`, `agent-procutil` and `agent-dropin-registry`. The native third-party
lock uses the same exact-pin/SHA-256 contract as candidate staging. Preparation
verifies wheel RECORDs, hashes, metadata, Python/platform tags and the complete
dependency closure, including `agent-index-service[native]` and core
`agent-index[store,server]`. Missing dependencies, direct URLs, incompatible
extras/versions/tags, duplicate artifacts and unpinned bases are errors.
Native third-party members have a bounded 256 MiB allowance for compiled store
libraries; first-party release members retain their 64 MiB limit. Both profiles
retain the 256 MiB total decompression, 512 MiB archive, 10,000-entry and 1 MiB
metadata/RECORD limits. Native admission does not relax hashes or RECORD checks.
Third-party lock environment markers are intentionally not supported: provide a
resolved lock for the explicit target. Third-party source builds are not allowed.
Kernel-dependent requirement markers need separate deployment admission and are
rejected rather than evaluated against the preparation host or guessed values.
The service image rejects the embedding-engine/torch/sentence-transformers
packages; a real warm engine remains an independently provisioned companion.

The context is an immutable snapshot of selected wheels, normalized release/
install contracts, a stdlib pre-install byte-verification adapter, a native
probe, Dockerfile and allowlist `.dockerignore`. It never uses `COPY . .`,
copies sibling configs/credentials or imports code from wheels during
preparation. Original lock comments and host paths are not embedded. Repeating
preparation with identical inputs verifies and preserves the context; conflicting
or unrelated existing output is refused. Keep the prepared context immutable
between preparation and build. Hash integrity does not establish source
authenticity or replace the trusted controller's release-acquisition decision.

Run the returned `build_argv` through the installed trusted SDK, not bare
`docker build` against a mutable directory:

```console
agent-index-service container build --context <prepared-directory> --expected-contract-sha256 <independently-pinned-context-digest> --docker <absolute-docker-executable> --docker-context <selected-local-context>
```

The digest must come from the trusted preparation/controller result, never be
recomputed from possibly modified input immediately before building. It binds
**all** prepared files, including Dockerfile and `.dockerignore`. The external
builder validates and captures exact bytes in a private bounded tar before any
Docker subprocess starts; Docker consumes that snapshot on stdin, never the
original directory. The recipe takes only the non-secret `CONTEXT_SHA256` build
argument, avoiding a circular contract/recipe hash. Limits are 1 MiB manifest,
4,096 files, 512 MiB per file and 1 GiB aggregate tar. Same-content preparation
replay remains read-only; partial preparation cleanup is ownership-fenced and
reports the original failure plus cleanup uncertainty rather than deleting an
unproven output.

Library callers may use `verified_context_snapshot` or
`build_container_context(context, *, expected_contract_sha256, docker=None,
docker_context="default", docker_config=None, timeout_seconds=600.0)`.
An explicit Docker executable is preferred; otherwise the SDK resolves it once.
An explicitly selected Docker client config directory is never copied into the
tar or image. With no config override the client uses a fresh empty config
directory and the named default daemon context, not ambient host credentials.
The cached approved base must match the Linux target architecture. The SDK
does not accept arbitrary Docker flags, downstream URLs or headers.

The generated multi-stage build also verifies the context contract/actual bytes
before installation, installs all locked wheels with `--no-index`,
`--require-hashes`, `--only-binary` and `--no-deps`, and probes native imports,
versions, extras/dependency closure and the explicit-source compatibility seam.
All build RUN networking is disabled. There are no credential-bearing build
arguments, online fallback, package-manager config mounts or engine/model
downloads. Only the final-path venv and empty owned state directory reach the
runtime stage; build-wheel caches do not. The image runs numeric non-root
UID/GID 10001 by default (explicit positive overrides are supported).
Docker clients are executed with canonical headless/owned process containment.
If a build fails, times out or is interrupted, terminating the client cannot
prove the external Docker daemon cancelled its work: the SDK reports an
**uncertain daemon outcome**, never a success or automatic cleanup claim.
Confirmed successful builds return the daemon-produced immutable image ID.

Runtime entry point is direct exec:
`/opt/agent-index/bin/python -I -m agent_index_service serve --config /etc/agent-index/host.yaml`.
Supply that config as a **read-only runtime mount**, not an image layer. Mount a
dedicated writable local state volume at `/var/lib/agent-index` with the selected
UID/GID ownership, and configure home/data/routing within it. Supply separate,
explicit corpus checkout paths. The selected numeric UID/GID must be able to
read config/source files and traverse their mounted directories; read-only
mounting does not override filesystem permissions. Keep any private host-side
parent directory private while granting access only to the intentionally mounted
input. Git connectors label indexed content by repository identity, including a
separate commit-source suffix; query filters use those indexed identities, not an
arbitrary configuration alias. Never mount the native host's live SQLite or
LanceDB into another writer. The image has no `EXPOSE`, public port or wildcard
listener; the API and external warm engine remain on loopback in the same
private execution namespace. A real engine's durable model cache belongs on an
independently managed volume. The image does not start/provision that engine.

Validate real queued synthetic ingestion, a mock-engine query, durable state
after restart and graceful termination. The test-only mock
must support both ingestion batch and query embedding without any lifecycle
or model-loading request. Seeded-store queries alone are not ingestion proof.
Gateway ingress, controller-issued source-token refresh, production data
migration and schema-safe rollback remain separate reviewed slices. The chosen
future target is the **new fleet-manager Gateway**; this adapter does not depend
on it or alter an older adopter Gateway.

A separately installable Python API/master-controller program, **not a Copilot
plugin and not `agent-index-engine`**. This first slice composes the existing
`agent_index` library's hosted API, native persistence, query coordination,
SQLite task queue, detached workers and zdd deployment. It does not require
Copilot, plugin discovery, a repository checkout or agent-worktrees when given
its explicit host configuration.

## Install and run

Install `agent-index-service[native]` from the selected release/package source.
The base dependency is the normal `agent-index` Python library. The `native`
extra adds that library's existing `store,server` extras; it does **not** install
torch or the embedding engine. There is no escaping uv path dependency to the
plugin tree. A source build needs the coordinator to provision the compatible
core library and its shared dependencies in a dedicated test/release environment.
The library must include `AGENT_INDEX_SOURCE_MODE` explicit-source support;
an older library is rejected rather than silently scanning adopted repositories.

```console
agent-index-service --help
agent-index-service --version
agent-index-service config --config /absolute/path/host.yaml
agent-index-service serve --config /absolute/path/host.yaml
agent-index-service status --config /absolute/path/host.yaml
agent-index-service deploy --config /absolute/path/host.yaml
```

Use native absolute paths on Windows. `start` is a foreground alias for `serve`,
not a detached supervisor. `deploy` delegates to the existing core zdd command:
passive successor, health gate, promotion/routing flip, read drain, worker
adoption and previous-instance retirement. The legacy `python -m agent_index`
successor/worker entry points remain intentional compatibility surfaces, running
from the **installed core in the same interpreter**. A PYTHONPATH-only checkout
cannot deploy: its `-I` successors cannot import that checkout, and the command
reports this before starting a cutover.
Explicit-source mode keeps successors in the current complete native interpreter
and bypasses legacy sibling server-venv discovery/foreground redispatch.

## Stage an immutable native candidate

This initial standalone **staging adapter supports Windows and Linux**. macOS
staging fails before subprocess launch because the implementation cannot yet
provide an equivalent owned-descendant cleanup authority there. Existing legacy
macOS host/client installation remains unchanged; it is not redirected through
this new adapter. Darwin parity is an explicit deferred lifecycle item, not a
claim that native compatibility is complete.

Its byte-identical versioned-runtime resource is delivered by the same canonical
build-only helper as the thin fleet client. Wheels/sdists remain independently
buildable, including after moving a service project into a nested directory.
This packaging reuse does not alter staging, activation or host configuration.

`stage` provisions a candidate but never selects it, starts a service or changes
the host configuration/data/routing. The install root must be an explicit
absolute dedicated directory, disjoint from the host home/data/routing, bundle,
tools and policy inputs. No default `~/.agent-index` installation is inferred.

```console
agent-index-service stage --config /absolute/host.yaml --install-root /absolute/service-runtime --descriptor /absolute/bundle/release.json --expected-source-commit <commit> --python /absolute/python --uv /absolute/uv --third-party-lock /absolute/native-lock.txt --uv-config /absolute/uv.toml
agent-index-service candidates --config /absolute/host.yaml --install-root /absolute/service-runtime
```

The platform-resolved third-party lock requires one exact `==` pin and one or
more SHA-256 hashes per requirement; no includes, VCS/editable/direct URLs,
environment markers, duplicate pins or replacements of bundled distributions.
Feed policy comes from the explicitly supplied operator-owned uv TOML, not a
hardcoded public registry. An offline wheelhouse is supported through that
policy.
Policy validation requires an explicit default index (`default-index`,
`index-url` or a `[[index]]` with `default = true`), or explicit
`offline = true` / `no-index = true`. An unrelated nonempty TOML table is not
admitted, because it would leave uv's built-in registry fallback active.
Build/probe environments use a minimal platform allowlist and only the
explicit Python/uv/runtime directories on PATH. Ambient tokens, proxies, CA
overrides, compiler flags and loader injections are not inherited. Additional
proxy/CA environment injection is not supported by this adapter; required
transport settings must be expressible in the supplied policy or staging fails.
The allowed platform environment is bound into the candidate identity.
The isolated probe checks dependency/extras closure, native imports,
package versions and the explicit-source core seam without contacting an engine.

Builds reserve the permanent `versions/<service-version>` path so POSIX venv
shebangs remain valid. Verified wheels are snapshotted and reverified, build
processes are bounded/owned, and canonical completion is published last. A
completed same-version identical request is idempotent; conflicting completed
or preexisting incomplete slots fail and remain inspectable. Failure cleanup
removes only the current transaction's proven-owned incomplete slot; unconfirmed
descendant cleanup preserves it.
Removable build output is cleaned even when teardown fails, with explicit
uncertainty if removal cannot complete. Receipt temporary files are created
exclusively with restrictive permissions before atomic publication. Invalid
lock/policy UTF-8 and preserved completed-slot receipt conflicts report their
specific cause without printing feed contents.

Windows staging requires strict owned Job termination and an empty-Job
confirmation before candidate deletion. Best-effort handle closure alone is
not accepted as proof that every descendant has exited; any teardown uncertainty
preserves the incomplete candidate and fails explicitly.
The child must also acquire its non-breakaway Job while still suspended.
Assignment failure never resumes it. Legacy process-helper callers retain their
existing breakaway behavior; staging explicitly selects the stricter mode.

`candidates` is read-only, including when the root does not exist. Completion
marker absence is reported as incomplete, including a crash after receipt
publication; a present marker with missing/invalid receipt is invalid. Completion
means **built and import-validated**, not passive service readiness or migration
safety. There is no active marker, stable launcher, autostart registration,
activation, rollback or pruning in this slice. Those operations require their
own reviewed readiness, queue/data-schema and retained-rollback gates.

## Explicit host configuration (schema 1)

```yaml
schema_version: 1
home: /absolute/service-home
# Optional absolute overrides; otherwise data=home/data and routing=home.
data: /absolute/service-data
routing: /absolute/service-routing
listener:
  host: 127.0.0.1
  port: 0
sources:
  - name: git:documents
    path: /absolute/documents-checkout
    ref: HEAD
  - name: github:owner/repo
    auth:
      account: selected-account
engine:
  host: 127.0.0.1
  port: 8421
  mode: external
  model: jinaai/jina-embeddings-v2-base-code
  device: cpu
limits:
  batch_size: 16
  stream_batch_size: 64
  indexer_nice: 10
```

Only `schema_version`, `home` and a nonempty `sources` list are required. The
example shows defaults for listener, engine and limits. Git sources require
an absolute `path`; GitHub sources name `github:owner/repo`. Optional `type`,
`auth.account` and `trust_domain` preserve core source metadata. Unknown keys,
duplicate YAML keys/source names, invalid paths, types, ports, limits and
non-local endpoints are errors. Empty sources are rejected because the current
core would otherwise synthesize an implicit `git` source.

`config` validates without importing the hosted stack or creating directories.
Every operational command requires `--config`; there is no implicit read of
`~/.agent-index`, no repository grafting, no registry resolution and no ambient
plugin installation-cell selection. Core environment selections are scoped to
the operation and inherited by its children. Data, routing, run, logs, cache,
backups and local provider discovery are selected from this configuration,
not ambient `AGENT_INDEX_*` values. Treat the home/provider directory as
operator-owned, trusted state. Credentials remain external; account selection
uses the existing source connector behavior, not bundled secrets.

### Renewable Gateway ingestion credentials

Install the selected `[fleet]` extra and set a source's
`auth: {account: selected-account, gateway: /absolute/worker-policy.json}`.
This absolute, non-secret reference is retained in detached workers' explicit
source configuration. It selects the
[credential worker policy](../../fleet-manager/docs/gateway-runtime.md#worker-local-source-consumption)
and never resolves an ambient `gh` account or environment token.

For Git, also supply `remote: https://example.test/owner/repo.git` and a dedicated
absolute `path` for the independently materialized **bare** repository. An
existing non-bare or differently configured repository is rejected, not reused
or overwritten. `ref` must select a remote-tracking revision such as
`origin/dev` (the default is remote `HEAD`), not a stale local branch or tag.
Discovery fetches before recording a cursor and reads the verified commit;
initialization alone is not successful ingestion.

Each HTTP request/retry and Git fetch obtains a newly verified, acknowledged
source credential through the configured subscribed controller. Git uses
URL-scoped child-process configuration, not token-bearing argv or persistent
Git config; helpers, prompts, redirects and ambient Git tracing are disabled.
Credential acquisition, unavailable controllers, failed fetches and stale
Gateway sessions fail explicitly. There is no anonymous, old-checkout or other
account fallback. Sources without `auth.gateway` retain native behavior.

The issuer and credential custodian own access-token reference renewal,
private-key custody, enrollment and actual source permission verification.
This consumer does not issue tokens, provision a controller service, authorize
deployment or prove the operator's corpus/model parity.

## Pinned release bundles

Release inspection does not require host configuration or load the core.
A bundle contains the normal wheels for `agent-index-service`, `agent-index`,
`agent-zdd`, `agent-procutil` and `agent-dropin-registry`. Native third-party
dependencies remain governed package-manager dependencies, not bundled models
or an implicit client installation.

```console
agent-index-service release describe --bundle /absolute/release --source-commit <commit>
agent-index-service release verify --descriptor /absolute/release/release.json --expected-source-commit <commit>
```

`describe` prints a schema-1 descriptor containing the selected source commit,
service/core versions, host configuration schema and wheel identities/digests.
Save that output beside its wheels. `verify` requires an independently selected
commit pin and checks the descriptor, referenced files, wheel metadata and core
dependency constraints without importing or extracting wheel code. Invalid,
incomplete or modified bundles fail explicitly; verification never installs,
activates, rewrites configuration or changes a running host.

Wheel validation includes the mandatory `RECORD` inventory and member
hashes/sizes, not just the outer archive digest. Supported core metadata extends
through 2.6. Resource admission is explicit: metadata/RECORD at most 1 MiB,
10,000 ZIP entries, 64 MiB per uncompressed member, 256 MiB total decompression
and 512 MiB archive size. Legacy wheel signature files are permitted by the
format but are not authenticated by this verifier.

This is an **integrity and pinning contract, not a signature or release approval**.
The trusted release controller must select a real released `main` snapshot and
obtain its bundle/descriptor through its own authenticated source. Supplying a
commit string to `describe` does not prove that arbitrary wheel contents were
built from that commit. Neither command polls `dev` or silently trusts a
descriptor's own commit as the desired release.

The descriptor checks the five bundled distributions' declared version/extra
relationships, including both base and native core requirements. It is not a
complete third-party lockfile or a platform compatibility admission check.
Verified inputs must remain immutable until a future installer consumes them.

Selecting the legacy home/data/routing paths explicitly allows legacy native
colocation. **Do not run `serve` over an existing active instance:** use the
authorized lifecycle owner and `deploy` instead. No migration, installation,
engine startup, model spinup/spindown, autostart registration or configuration
rewrite is performed here. Service and worker runtimes must have the native
extras provisioned. Missing native dependencies fail before hosting.

## Compatibility and honest boundaries

The running marker, routing entry and HTTP health/status versions use this
distribution's version via `AGENT_INDEX_RUNTIME_VERSION`, not the plugin/core
version. `status` also reports `invoked_version` separately from the observed
service version and exits nonzero if not running. The legacy `plugin:
agent-index`, client paths, HTTP payloads, configuration module, rendezvous and
zdd protocols remain unchanged for existing clients.

Native persistence means existing LanceDB files plus local SQLite job state;
the API and workers still open them through the core. **Database separation is
not complete.** Warm query embedding uses the existing external `EngineClient`;
this distribution never provisions or cold-restarts an engine. Engine readiness
and task progress are not implied by `/health` process/read-admission health.
Only the existing local API listener is exposed; no new component/control/DB
ports or authenticated remote adapters are provided.

Remaining extraction seams are controller-to-persistence enqueue/claim/query,
execution-to-persistence checkpoint/batch reconciliation, controller-to-worker
assignment/progress/cancellation and query embedding/storage composition.
Future authenticated remote adapters need identity/role/job/source authorization
tests; a shared SQLite mount is not such an adapter.

Health-gated slot activation, authenticated release acquisition/build provenance,
durable supervision and rollback/schema policy belong to
the coordinator's next lifecycle/release slice. `deploy` is not an installer
or continuous restart authority. Never infer rollback safety from executable
version slots alone.

## Focused tests

Required PR CI path-gates the Linux/Windows controller contract smoke to service,
core, shared-library, runner/release-tool or CI changes. Real hosted/deployment
cycles stay in the exhaustive promotion and manual lanes:

```console
python tools/run-standalone-tests.py services/index --prepare --smoke
python tools/run-standalone-tests.py services/index --prepare
```

Released installs require `agent-index>=0.10.12.dev1`; earlier released cores
lack the explicit-source seam. The test runner alone overrides the core's frozen
`dev` source version while installing that same checkout's core. This does not
relax wheel metadata or the runtime compatibility check for ordinary installs.
Staging also requires `agent-procutil>=0.1.0.dev8` for strict assignment and
non-breakaway ownership. Local test preparation selects the same-checkout helper
explicitly despite its frozen `dev` version; real wheel bundles must satisfy
the released dependency floor.
Smoke preparation installs only the base core and test extra; native storage
and hosting extras are reserved for full/manual/promotion execution.

The wheel carries the unchanged canonical stdlib versioned-runtime primitive as
a build-generated package resource. A source build requires the full released
checkout, or the complete sdist containing that resource. Sdist-to-wheel builds
work outside the repository; missing or conflicting copies fail explicitly.
No extra maintained source mirror or plugin dependency is introduced.
Editable/source-only imports remain suitable for read-only development, but
are not an installer runtime: staging requires a built, installed wheel.

The optional real staging integration test consumes an explicitly prepared
`STANDALONE_STAGING_TEST_FIXTURE` JSON containing descriptor, source commit,
Python/uv paths, hash-lock and uv-config paths. Its wheels must come from the
approved package source for that host. Run it with the installed package's
isolated interpreter; a source-only import intentionally lacks the built resource.

In an already provisioned test environment:

```console
python -m pytest services/index/tests plugins/agent-index/tests/test_standalone_explicit_sources.py -q
```

POSIX hosted/deployment tests require the contained runner above; their children
remain in its owner-controlled process group, including after abrupt pytest
exit. Windows additionally uses nested kill-on-close Jobs for per-test teardown.

The tests select temporary homes/config/data/routing and an isolated loopback
embedding-protocol fixture; they never read the real native service home or
use the production engine. They cover light imports, strict configuration,
native failure reporting, source isolation, existing-client interoperability,
real hosted startup and no model lifecycle requests. With the core installed in
the test interpreter, the real deployment test performs two zdd deployments,
checks serving after each updater exits, verifies custom data/routing/engine
configuration despite contaminated ambient settings, retains a queued task and
adopts a live **synthetic** worker PID across cutover. An assertion-failure test
verifies owned-tree teardown; Windows tests use direct windowless worker identity
and kill-on-close Job containment rather than trusting venv supervisor PIDs.
The wheel test requires the declared `test` extra's setuptools/wheel dependencies.
Distribution tests use short, unique build roots inside the contained runner's
sandbox to leave room for nested wheel/sdist paths on Windows. Metadata and
missing/conflicting runtime-resource checks remain unchanged.
Actual source-ingestion/embedding workers, first-touch installation, updater-Job
breakaway and release-slot lifecycle remain separate validation requirements;
synthetic-worker adoption does not prove all of those contracts.
