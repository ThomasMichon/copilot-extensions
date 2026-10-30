# Isolated uv version-slot experiment

This is a deliberately small, **nonproduction** alternative-installation
experiment. It does not convert an existing runtime, register a plugin, start
a supervisor, or implement the production source-admission contract.
It exercises the existing vendor-pointer assembly pattern and immutable
version-directory shape, not production release qualification.

The `dev` fixture has a standalone loopback service, a standalone tool, and
one canonical library. The actual main materializer's **opt-in** `--uv-example`
option copies that library into each final project's `libs/example-message`,
rewrites its uv source to a noneditable local path, and locks each assembled
project. Normal materialization is unchanged. Shared services would be separate
projects with their own version slots and binstubs, never library dependencies.

## Flow

### Current milestone: environment setup only

The current suite qualification does **not** run the toy lifecycle below.
It uses the actual main materializer's opt-in `--uv-suite` mode to compose
all declared Python owner projects under `install-source/uv-projects`
(21 projects in the initial validation snapshot).
First-party dependencies are included by relative local sources; standalone
build resources use their existing independently buildable sdist layout.
Each project receives a genuine universal uv lock and a local Git ignore
exception retaining `uv.lock`. No textual lock rewriting occurs.

The deterministic suite witness commits these ready projects with ordinary
`git add -A`, checks out a separate Git copy, removes the original input paths,
copies each project to a fresh version slot and runs uv initialization plus
an installed-Python syntax probe. The probe compiles an installed file without
executing application imports, install commands, authentication or service code.
No global app commands, pointers or install-success markers are published.
Base environments are selected; optional profiles are listed but not credited.
Universal locks include ordinary uv hashes. Build requirements retain the
projects' declared constraints; this does not introduce a separate build-tool lock.
Each successful Windows environment also receives an unchanged-lock Linux-target
`uv sync --dry-run` resolution check. That is Linux resolution evidence, not
Linux execution or service readiness.

```powershell
python -m unittest discover -s examples\uv-versioned-service -p 'test_*.py' -v
.\examples\uv-versioned-service\windows-clean-room.ps1 -EnvironmentOnly -SourceCommit <revision> -Image <generic-windows-cleanroom-image> -UvExecutable <uv.exe>
```

Add `-TwoPass` for a fresh-environment cache-rebuild comparison within that same
container. Pass 1 starts with no version-slot environments and an empty shared
runtime cache. The witness then removes only its recorded exact version-slot
leaves, preserves the materialized projects/cache/toolchain, recopies those same
projects and repeats the identical locked initialization/probe commands.
Per-pass rows retain copy/init timings and hardlink evidence. Native cache-file
identity observations show whether each root project's cached artifact survived
and was reused; they are filesystem observations, not content fingerprints.
Aggregate totals, ratios, percentage reductions and per-project regressions
are recorded in `rebuild_comparison`. Existing-venv warm probes and Linux dry-run
checks run only after both measured passes. This is one paired trial, with no
statistical-significance or no-network claim.

Locks and construction use an explicit public PyPI index in this isolated
clean-room experiment, without inherited host uv configuration or credentials,
and retain the seven-day cutoff. This opt-in path is not a production feed-policy
or installer replacement. Per-project failures remain explicit; environment
qualification never means an installed app, running service, model readiness or
qualification of all optional profiles.

### Historical isolated toy activation flow

Use Python 3.11+, an already installed uv, Git, and PowerShell on Windows.
External packages retain the ordinary configured feeds and seven-day cutoff.
The example's exact setuptools build requirement is declared in each project;
the generated `uv.lock` pins the runtime closure. uv's lock is not a separate
lock of isolated build dependencies, so build requirements are pinned explicitly.

1. Commit the example before staging a selected source revision. From this
   example directory, stage an explicitly selected local or remote Git revision:

   ```powershell
   python spike.py stage --repository <git-url> --ref <git-ref> --dest .runs\dev
   ```

   This uses `git fetch --depth 1` followed by detached checkout. Existing stage
   folders are reused without recopying or additional head/content checks.
   Failed stages receive a unique `.failed.*` suffix; retry uses a fresh folder.
   A local candidate is not evidence that remote `main` contains this example.

