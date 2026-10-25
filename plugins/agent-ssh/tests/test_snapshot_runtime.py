"""Real snapshot dispatch through canonical, disposable installation owners."""

from __future__ import annotations

import importlib.util
import compileall
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import venv
from types import SimpleNamespace

import pytest
from agent_procutil.owned_process import run_caller_owned

_PLUGIN = Path(__file__).resolve().parents[1]
_REPO = _PLUGIN.parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


launcher = load(_PLUGIN / "scripts" / "snapshot_runtime.py", "snapshot_launcher_test")
writer = load(_PLUGIN / "scripts" / "write_session_guidance.py", "snapshot_writer_test")


def test_windowless_parent_retains_native_pipe_delivery(tmp_path, monkeypatch):
    monkeypatch.setattr(writer.sys, "executable", str(tmp_path / "pythonw.exe"))
    argv = writer._contributor_argv(_PLUGIN, "emit-mesh-pointer")
    assert argv[0] == str(tmp_path / "python.exe")
    assert argv[1:4] == ["-I", "-X", "utf8"]


def test_legacy_decision_and_slot_selection_belong_to_packaged_owners(tmp_path, monkeypatch):
    calls = []

    class Primitive:
        def probe_legacy_entrypoint(self, **kwargs):
            calls.append(kwargs)
            return {"probeReason": "legacy-active", "legacy": {"root": str(tmp_path)}}

    class Runtime:
        def resolve_python(self, root):
            assert root == tmp_path
            return tmp_path / "owned-python"

    monkeypatch.setattr(
        launcher,
        "load",
        lambda path, _: Primitive() if "installation-context" in str(path) else Runtime(),
    )
    command, environment = launcher.runtime_command(
        tmp_path,
        "agent-ssh",
        {
            "PYTHONPATH": "foreign",
            "PYTHONHOME": "foreign",
            "VIRTUAL_ENV": "foreign",
        },
    )
    assert command[:4] == [str(tmp_path / "owned-python"), "-I", "-X", "utf8"]
    assert calls[0]["plugin_id"] == "agent-ssh"
    assert not {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"} & environment.keys()
    assert environment["AGENT_SSH_NO_SELFPROVISION"] == "1"


@pytest.mark.parametrize(
    "reason", ["migration-required", "maintenance-active", "foreign-environment"]
)
def test_nonlegacy_decision_never_selects_a_legacy_runtime(tmp_path, monkeypatch, reason):
    class Primitive:
        def probe_legacy_entrypoint(self, **_):
            return {"probeReason": reason, "context": None}

    def imported(path, _):
        assert path.name != "versioned_runtime.py", "refusal fell back to legacy"
        return Primitive()

    monkeypatch.setattr(launcher, "load", imported)
    with pytest.raises(ValueError, match="refuses"):
        launcher.runtime_command(tmp_path, "agent-ssh", {})


def test_missing_completed_runtime_cannot_fall_back_to_running_interpreter(tmp_path, monkeypatch):
    class Primitive:
        def probe_legacy_entrypoint(self, **_):
            return {"probeReason": "legacy-active", "legacy": {"root": str(tmp_path)}}

        def resolve_python(self, _):
            return None

    monkeypatch.setattr(launcher, "load", lambda *_: Primitive())
    with pytest.raises(FileNotFoundError, match="completed runtime"):
        launcher.runtime_command(tmp_path, "agent-ssh", {})


def _purelib(python, *, scheme=None):
    code = (
        "import json,sys,sysconfig; "
        "options={'scheme':sys.argv[1]} if len(sys.argv)>1 else {}; "
        "print(json.dumps(sysconfig.get_path('purelib',**options)))"
    )
    result = run_caller_owned(
        [str(python), "-I", "-X", "utf8", "-c", code, *([scheme] if scheme else [])],
        timeout=5,
        max_stdout_bytes=8192,
        max_stderr_bytes=8192,
    )
    if result.returncode:
        raise ValueError("Selected interpreter purelib query failed")
    path = Path(json.loads(result.stdout))
    if not path.is_absolute():
        raise ValueError("Selected interpreter purelib must be absolute")
    return path


def test_selected_peer_purelib_uses_its_minor_not_the_runner_minor(monkeypatch):
    selected_python = sys.executable
    selected_version = sys.version_info
    runner_minor = 10 if selected_version.minor != 10 else 11
    module = sys.modules[__name__]
    monkeypatch.setattr(
        module,
        "sys",
        SimpleNamespace(
            executable=selected_python,
            version_info=SimpleNamespace(major=3, minor=runner_minor),
        ),
    )
    # Ask the real selected interpreter for a POSIX layout even on Windows;
    # no second host runtime or fabricated interpreter answer is required.
    path = _purelib(selected_python, scheme="posix_prefix")
    assert f"python{selected_version.major}.{selected_version.minor}" in str(path)
    assert f"python3.{runner_minor}/" not in path.as_posix()


def _cell(root, monkeypatch, *, host_context=False, legacy=False):
    original_home = Path.home()
    original_peer_site = None
    original_peer_python = None
    if host_context:
        runtime = launcher.load(
            _PLUGIN / "scripts" / "versioned_runtime.py", "native_fixture_runtime"
        )
        peer_python = runtime.resolve_python(original_home / ".agent-worktrees")
        assert peer_python is not None
        original_peer_python = peer_python
        original_peer_site = _purelib(peer_python)
    if legacy:
        home = root / "home"
        home.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
        monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
        monkeypatch.delenv("AGENT_RT_ROOT", raising=False)
        monkeypatch.setenv("AGENT_HOME", str(original_home if host_context else home))
        copilot = home / ".copilot"
        copilot.mkdir()
        monkeypatch.setenv("COPILOT_HOME", str(copilot))
        monkeypatch.setenv("COPILOT_CACHE_HOME", str(home / ".copilot-cache"))
        (copilot / "settings.json").write_text(
            json.dumps(
                {
                    "extraKnownMarketplaces": {
                        "snapshot-tools": {
                            "source": {"source": "github", "repo": "example/snapshot-tools"},
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        catalog = root / ".github" / "plugin"
        catalog.mkdir(parents=True)
        (catalog / "marketplace.json").write_text(
            json.dumps(
                {
                    "name": "snapshot-tools",
                    "owner": {"name": "example"},
                    "plugins": [
                        {
                            "name": name,
                            "source": f"./home/.copilot/installed-plugins/snapshot-tools/{name}",
                        }
                        for name in ("agent-ssh", "agent-worktrees")
                    ],
                }
            ),
            encoding="utf-8",
        )
        cached_catalog = (
            home / ".copilot-cache" / "marketplaces" / "snapshot-tools" / ".github" / "plugin"
        )
        cached_catalog.mkdir(parents=True)
        shutil.copyfile(catalog / "marketplace.json", cached_catalog / "marketplace.json")
    primitive = load(
        _PLUGIN / "scripts" / "installation-context" / "installation_context.py",
        "snapshot_fixture_primitive",
    )
    durable = root / "durable"
    contexts = {}
    payloads = {}
    for plugin in ("agent-ssh", "agent-worktrees"):
        payload = (
            home / ".copilot" / "installed-plugins" / "snapshot-tools" / plugin
            if legacy
            else root / "payloads" / plugin
        )
        source = _REPO / "plugins" / plugin
        shutil.copytree(source / "scripts", payload / "scripts")
        shutil.copytree(source / "src", payload / "src")
        shutil.copyfile(source / "plugin.json", payload / "plugin.json")
        shutil.copytree(source / "bin", payload / "bin")
        procutil = payload / "libs" / "agent-procutil" / "src" / "agent_procutil"
        procutil.mkdir(parents=True)
        shutil.copyfile(
            _REPO / "libs" / "agent-procutil" / "src" / "agent_procutil" / "__init__.py",
            procutil / "__init__.py",
        )
        environment = {
            k: v
            for k, v in os.environ.items()
            if k
            not in {
                "COPILOT_EXTENSIONS_CONTEXT",
                "COPILOT_PLUGIN_ROOT",
                "AGENT_RT_ROOT",
            }
        }
        if legacy:
            receipt = home / f".{plugin}" / "install.json"
            receipt.parent.mkdir(parents=True, exist_ok=True)
        else:
            context = primitive.stamp_context(
                payload_version="1.0.0",
                payload_origin="explicit",
                expected_namespace_generation=0 if not contexts else 1,
                expected_install_generation=0,
                payload_root=payload,
                plugin_id=plugin,
                durable_home=durable,
                source_descriptor={"kind": "github", "repo": "example/snapshot-tools"},
                marketplace_key="snapshot-tools",
                environment=environment,
            )
            receipt = Path(context["installReceipt"])
            primitive.compare_and_swap_activation(
                context=receipt,
                expected_marketplace_id=context["marketplaceId"],
                expected_plugin_id=plugin,
                expected_namespace_generation=1,
                expected_install_generation=1,
                expected_activation_generation=0,
                activation_mode="namespaced",
                activation_state="active",
                legacy_disposition="absent",
                legacy_probe={
                    "declared": True,
                    "result": "absent",
                    "checkedAt": "2026-01-01T00:00:00Z",
                },
                durable_home=durable,
                environment=environment,
            )
        slot = receipt.parent / "versions" / "1.0.0"
        native_peer = host_context and plugin == "agent-worktrees"
        if native_peer:
            subprocess.run(
                [str(original_peer_python), "-m", "venv", "--without-pip", str(slot)],
                check=True,
                capture_output=True,
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            site = slot / original_peer_site.relative_to(original_peer_python.parent.parent)
            # Preserve the independent peer's installed package/dependency bytes
            # and interpreter ABI instead of mixing in source dependencies.
            shutil.copytree(original_peer_site, site, dirs_exist_ok=True)
            shutil.rmtree(site / "agent_worktrees")
            shutil.copytree(
                payload / "src" / "agent_worktrees",
                site / "agent_worktrees",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            subprocess.run(
                [
                    str(slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")),
                    "-I",
                    "-m",
                    "compileall",
                    "-q",
                    str(site / "agent_worktrees"),
                ],
                check=True,
                capture_output=True,
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        else:
            venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(slot)
            site = slot / (
                "Lib/site-packages"
                if os.name == "nt"
                else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
            )
        # Materialize an installed package layout, not dozens of editable
        # source roots on sys.path (which has very different native I/O cost).
        ignore = shutil.ignore_patterns("__pycache__")
        if not native_peer:
            for source_root in (payload / "src", *(_REPO / "libs").glob("*/src")):
                for package in source_root.iterdir():
                    if package.is_dir():
                        shutil.copytree(
                            package, site / package.name, dirs_exist_ok=True, ignore=ignore
                        )
                    elif package.suffix == ".py":
                        shutil.copyfile(package, site / package.name)
            paths = [str(p) for p in sys.path if "site-packages" in p]
            (site / "snapshot-fixture.pth").write_text("\n".join(paths) + "\n", encoding="utf-8")
        (slot / ".install-complete.json").write_text(
            json.dumps(
                {
                    "version": "1.0.0",
                    "completed_at": "2026-01-01T00:00:00Z",
                    "pid": os.getpid(),
                }
            ),
            encoding="utf-8",
        )
        (receipt.parent / "current-version").write_text("1.0.0", encoding="utf-8")
        if not native_peer:
            compileall.compile_dir(site, quiet=1)
        contexts[plugin] = receipt
        payloads[plugin] = payload
    if not legacy:
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(contexts["agent-ssh"]))
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", str(payloads["agent-ssh"]))
    monkeypatch.setenv("AGENT_SSH_PAYLOAD_ROOT", str(payloads["agent-ssh"]))
    monkeypatch.setenv("AGENT_RT_ROOT", str(contexts["agent-ssh"].parent))
    return contexts, payloads


def _snapshot(home):
    content = (
        home
        / ".copilot"
        / "session-state"
        / "session-1"
        / "instructions"
        / "agent-ssh"
        / "session-guidance.instructions.md"
    ).read_text(encoding="utf-8")
    assert "session command catalog" in content
    assert len(content.encode("utf-8")) <= 4096
    values = [json.loads(part.split("\n```", 1)[0]) for part in content.split("```json\n")[1:]]
    return next(
        value
        for value in values
        if value.get("schema") == "copilot-extensions.ssh-session-snapshot"
    )


def _configured_caller(root):
    repo = root / "caller"
    state = root / "state"
    for path in (repo, state):
        path.mkdir()
        subprocess.run(
            ["git", "init", "-q", str(path)],
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    (repo / ".agent-worktrees").mkdir()
    (repo / ".agent-worktrees" / "config.yaml").write_text(
        "stateless: true\nrequires_external_state_root: true\n",
        encoding="utf-8",
    )
    registry = Path.home() / ".agent-worktrees"
    registry.mkdir(parents=True, exist_ok=True)
    (registry / "projects.yaml").write_text(
        json.dumps(
            {
                "projects": {"caller": {"anchor": str(repo)}},
            }
        ),
        encoding="utf-8",
    )
    (registry / "repos.yaml").write_text(
        json.dumps(
            {
                "repos": {
                    name: {
                        "class": "worktree",
                        "remote": f"https://github.com/example/{name}",
                        "windows": str(path),
                        "linux": str(path),
                        "macos": str(path),
                    }
                    for name, path in (("caller", repo), ("state", state))
                },
            }
        ),
        encoding="utf-8",
    )
    config = Path.home() / ".caller"
    config.mkdir()
    (config / "config.yaml").write_text(
        json.dumps(
            {
                "knowledge_repo": "state",
                "repos": {
                    "caller": {
                        "anchor": str(repo),
                        "worktree_root": str(root / "worktrees"),
                        "stateless": True,
                        "requires_external_state_root": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    machines = state / ".agent-worktrees"
    machines.mkdir()
    (machines / "machines.yaml").write_text(
        json.dumps(
            {
                "machines": {
                    "host": {
                        "ssh": {
                            "ready": True,
                            "environments": [
                                {"name": "linux", "alias": "host-a"},
                            ],
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return repo, machines / "machines.yaml"


@pytest.mark.timeout(150)
def test_real_native_writer_avoids_slow_shell_and_refreshes_external_snapshot(
    tmp_path, monkeypatch, capsys
):
    _, payloads = _cell(tmp_path / "cell", monkeypatch, legacy=True)
    home = tmp_path / "output"
    home.mkdir()
    repo, machines = _configured_caller(tmp_path)
    assert not (Path.home() / ".agent-worktrees" / "payload-dir").exists()
    monkeypatch.setenv("AGENT_SSH_NO_SELFPROVISION", "1")
    observed_calls = []
    owned = writer._run_owned_contributor

    def observed(*args, **kwargs):
        started = time.monotonic()
        try:
            result = owned(*args, **kwargs)
            observed_calls.append((round(time.monotonic() - started, 3), result.returncode))
            return result
        except Exception as error:
            observed_calls.append((round(time.monotonic() - started, 3), type(error).__name__))
            raise

    monkeypatch.setattr(writer, "_run_owned_contributor", observed)
    # A controlled slow shell reproduces the old route's deadline failure.
    shell_script = (
        payloads["agent-ssh"]
        / "scripts"
        / ("emit-mesh-pointer.ps1" if os.name == "nt" else "emit-mesh-pointer.sh")
    )
    shell_script.write_text("Start-Sleep -Seconds 11\n" if os.name == "nt" else "sleep 11\n")
    new_argv = writer._contributor_argv
    shell = (
        (shutil.which("pwsh") or shutil.which("powershell.exe"))
        if os.name == "nt"
        else shutil.which("bash")
    )
    old_argv = (
        [shell, "-NoProfile", "-File", str(shell_script)]
        if os.name == "nt"
        else [shell, str(shell_script)]
    )
    with monkeypatch.context() as old:
        old.setattr(
            writer,
            "_contributor_argv",
            lambda root, stem: old_argv if stem == "emit-mesh-pointer" else new_argv(root, stem),
        )
        assert writer.write_session_guidance(
            {"sessionId": "session-1", "cwd": str(repo)}, home=home
        )
        assert (
            "snapshot contributor failed or timed out"
            in (
                home
                / ".copilot/session-state/session-1/instructions/agent-ssh/session-guidance.instructions.md"
            ).read_text()
        )
    samples = []
    captured = []
    for alias in ("host-a", "host-b"):
        started = time.monotonic()
        assert writer.write_session_guidance(
            {"sessionId": "session-1", "cwd": str(repo)},
            home=home,
        )
        samples.append(time.monotonic() - started)
        try:
            value = _snapshot(home)
        except StopIteration:
            pytest.fail(f"contributor outcomes: {observed_calls}; writer samples: {samples}")
        assert value["connectivity"] == value["effective_routes"] == "unobserved"
        assert value["topology"]["status"] == "available", (value["state_root"], samples)
        assert value["topology"]["origin"] == "external-state"
        assert value["state_root"] == "bound"
        assert value["aliases"][0]["alias"] == alias
        captured.append(value["captured_at"])
        machines.write_text(machines.read_text().replace("host-a", "host-b"))
    assert captured[0] != captured[1]
    assert writer._SUBPROCESS_TIMEOUT_S == 10
    from agent_ssh import session_snapshot

    profiles = tmp_path / "profiles"
    profiles.mkdir()
    controls = load(_PLUGIN / "tests" / "test_session_snapshot.py", "snapshot_profile_controls")
    config_d = controls.fragment(profiles)
    before = {path: path.read_bytes() for path in profiles.rglob("*") if path.is_file()}
    shutil.rmtree(Path.home() / ".agent-worktrees" / "versions")
    unavailable = session_snapshot.capture(cwd=repo, config_d=config_d)
    assert unavailable["topology"]["status"] == unavailable["state_root"] == "unavailable"
    assert unavailable["profiles"]["validation"] == "non-executing-structure-and-source"
    assert unavailable["profiles"]["status"] != "not-inspected"
    assert before == {path: path.read_bytes() for path in profiles.rglob("*") if path.is_file()}
    # A recorded payload marker is not a readiness requirement for an already
    # completed, inventory-qualified legacy runtime.
    (Path.home() / ".agent-worktrees" / "payload-dir").write_text("relative-peer")
    manifest = payloads["agent-worktrees"] / "plugin.json"
    manifest.write_text(json.dumps({"name": "foreign-peer"}), encoding="utf-8")
    assert writer.write_session_guidance({"sessionId": "session-1", "cwd": str(repo)}, home=home)
    assert _snapshot(home)["topology"]["status"] == "refused"
    shutil.rmtree(payloads["agent-worktrees"])
    assert writer.write_session_guidance({"sessionId": "session-1", "cwd": str(repo)}, home=home)
    assert _snapshot(home)["topology"]["status"] == "unavailable"
    with capsys.disabled():
        print(
            json.dumps(
                {
                    "measurement": "canonical-legacy-source-writer",
                    "n": 2,
                    "samples_seconds": samples,
                    "cache": "not-controlled",
                    "phases": ["fresh", "resumed"],
                    "external_state": "available",
                    "composed_two_second_gate": "unqualified",
                }
            )
        )


@pytest.mark.timeout(150)
def test_explicit_native_owner_and_foreign_context_refusal(tmp_path, monkeypatch, capsys):
    from agent_ssh import _worktrees

    home = tmp_path / "home"
    home.mkdir()
    for name in ("HOME", "USERPROFILE", "AGENT_HOME"):
        monkeypatch.setenv(name, str(home))
    contexts, payloads = _cell(tmp_path / "cell", monkeypatch)
    command, environment = launcher.runtime_command(
        payloads["agent-ssh"],
        "agent-ssh",
        dict(os.environ),
    )
    assert Path(command[0]).is_relative_to(contexts["agent-ssh"].parent / "versions")
    assert environment["COPILOT_EXTENSIONS_CONTEXT"] == str(contexts["agent-ssh"])
    active = _worktrees.validate_context()
    repo, machines = _configured_caller(tmp_path)
    from agent_procutil.owned_process import run_caller_owned

    argv, selected = launcher.worktrees_command(payloads["agent-ssh"], active)
    prepare = (
        "import sys\nfrom pathlib import Path\n"
        "from agent_worktrees import registry_paths,project_state\n"
        "root=registry_paths.registry_root()\n"
        "assert root==Path(sys.argv[1])\n"
        "for name in ('projects.yaml','repos.yaml'):\n"
        " (root/name).write_bytes((Path.home()/'.agent-worktrees'/name).read_bytes())\n"
        "state=project_state.ensure_project_state('caller')\n"
        "(state/'config.yaml').write_bytes((Path.home()/'.caller/config.yaml').read_bytes())\n"
        "print('fixture-bound')\n"
    )
    prepared = run_caller_owned(
        [*argv[:-1], "-c", prepare, str(contexts["agent-worktrees"].parent)],
        cwd=repo,
        env=selected,
        timeout=15,
        max_stdout_bytes=1024,
        max_stderr_bytes=4096,
    )
    assert prepared.returncode == 0, prepared.stderr
    assert prepared.stdout.strip() == "fixture-bound"
    # Only canonical cell-local state may answer the selected peer. Leaving
    # duplicate legacy data could conceal a wrong-owner fallback.
    shutil.rmtree(home / ".agent-worktrees")
    shutil.rmtree(home / ".caller")
    samples = []
    for _ in range(2):
        started = time.monotonic()
        assert _worktrees.state_root(repo, active) == (machines.parent.parent, "bound")
        samples.append(round(time.monotonic() - started, 3))
    with capsys.disabled():
        print(
            json.dumps(
                {
                    "measurement": "canonical-namespaced-optional-state-root",
                    "n": 2,
                    "samples_seconds": samples,
                    "cache": "not-controlled",
                    "state_root": "bound",
                    "optional_peer_deadline_seconds": 5,
                }
            )
        )
    for field in ("generation", "namespaceGeneration", "sourceFingerprint"):
        restricted = {**active, field: "changed"}
        with pytest.raises(_worktrees.ContextRefused):
            _worktrees.state_root(repo, restricted)
    slot = Path(argv[0]).parent.parent
    site = slot / (
        "Lib/site-packages"
        if os.name == "nt"
        else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )
    api = site / "agent_worktrees" / "state_root_request.py"
    supported = api.read_bytes()
    api.unlink()
    try:
        assert _worktrees.state_root(repo, active) == (None, "unavailable")
    finally:
        api.write_bytes(supported)
    shutil.rmtree(contexts["agent-worktrees"].parent / "versions")
    with pytest.raises(_worktrees.ContextRefused):
        _worktrees.state_root(repo, active)
    contexts["agent-worktrees"].unlink()
    with pytest.raises(_worktrees.ContextRefused):
        _worktrees.state_root(tmp_path, active)
    shutil.rmtree(contexts["agent-worktrees"].parent)
    assert _worktrees.state_root(tmp_path, active) == (None, "unavailable")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(contexts["agent-worktrees"]))
    with pytest.raises((ValueError, RuntimeError)):
        launcher.runtime_command(payloads["agent-ssh"], "agent-ssh", dict(os.environ))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(contexts["agent-ssh"]))
    contexts["agent-ssh"].write_text("{}", encoding="utf-8")
    with pytest.raises((ValueError, RuntimeError)):
        launcher.runtime_command(payloads["agent-ssh"], "agent-ssh", dict(os.environ))


@pytest.mark.timeout(150)
def test_native_optional_deadline_reaps_actual_adapter_descendants(tmp_path, monkeypatch):
    from agent_ssh import _worktrees

    _, payloads = _cell(tmp_path / "cell", monkeypatch, legacy=True)
    repo, _ = _configured_caller(tmp_path)
    resolver = payloads["agent-ssh"] / "scripts" / "snapshot_runtime.py"
    original = resolver.read_text(encoding="utf-8")
    adapter = payloads["agent-ssh"] / "scripts" / "snapshot_worktrees.py"
    controls = load(
        _PLUGIN / "tests" / "test_write_session_guidance.py", "snapshot_cleanup_controls"
    )
    for phase in ("resolution", "execution"):
        pid_file = tmp_path / f"{phase}.pid"
        blocked = (
            "import os,subprocess,sys,time\nfrom pathlib import Path\n"
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],"
            "creationflags=0x08000000 if os.name=='nt' else 0)\n"
            f"Path({str(pid_file)!r}).write_text(str(p.pid))\n"
            "time.sleep(60)\n"
        )
        resolver.write_text(original, encoding="utf-8")
        if phase == "resolution":
            resolver.write_text(
                original
                + "\ndef worktrees_command(*args):\n"
                + "".join("    " + line + "\n" for line in blocked.splitlines()),
                encoding="utf-8",
            )
        else:
            adapter.write_text(blocked, encoding="utf-8")
        try:
            started = time.monotonic()
            assert _worktrees.state_root(repo, None) == (None, "unavailable")
            assert time.monotonic() - started < 8  # 5s plus owned cleanup, not a new deadline.
            assert pid_file.is_file()
            assert not controls._pid_running(int(pid_file.read_text()))
        finally:
            if pid_file.exists() and controls._pid_running(int(pid_file.read_text())):
                os.kill(int(pid_file.read_text()), __import__("signal").SIGTERM)


@pytest.mark.skipif(
    not os.environ.get("AGENT_SSH_NATIVE_CALLER"),
    reason="requires an explicitly selected, configured native caller",
)
@pytest.mark.timeout(150)
def test_native_configured_external_state_source_consumer(tmp_path, monkeypatch, capsys):
    caller = Path(os.environ["AGENT_SSH_NATIVE_CALLER"]).resolve(strict=True)
    assert caller.is_dir()
    _cell(tmp_path / "cell", monkeypatch, host_context=True, legacy=True)
    output = tmp_path / "output"
    output.mkdir()
    samples, captured = [], []
    for phase in ("fresh", "resumed"):
        started = time.monotonic()
        assert writer.write_session_guidance(
            {"sessionId": "session-1", "cwd": str(caller)}, home=output
        )
        samples.append(round(time.monotonic() - started, 3))
        value = _snapshot(output)
        assert value["topology"]["status"] == "available", (value["state_root"], samples)
        assert value["topology"]["origin"] == "external-state"
        assert value["state_root"] == "bound"
        assert value["connectivity"] == value["effective_routes"] == "unobserved"
        captured.append(value["captured_at"])
    assert captured[0] != captured[1]
    with capsys.disabled():
        print(
            json.dumps(
                {
                    "measurement": "native-configured-external-state-source-consumer",
                    "n": 2,
                    "samples_seconds": samples,
                    "phases": ["fresh", "resumed"],
                    "cache": "not-controlled",
                    "contributors": "actual",
                    "topology": "available",
                    "same_session_refreshed": True,
                    "contributor_deadline_seconds": 10,
                    "optional_peer_deadline_seconds": 5,
                    "composed_two_second_gate": "unqualified",
                }
            )
        )
