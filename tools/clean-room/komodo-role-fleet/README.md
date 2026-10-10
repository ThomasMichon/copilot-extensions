# Komodo Linux role proof

Opt-in **host-driven Tier-P integration proof**, not a production installer or
the ordinary single-box `run.ps1` scenario. It needs a running Linux Docker
engine and explicit permission to create a privileged nested-Docker target.
Do not run it on a host that cannot safely accommodate this trust level.

```text
python tools/clean-room/komodo-role-fleet/run.py --allow-privileged-dind --results-root <private-scratch-root>
```

The runner creates a unique Compose project and private run folder, synthesizes
one-run credentials, pulls digest-pinned artifacts, and removes all project
profiles/volumes/networks and secret files afterward. It never receives live
credentials, mounts a host Docker socket into Periphery, or enrolls a live host.
The nested daemon is privileged inside the selected Linux Docker engine;
this is not presented as a security sandbox against malicious containers.

Core/database/Periphery/client use an internal-only network with **no published
ports**. Periphery receives only the nested daemon's socket. The host runner
preloads the pinned BusyBox artifact into that daemon, so the role itself has
no network access. Synthetic passwords, JWT material and short-lived onboarding
key files remain in the private run directory only during the experiment.
The caller must ensure `--results-root` is private; the runner's fresh child
inherits that ACL on Windows and uses `mkdtemp` permissions on POSIX.

## Contract

1. Fresh Core 2.3.3 creates the configured initial admin without a browser.
2. An isolated client authenticates using the supported local-login API.
3. A short-lived, nonprivileged onboarding key enrolls a second real Periphery.
4. The supported manager API deploys one fixed harmless role into nested Docker.
5. Independent daemon inspection proves it is running, including while Core and
   the enrolled Periphery are stopped.
6. Manager/agent restart and repeat Compose/API operations preserve server count.
7. All owned project resources and synthetic secret files are removed.

`receipt.json` is bounded non-secret outcome evidence, not a production
deployment receipt. Failed setup and failed cleanup remain failures. A container
manager acceptance is not treated as completion: the execute update is polled
and the running role is inspected independently.

This proves headless bootstrap/enrollment and one disposable named deployment,
not two independent hosts, real source credentials, fleet Gateway authentication,
stateful movement, resource scaling, service-safe update/drain or production
role packaging. Those remain in the [Linux role fleet effort](../../../efforts/active/linux-role-fleet/README.md).
The BusyBox command is fixed test-fixture behavior, not an exposed generic
remote-execution API.

This lane is intentionally not added to fast guards or required PR CI. Explicit
integration execution owns its image/network/capacity costs and trust decision.