2. Produce a **sibling** main-style tree outside the staged dev checkout:

   ```powershell
   python .runs\dev\tools\materialize_main.py --uv-example --dest .runs\main
   ```

   The ready projects are under
   `.runs\main\examples\uv-versioned-service\install-source\services\example-service`
   and `install-source\tools\example-tool`. Composition runs `uv lock`, **not**
   `uv sync`, and produces no `.venv`. Do not use `--candidate`: this experiment
   does not request custom fingerprints, receipts or authenticity machinery.
   The ordinary materializer replaces its destination, so supply a fresh,
   caller-owned destination outside its input checkout.
   Each ready project already includes `bin/invoke.cmd`, `bin/invoke.ps1`
   and `bin/invoke.sh`; their uv options and project-derived cache configuration
   are part of the complete install-source footprint, not generated by setup.

3. Copy the complete candidate, run its project-owned install command directly
   through uv, and **only then** publish the global binstub and markers:

   ```powershell
   python spike.py bootstrap --kind service --project <ready-service-project> --home <example-home>
   python spike.py bootstrap --kind tool --project <ready-tool-project> --home <example-home>
   ```

   Choose a dedicated home for repeatable clean runs. An explicit real-home
   check may use `$HOME\.copilot-extensions`, provided these synthetic names do
   not already belong to another test. Layout:
   `<example-home>/<services|tools>/<project-name>/versions/<version>`.
   Global `.cmd`, `.ps1` and `.sh` binstubs are under `<example-home>/bin`; the cache is
   `<example-home>/cache/uv`, on the same volume as each environment.
   No library runtime slots or cross-slot source paths exist.

   The sequence for each app is:

   - Copy the ready project into its candidate slot, excluding `.venv`.
     There is no global binstub, current pointer or success marker yet.
   - Run
     `uv run --locked --no-dev --no-editable --link-mode hardlink --project <absolute-candidate> python -m example_<service|tool> install`.
     uv initializes/synchronizes the candidate environment; the project's
     minimal `install` command succeeds without starting a background process.
   - After that command exits successfully, publish the global binstub,
     `current-version` and the candidate's `.install-complete` marker.

   Setup itself does no vendoring, metadata rewrite, locking or eager `uv sync`.
   All environment construction belongs to this direct `uv run`. The toy install
   command is intentionally a no-op after importing the installed vendored
   library; this is not a generalized lifecycle/cutover framework.

4. Invoke the binstub directly, or exercise the finite service through:

   ```powershell
   python spike.py run --home <example-home> --run-dir .runs\first-start
   ```

   **Every global binstub** only reads the plain-text `current-version` marker
   and forwards to its matching slot-local `bin/invoke.<cmd|ps1|sh>` wrapper.
   CMD reads the marker with `set /p`, without launching PowerShell or Python.
   The global layer contains no uv or cache logic. **Every slot-local wrapper** uses
   `uv run --locked --no-dev --no-editable --link-mode hardlink --project <absolute-slot>`.
   Since candidate install already initialized the environment, the first
   published binstub call normally reuses it. uv retains its normal
   synchronization on every call; there is no `--no-sync` warm path.
   uv's own package cache and lock behavior are the only package machinery.
   The binstub preserves the caller's working directory and arguments; it does
   not use `--directory` or change directory. Service runtime files are supplied
   as absolute paths by its caller.

The service binds OS-assigned IPv4 loopback, handles synthetic health requests,
and stops after at most 30 seconds in the runner (60 seconds maximum when invoked
directly). The runner owns the complete invocation, requests graceful stop,
waits for exit and checks listener closure. It never detaches a durable daemon.
Run directories retain startup stderr, timing and cleanup evidence.

## Reuse and retry

