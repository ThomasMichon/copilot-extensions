"""Devcontainer-CLI-backed single-container launch (Model A: repo cloned
inside the container).

Split out of ``fleet.py`` (module-size guard) -- this holds the
``devcontainer`` CLI invocation and the post-create repo materialization
(dotfiles/harness reproduction) used by the ``devcontainer_path`` fleet
backend. Fleet-level orchestration (``reconcile_up``, ``recreate_member``,
etc.) and the ``image:``-backed launch path stay in ``fleet.py``.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess

from agent_procutil import no_window_flags

from .config import (
    FLEET_LABEL,
    SECURITY_PROFILE_LABEL,
    DotfilesConfig,
    FleetConfig,
    HarnessConfig,
)
from .lifecycle import _docker

log = logging.getLogger("agent-containers")


def _devcontainer_up(
    fleet_name: str,
    fleet: FleetConfig,
    name: str,
    dotfiles: DotfilesConfig | None = None,
    harness: HarnessConfig | None = None,
    exec_user: str = "vscode",
) -> str:
    """Bring up one container via the devcontainer CLI; return its name.

    Tags the container with ``agent-containers.fleet`` (via id-label, which
    devcontainer applies as a docker label) and renames it to ``name``. When
    ``fleet.devcontainer_config`` is set it is passed as ``--config`` (for
    nested specs). When ``dotfiles.repo`` is set the host dotfiles repo is
    reproduced inside the container after creation (via ``docker cp``); likewise
    ``harness.repo`` reproduces the control-plane harness checkout at its
    (distinct) ``target``.
    """
    devcontainer_exe = shutil.which("devcontainer")
    if not devcontainer_exe:
        raise RuntimeError(
            "devcontainer CLI not found. Install with "
            "`npm i -g @devcontainers/cli`, or use an image-based fleet."
        )
    args = [
        devcontainer_exe, "up",
        "--workspace-folder", fleet.devcontainer_path,
        "--id-label", f"{FLEET_LABEL}={fleet_name}",
        "--id-label", f"agent-containers.instance={name}",
        "--id-label", f"{SECURITY_PROFILE_LABEL}={fleet.security_profile}",
    ]
    config_path = fleet.resolved_config()
    if config_path:
        args += ["--config", config_path]
    log.info("devcontainer up: %s", " ".join(args))
    res = subprocess.run(
        args, capture_output=True, text=True, timeout=1800,
        creationflags=no_window_flags(),
    )
    if res.returncode != 0:
        raise RuntimeError(f"devcontainer up failed for {name}: {res.stderr.strip()}")

    container_id = None
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        container_id = obj.get("containerId") or container_id
    if not container_id:
        raise RuntimeError(
            f"Could not determine containerId from devcontainer up output for {name}"
        )

    rename = _docker(["rename", container_id, name])
    if rename.returncode != 0:
        log.warning("Could not rename %s to %s: %s", container_id, name, rename.stderr.strip())
        name = container_id

    if dotfiles and dotfiles.host_repo():
        _materialize_repo(name, exec_user, dotfiles, label="dotfiles")
    if harness and harness.host_repo():
        _materialize_repo(name, exec_user, harness, label="harness")
    return name


def _materialize_repo(
    container: str, user: str, spec: DotfilesConfig | HarnessConfig, *, label: str,
) -> None:
    """Reproduce a host repo (``spec.repo``) inside the container (copy + optional
    install).

    Copies the host repo into the container at ``spec.target`` via ``docker cp``
    (the host checkout is only read, never mounted, so it is never mutated),
    chowns it to the remote user, then runs ``spec.install_command`` (if any) in
    ``target`` as that user. Used for BOTH the dotfiles shim (``label`` =
    ``"dotfiles"``, runs ``install.sh``) and the control-plane harness (``label``
    = ``"harness"``, no install by default). Best-effort: a failed copy/install
    is warned about, never fatal (the container is already usable).
    """
    host_repo = spec.host_repo()
    if host_repo is None:
        return
    target = spec.target

    mk = _docker(
        ["exec", "-u", "0", container, "bash", "-lc", f"mkdir -p {target}"],
        timeout=60,
    )
    if mk.returncode != 0:
        log.warning(
            "%s target mkdir failed in %s: %s",
            label, container, mk.stderr.strip() or mk.stdout.strip(),
        )
        return
    cp = _docker(
        ["cp", f"{host_repo.as_posix()}/.", f"{container}:{target}"], timeout=300
    )
    if cp.returncode != 0:
        log.warning(
            "%s copy into %s failed: %s",
            label, container, cp.stderr.strip() or cp.stdout.strip(),
        )
        return
    chown = _docker(
        ["exec", "-u", "0", container, "chown", "-R", f"{user}:{user}", target],
        timeout=120,
    )
    if chown.returncode != 0:
        log.warning(
            "%s chown in %s failed (continuing): %s",
            label, container, chown.stderr.strip() or chown.stdout.strip(),
        )
    log.info("Reproduced %s repo at %s in %s", label, target, container)

    if not spec.install_command:
        return
    res = _docker(
        [
            "exec", "-u", user, "-w", target, container,
            "bash", "-lc", spec.install_command,
        ],
        timeout=600,
    )
    if res.returncode != 0:
        log.warning(
            "%s install_command failed in %s (non-fatal): %s",
            label, container, res.stderr.strip() or res.stdout.strip(),
        )
    else:
        log.info("Ran %s install_command in %s", label, container)
