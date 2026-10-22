"""Repository-scoped plugin preparation on the existing SSH CLI channel."""

from __future__ import annotations

import json
import base64
import os
import shutil
import subprocess
import shlex
import signal
import threading
from pathlib import Path

from agent_procutil import no_window_flags
from plugin_resolve.payload import is_harness_only
from plugin_resolve.remote_identity import normalize_remote
from plugin_resolve.reflection import (
    build_plan,
    build_prepare_python,
    parse_prepared_context,
    selection_spec,
)
from plugin_resolve.target import requires_repository_binding
from venue_copilot.refs import require_cli_context
from venue_copilot import last_json

_CHECKOUT_TIMEOUT = 90.0
_PREPARATION_TIMEOUT = 540.0
_TRANSPORT_GRACE = 60.0
_DISCOVERY_TIMEOUT = 60.0
_DISCOVERY_TRANSPORT_GRACE = 20.0
PREPARATION_RESERVATION_BUDGET = _CHECKOUT_TIMEOUT + _PREPARATION_TIMEOUT + 2 * _TRANSPORT_GRACE


def repository_key(remote: str) -> str:
    normalized = normalize_remote(remote)
    if normalized and normalized.startswith("network:"):
        github = normalized.removeprefix("network:github.com/")
        return github if github != normalized and github.count("/") == 1 else normalized
    if remote and normalized is None:
        raise ValueError("SSH checkout has an invalid Git remote identity")
    return f"filesystem:{remote}" if remote else ""


def _repository_equal(left: str, right: str) -> bool:
    if all(":" not in value and value.count("/") == 1 for value in (left, right)):
        return left.casefold() == right.casefold()
    return left == right


def run_python(adapter, program: str, *, timeout: float, transport_grace: float = _TRANSPORT_GRACE):
    from plugin_resolve import receiver

    source = Path(receiver.__file__).read_text(encoding="utf-8")
    encoded = base64.b64encode(program.encode("utf-8")).decode("ascii")
    payload = (
        "import sys\n"
        "if sys.version_info < (3,10):\n"
        "    raise RuntimeError('SSH CLI plugin preparation requires Python 3.10 or newer')\n"
        f"exec({source!r})\n"
        f"supervise({encoded!r},{timeout!r})\n"
    )
    done = threading.Event()
    results, failures = [], []

    def execute():
        try:
            results.append(adapter.run_input(
                "python3 -I -S -X utf8 -u -", payload.encode("utf-8"), timeout=timeout + transport_grace,
            ))
        except BaseException as exc:
            failures.append(exc)
        finally:
            done.set()

    interrupted = False
    def defer_interrupt(_signum, _frame):
        nonlocal interrupted
        interrupted = True
    main_thread = threading.current_thread() is threading.main_thread()
    previous = signal.getsignal(signal.SIGINT) if main_thread else None
    installed = main_thread and previous != signal.SIG_IGN
    if installed:
        signal.signal(signal.SIGINT, defer_interrupt)
    try:
        threading.Thread(target=execute, daemon=True).start()
        while True:
            try:
                if done.is_set():
                    break
                done.wait()
            except KeyboardInterrupt:
                interrupted = True
    finally:
        if installed:
            signal.signal(signal.SIGINT, previous)
    if interrupted:
        raise KeyboardInterrupt
    if failures:
        raise failures[0]
    return results[0]


