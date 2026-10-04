"""Networking-scope helpers for ``tools/run_tests_in_devcontainer.py``'s
Phase 2 split -- factored out to keep that module under its line-count
cap. See that module's docstring and
``efforts/active/devcontainer-test-isolation/README.md`` for the design
rationale: dependency resolution gets a network-enabled pass
(``prepare_dependencies``), then every attached network is removed
(``disconnect_container_networks``) before the real, network-disconnected
test pass runs. Each caller-supplied ``canonicalize`` argument is that
module's own ``_canonicalize_flag`` -- kept there (not duplicated here) so
there is exactly one place that resolves an abbreviated passthrough flag.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable

Canonicalize = Callable[[str], str]


def is_list_only(passthrough: list[str], canonicalize: Canonicalize) -> bool:
    """True for a bare ``--list`` request -- it never touches a venv, so
    the network-scoping split would otherwise build every matched
    plugin's venv just to list it."""
    return any(canonicalize(arg.partition("=")[0]) == "--list" for arg in passthrough)


def prepare_dependencies(
    exe: str,
    repo: Path,
    container_id: str,
    config_path: Path,
    passthrough: list[str],
    canonicalize: Canonicalize,
) -> None:
    """Install every targeted plugin's venv deps while the container
    still has outbound reach, so the real test pass right after can run
    fully network-disconnected. Reuses ``run-plugin-tests.py``'s own
    ``--prepare-only`` mode (same dependency-install path a real run
    uses) instead of a second install path. Deliberately NOT
    ``--collect-only``: that still runs pytest's own collection, which
    IMPORTS every test module/``conftest.py`` and executes their
    module-level code and collection hooks -- a buggy or adversarial
    test could still open a socket during that import, with network
    reach, before this pass's own disconnect ever runs. ``--prepare-only``
    never imports a single test file."""
    prep_passthrough = list(passthrough)
    if not any(
        canonicalize(arg.partition("=")[0]) == "--prepare-only"
        for arg in prep_passthrough
    ):
        prep_passthrough.append("--prepare-only")
    args = [
        exe, "exec",
        "--workspace-folder", str(repo),
        "--config", str(config_path),
        "--container-id", container_id,
        "--", "python", "tools/run-plugin-tests.py", *prep_passthrough,
    ]
    res = subprocess.run(args)
    if res.returncode != 0:
        raise SystemExit(
            f"dependency-preparation pass (--prepare-only) failed with "
            f"exit code {res.returncode} -- see output above; the real, "
            "network-disconnected test run was not attempted."
        )


def strip_reinstall(passthrough: list[str], canonicalize: Canonicalize) -> list[str]:
    """Remove every ``--reinstall`` occurrence from the REAL pass's
    passthrough once the preparation pass above has already (re)built
    each target's venv -- otherwise `_ensure_venv` deletes that
    freshly-prepared venv and tries to rebuild it again during the real
    pass, which by then has no network to do so with."""
    return [
        arg for arg in passthrough
        if canonicalize(arg.partition("=")[0]) != "--reinstall"
    ]


def disconnect_container_networks(container_id: str) -> None:
    """Detach the container from every attached network -- closes the
    Phase 1 networking gap: the real test pass that follows has no
    outbound reach. Reads the live network set AND ``HostConfig.NetworkMode``
    from ``docker inspect`` rather than assuming a fixed name like "bridge"
    (not a stable devcontainer-CLI contract). Fails CLOSED for an
    uninspectable namespace-sharing mode (``host``/``container:<id>``,
    e.g. `plugins/agent-containers/src/agent_containers/lifecycle.py`'s
    own restricted-fleet check applies the same rule): disconnecting
    named networks cannot isolate those modes, so an empty
    ``NetworkSettings.Networks`` there must never be read as "already
    isolated" -- it would silently leave the real test pass with full
    outbound reach. ``-f`` avoids a hang if something still holds an open
    connection."""
    res = subprocess.run(
        ["docker", "inspect", "-f",
         "{{.HostConfig.NetworkMode}}\t{{json .NetworkSettings.Networks}}", container_id],
        capture_output=True, text=True, timeout=30,
    )
    if res.returncode != 0:
        raise SystemExit(
            f"docker inspect failed while resolving {container_id}'s "
            f"attached networks: {res.stderr.strip()}"
        )
    network_mode, _, networks_json = res.stdout.strip().partition("\t")
    try:
        networks = json.loads(networks_json)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"could not parse docker inspect's network output for "
            f"{container_id}: {exc}"
        ) from exc
    if not isinstance(networks, dict):
        raise SystemExit(
            f"unexpected docker inspect network shape for {container_id}: {networks!r}"
        )
    if network_mode == "host" or network_mode.startswith("container:"):
        raise SystemExit(
            f"container {container_id} uses network mode {network_mode!r}, which shares a "
            "namespace this wrapper cannot isolate by disconnecting named networks -- "
            "refusing to proceed with network potentially still reachable."
        )
    if network_mode == "none":
        # `NetworkMode` reflects CREATION-time config, not live state -- a
        # later `docker network connect` can attach a real network to a
        # "none"-mode container while this field stays frozen at "none".
        # Only the NETWORK KEY is the isolation invariant (matching
        # `agent-containers`' own restricted-fleet check) -- a legitimate
        # `--network none` container's single "none" entry still carries
        # real `EndpointSettings` metadata, so comparing the whole value
        # to `{}` would wrongly reject it.
        attached = set(networks.keys())
        if attached != {"none"}:
            raise SystemExit(
                f"container {container_id} reports network mode 'none' but its attached "
                f"networks are {sorted(attached)!r}, not just 'none' -- refusing to assume "
                "it is still isolated."
            )
        return
    if not networks:
        raise SystemExit(
            f"container {container_id} reports network mode {network_mode!r} with no "
            "inspectable attached networks -- refusing to assume it is safely isolated."
        )
    for net_name in networks:
        disc = subprocess.run(
            ["docker", "network", "disconnect", "-f", net_name, container_id],
            capture_output=True, text=True, timeout=30,
        )
        if disc.returncode != 0:
            raise SystemExit(
                f"failed to disconnect {container_id} from network "
                f"{net_name!r}: {disc.stderr.strip()}"
            )
