"""Cold installed project-command qualification on a caller-owned Windows TTY."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from contextlib import contextmanager
from pathlib import Path

import tomllib

REPO = Path(__file__).resolve().parents[3]
_WORK_DEADLINE = None
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "libs" / "agent-procutil" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _diagnostic_excerpt(path, limit=16384):
    info = path.lstat()
    if path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("Diagnostic log must be an ordinary owned file")
    if not path.is_file() or info.st_size > 1 << 20:
        raise ValueError("Diagnostic log exceeds its retained bound")
    content = path.read_bytes()
    if len(content) <= limit:
        return content.decode("utf-8", "replace")
    quarter = limit // 4
    context_budget = limit - 2 * quarter
    contexts = []
    for line in content.decode("utf-8", "replace").splitlines():
        plain = re.sub(r"\x1b\[[0-9;]*m", "", line)
        if len(line) > 1200 or "CalledProcessError: Command" in plain:
            continue
        if not re.search(r"error|exception|fail|refus|unavailable|ownership|admitt|cannot|can't",
                         plain, re.IGNORECASE):
            continue
        encoded = (line + "\n").encode("utf-8")
        if len(encoded) <= context_budget:
            contexts.append(line)
            context_budget -= len(encoded)
    return (
        "[diagnostic head]\n" + content[:quarter].decode("utf-8", "replace")
        + "\n[diagnostic causal lines]\n" + "\n".join(contexts)
        + f"\n[diagnostic tail; original bytes={len(content)}]\n"
        + content[-quarter:].decode("utf-8", "replace")
    )


def _failure_evidence(root):
    records = {}
    remaining = 192 << 10
    for name, allocation in (
        ("register-project.stdout.txt", 16 << 10), ("register-project.stderr.txt", 16 << 10),
        ("register-project.custody.json", 8 << 10),
        ("install-engine.stderr.txt", 64 << 10), ("install-engine.stdout.txt", 16 << 10),
        ("install-manager.stderr.txt", 16 << 10), ("install-manager.driver-0.txt", 16 << 10),
        ("seal-start-census.json", 8 << 10), ("install-manager.custody.json", 4 << 10),
        ("install-engine.custody.json", 4 << 10), ("cmd.terminal.txt", 32 << 10),
        ("powershell.terminal.txt", 32 << 10),
        ("cmd.processes.json", 8 << 10), ("powershell.processes.json", 8 << 10),
        ("cmd.exit.json", 8 << 10), ("powershell.exit.json", 8 << 10),
    ):
        path = root / "diagnostics" / name
        if not path.exists() or remaining < 1024:
            continue
        text = _diagnostic_excerpt(path, min(allocation, remaining - 512))
        content = path.read_bytes()
        records[name] = {
            "bytes": len(content), "sha256": _digest(content),
            "truncated": len(content) > min(allocation, remaining - 512), "text": text,
        }
        remaining -= len(text.encode("utf-8")) + 512
    return records


def _command_failure(label, code, stdout, stderr):
    return (
        f"{label} exited {code}; stdout_bytes={stdout.stat().st_size}"
        f" stderr_bytes={stderr.stat().st_size}\n"
        "[command stdout]\n" + _diagnostic_excerpt(stdout, 8192)
        + "\n[command stderr]\n" + _diagnostic_excerpt(stderr, 8192)
    )


def _git_environment(environment=None):
    source = str(REPO / "plugins" / "agent-worktrees" / "src")
    inserted = source not in sys.path
    if inserted:
        sys.path.insert(0, source)
    try:
        from agent_worktrees.git_repository_environment import repository_identity_env

        env = repository_identity_env(environment)
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        return env
    finally:
        if inserted:
            sys.path.remove(source)


class _InstalledLaunchRejected(RuntimeError):
    pass


class _ConsumerExited(Exception):
    def __init__(self, label, code, control):
        super().__init__(label)
        self.label, self.code, self.control = label, code, control


def _classify_consumer_exit(error, output):
    text = _visible(output)
    if (
        error.control and error.label == "rendered project Picker" and error.code != 0
        and "No usable Worktree Manager is available right now." in text
    ):
        raise _InstalledLaunchRejected("Controlled installed Manager launch rejected: " + text[-2000:])
    raise RuntimeError(f"Consumer exited {error.code} before {error.label}: {text[-2000:]}")


def _confirm_job_empty(job, timeout, label):
    from plugin_test_containment import UnreapedProcessError

    try:
        job.terminate_and_wait(timeout)
    except UnreapedProcessError:
        raise
    except (OSError, RuntimeError, AssertionError) as error:
        raise UnreapedProcessError(f"{label} cleanup could not be confirmed", job=job) from error


@contextmanager
def _owned_console(argv, *, env, cwd):
    from _terminal_console_fixture import Console
    from plugin_test_containment import UnreapedProcessError

    console = Console(argv, env=env, cwd=cwd)
    unreaped = False
    exited = None
    try:
        yield console
    except UnreapedProcessError:
        unreaped = True
        raise
    except _ConsumerExited as error:
        exited = error
    finally:
        if not unreaped:
            job = console.job
            if job is not None:
                _confirm_job_empty(job, 5, "terminal Job")
                console.job = None
            try:
                console.close()
            except (OSError, RuntimeError, AssertionError) as error:
                raise UnreapedProcessError("Terminal cleanup could not be confirmed", job=job) from error
    if exited is not None:
        _classify_consumer_exit(exited, console.output)


def _slot_census(root, diagnostics):
    """Observe only the private candidate slot; the real seal remains untouched."""
    deadline = min(time.monotonic() + 10, _WORK_DEADLINE or float("inf"))
    record = {"complete": False, "observed_at_ns": time.time_ns(), "roots": {}}
    try:
        versions = root / "versions"
        slots = [path for path in versions.iterdir() if path.is_dir()]
        if len(slots) != 1:
            raise ValueError("Census requires exactly one fixture candidate slot")
        slot = slots[0]
        if slot.is_symlink() or slot.resolve().parent != versions.resolve():
            raise ValueError("Census candidate is indirect or outside the private root")
        count = 0
        pending = [slot]
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    if time.monotonic() >= deadline or count >= 100000:
                        raise TimeoutError("Read-only census reached its diagnostic bound")
                    path = Path(entry.path)
                    if path.parent == slot and path.name in (
                        ".manager-artifacts.json", ".install-complete.json",
                    ):
                        continue
                    info = entry.stat(follow_symlinks=False)
                    if entry.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                        raise ValueError("Read-only census refuses indirect slot entries")
                    relative = path.relative_to(slot)
                    category = relative.parts[0]
                    row = record["roots"].setdefault(category, {"files": 0, "directories": 0})
                    is_dir = entry.is_dir(follow_symlinks=False)
                    row["directories" if is_dir else "files"] += 1
                    count += 1
                    if is_dir:
                        pending.append(path)
        record.update(complete=True, total_entries=count, slot=slot.name)
    except (OSError, ValueError, TimeoutError) as error:
        record["error"] = str(error)
    record["finished_at_ns"] = time.time_ns()
    (diagnostics / "seal-start-census.json").write_text(json.dumps(record), encoding="utf-8")


def _run(arguments, env, cwd, diagnostics, label, *, timeout=None):
    from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job
    from agent_procutil._daemon_instance_identity import birth_identity

    argv = list(map(str, arguments))
    remaining = (_WORK_DEADLINE or time.monotonic() + 30) - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Installed TTY shared work deadline expired before command admission")
    timeout = min(timeout, remaining) if timeout is not None else remaining
    out, err = (diagnostics / f"{label}.{name}.txt" for name in ("stdout", "stderr"))
    print(f"installed-tty: {label}", flush=True)
    started = time.monotonic()
    receipt = {
        "label": label, "started_at_ns": time.time_ns(), "settled": False,
        "argv": argv, "cwd": str(cwd),
        "fixture_environment": {
            key: env.get(key) for key in (
                "HOME", "USERPROFILE", "AGENT_WORKTREES_HOME", "WORKTREE_MANAGER_ROOT",
                "AGENT_WORKTREES_STATUS_MONITOR", "AGENT_WORKTREES_NO_SELFPROVISION",
                "CE_TERMINAL_LAUNCH", "CE_TERMINAL_CAPABILITY_QUERY",
            )
        },
    }
    with out.open("wb") as stdout, err.open("wb") as stderr:
        process, job = spawn_sync_in_kill_on_close_job(
            argv, require_job=True, allow_breakaway=False, env=env, cwd=cwd,
            stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, **no_window_kwargs(),
        )
        receipt.update(pid=process.pid, birth=birth_identity(process.pid, include_exited=True))
        observed = 0.0
        try:
            while process.poll() is None:
                if time.monotonic() - started >= timeout:
                    raise subprocess.TimeoutExpired(argv, timeout)
                if max(out.stat().st_size, err.stat().st_size) > 1 << 20:
                    raise RuntimeError(f"{label} exceeded its 1 MiB output bound")
                if time.monotonic() - observed >= 10:
                    driver = Path(env["USERPROFILE"]) / ".copilot-extensions" / "update-driver"
                    rows = []
                    for index, path in enumerate(driver.rglob("*.log")):
                        if index >= 12:
                            break
                        with path.open("rb") as stream:
                            stream.seek(max(0, path.stat().st_size - 65536))
                            content = stream.read(65536)
                        (diagnostics / f"{label}.driver-{index}.txt").write_bytes(content)
                        if label == "install-manager" and b"phase=inventory" in content and not (
                            diagnostics / "seal-start-census.json"
                        ).exists():
                            _slot_census(Path(env["WORKTREE_MANAGER_ROOT"]), diagnostics)
                        rows.append({"name": path.name, "bytes": path.stat().st_size,
                                     "mtime_ns": path.stat().st_mtime_ns})
                    (diagnostics / f"{label}.progress.json").write_text(json.dumps({
                        "observed_at_ns": time.time_ns(), "driver_logs": rows,
                        "stdout_bytes": out.stat().st_size, "stderr_bytes": err.stat().st_size,
                    }), encoding="utf-8")
                    print(f"installed-tty: {label} elapsed={time.monotonic()-started:.1f}s"
                          f" stdout={out.stat().st_size} stderr={err.stat().st_size}"
                          f" driver-log-bytes={sum(row['bytes'] for row in rows)}", flush=True)
                    observed = time.monotonic()
                time.sleep(.1)
            receipt["exit_code"] = process.returncode
        finally:
            _confirm_job_empty(job, 10, label)
            try:
                process.wait(timeout=5)
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                from plugin_test_containment import UnreapedProcessError

                raise UnreapedProcessError(f"{label} root retirement could not be confirmed",
                                          job=job) from error
            receipt.update(settled=True, native_job_empty=True, finished_at_ns=time.time_ns())
            receipt.update(stdout_bytes=out.stat().st_size, stderr_bytes=err.stat().st_size,
                           elapsed_seconds=time.monotonic() - started)
            (diagnostics / f"{label}.custody.json").write_text(
                json.dumps(receipt), encoding="utf-8",
            )
    print(f"installed-tty: {label} completed exit={process.returncode}"
          f" elapsed={receipt['elapsed_seconds']:.3f}s stdout={receipt['stdout_bytes']}"
          f" stderr={receipt['stderr_bytes']}", flush=True)
    if process.returncode:
        raise RuntimeError(_command_failure(label, process.returncode, out, err))
    return out.read_text(encoding="utf-8").strip()


def _candidate_snapshot(root, identity, env, diagnostics):
    from agent_procutil import no_window_kwargs
    import launch_wrapper_assets_ref as assets

    actual = _run(["git", "rev-parse", "HEAD"], env, REPO, diagnostics, "candidate-head")
    tree = _run(["git", "rev-parse", "HEAD^{tree}"], env, REPO, diagnostics, "candidate-tree")
    if actual != identity["candidate_commit"] or tree != identity["candidate_tree"]:
        raise RuntimeError("Executing checkout is not the exact admitted candidate")
    selectors = [
        "worktree-manager", "plugins/agent-worktrees", "libs", "tools", "docs",
        "plugins/agent-containers/setup.py", ".github/plugin/marketplace.json",
    ]
    required = {
        manifest.source_relative(name)
        for manifest in assets.load_manifest_groups(
            REPO / "worktree-manager", manifest_name=assets.PACKAGED_MANIFEST_NAME,
        )
        for name in manifest.files
    }
    selectors.extend(sorted(
        name for name in required
        if not any(name == parent or name.startswith(parent + "/") for parent in selectors)
    ))
    archive = root / "candidate.tar"
    result = subprocess.run(
        ["git", "archive", "--format=tar", "--output", str(archive), actual, *selectors],
        cwd=REPO, env=env, timeout=30, capture_output=True, check=False, **no_window_kwargs(),
    )
    if result.returncode:
        raise RuntimeError("Could not retain the exact candidate consumer bytes")
    snapshot = root / "candidate"
    snapshot.mkdir()
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            path = snapshot / member.name
            if not path.resolve().is_relative_to(snapshot.resolve()) or not (
                member.isfile() or member.isdir()
            ):
                raise RuntimeError("Candidate archive contains a non-file or unsafe path")
        source.extractall(snapshot, filter="data")
    archive.unlink()
    return snapshot


def _payloads(root, identity, env, diagnostics):
    import prepare_runtime_source

    snapshot = _candidate_snapshot(root, identity, env, diagnostics)
    marketplace = root / "payloads" / ".github" / "plugin"
    marketplace.mkdir(parents=True)
    shutil.copy2(snapshot / ".github" / "plugin" / "marketplace.json",
                 marketplace / "marketplace.json")
    payloads = {}
    for component in ("worktree-manager", "agent-worktrees"):
        source = snapshot / ("worktree-manager" if component == "worktree-manager"
                             else "plugins/agent-worktrees")
        destination = root / "payloads" / (
            component if component == "worktree-manager" else "plugins/" + component
        )
        if (source / "runtime-source.json").is_file():
            shutil.copytree(source, destination)
        else:
            prepare_runtime_source._materialize(snapshot, component, destination)
            sys.path.insert(0, str(REPO / "libs" / "versioned-runtime"))
            import versioned_runtime_source as provenance

            version = tomllib.loads((destination / "pyproject.toml").read_text(
                encoding="utf-8",
            ))["project"]["version"]
            assembly = provenance.assembly_hash(destination)
            source_tree = _run(
                ["git", "rev-parse", identity["source_commit"] + "^{tree}"],
                env, REPO, diagnostics, component + "-source-tree",
            )
            receipt = provenance.SourceIdentity(
                component, version, identity["source_commit"], source_tree, assembly,
                provenance.runtime_version(version, identity["source_commit"], assembly),
            )
            (destination / provenance.SOURCE_FILE).write_text(
                json.dumps(receipt.document()), encoding="utf-8",
            )
        receipt = json.loads((destination / "runtime-source.json").read_text(encoding="utf-8"))
        if receipt["source_commit"] != identity["source_commit"]:
            raise RuntimeError(f"{component} has different release-source provenance")
        payloads[component] = destination
    return payloads


def _host_state():
    home = Path.home()
    files = [
        home / ".worktree-manager" / "current-version",
        home / ".agent-worktrees" / "current-version",
        home / ".agent-worktrees" / "projects.yaml",
        home / ".agent-worktrees" / "repos.yaml",
    ]
    files.extend(home / ".local" / "bin" / (command + suffix)
                 for command in ("agent-worktrees", "worktree-manager", "tty-harness")
                 for suffix in (".ps1", ".cmd"))
    return {str(path): _digest(path.read_bytes()) if path.is_file() else None for path in files}


def _verify_retained_source(root, slot, component, receipt):
    if component == "worktree-manager":
        retained_source = slot / "payload" / "runtime-source.json"
    else:
        generation = receipt["runtime_version"]
        if not isinstance(generation, str) or not generation or generation in (".", "..") or (
            Path(generation).name != generation or "/" in generation or "\\" in generation
        ):
            raise ValueError("Candidate source generation must name one immutable snapshot")
        retained_source = root / "snapshots" / generation / "runtime-source.json"
    if json.loads(retained_source.read_text(encoding="utf-8")) != receipt:
        raise RuntimeError(f"{component} constructor retained another source identity")


def _installed(root, payload, component, env, diagnostics):
    slot = root / "versions" / (root / "current-version").read_text(encoding="utf-8").strip()
    python = slot / "Scripts" / "python.exe"
    code = (
        "import hashlib,importlib,json,pathlib,sys;"
        f"m=importlib.import_module({component.replace('-', '_')!r});"
        "p=pathlib.Path(m.__file__);"
        "print(json.dumps({'prefix':sys.prefix,'module':str(p),"
        "'module_hash':hashlib.sha256(p.read_bytes()).hexdigest()}))"
    )
    observed = json.loads(_run(
        [python, "-I", "-B", "-c", code], env, payload, diagnostics, component + "-origin",
        timeout=30,
    ))
    module = Path(observed["module"])
    expected = payload / "src" / component.replace("-", "_") / "__init__.py"
    if (
        Path(observed["prefix"]).resolve() != slot.resolve()
        or not module.resolve().is_relative_to(slot.resolve())
        or observed["module_hash"] != _digest(expected.read_bytes())
    ):
        raise RuntimeError(f"{component} did not import its exact installed candidate")
    package = payload / "src" / component.replace("-", "_")
    checked = 0
    for path in package.rglob("*.py"):
        if component == "agent-worktrees" and path.relative_to(package).as_posix() == "_build_info.py":
            continue
        installed = module.parent / path.relative_to(package)
        if not installed.is_file() or installed.read_bytes() != path.read_bytes():
            raise RuntimeError(f"{component} installed different candidate module bytes")
        checked += 1
    if not checked:
        raise RuntimeError(f"{component} has no verified candidate source modules")
    completion = json.loads((slot / ".install-complete.json").read_text(encoding="utf-8"))
    origins = slot / ".runtime-input-origins.json"
    if not completion or not origins.is_file():
        raise RuntimeError(f"{component} has no completed constructor origins")
    receipt = json.loads((payload / "runtime-source.json").read_text(encoding="utf-8"))
    _verify_retained_source(root, slot, component, receipt)
    return {
        "generation": slot.name, "source_commit": receipt["source_commit"],
        "assembly_hash": receipt["assembly_hash"], "origins_verified": True,
        "candidate_bytes_verified": True, "module_hash": observed["module_hash"],
        "verified_module_count": checked,
    }, python


def _visible(output):
    text = output.decode("utf-8", "replace")
    return re.sub(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))", "", text)


def _picker_rendered(output):
    text = _visible(output)
    return (
        b"\x1b[?1049h" in output
        and "tty-harness" in text
        and "worktrees" in text.casefold()
    )


def _image(console, handle):
    import ctypes
    from ctypes import wintypes

    query = console.api.QueryFullProcessImageNameW
    query.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                      ctypes.POINTER(wintypes.DWORD)]
    query.restype = wintypes.BOOL
    text = ctypes.create_unicode_buffer(32768)
    size = wintypes.DWORD(len(text))
    if not query(handle, 0, text, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    return Path(text.value)


def _live_image(console, handle):
    if console.exited(handle):
        return None
    try:
        return _image(console, handle)
    except OSError:
        timeout = 250
        if _WORK_DEADLINE is not None:
            timeout = max(0, min(timeout, int((_WORK_DEADLINE - time.monotonic()) * 1000)))
        # Image teardown can precede the same retained process handle becoming signalled.
        if not console.exited(handle, timeout):
            raise
        return None


def _project_entry_command(shell, stub):
    if shell == "cmd":
        # CALL keeps CMD's /s quote removal away from the encoded path argument.
        return [os.environ["COMSPEC"], "/d", "/s", "/c", "call", str(stub)]
    return [shutil.which("pwsh"), "-NoLogo", "-NoProfile", "-File", str(stub)]


def _installed_picker(console, manager_python, engine_python, observations, retained):
    if not _picker_rendered(console.output):
        return None
    descendants = console.pin_descendants()
    retained.update(descendants)
    pinned = {}
    observations.clear()
    expected = (manager_python.resolve(), engine_python.resolve())
    for pid, (name, handle) in descendants.items():
        try:
            image = _live_image(console, handle)
        except OSError as error:
            error.add_note(f"Could not identify retained TTY descendant {pid} ({name})")
            raise
        live = image is not None and not console.exited(handle)
        if len(observations) < 16:
            observations[str(pid)] = {
                "name": name, "image": str(image) if image is not None else None, "live": live,
            }
        if live and image.resolve() in expected:
            pinned[str(image.resolve())] = {"pid": pid, "birth": console.birth(handle)}
    return (descendants, pinned) if str(expected[0]) in pinned else None


def _consumer_exit_evidence(console, error, descendants, started, input_sent):
    retained = {}
    for pid, (name, handle) in list(descendants.items())[:16]:
        retained[str(pid)] = {
            "name": name, "birth": console.birth(handle), "exit_code": console.exit_code(handle),
        }
    return {
        "label": error.label, "exit_code": error.code, "input_sent": input_sent,
        "elapsed_seconds": time.monotonic() - started,
        "root": {"pid": console.process.pid, "birth": console.birth(console.process.process)},
        "retained_descendants": retained,
    }


def _witness(shell, stub, env, project, diagnostics, manager_python, engine_python, *, control=False):
    label = shell + ("-negative" if control else "")
    fault_directory = diagnostics / (label + "-faults")
    fault_directory.mkdir()
    env = {**env, "WORKTREE_MANAGER_FAULT_DIR": str(fault_directory)}
    argv = _project_entry_command(shell, stub)
    captured = b""
    started = time.monotonic()
    input_sent = False
    with _owned_console(argv, env=env, cwd=project) as console:
        pinned = {}
        descendants = {}
        observations = {}
        next_probe = 0.0

        def wait(predicate, label):
            deadline = min(time.monotonic() + 30, _WORK_DEADLINE or float("inf"))
            while time.monotonic() < deadline:
                if predicate():
                    return
                if console.exited(console.process.process):
                    raise _ConsumerExited(label, console.wait(1000), control)
                if console.read_error:
                    raise RuntimeError(console.read_error)
                time.sleep(.025)
            raise TimeoutError(f"{shell}: no {label} within the native 30-second boundary")

        try:
            def installed_ready():
                nonlocal pinned, next_probe
                now = time.monotonic()
                if now < next_probe:
                    return False
                next_probe = now + .1
                selected = _installed_picker(
                    console, manager_python, engine_python, observations, descendants,
                )
                if selected is None:
                    return False
                _, pinned = selected
                return True

            wait(installed_ready, "rendered project Picker")
            offers = []
            for path in Path(env["TEMP"]).glob("ce-terminal-*/request.json"):
                from agent_procutil._terminal_offer import validate

                _, offer = validate(str(path))
                if offer["host_pid"] in descendants:
                    offers.append(offer)
            if not offers:
                raise RuntimeError("Interactive shell bypassed its native terminal capability admission")
            before = bytes(console.output)
            console.send(b"q")
            input_sent = True
            wait(lambda: "Leave the worktree picker?" in _visible(console.output[len(before):]),
                 "keyboard-driven quit dialog")
            after = bytes(console.output)
            console.send(b"y")
            code = console.wait(15000)
            if code != 0:
                raise RuntimeError(f"{shell}: interactive exit returned {code}")
            for name, handle in descendants.values():
                if not console.exited(handle, 5000):
                    raise RuntimeError(f"{shell}: owned {name} survived clean interactive exit")
            captured = bytes(console.output)
            return {
                "shell": shell, "terminal": "conpty", "project_binstub": True,
                "capability_query": True, "rendered": True, "input_visible_change": True,
                "clean_exit": True, "exit_code": code,
                "screen_before_sha256": _digest(before),
                "screen_after_sha256": _digest(after),
                "process_id": console.process.pid,
                "process_birth": console.birth(console.process.process),
                "installed_processes": pinned,
                "input_transition": {"key": "q", "expected_text": "Leave the worktree picker?",
                                     "observed": True},
                "native_terminal_offer_hosts": [offer["host_pid"] for offer in offers],
            }
        except _ConsumerExited as error:
            label = shell + ("-negative" if control else "")
            (diagnostics / (label + ".exit.json")).write_text(json.dumps(
                _consumer_exit_evidence(console, error, descendants, started, input_sent),
            ), encoding="utf-8")
            raise
        finally:
            captured = bytes(console.output)
            label = shell + ("-negative" if control else "")
            (diagnostics / (label + ".terminal.txt")).write_bytes(captured)
            (diagnostics / (label + ".processes.json")).write_text(json.dumps({
                "expected_manager": str(manager_python), "expected_engine": str(engine_python),
                "observed": observations,
            }), encoding="utf-8")
            if console.job is not None:
                _confirm_job_empty(console.job, 5, label)
                console.job = None
            (diagnostics / (label + ".custody.json")).write_text(json.dumps({
                "pid": console.process.pid, "birth": console.birth(console.process.process),
                "native_job_empty": True,
            }), encoding="utf-8")


def _environment(root):
    from plugin_test_environment import isolated_environment

    if os.name != "nt" or not shutil.which("pwsh") or not shutil.which("uv"):
        raise RuntimeError("Native Windows, PowerShell 7 and configured uv are required")
    env = isolated_environment(_git_environment(), root, allow_explicit_tiers=True)
    home = root / "h"
    home.mkdir(exist_ok=True)
    env.update({
        "HOME": str(home), "USERPROFILE": str(home), "AGENT_HOME": str(home),
        "APPDATA": str(home / "r"), "LOCALAPPDATA": str(home / "l"),
        "WORKTREE_MANAGER_ROOT": str(root / "m"),
        "AGENT_WORKTREES_HOME": str(home / ".agent-worktrees"),
        "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR": str(root / "p"),
        "WORKTREE_NO_UPDATE": "1", "AGENT_WORKTREES_STATUS_MONITOR": "0",
        "AGENT_WORKTREES_NO_SELFPROVISION": "1", "UV_PYTHON_DOWNLOADS": "never",
    })
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "WT_SESSION", "TMUX",
                "TMUX_PANE", "CE_TERMINAL_LAUNCH", "CE_TERMINAL_CAPABILITY_QUERY",
                "WORKTREE_MANAGER_ENGINE_ARGV", "WORKTREE_MANAGER_ENGINE_CMD"):
        env.pop(key, None)
    for key in list(env):
        if key == "NETRC" or (
            key.startswith("UV_INDEX_") and key.endswith(("_USERNAME", "_PASSWORD"))
        ):
            env.pop(key)
    bin_dir = home / ".local" / "bin"
    env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
    return env


def prepare(root, identity):
    diagnostics = root / "diagnostics"
    diagnostics.mkdir()
    env = _environment(root)
    manager_root, engine_root = Path(env["WORKTREE_MANAGER_ROOT"]), Path(env["AGENT_WORKTREES_HOME"])
    if any((path / "current-version").exists() for path in (manager_root, engine_root)):
        raise RuntimeError("Installed TTY qualification requires a cold private installation")
    payloads = _payloads(root, identity, env, diagnostics)
    (root / "prepared.json").write_text(json.dumps({
        "identity": identity, "cold": True,
        "payloads": {name: str(path) for name, path in payloads.items()},
    }), encoding="utf-8")


def construct(root, identity, component):
    env = _environment(root)
    diagnostics = root / "diagnostics"
    prepared = json.loads((root / "prepared.json").read_text(encoding="utf-8"))
    if prepared["identity"] != identity or prepared["cold"] is not True:
        raise RuntimeError("Construction lost its exact cold candidate admission")
    manager_root, engine_root = Path(env["WORKTREE_MANAGER_ROOT"]), Path(env["AGENT_WORKTREES_HOME"])
    base = Path(getattr(sys, "_base_executable", sys.executable))
    manager, engine = (Path(prepared["payloads"][name])
                       for name in ("worktree-manager", "agent-worktrees"))
    if component == "manager":
        _run([base, "-I", "-S", "-B", manager / "scripts" / "runtime_install.py",
              "--root", manager_root, "--payload", manager, "--", "--version"],
             env, root, diagnostics, "install-manager")
    else:
        _run([base, "-I", "-B", engine / "scripts" / "runtime_install.py",
              "--root", engine_root, "--plugin", engine, "--payload", engine,
              "--uv", shutil.which("uv"), "--base-python", base,
              "--frontend", json.dumps({
                  "shell": "ps1", "executable": shutil.which("pwsh"), "scope": "resolvers",
              }), "--result", root / "engine-install.json"],
             env, root, diagnostics, "install-engine")


def _harness_registration(engine_python, project, marketplace_root):
    config = project / ".agent-worktrees"
    config.mkdir()
    (config / "config.yaml").write_text(
        "project: tty-harness\nexpose_agent: true\nstateless: true\n", encoding="utf-8",
    )
    settings = project / ".github" / "copilot"
    settings.mkdir(parents=True)
    marketplace_name = json.loads((
        marketplace_root / ".github" / "plugin" / "marketplace.json"
    ).read_text(encoding="utf-8"))["name"]
    (settings / "settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {
            marketplace_name: {"source": {"source": "directory", "path": str(marketplace_root)}},
        },
        "enabledPlugins": {"agent-worktrees@" + marketplace_name: True},
    }), encoding="utf-8")
    return [
        engine_python, "-I", "-B", "-m", "agent_worktrees", "register", "tty-harness",
        "--repo-dir", project, "--machine", "tty-fixture", "--default-branch", "main", "--no-agent",
    ]


def _negative_control(manager_python, launch):
    from plugin_test_containment import UnreapedProcessError

    hidden = manager_python.with_suffix(".negative-control")
    manager_python.rename(hidden)
    unreaped = None
    try:
        try:
            launch()
        except UnreapedProcessError as error:
            unreaped = error
            raise
        except _InstalledLaunchRejected:
            return True
        return False
    finally:
        try:
            hidden.rename(manager_python)
        finally:
            if unreaped is not None:
                raise unreaped


def exercise(root, identity):
    env = _environment(root)
    diagnostics = root / "diagnostics"
    before_host = _host_state()
    prepared = json.loads((root / "prepared.json").read_text(encoding="utf-8"))
    if prepared["identity"] != identity or prepared["cold"] is not True:
        raise RuntimeError("TTY consumer lost its exact cold candidate admission")
    manager, engine = (Path(prepared["payloads"][name])
                       for name in ("worktree-manager", "agent-worktrees"))
    manager_root, engine_root = Path(env["WORKTREE_MANAGER_ROOT"]), Path(env["AGENT_WORKTREES_HOME"])
    bin_dir = Path(env["USERPROFILE"]) / ".local" / "bin"
    manager_identity, manager_python = _installed(
        manager_root, manager, "worktree-manager", env, diagnostics,
    )
    engine_identity, engine_python = _installed(
        engine_root, engine, "agent-worktrees", env, diagnostics,
    )
    project = root / "tty-harness"
    project.mkdir()
    for label, args in (
        ("init", ["init", "-q", "-b", "main"]),
        ("commit", ["-c", "user.name=TTY Fixture", "-c", "user.email=fixture@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", "TTY fixture"]),
    ):
        _run(["git", "-C", project, *args], env, root, diagnostics, "project-" + label, timeout=30)
    _run(_harness_registration(engine_python, project, root / "payloads"),
         env, project, diagnostics, "register-project", timeout=30)
    _run([
        engine_python, "-I", "-B", "-c",
        ("import pathlib;from agent_worktrees import installer as i;"
        f"o,p,s,a=i._project_binstub_context('tty-harness',repo_dir=pathlib.Path({str(project)!r}));"
        "r=i._read_receipt('tty-harness');assert r and r['owner']==o and r['project']==p;"
        "assert all(i._stub_hash(r,path)==i._file_sha256(path)"
        " and path.read_bytes() in (text.encode(),text.replace('\\r\\n','\\n').encode())"
        " for path,text in s);print('project receipt and generated bytes verified')"),
    ], env, project, diagnostics, "project-binstub-ownership", timeout=30)
    witnesses = []
    for shell, suffix in (("cmd", ".cmd"), ("powershell", ".ps1")):
        stub = bin_dir / ("tty-harness" + suffix)
        if not stub.is_file() or "agent-worktrees project binstub" not in stub.read_text(encoding="utf-8"):
            raise RuntimeError("Real project registration did not publish its owned binstub")
        witnesses.append(_witness(
            shell, stub, env, project, diagnostics, manager_python, engine_python,
        ))
    rejected = _negative_control(manager_python, lambda: _witness(
        "cmd", bin_dir / "tty-harness.cmd", env, project, diagnostics,
        manager_python, engine_python, control=True,
    ))
    if not rejected:
        raise RuntimeError("Install-success/launch-failure negative control was not rejected")
    if before_host != _host_state():
        raise RuntimeError("Host runtime selectors or command/registration state changed")
    return {
        "installation": {"cold": True, "installed": True,
                         "manager": manager_identity, "engine": engine_identity},
        "project": {"name": "tty-harness", "registered": True,
                    "generated_binstub": True, "receipt_verified": True},
        "witnesses": witnesses,
        "negative_control": {"install_succeeded": True, "launch_failed": True,
                             "witness_rejected": True},
        "cleanup": {"complete": True, "owned_processes_reaped": True,
                    "host_state_unchanged": True},
    }


def main():
    global _WORK_DEADLINE

    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--exercise", type=Path, required=True)
    parser.add_argument("--identity", type=json.loads, required=True)
    parser.add_argument("--phase", choices=("prepare", "manager", "engine", "tty"), required=True)
    parser.add_argument("--work-deadline", type=float, required=True)
    args = parser.parse_args()
    _WORK_DEADLINE = args.work_deadline
    if not 0 < _WORK_DEADLINE - time.monotonic() <= 720:
        parser.error("native phase requires the caller's finite shared work deadline")
    if args.phase == "prepare":
        prepare(args.exercise, args.identity)
        return 0
    if args.phase in ("manager", "engine"):
        construct(args.exercise, args.identity, args.phase)
        return 0
    result = exercise(args.exercise, args.identity)
    (args.exercise / "native-result.json").write_text(json.dumps(result), encoding="utf-8")
    return 0


def _receipt_fixture():
    """Synthetic parser input only; this never supplies native acceptance."""
    identity = {
        "candidate_commit": "a" * 40, "candidate_tree": "b" * 40,
        "source_commit": "c" * 40, "main_before": "d" * 40, "run_id": 1, "run_attempt": 2,
    }
    component = {
        "generation": "1.2.3+source.example", "source_commit": identity["source_commit"],
        "assembly_hash": "e" * 64, "origins_verified": True, "candidate_bytes_verified": True,
    }
    return identity, {
        "schema": "worktree-manager.installed-tty", "version": 1, "identity": identity.copy(),
        "platform": "win32", "complete": True, "errors": [],
        "whole_run_seconds": 720, "cleanup_reserve_seconds": 30, "elapsed_seconds": 600,
        "installation": {"cold": True, "installed": True,
                         "manager": component.copy(), "engine": component.copy()},
        "project": {"name": "tty-harness", "registered": True,
                    "generated_binstub": True, "receipt_verified": True},
        "witnesses": [{
            "shell": shell, "terminal": "conpty", "project_binstub": True,
            "capability_query": True, "rendered": True, "input_visible_change": True,
            "clean_exit": True, "exit_code": 0, "process_id": 100, "process_birth": 1000,
            "screen_before_sha256": "1" * 64, "screen_after_sha256": "2" * 64,
            "input_transition": {"key": "q", "expected_text": "Leave the worktree picker?",
                                 "observed": True},
            "native_terminal_offer_hosts": [101],
        } for shell in ("cmd", "powershell")],
        "negative_control": {"install_succeeded": True, "launch_failed": True,
                             "witness_rejected": True},
        "cleanup": {"complete": True, "owned_processes_reaped": True,
                    "host_state_unchanged": True},
    }


def test_receipt_requires_complete_identity_bound_native_consumer_evidence(tmp_path):
    import copy
    import runpy

    validate = runpy.run_path(str(REPO / "tools" / "run-manager-runtime-acceptance.py"))[
        "_installed_tty_complete"
    ]
    identity, original = _receipt_fixture()
    report = tmp_path / "receipt.json"
    report.write_text(json.dumps(original), encoding="utf-8")
    assert validate(report, identity)
    mutations = [
        (("complete",), False), (("platform",), "linux"), (("errors",), ["timeout"]),
        (("whole_run_seconds",), 1200), (("cleanup_reserve_seconds",), 0),
        (("elapsed_seconds",), 721), (("elapsed_seconds",), float("nan")),
        (("identity", "candidate_commit"), "f" * 40), (("identity", "candidate_tree"), "f" * 40),
        (("identity", "source_commit"), "f" * 40), (("identity", "main_before"), "f" * 40),
        (("identity", "run_id"), True), (("identity", "run_attempt"), 3),
        (("installation", "cold"), False),
        (("installation", "manager", "source_commit"), identity["candidate_commit"]),
        (("installation", "engine", "origins_verified"), False),
        (("installation", "manager", "candidate_bytes_verified"), False),
        (("project", "generated_binstub"), False), (("project", "receipt_verified"), False),
        (("witnesses", 0, "terminal"), "headless"), (("witnesses", 0, "exit_code"), False),
        (("witnesses", 0, "input_visible_change"), False),
        (("witnesses", 0, "input_transition", "expected_text"), "spinner changed"),
        (("witnesses", 0, "input_transition", "observed"), 1),
        (("witnesses", 0, "native_terminal_offer_hosts"), []),
        (("witnesses", 0, "capability_query"), False),
        (("witnesses", 0, "screen_after_sha256"), "1" * 64),
        (("witnesses", 0, "process_birth"), None),
        (("witnesses", 1, "shell"), "cmd"),
        (("negative_control", "witness_rejected"), False),
        (("cleanup", "owned_processes_reaped"), False),
        (("cleanup", "host_state_unchanged"), False),
    ]
    for path, value in mutations:
        changed = copy.deepcopy(original)
        target = changed
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        report.write_text(json.dumps(changed), encoding="utf-8")
        assert not validate(report, identity), path
    for witnesses in ([], original["witnesses"][:1], original["witnesses"] * 2):
        changed = copy.deepcopy(original)
        changed["witnesses"] = witnesses
        report.write_text(json.dumps(changed), encoding="utf-8")
        assert not validate(report, identity)


def test_receipt_refuses_missing_linked_oversize_duplicate_and_incomplete_files(tmp_path):
    import runpy

    validate = runpy.run_path(str(REPO / "tools" / "run-manager-runtime-acceptance.py"))[
        "_installed_tty_complete"
    ]
    identity, original = _receipt_fixture()
    report = tmp_path / "receipt.json"
    assert not validate(report, identity)
    for content in ('{"schema":', " " * ((1 << 20) + 1), json.dumps(original).replace(
        '"complete": true', '"complete": false, "complete": true', 1,
    )):
        report.write_text(content, encoding="utf-8")
        assert not validate(report, identity)
    report.write_text(json.dumps(original), encoding="utf-8")
    link = tmp_path / "linked.json"
    try:
        link.symlink_to(report)
    except OSError:
        return
    assert not validate(link, identity)


def test_actual_installed_project_binstubs_reach_input_ready_windows_tty(record_testsuite_property):
    """The hosted native selector executes installation, not receipt fixtures."""
    import runpy

    from agent_procutil import no_window_kwargs
    from plugin_test_containment import TestSandbox

    def git(argument):
        return subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", argument], check=True,
            capture_output=True, text=True, timeout=15, env=_git_environment(),
            **no_window_kwargs(),
        ).stdout.strip()

    commit = git("HEAD")
    receipt = REPO / "worktree-manager" / "runtime-source.json"
    source = json.loads(receipt.read_text(encoding="utf-8"))["source_commit"] if receipt.is_file() else commit
    identity = {
        "candidate_commit": commit, "candidate_tree": git("HEAD^{tree}"),
        "source_commit": source, "main_before": git("HEAD^"),
        "run_id": int(os.environ.get("GITHUB_RUN_ID", "1")),
        "run_attempt": int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")),
    }
    namespace = runpy.run_path(str(REPO / "tools" / "run-manager-runtime-acceptance.py"))
    with TestSandbox(prefix="tty-", dir=REPO) as raw:
        root = Path(raw) / "it"
        report = root / "installed-tty-result.json"
        result = namespace["_installed_tty_acceptance"](root, report, identity)
        document = report.read_text(encoding="utf-8")
        record_testsuite_property("installed-tty-native-result", document)
        diagnostics = _failure_evidence(root) if result else {}
        if diagnostics:
            record_testsuite_property("installed-tty-native-diagnostics", json.dumps(diagnostics))
        assert result == 0, document + "\n" + json.dumps(diagnostics, indent=2)
        assert namespace["_installed_tty_complete"](report, identity)


def test_failure_evidence_preserves_inner_refusal_before_giant_outer_argv(tmp_path):
    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    path = diagnostics / "install-engine.stderr.txt"
    content = (
        "initial preparation\n" * 300
        + "RuntimeError: controlled inner installation refusal\n"
        + "subprocess.CalledProcessError: Command [" + "'long-outer-argument'," * 3000
        + "] returned non-zero exit status 1\n"
    ).encode()
    path.write_bytes(content)
    excerpt = _diagnostic_excerpt(path, 4096)
    assert "controlled inner installation refusal" in excerpt
    assert "initial preparation" in excerpt and "returned non-zero exit status 1" in excerpt
    assert len(excerpt.encode()) < 4608
    evidence = _failure_evidence(tmp_path)["install-engine.stderr.txt"]
    assert evidence["sha256"] == _digest(content) and evidence["bytes"] == len(content)
    assert "controlled inner installation refusal" in evidence["text"]


def test_failure_evidence_retains_complete_bounded_stderr_and_refuses_oversize(tmp_path):
    import pytest

    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    path = diagnostics / "install-engine.stderr.txt"
    content = b"RuntimeError: controlled native error\nTraceback follows\n"
    path.write_bytes(content)
    evidence = _failure_evidence(tmp_path)["install-engine.stderr.txt"]
    assert evidence["text"].encode() == content and evidence["truncated"] is False
    path.write_bytes(b"x" * ((1 << 20) + 1))
    with pytest.raises(ValueError, match="retained bound"):
        _failure_evidence(tmp_path)


def test_consumer_exit_evidence_precedes_owned_job_cleanup(tmp_path, monkeypatch):
    _consumer_exit_case(tmp_path, monkeypatch, pending=False)


def test_consumer_exit_after_pending_probe_preserves_pre_input_evidence(tmp_path, monkeypatch):
    _consumer_exit_case(tmp_path, monkeypatch, pending=True)


def _consumer_exit_case(tmp_path, monkeypatch, *, pending):
    from types import SimpleNamespace

    import _terminal_console_fixture
    import pytest
    from agent_procutil import _terminal_offer

    request = tmp_path / "ce-terminal-fixture" / "request.json"
    request.parent.mkdir()
    request.touch()
    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    exit_path = diagnostics / "cmd.exit.json"
    cleaned = []

    class FailedConsole:
        process = SimpleNamespace(pid=100, process=1000)
        output = b"installed Picker frame"
        read_error = None
        input_sent = False

        def __init__(self, *_args, **_kwargs):
            self.job = SimpleNamespace(terminate_and_wait=self.cleanup)

        def cleanup(self, _timeout):
            evidence = json.loads(exit_path.read_text(encoding="utf-8"))
            assert evidence["input_sent"] is (not pending)
            assert evidence["exit_code"] == 0xFFFFFFFF
            assert evidence["retained_descendants"] == {
                "101": {"name": "manager", "birth": 1020, "exit_code": 7},
                "102": {"name": "engine", "birth": 1030, "exit_code": None},
            }
            cleaned.append(True)

        def send(self, value):
            assert value == b"q"
            self.input_sent = True

        def exited(self, handle):
            return pending or self.input_sent

        def wait(self, _timeout):
            return 0xFFFFFFFF

        def birth(self, handle):
            return handle + 10

        def exit_code(self, handle):
            assert not cleaned
            return 7 if handle == 1010 else None

        def close(self):
            assert cleaned

    monkeypatch.setattr(_terminal_console_fixture, "Console", FailedConsole)
    monkeypatch.setattr(_terminal_offer, "validate", lambda _: (request, {"host_pid": 101}))

    def probe(_console, _manager, _engine, _observations, retained):
        sampled = {101: ("manager", 1010), 102: ("engine", 1020)}
        retained.update(sampled)
        return None if pending else (sampled, {})

    monkeypatch.setitem(globals(), "_installed_picker", probe)
    label = "rendered project Picker" if pending else "keyboard-driven quit dialog"
    with pytest.raises(RuntimeError, match="Consumer exited 4294967295 before " + label):
        _witness("cmd", tmp_path / "project.cmd", {"TEMP": str(tmp_path)}, tmp_path,
                 diagnostics, tmp_path / "manager", tmp_path / "engine")
    assert _failure_evidence(tmp_path)["cmd.exit.json"]["truncated"] is False


def test_retained_engine_source_uses_published_snapshot_not_runtime_slot(tmp_path):
    import pytest

    receipt = {
        "runtime_version": "1.2.3+source.fixture", "source_commit": "a" * 40,
        "assembly_hash": "b" * 64,
    }
    slot = tmp_path / "versions" / (receipt["runtime_version"] + ".profile.1234567890abcdef")
    slot.mkdir(parents=True)
    snapshot = tmp_path / "snapshots" / receipt["runtime_version"]
    snapshot.mkdir(parents=True)
    path = snapshot / "runtime-source.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    _verify_retained_source(tmp_path, slot, "agent-worktrees", receipt)
    assert not (slot / "runtime-source.json").exists()
    path.write_text(json.dumps({**receipt, "source_commit": "c" * 40}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="another source identity"):
        _verify_retained_source(tmp_path, slot, "agent-worktrees", receipt)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        _verify_retained_source(tmp_path, slot, "agent-worktrees", receipt)


def test_command_failure_preserves_stdout_only_registration_refusal(tmp_path):
    stdout, stderr = tmp_path / "stdout.txt", tmp_path / "stderr.txt"
    stdout.write_text("Registering project: fixture\nRegistration refused: controlled scope error\n",
                      encoding="utf-8")
    stderr.write_bytes(b"")
    message = _command_failure("register-project", 1, stdout, stderr)
    assert "controlled scope error" in message and "stderr_bytes=0" in message
    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    (diagnostics / "register-project.stdout.txt").write_bytes(stdout.read_bytes())
    (diagnostics / "register-project.stderr.txt").write_bytes(stderr.read_bytes())
    evidence = _failure_evidence(tmp_path)
    assert evidence["register-project.stdout.txt"]["text"].encode("utf-8") == stdout.read_bytes()
    assert evidence["register-project.stderr.txt"]["bytes"] == 0


def test_representative_harness_is_admissible_without_agent_launch_intent(tmp_path, monkeypatch):
    import pytest

    monkeypatch.syspath_prepend(str(REPO / "libs" / "repository-policy" / "src"))
    from repository_policy import AgentHostingDenied, require_agent_hosting

    project = tmp_path / "harness"
    project.mkdir()
    (project / ".git").mkdir()
    marketplace = tmp_path / "marketplace"
    manifest = marketplace / ".github" / "plugin" / "marketplace.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"name": "fixture-marketplace"}), encoding="utf-8")
    command = _harness_registration(tmp_path / "installed" / "python.exe", project, marketplace)
    policy = require_agent_hosting(project, operation="project adoption")
    assert policy.expose_agent is True
    assert command[4:7] == ["agent_worktrees", "register", "tty-harness"]
    assert "--no-agent" in command and "--agent" not in command and "--headless" not in command
    (project / ".agent-worktrees" / "config.yaml").write_text(
        "expose_agent: false\n", encoding="utf-8",
    )
    with pytest.raises(AgentHostingDenied, match="project adoption refused"):
        require_agent_hosting(project, operation="project adoption")


def test_native_cmd_project_binstub_handles_space_path(tmp_path):
    """Actual CMD/ConPTY invocation only, not installation or Picker acceptance."""
    directory = tmp_path / "project command"
    directory.mkdir()
    stub = directory / "fixture project.cmd"
    stub.write_bytes(b"@echo off\r\necho NATIVE PROJECT COMMAND REACHED\r\nexit /b 7\r\n")
    with _owned_console(_project_entry_command("cmd", stub), env=dict(os.environ), cwd=directory) as console:
        code = console.wait(5000)
    assert code == 7, console.output.decode("utf-8", "replace")
    assert b"NATIVE PROJECT COMMAND REACHED" in console.output


def test_picker_readiness_accepts_tab_casing_but_requires_project_and_alternate_screen():
    for label in (b"Worktrees", b"WORKTREES"):
        assert _picker_rendered(b"\x1b[?1049hWorktree Picker - tty-harness\n" + label)
    for output in (
        b"tty-harness WORKTREES",
        b"\x1b[?1049hWorktree Picker - tty-harness\nResolving project binding",
        b"\x1b[?1049hWorktree Manager\nWORKTREES",
    ):
        assert not _picker_rendered(output)


def test_rendered_picker_waits_for_its_live_installed_process(monkeypatch, tmp_path):
    from types import SimpleNamespace

    manager, engine = tmp_path / "manager" / "python.exe", tmp_path / "engine" / "python.exe"
    snapshots = iter(({1: ("engine", 11)}, {1: ("engine", 11), 2: ("manager", 22)}))
    console = SimpleNamespace(
        output=b"\x1b[?1049hWorktree Picker - tty-harness\nWORKTREES",
        pin_descendants=lambda: next(snapshots), exited=lambda _handle: False,
        birth=lambda handle: handle * 100,
    )
    monkeypatch.setitem(_installed_picker.__globals__, "_live_image",
                        lambda _console, handle: engine if handle == 11 else manager)
    observed, retained = {}, {}
    assert _installed_picker(console, manager, engine, observed, retained) is None
    assert retained == {1: ("engine", 11)}
    assert observed == {"1": {"name": "engine", "image": str(engine), "live": True}}
    descendants, pinned = _installed_picker(console, manager, engine, observed, retained)
    assert descendants[2] == ("manager", 22)
    assert pinned[str(manager.resolve())] == {"pid": 2, "birth": 2200}


def test_rendered_picker_does_not_accept_foreign_or_exited_installed_image(monkeypatch, tmp_path):
    from types import SimpleNamespace

    manager, engine = tmp_path / "manager" / "python.exe", tmp_path / "engine" / "python.exe"
    console = SimpleNamespace(
        output=b"\x1b[?1049hWorktree Picker - tty-harness\nWORKTREES",
        pin_descendants=lambda: {1: ("python.exe", 11)}, exited=lambda _handle: False,
        birth=lambda _handle: 100,
    )
    owner = _installed_picker.__globals__
    observed = {}
    monkeypatch.setitem(owner, "_live_image", lambda *_: tmp_path / "foreign" / "python.exe")
    assert _installed_picker(console, manager, engine, observed, {}) is None
    monkeypatch.setitem(owner, "_live_image", lambda *_: manager)
    console.exited = lambda _handle: True
    assert _installed_picker(console, manager, engine, observed, {}) is None


def test_rendered_picker_does_not_turn_live_image_query_error_into_pending(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import pytest

    console = SimpleNamespace(
        output=b"\x1b[?1049hWorktree Picker - tty-harness\nWORKTREES",
        pin_descendants=lambda: {1: ("python.exe", 11)},
    )
    failure = PermissionError("still-live image unavailable")

    def unreadable(*_):
        raise failure

    monkeypatch.setitem(_installed_picker.__globals__, "_live_image", unreadable)
    with pytest.raises(PermissionError) as caught:
        _installed_picker(console, tmp_path / "manager", tmp_path / "engine", {}, {})
    assert caught.value is failure


def test_process_image_attribution_ignores_only_confirmed_exited_handles(monkeypatch):
    from types import SimpleNamespace

    import pytest

    owner = _live_image.__globals__
    image = Path("installed-manager-python.exe")
    handle = object()
    calls = []
    states = iter((False,))
    console = SimpleNamespace(
        exited=lambda actual, timeout=0: next(states) if actual is handle else None,
    )

    def query(actual_console, actual_handle):
        assert actual_console is console and actual_handle is handle
        calls.append(actual_handle)
        return image

    monkeypatch.setitem(owner, "_image", query)
    assert _live_image(console, handle) == image
    states = iter((True,))
    assert _live_image(console, handle) is None
    assert calls == [handle]

    error = OSError("controlled image query failure")

    def failed_query(*_args):
        raise error

    monkeypatch.setitem(owner, "_image", failed_query)
    states = iter((False, True))
    assert _live_image(console, handle) is None
    states = iter((False, False))
    with pytest.raises(OSError) as caught:
        _live_image(console, handle)
    assert caught.value is error


def test_process_image_attribution_waits_only_for_retained_exit(monkeypatch):
    from types import SimpleNamespace

    import pytest

    owner = _live_image.__globals__
    handle = object()
    error = PermissionError("controlled image query failure during process exit")
    error.winerror = 5

    def failed_query(console, actual):
        assert actual is handle
        raise error

    monkeypatch.setitem(owner, "_image", failed_query)
    monkeypatch.setitem(owner, "time", SimpleNamespace(monotonic=lambda: 10.0))
    for deadline, limit in ((None, 250), (10.02, 20), (9.0, 0)):
        checks = []

        def exited(actual, timeout=0):
            assert actual is handle
            checks.append(timeout)
            return timeout > 0

        monkeypatch.setitem(owner, "_WORK_DEADLINE", deadline)
        console = SimpleNamespace(exited=exited)
        if limit:
            assert _live_image(console, handle) is None
        else:
            with pytest.raises(PermissionError) as caught:
                _live_image(console, handle)
            assert caught.value is error
        assert len(checks) == 2 and checks[0] == 0
        assert 0 < checks[1] <= limit if limit else checks[1] == 0


def test_sparse_candidate_materializes_invocation_admission_toolkit(tmp_path):
    from materialize_main import materialize_payload_invocation_into
    import launch_wrapper_assets_ref as assets

    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    env = _git_environment()
    env.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path))
    commit = _run(["git", "rev-parse", "HEAD"], env, REPO, diagnostics, "source-head")
    tree = _run(["git", "rev-parse", "HEAD^{tree}"], env, REPO, diagnostics, "source-tree")
    identity = {"candidate_commit": commit, "candidate_tree": tree, "source_commit": commit}

    snapshot = _candidate_snapshot(tmp_path, identity, env, diagnostics)
    for manifest in assets.load_manifest_groups(
        snapshot / "worktree-manager", manifest_name=assets.PACKAGED_MANIFEST_NAME,
    ):
        for name in manifest.files:
            relative = manifest.source_relative(name)
            assert (snapshot / relative).read_bytes() == (REPO / relative).read_bytes()
    consumer = snapshot / "plugins" / "agent-worktrees"
    log = materialize_payload_invocation_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=snapshot,
    )
    assert log and not any(entry.startswith("SKIP ") for entry in log), log
    toolkit = consumer / "scripts" / "_runtime_admission"
    inventory = json.loads((toolkit / "toolkit.json").read_text(encoding="utf-8"))
    assert inventory["files"]
    assert all((toolkit / relative).is_file() for relative in inventory["files"])
    resource = Path("plugins") / "agent-containers" / "setup.py"
    assert (toolkit / resource).read_bytes() == (
        snapshot / resource
    ).read_bytes()
    (snapshot / resource).unlink()
    refused = materialize_payload_invocation_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=snapshot,
    )
    assert len(refused) == 1 and refused[0].startswith("SKIP Invocation materialization:")


def test_unreaped_error_preserves_enclosing_sandboxes_and_original_exception(tmp_path, monkeypatch):
    import runpy
    from types import SimpleNamespace

    import pytest
    from plugin_test_containment import TestSandbox as Sandbox
    from plugin_test_containment import UnreapedProcessError

    for mode in ("ordinary", "diagnostic-copy-fails", "receipt-write-fails",
                 "job-timeout", "job-runtime", "job-assertion"):
        namespace = runpy.run_path(str(REPO / "tools" / "run-manager-runtime-acceptance.py"))
        accept = namespace["_installed_tty_acceptance"]
        owner = accept.__globals__
        monkeypatch.setitem(owner, "sys", SimpleNamespace(platform="win32", executable=sys.executable))
        monkeypatch.setitem(owner, "runpy", SimpleNamespace(
            run_path=lambda _path: {"_host_state": dict},
        ))
        monkeypatch.setitem(owner, "isolated_environment", lambda *_args, **_kwargs: {})
        job = object()
        original = UnreapedProcessError("controlled unconfirmed cleanup", job=job)
        raw_error = {
            "job-timeout": TimeoutError, "job-runtime": RuntimeError,
            "job-assertion": AssertionError,
        }.get(mode)
        if raw_error is not None:
            raw_error = raw_error("controlled native cleanup failure")

            class FailedJob:
                error = raw_error

                def terminate_and_wait(self, _timeout):
                    raise self.error
            job = FailedJob()
        observed = {}

        def unsettled(_arguments, _observed=observed, _error=original,
                      _raw_error=raw_error, _job=job, **options):
            sandbox = options["sandbox"]
            _observed["inner"] = sandbox
            (sandbox / "pending-owner.txt").write_text("no process launched; test-owned state")
            (sandbox / "diagnostics").mkdir()
            (sandbox / "diagnostics" / "failure.txt").write_text("controlled diagnostic")
            if _raw_error is None:
                _observed["raised"] = _error
                raise _error
            try:
                _confirm_job_empty(_job, 5, "controlled Job")
            except UnreapedProcessError:
                _observed["raised"] = sys.exc_info()[1]
                raise

        monkeypatch.setitem(owner, "run_contained", unsettled)
        if mode == "diagnostic-copy-fails":
            def failed_copy(*_args):
                raise OSError("controlled diagnostic copy failure")
            monkeypatch.setitem(owner, "shutil", SimpleNamespace(copytree=failed_copy))
        outer = Sandbox(prefix="unreaped-unit-", dir=tmp_path)
        try:
            with pytest.raises(UnreapedProcessError) as caught, outer as raw:
                root = Path(raw) / "it"
                report = root / "installed-tty-result.json"
                output = report
                if mode == "receipt-write-fails":
                    class FailedReceipt:
                        parent = root

                        def write_text(self, *_args, **_kwargs):
                            raise OSError("controlled receipt write failure")
                    output = FailedReceipt()
                identity, _ = _receipt_fixture()
                accept(root, output, identity)
            assert caught.value is observed["raised"] and caught.value.job is job
            if raw_error is not None:
                assert caught.value.__cause__ is raw_error
            assert outer.retained and Path(outer.name).is_dir()
            assert (observed["inner"] / "pending-owner.txt").is_file()
            if mode != "receipt-write-fails":
                evidence = json.loads(report.read_text(encoding="utf-8"))
                assert evidence["complete"] is False
                assert evidence["cleanup"]["owned_processes_reaped"] is False
        finally:
            # No process was launched: retire only these synthetic retained files.
            outer._rmtree(outer.name)


def test_negative_control_does_not_accept_unreaped_error(tmp_path):
    import pytest
    from plugin_test_containment import UnreapedProcessError

    interpreter = tmp_path / "python.exe"
    interpreter.write_bytes(b"unit fixture")
    original = UnreapedProcessError("controlled unsettled terminal", job=object())

    def unsettled():
        raise original

    with pytest.raises(UnreapedProcessError) as caught:
        _negative_control(interpreter, unsettled)
    assert caught.value is original
    assert interpreter.read_bytes() == b"unit fixture"
    assert not interpreter.with_suffix(".negative-control").exists()


def test_negative_control_accepts_only_documented_installed_consumer_rejection(tmp_path):
    import pytest

    interpreter = tmp_path / "python.exe"
    interpreter.write_bytes(b"unit fixture")
    for kind in (RuntimeError, TimeoutError, AssertionError):
        original = kind("controlled infrastructure failure")

        def fail(error=original):
            raise error

        with pytest.raises(kind) as caught:
            _negative_control(interpreter, fail)
        assert caught.value is original
        assert interpreter.read_bytes() == b"unit fixture"
    output = b"No usable Worktree Manager is available right now.\n"
    refusal = _ConsumerExited("rendered project Picker", 1, True)
    assert _negative_control(interpreter, lambda: _classify_consumer_exit(refusal, output))
    for exit_error in (
        _ConsumerExited("rendered project Picker", 0, True),
        _ConsumerExited("rendered project Picker", 1, False),
        _ConsumerExited("keyboard-driven quit dialog", 1, True),
    ):
        with pytest.raises(RuntimeError) as caught:
            _negative_control(
                interpreter, lambda error=exit_error: _classify_consumer_exit(error, output),
            )
        assert not isinstance(caught.value, _InstalledLaunchRejected)


def test_terminal_teardown_errors_remain_unreaped_not_negative_success(tmp_path, monkeypatch):
    import _terminal_console_fixture
    import pytest
    from plugin_test_containment import TestSandbox as Sandbox
    from plugin_test_containment import UnreapedProcessError

    for kind in (TimeoutError, RuntimeError, AssertionError):
        raw_error = kind("controlled terminal teardown failure")

        class FailedConsole:
            job = None
            output = b"No usable Worktree Manager is available right now.\n"
            error = raw_error

            def __init__(self, *_args, **_kwargs):
                pass

            def close(self):
                raise self.error
        monkeypatch.setattr(_terminal_console_fixture, "Console", FailedConsole)
        owner = Sandbox(prefix="terminal-unit-", dir=tmp_path)
        try:
            with pytest.raises(UnreapedProcessError) as caught, owner as raw:
                root = Path(raw)
                interpreter = root / "python.exe"
                interpreter.write_bytes(b"unit fixture")

                def rejected(cwd=root):
                    with _owned_console(["unit"], env={}, cwd=cwd):
                        raise _ConsumerExited("rendered project Picker", 1, True)
                _negative_control(interpreter, rejected)
            assert caught.value.__cause__ is raw_error
            assert owner.retained and (root / "python.exe").is_file()
        finally:
            # No native console was launched in this bookkeeping regression.
            owner._rmtree(owner.name)


def test_inherited_git_context_cannot_redirect_private_repository(tmp_path, monkeypatch):
    from agent_procutil import no_window_kwargs

    clean = _git_environment()
    clean.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    caller, target = tmp_path / "caller", tmp_path / "target"
    for path in (caller, target):
        path.mkdir()
        subprocess.run(["git", "-C", str(path), "init", "-q"], env=clean,
                       check=True, timeout=10, **no_window_kwargs())
    foreign_index = caller / "foreign-index"
    foreign_index.write_bytes(b"untouched caller index")
    foreign_config = caller / "foreign-config"
    foreign_config.write_text("[core]\nworktree = " + caller.as_posix() + "\n", encoding="utf-8")
    (target / "probe.txt").write_text("private fixture")
    for key, value in {
        "GIT_DIR": str(caller / ".git"), "GIT_WORK_TREE": str(caller),
        "GIT_INDEX_FILE": str(foreign_index), "GIT_CONFIG": str(foreign_config),
        "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.worktree",
        "GIT_CONFIG_VALUE_0": str(caller),
        "GIT_CONFIG_PARAMETERS": "'core.worktree=" + caller.as_posix() + "'",
        "GIT_OBJECT_DIRECTORY": str(caller / ".git" / "objects"),
        "GIT_NAMESPACE": "wrong-candidate", "GIT_CONFIG_GLOBAL": str(foreign_config),
        "GIT_CONFIG_SYSTEM": str(foreign_config),
    }.items():
        monkeypatch.setenv(key, value)
    for env in (_git_environment(), _environment(tmp_path / "environment")):
        result = subprocess.run(
            ["git", "-C", str(target), "rev-parse", "--show-toplevel"], env=env,
            check=True, capture_output=True, text=True, timeout=10, **no_window_kwargs(),
        )
        assert Path(result.stdout.strip()).resolve() == target.resolve()
        subprocess.run(["git", "-C", str(target), "add", "probe.txt"], env=env,
                       check=True, timeout=10, **no_window_kwargs())
    assert (target / ".git" / "index").is_file()
    assert foreign_index.read_bytes() == b"untouched caller index"


if "pytest" in sys.modules:
    import pytest

    test_actual_installed_project_binstubs_reach_input_ready_windows_tty = pytest.mark.timeout(780)(
        pytest.mark.skipif(os.name != "nt", reason="Native Windows ConPTY qualification")(
            test_actual_installed_project_binstubs_reach_input_ready_windows_tty,
        ),
    )
    test_native_cmd_project_binstub_handles_space_path = pytest.mark.skipif(
        os.name != "nt", reason="Native Windows CMD/ConPTY invocation",
    )(test_native_cmd_project_binstub_handles_space_path)
    test_inherited_git_context_cannot_redirect_private_repository = pytest.mark.skipif(
        os.name != "nt", reason="Native Windows fixture environment",
    )(test_inherited_git_context_cannot_redirect_private_repository)
    test_sparse_candidate_materializes_invocation_admission_toolkit = pytest.mark.timeout(180)(
        pytest.mark.skipif(os.name != "nt", reason="Native Windows candidate preparation owner")(
            test_sparse_candidate_materializes_invocation_admission_toolkit,
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