def probe_checkout(
    adapter, workspace: str, *, timeout: float = _CHECKOUT_TIMEOUT,
    transport_grace: float = _TRANSPORT_GRACE,
) -> dict:
    program = (
        "import json,os,pathlib,shutil,subprocess\n"
        f"requested={workspace!r}\n"
        "cwd=pathlib.Path(requested).resolve(strict=True)\n"
        "if not cwd.is_dir(): raise ValueError('SSH workspace is not a directory')\n"
        "os.chdir(cwd)\n"
        "env={key:value for key,value in os.environ.items() if not key.startswith('GIT_')}\n"
        "git=shutil.which('git',path=env.get('PATH',''))\n"
        "if not git: raise RuntimeError('SSH checkout lookup requires Git')\n"
        "owner=shutil.which('agent-worktrees',path=env.get('PATH',''))\n"
        "if not owner: raise RuntimeError('SSH checkout lookup requires the native worktree owner')\n"
        "def query(*args):\n"
        "    return subprocess.run([git,*args],cwd=cwd,env=env,capture_output=True,\n"
        "        text=True,timeout=30)\n"
        "root=query('rev-parse','--show-toplevel')\n"
        "if root.returncode: raise RuntimeError('SSH workspace is not a Git checkout')\n"
        "selection=subprocess.run([owner,'get','git-remote'],cwd=cwd,env=env,\n"
        "    capture_output=True,text=True,timeout=30)\n"
        "remote=selection.stdout.strip()\n"
        "if selection.returncode or not remote or '\\n' in remote or '\\0' in remote:\n"
        "    raise RuntimeError('SSH native owner did not resolve its configured Git remote')\n"
        "origin=query('config','--get',f'remote.{remote}.url')\n"
        "if origin.returncode not in (0,1): raise RuntimeError('SSH configured remote lookup failed')\n"
        "print('__SSH_CHECKOUT__'+json.dumps({'requested':requested,'cwd':str(cwd),\n"
        "    'root':str(pathlib.Path(root.stdout.strip()).resolve(strict=True)),\n"
        "    'origin':origin.stdout.strip()}))\n"
    )
    rc, output, error = run_python(adapter, program, timeout=timeout, transport_grace=transport_grace)
    if rc:
        raise RuntimeError("SSH execution checkout lookup failed: " + error.strip())
    line = next((line for line in reversed(output.splitlines()) if line.startswith("__SSH_CHECKOUT__")), None)
    if line is None:
        raise RuntimeError("SSH execution checkout lookup returned no proof")
    proof = json.loads(line.removeprefix("__SSH_CHECKOUT__"))
    if (
        not isinstance(proof, dict) or proof.get("requested") != workspace
        or any(not isinstance(proof.get(key), str) for key in ("cwd", "root", "origin"))
        or any(not proof[key].startswith("/") or "\0" in proof[key] for key in ("cwd", "root"))
    ):
        raise ValueError("SSH execution checkout proof does not match the selected workspace")
    proof["repository"] = repository_key(proof["origin"])
    return proof


def _registry(arguments) -> dict | None:
    command = shutil.which("agent-worktrees")  # marketplace-isolation: allow legacy management registry lookup
    if command is None:
        return None
    argv = [command, *arguments]
    if os.name == "nt" and command.lower().endswith((".cmd", ".bat")):
        argv = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", *argv]
    result = subprocess.run(
        argv, capture_output=True, text=True, timeout=30, creationflags=no_window_flags(),
    )
    if result.returncode:
        raise RuntimeError("Target plugin ownership registry command failed: " + result.stderr.strip())
    data = json.loads(result.stdout)
    if not isinstance(data, dict):
        raise RuntimeError("Target plugin ownership registry returned no object")
    return data