A successful project install permits publication of `.install-complete`,
the global binstub and current pointer. A completed same-version directory
skips copying; setup may run its project-owned no-op install again. An incomplete
attempt is renamed to a unique `.failed.*` directory before fresh copying.
A failed uv/install command publishes nothing: the previous global binstub,
current pointer and success-marker bytes remain untouched. A failed new
candidate has no success marker and receives a unique `.failed.*` suffix.
Repeat bootstrap to copy a fresh candidate and retry. A runtime `serve` failure
does not republish or roll back installation state.
There is one writer, no new lease/lock system, custom hashes or content scans.

These are intentional **experimental exceptions**, not changes to production's
source admission, immutable constructor, resolver, health or lifecycle contracts.
In particular, normal uv synchronization can repair an example environment;
the experiment does not claim production's stronger immutable-environment proof.

## Validation

```powershell
python -m unittest discover -s examples\uv-versioned-service -p test_spike.py -v
python examples\uv-versioned-service\validate.py --source-commit <committed-revision> --real-home
```

The second command stages a shallow **local committed candidate**, materializes
it using the actual main materializer, and retains results beneath `.runs`.
It does not assert that a remote release was tested. Failed attempts and
isolated caches remain for explicit caller-owned cleanup; never clear a shared
uv cache. Production upgrades, restarts, registry changes and release publication
are outside this experiment.

The local candidate checkout uses Git sparse selection for the example and
the materializer's tooling, not unrelated production products. It is still
the selected committed source and the actual main materializer. The witness
starts two direct candidate `uv run` read-only commands concurrently against
one copied tool project with no environment and an empty cache. Neither test
invocation publishes a global binstub or marker. Separately, after successful
install/publication, real binstub calls verify installed imports, exact argument
forwarding and caller working-directory preservation. Both layers have all three
shell variants. The witness runs real CMD and PS1 forwarding/exit-code tests on
Windows and SH through Git Bash when available, reporting a missing shell
explicitly. uv owns its own
environment synchronization, as documented in
[Astral's cache-safety contract](https://docs.astral.sh/uv/concepts/cache/#cache-safety);
there is no additional installer/binstub lock.

Results separate source-copy, uv initialization/project-install, global
publication and warm-launch timings. A small paired warm tool measurement
reports direct uv calls versus the outer binstub, without presenting the
difference as a general performance guarantee. Candidate environments and their
dedicated uv caches are cold; the host's Python/uv toolchain is already installed
and warm. The optional actual-home check reports whether its cache preexisted
and removes only its owned synthetic slot/binstub, never the shared cache.
The host witness is not a clean Windows container or remote-release qualification.

### Windows Docker lane (Tier P)

`windows-clean-room.ps1` uses an already reachable Windows Docker context and an
explicitly selected, already present stock Python Windows image. It never switches
daemon mode, restarts Docker, pulls or removes images, mounts a host profile or
Docker socket, or forwards credentials. Supply a clean toolchain directory with
`uv.exe` and an unconfigured portable `Git/bin/git.exe` tree, or select an existing
generic clean-room image with Python/Git and explicitly supply only a prerequisite
uv binary:

```powershell
.\windows-clean-room.ps1 -SourceCommit <revision> -ToolchainDirectory <clean-toolchain-directory>
.\windows-clean-room.ps1 -SourceCommit <revision> -Image <generic-windows-cleanroom-image> -UvExecutable <uv.exe>
```

Only a shallow local Git fetch of the selected source commit and prerequisite
binaries are copied into a uniquely named container. The container checks out
that exact commit; it does not recreate a synthetic source commit from an archive.
Both outer and environment-result reports name the selected source commit, with native
Git commit/tree output retained in `source-git.txt`. Its runtime and uv cache start
empty. The deterministic whole-flow
witness runs inside that container; image/toolchain cache warmth is reported
separately. Results record the actual container ID and exact-ID removal.
Input/output copies occur while this owned container is stopped, including on
Hyper-V isolation hosts; the runner never changes daemon isolation or settings.
An unreachable Windows daemon or missing image/toolchain is a blocker, never
replaced with Linux or credited as a Windows end-to-end pass. This narrow Windows
scenario does not change the existing Linux clean-room rig.
