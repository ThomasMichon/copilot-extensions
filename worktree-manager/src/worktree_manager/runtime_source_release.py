"""Use published wheel inputs without changing Manager's installed footprint."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Iterator
from urllib.parse import urlsplit
import uuid

import agent_procutil

from .engine_environment import _engine_environment
from . import engine_client
from .runtime_source import acquire
from .runtime_source_config import RuntimeSource
from .self_install_runtime import load_runtime_installer
from .runtime_identity import runtime_scripts, running_payload_dir


def _scoped_client(client: list[str], repository: str) -> list[str] | None:
    from .repository_authentication import _runtime_command

    selected = engine_client.installed_engine_command()
    if (
        selected is None or _runtime_command(selected) is None
        or _runtime_command(client) != _runtime_command(selected)
    ):
        return None
    slot = Path(selected[0]).parent.parent
    pattern = (
        "Lib/site-packages/agent_worktrees/repos-gh-v1.json" if os.name == "nt"
        else "lib/python*/site-packages/agent_worktrees/repos-gh-v1.json"
    )
    try:
        paths = list(slot.glob(pattern))
        if len(paths) != 1:
            return None
        descriptor, = paths
        if not descriptor.resolve().is_relative_to(slot.resolve()) or descriptor.stat().st_size > 65536:
            return None
        value = json.loads(descriptor.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(value, dict) or value.get("contract") != "agent-worktrees.repos-gh"
        or type(value.get("version")) is not int or value["version"] != 1
        or not isinstance(value.get("capabilities"), list)
        or "require-account" not in value["capabilities"]
    ):
        return None
    return [*selected, "repos", "gh", "--require-account", repository, "--"]


def _repository(locator: str) -> str | None:
    match = re.fullmatch(r"git@github\.com:([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?", locator)
    if match is not None:
        return match[1]
    parsed = urlsplit(locator) if "://" in locator else None
    if parsed is None or (
        parsed.scheme not in {"https", "ssh"} or parsed.hostname != "github.com"
        or parsed.password is not None or parsed.query or parsed.fragment
        or parsed.port is not None or parsed.scheme == "https" and parsed.username is not None
    ):
        return None
    repository = parsed.path.strip("/").removesuffix(".git")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None:
        return None
    return repository


def _provider(scripts: Path) -> Path:
    packaged = scripts / "runtime-input-provider/tools/bootstrap_manager_release.py"
    if packaged.is_file():
        return packaged
    payload = running_payload_dir()
    canonical = payload.parent / "tools/bootstrap_manager_release.py"
    if (payload.parent / ".git").exists() and canonical.is_file():
        return canonical
    raise ValueError("Manager released-wheel preparation assets are unavailable; repair the Manager payload")


@contextmanager
def _preparation_python(
    engine: Any, uv: str, environment: dict[str, str], deadline: float,
) -> Iterator[Path]:
    # Bootstrap Python has no Manager dependencies. Keep preparation tools out
    # of that interpreter and the runtime that this operation will install.
    with engine._temporary_inputs(None) as temporary:
        root = Path(engine._native_retained_path(engine._private_retained_root(Path(temporary))))
        tools = root / "tools"
        engine.run_owned(
            [uv, "venv", "--no-project", "--no-python-downloads",
             "--python", sys.executable, str(tools)],
            cwd=root, env=environment, timeout=deadline - time.monotonic(),
            deadline=deadline, procutil=agent_procutil, construction_fd=None,
        )
        python = tools / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        with engine.retain_uv_install(
            uv, ["--python", str(python), "packaging>=25"],
            env=environment, cwd=root, deadline=deadline,
        ) as transaction:
            engine.run_owned(
                list(transaction.argv), cwd=transaction.cwd, env=transaction.environment,
                timeout=deadline - time.monotonic(), deadline=deadline,
                procutil=agent_procutil, construction_fd=None,
            )
            yield python


@contextmanager
def prepared(
    repo: str, root: Path, *, environment: dict[str, str] | None = None,
    uv: Path | None = None,
) -> Iterator[tuple[Path, dict[str, Any]] | None]:
    """Keep the prepared source and admitted wheels held through native update."""
    repository = _repository(repo)
    client = engine_client.engine_base_command() if repository is not None else None
    if repository is not None and client is not None:
        client = _scoped_client(client, repository)
    if repository is None or client is None:
        print(
            "Repository-scoped release access is unavailable for this source; using source installation.",
            file=sys.stderr,
        )
        yield None
        return
    deadline = time.monotonic() + 600
    scripts = runtime_scripts(Path(__file__).resolve().parent, running_payload_dir)
    installer = load_runtime_installer(scripts)
    provider = _provider(scripts)
    selected_uv = installer.engine._select_uv_executable(str(uv) if uv is not None else shutil.which("uv") or "")
    base = root / "manager-source"
    output = base / "runtime-sources" / "released" / uuid.uuid4().hex
    expected: Path | None = None
    with acquire(
        RuntimeSource("main", repo=repo), root=base,
        environment=environment, deadline=deadline, compact=os.name == "nt",
    ) as resolved, _preparation_python(
        installer.engine, selected_uv, _engine_environment(environment), deadline,
    ) as python:
        command = [
            str(python), "-I", "-B", "-X", "utf8", str(provider),
            "--checkout", str(resolved.checkout), "--commit", resolved.commit,
            "--repository", repository, "--output", str(output),
            "--component", "worktree-manager", "--snapshot",
            "--uv", selected_uv, "--deadline", str(deadline),
            "--github-command", json.dumps(client),
        ]
        try:
            result = installer.engine.run_owned(
                command, cwd=Path(provider.anchor) if os.name == "nt" else provider.parent,
                env=_engine_environment(environment),
                timeout=deadline - time.monotonic(), deadline=deadline,
                procutil=agent_procutil, construction_fd=None,
            )
        except subprocess.CalledProcessError as error:
            if error.returncode != 3:
                raise
            result = error
        if time.monotonic() >= deadline:
            raise ValueError("Manager released-wheel preparation deadline expired")
        if result.returncode != 3:
            if not isinstance(result.stdout, str) or len(result.stdout.encode("utf-8")) > 65536:
                raise ValueError("Manager released-wheel preparation returned an over-budget receipt")
            record = json.loads(result.stdout)
            expected = Path(installer.engine._native_retained_path(output / "profile/snapshot"))
            if (
                not isinstance(record, dict) or set(record) != {"snapshot"}
                or not isinstance(record["snapshot"], str)
                or Path(installer.engine._native_retained_path(Path(record["snapshot"]))) != expected
            ):
                raise ValueError("Manager released-wheel preparation returned a different snapshot")
    if expected is None:
        print(
            "Selected source has no published wheel artifact; using source installation.",
            file=sys.stderr,
        )
        yield None
        return
    with installer.prepared_manager_inputs(expected) as (selected, options):
        if (
            selected.identity.source_commit != resolved.commit
            or selected.identity.source_tree != resolved.tree
            or "wheel_inputs" not in options or "local_packages" not in options
        ):
            raise ValueError("Manager released wheels differ from the selected source")
        print("Installing Manager from its published wheel inputs.", file=sys.stderr)
        yield selected.payload, {
            "_wheel_inputs": options["wheel_inputs"],
            "_local_packages": options["local_packages"],
        }