def resolve_launch(adapter, plan: dict) -> None:
    """Resolve the existing anchor owner before reserving or preparing it."""
    proof = probe_checkout(adapter, plan["workspace"])
    expected = getattr(getattr(adapter, "plugin_args", None), "expected_repository", None)
    if expected is not None and not _repository_equal(expected, proof["repository"]):
        raise RuntimeError("SSH execution repository changed after controller plugin selection")
    rc, output, error = adapter.run(
        f"cd {shlex.quote(proof['cwd'])} && agent-worktrees embody --anchor --dry-run --json",
        timeout=90,
    )
    native = last_json(output)
    if (
        rc or native.get("ok") is not True or native.get("dry_run") is not True
        or native.get("anchor") is not True
        or not isinstance(native.get("worktree_id"), str) or not native["worktree_id"]
        or not isinstance(native.get("session"), str) or not native["session"]
        or not isinstance(native.get("work_dir"), str)
        or not native["work_dir"].startswith("/") or "\0" in native["work_dir"]
    ):
        raise RuntimeError(
            "SSH CLI owner did not resolve the configured checkout's native anchor: " + error.strip(),
        )
    if native["work_dir"] != proof["root"]:
        native_proof = probe_checkout(adapter, native["work_dir"])
        if native_proof["cwd"] != proof["root"]:
            raise RuntimeError("SSH CLI native anchor is not the configured checkout's root")
    old_scope = plan["scope_id"]
    plan.update(
        workspace=proof["root"], identity=native["worktree_id"],
        scope_id=f"{native['worktree_id']}@{adapter.target}", mux_session=native["session"],
    )
    plan["venue"]["mux_session_name"] = native["session"]
    if adapter.hold_id == old_scope:
        adapter.hold_id = plan["scope_id"]


def _binding(repository: str) -> dict:
    inventory = _registry(["repos", "list", "--strict", "--json"])
    if (
        inventory is None or inventory.get("inventory_authority") != "complete"
        or not isinstance(inventory.get("repos"), list)
    ):
        raise RuntimeError("Absolute SSH plugin sources require a complete repository registry")
    roots = []
    for row in inventory["repos"]:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise RuntimeError("Repository registry returned an invalid target binding")
        remote = row.get("remote", "")
        if not isinstance(remote, str):
            raise RuntimeError("Repository registry returned an invalid remote")
        if repository != row["name"] and not _repository_equal(repository_key(remote), repository):
            continue
        found = _registry(["repos", "find", row["name"], "--registered-only", "--json"])
        if (
            found is None or found.get("registration") != "ready"
            or found.get("name") != row["name"] or found.get("remote") != remote
            or not isinstance(found.get("path"), str) or not Path(found["path"]).is_absolute()
        ):
            raise RuntimeError("Registered target plugin ownership changed during resolution")
        roots.append(Path(found["path"]).resolve(strict=True))
    return {"target_repository": repository, "target_source_roots": roots} if roots else {}


def prepare(args, adapter, plan: dict) -> dict:
    proof = probe_checkout(adapter, plan["workspace"])
    expected = getattr(args, "expected_repository", None)
    if expected is not None and not _repository_equal(expected, proof["repository"]):
        raise RuntimeError("SSH execution repository changed after controller plugin selection")
    specs = [selection_spec(value) for value in getattr(args, "stage_plugins", [])]
    specs = [spec for spec in specs if not is_harness_only(spec["source"])]
    roots = [Path(root) for root in getattr(args, "plugin_roots", [])]
    binding = {}
    if proof["repository"] and requires_repository_binding(specs, roots):
        binding = _binding(proof["repository"])
    producer = build_plan(specs, roots, allow_external=True, **binding)
    program = build_prepare_python(producer, proof["cwd"], include_prepared_context=True)
    rc, output, error = run_python(adapter, program, timeout=_PREPARATION_TIMEOUT)
    if rc:
        raise RuntimeError("Required SSH CLI plugin preparation failed: " + error.strip())
    context = require_cli_context(parse_prepared_context(output, expected_repo=proof["root"]))
    return {**context, "cwd": proof["cwd"], "repository_root": proof["root"]}


def disabled_plugin(source: str) -> dict:
    return selection_spec({"source": source, "enable": False})


def target_repo(args) -> int:
    from .copilot_detach import _SshAdapter, _prepare_ssh_and_workspace, plan_for

    try:
        config = _prepare_ssh_and_workspace(args, dry_run=True)
        adapter = _SshAdapter(target=args.target, ssh_config=config)
        plan = plan_for(args)
        proof = probe_checkout(
            adapter, plan["workspace"], timeout=_DISCOVERY_TIMEOUT,
            transport_grace=_DISCOVERY_TRANSPORT_GRACE,
        )
        print(proof["repository"])
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        import sys
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
