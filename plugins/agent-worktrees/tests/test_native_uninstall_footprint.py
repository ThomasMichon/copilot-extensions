"""Disposable native entrypoints; no host registration or runtime construction."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys

import pytest

from native_shell_support import admit_native_bash, bash_path, run_headless
from runtime_install_fixture import isolate_account_profile

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN / "scripts"
REPO = PLUGIN.parents[1]
pytestmark = pytest.mark.guard


def _load(path: Path):
    name = "_aw_uninstall_" + path.stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _host(shell: str) -> str:
    if shell == "bash":
        return admit_native_bash()
    executable = shutil.which(shell)
    if executable is None and os.name != "nt":
        pytest.skip(f"{shell} is not a POSIX platform requirement")
    assert executable is not None, f"Required native Windows host unavailable: {shell}"
    return executable


def _run(arguments, home, *, env=None, input=None, timeout=60, cwd=None):
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_PLUGIN_", "COPILOT_EXTENSIONS_", "AGENT_WORKTREES_"))
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home), LOCALAPPDATA=str(home / "AppData/Local"),
        PYTHONDONTWRITEBYTECODE="1", COPILOT_EXTENSIONS_TEST_CONTAINED="1",
    )
    environment.update(env or {})
    return run_headless(
        arguments, cwd=home if cwd is None else cwd, env=environment, input=input,
        capture_output=True, text=True, encoding="utf-8", timeout=timeout,
    )


@pytest.fixture
def native_install(tmp_path, tmp_path_factory, request):
    parameter = getattr(request, "param", "home")
    home = tmp_path_factory.mktemp("p") if parameter == "short-project-home" else tmp_path / parameter
    payload = home / ".copilot/installed-plugins/example/agent-worktrees"
    payload.mkdir(parents=True)
    for folder in ("scripts", "bin"):
        shutil.copytree(
            PLUGIN / folder, payload / folder,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    if parameter == "short-project-home":
        shutil.copytree(
            PLUGIN / "src", payload / "src",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    else:
        (payload / "src/agent_worktrees").mkdir(parents=True)
        shutil.copyfile(
            PLUGIN / "src/agent_worktrees/native_source_context.py",
            payload / "src/agent_worktrees/native_source_context.py",
        )
    for name in ("plugin.json", "pyproject.toml"):
        shutil.copyfile(PLUGIN / name, payload / name)
    for path in (REPO / "libs/installer-engine").iterdir():
        if path.is_file() and path.suffix in {".py", ".sh", ".ps1"}:
            shutil.copyfile(path, payload / "scripts" / path.name)
    isolate_account_profile(
        payload, home,
        runtime_package="agent_worktrees" if parameter == "short-project-home" else None,
    )
    (home / ".copilot/settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Example/Plugins"}},
        },
    }), encoding="utf-8")
    root = home / ".agent-worktrees"
    if parameter == "cold-home":
        return home, payload, root, [], {}
    root.mkdir()
    binding = [
        sys.executable, "-I", "-S", "-B", str(payload / "scripts/command_binding.py"),
        "--payload-root", str(payload), "--plugin-id", "agent-worktrees",
        "--legacy-root", str(root), "--source-payload-root", str(payload),
        "--project-root", str(home), "--publish-legacy", "--write-manifest",
    ]
    result = _run(binding, home, input=json.dumps({
        "schema_version": 3, "service": "agent-worktrees",
        "source": {"kind": "marketplace", "plugin": "agent-worktrees", "path": str(payload)},
    }))
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((root / "deploy-manifest.json").read_text())["commandBinding"]
    profile = json.loads((payload / "scripts/uninstall-profile.json").read_text())
    owned = []
    for folder, entries in (("", profile["root_entries"]), ("bin", profile["bin_entries"]),
                            ("scripts", profile["script_entries"])):
        for name in entries:
            if folder == "" and not name.endswith(".py"):
                continue
            source = payload / "scripts" / name
            if name == "native_source_context.py":
                source = payload / "src/agent_worktrees" / name
            target = root / folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            owned.append(target)
    for relative in (
        "versions/1.0.0/runtime.py", "snapshots/1.0.0/runtime.py",
        "current-version", "last-known-good", "stamped-version",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("1.0.0\n", encoding="utf-8")
        owned.append(path)
    durable = {}
    for relative in (
        "config.yaml", "repos.yaml", "projects.yaml", "sessions.db",
        "bin/unknown.py", "bin/conduct/operator.md", "scripts/unknown.sh",
        ".install.lock", ".retention-leases/witness", ".install-stage/witness",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"retained\0\xff")
        durable[path] = (path.read_bytes(), path.stat().st_dev, path.stat().st_ino)
    for relative in (
        ".fixture/worktrees/witness/session.json", ".tmux.conf", ".psmux.conf",
        "AppData/Local/Microsoft/Windows Terminal/Fragments/AgentWorktrees/witness",
    ):
        path = home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"operator witness")
        durable[path] = (path.read_bytes(), path.stat().st_dev, path.stat().st_ino)
    return home, payload, root, owned, durable


def _native_arguments(shell, executable, payload, root):
    if shell == "bash":
        return [executable, bash_path(executable, payload / "scripts/install.sh"),
                "uninstall", "--install-dir", bash_path(executable, root)]
    return [executable, "-NoProfile", "-NonInteractive", "-File",
            str(payload / "scripts/install.ps1"), "uninstall", "-InstallDir", str(root),
            "-BootstrapPython", sys.executable]


def _preserved(durable):
    for path, state in durable.items():
        assert (path.read_bytes(), path.stat().st_dev, path.stat().st_ino) == state


def _snapshot(root):
    files = {path: (path.read_bytes(), path.stat().st_dev, path.stat().st_ino)
             for path in root.rglob("*") if path.is_file()}
    directories = {path: (path.stat().st_dev, path.stat().st_ino)
                   for path in (root, *root.rglob("*")) if path.is_dir()}
    return files, directories


def _unchanged(snapshot, durable):
    files, directories = snapshot
    _preserved(files)
    _preserved(durable)
    for path, identity in directories.items():
        assert path.is_dir()
        assert (path.stat().st_dev, path.stat().st_ino) == identity


def _module_refusal(result):
    return re.fullmatch(
        r"Runtime uninstall refused: Unattributed module consumer \(pid=([1-9][0-9]*)\)",
        result.stderr.strip(),
    ) if result.returncode else None


def _skip_unrelated_module_consumer(result, snapshot, durable, child=None):
    refused = _module_refusal(result)
    if refused is None or (child is not None and int(refused[1]) == child.pid):
        return
    _unchanged(snapshot, durable)
    if child is not None:
        assert child.poll() is None
    pytest.skip(
        f"#8669: native guard refused unrelated module pid={refused[1]}; "
        "non-quiescent namespace, not positive Windows qualification"
    )


@pytest.mark.parametrize("message,code,accepted", [
    ("Runtime uninstall refused: Unattributed module consumer (pid=123)", 1, True),
    ("Runtime uninstall refused: Unattributed module consumer (pid=123)", 0, False),
    ("Runtime uninstall refused: Unattributed module consumer (pid=0)", 1, False),
    ("Runtime uninstall refused: Active rooted runtime consumer (pid=123)", 1, False),
    ("Runtime uninstall refused: Runtime lifecycle ticket is missing.", 1, False),
    ("Runtime uninstall refused: Foreign deployment manifest", 1, False),
    ("Runtime uninstall refused: Unattributed module consumer (pid=123)\nother error", 1, False),
])
def test_module_refusal_matcher_is_literal(message, code, accepted):
    from types import SimpleNamespace

    assert (_module_refusal(SimpleNamespace(stderr=message, returncode=code)) is not None) == accepted


def _assert_default_uninstall(native_install, shell):
    home, payload, root, owned, durable = native_install
    executable = _host(shell)
    arguments = _native_arguments(shell, executable, payload, root)
    for _ in range(2):
        before = _snapshot(root)
        result = _run(arguments, home)
        _skip_unrelated_module_consumer(result, before, durable)
        assert result.returncode == 0, result.stdout + result.stderr
        assert all(not path.exists() for path in owned)
        assert not (root / "deploy-manifest.json").exists()
        _preserved(durable)
    assert not (root / ".venv").exists()
    assert not (root / "tool").exists()


@pytest.mark.parametrize("shell", ["bash", "powershell", "pwsh"])
def test_native_default_uninstall_removes_writer_footprint(native_install, shell):
    _assert_default_uninstall(native_install, shell)


def test_native_bash_uninstall_does_not_discover_an_unused_repository(native_install):
    home, payload, root, owned, durable = native_install
    executable = _host("bash")
    binaries = home / "git-probe"
    binaries.mkdir()
    witness = home / "unexpected-git-call"
    git = binaries / "git"
    git.write_text(
        '#!/bin/bash\nprintf invoked > "$HOME/unexpected-git-call"\nexit 97\n',
        encoding="utf-8",
    )
    git.chmod(0o700)
    before = _snapshot(root)
    result = _run(
        _native_arguments("bash", executable, payload, root), home,
        env={"PATH": os.pathsep.join((str(binaries), os.environ["PATH"]))},
    )
    _skip_unrelated_module_consumer(result, before, durable)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not witness.exists()
    assert all(not path.exists() for path in owned)
    _preserved(durable)


@pytest.mark.parametrize("native_install", ["home's quoted space"], indirect=True)
@pytest.mark.parametrize("shell", ["bash", "powershell", "pwsh"])
def test_native_default_uninstall_accepts_quoted_space_paths(native_install, shell):
    _assert_default_uninstall(native_install, shell)


@pytest.mark.parametrize("shell", ["bash", "powershell", "pwsh"])
@pytest.mark.parametrize("unknown", [False, True])
def test_native_uninstall_no_provision_and_unknown_retry(native_install, shell, unknown):
    home, payload, _, _, durable = native_install
    root = home / "uninstalled-runtime"
    staging = home / ".agent-worktrees/.install-stage"
    staged = sorted(staging.iterdir())
    if unknown:
        root.mkdir()
        (root / "operator.py").write_bytes(b"unknown content")
        stat = (root / "operator.py").stat()
    arguments = _native_arguments(shell, _host(shell), payload, root)
    for _ in range(2):
        result = _run(arguments, home)
        assert result.returncode == 0, result.stdout + result.stderr
        if unknown:
            path = root / "operator.py"
            assert list(root.iterdir()) == [path]
            assert path.read_bytes() == b"unknown content"
            assert (path.stat().st_dev, path.stat().st_ino) == (stat.st_dev, stat.st_ino)
        else:
            assert not root.exists()
        assert sorted(staging.iterdir()) == staged
        _preserved(durable)


@pytest.mark.parametrize("native_install", ["cold-home"], indirect=True)
@pytest.mark.parametrize("shell", ["bash", "powershell", "pwsh"])
def test_cold_default_uninstall_never_creates_runtime(native_install, shell: str) -> None:
    home, payload, root, _, _ = native_install
    before = _snapshot(home / ".copilot")
    for _ in range(2):
        result = _run(_native_arguments(shell, _host(shell), payload, root), home)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["status"] == "absent"
        assert not root.exists()
        assert not (home / ".local").exists()
        _unchanged(before, {})
        assert _snapshot(home / ".copilot") == before


def test_cold_uninstall_rejects_unknown_arguments_before_absent_return(native_install):
    home, payload, _, _, durable = native_install
    root = home / "absent-runtime"
    arguments = _native_arguments("bash", _host("bash"), payload, root) + ["--unknown-option"]
    result = _run(arguments, home)
    assert result.returncode != 0
    assert "Unknown option: --unknown-option" in result.stderr
    assert not root.exists()
    _preserved(durable)


def test_native_real_module_refusal_preserves_footprint(native_install):
    import subprocess

    home, payload, root, _, durable = native_install
    before = _snapshot(root)
    staged = sorted((root / ".install-stage").iterdir())
    child = subprocess.Popen(
        [sys.executable, "-I", "-S", "-B", "-c",
         "import sys; sys.stdin.buffer.read()", "agent_worktrees"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        result = _run(_native_arguments("pwsh", _host("pwsh"), payload, root), home)
        assert _module_refusal(result) is not None, result.stdout + result.stderr
        _unchanged(before, durable)
        assert sorted((root / ".install-stage").iterdir()) == staged
        assert child.poll() is None
    finally:
        child.communicate(input=b"", timeout=10)


@pytest.mark.parametrize("shell", ["bash", "powershell", "pwsh"])
def test_native_unattributed_lifecycle_reaches_consumer_admission(native_install, shell):
    home, payload, _, _, durable = native_install
    (home / ".copilot/settings.json").write_text("{}", encoding="utf-8")
    root = home / "unattributed-runtime"
    root.mkdir()
    result = _run([
        sys.executable, "-I", "-S", "-B", str(payload / "scripts/command_binding.py"),
        "--payload-root", str(payload), "--plugin-id", "agent-worktrees",
        "--legacy-root", str(root), "--source-payload-root", str(payload),
        "--project-root", str(home), "--publish-legacy", "--write-manifest",
    ], home, input=json.dumps({
        "schema_version": 3, "service": "agent-worktrees",
        "source": {"kind": "marketplace", "plugin": "agent-worktrees", "path": str(payload)},
    }))
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = root / "deploy-manifest.json"
    assert "commandBinding" not in json.loads(manifest.read_text())
    resolver = root / "bin/resolve-runtime.sh"
    resolver.parent.mkdir()
    shutil.copyfile(payload / "scripts/resolve-runtime.sh", resolver)
    before = {path: (path.read_bytes(), path.stat().st_dev, path.stat().st_ino)
              for path in (manifest, resolver)}
    result = _run(_native_arguments(shell, _host(shell), payload, root), home)
    output = result.stdout + result.stderr
    assert "unverifiable lifecycle ticket" not in output.lower()
    if result.returncode:
        # This host's real AW consumers remain blocking, never fixture exemptions.
        assert "consumer" in output, output
        _preserved(before)
    else:
        assert not resolver.exists()
        assert not manifest.exists()
    _preserved(durable)


def test_unknown_only_purge_still_checks_consumers(native_install):
    import subprocess

    home, payload, _, _, durable = native_install
    root = home / "unknown-only-runtime"
    root.mkdir()
    witness = root / "operator.py"
    witness.write_bytes(b"unknown content")
    before = {witness: (witness.read_bytes(), witness.stat().st_dev, witness.stat().st_ino)}
    child = subprocess.Popen(
        [sys.executable, "-I", "-S", "-B", "-c",
         "import sys; sys.stdin.buffer.read()", str(witness)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        arguments = _native_arguments("pwsh", _host("pwsh"), payload, root) + ["-RemoveConfig"]
        result = _run(arguments, home)
        assert result.returncode != 0
        assert "consumer" in result.stdout + result.stderr
        _preserved(before)
        _preserved(durable)
        assert child.poll() is None
    finally:
        child.communicate(input=b"", timeout=10)


@pytest.mark.parametrize("shell", ["bash", "powershell", "pwsh"])
@pytest.mark.parametrize("obstacle", ["legacy-lib", "foreign", "managed", "linked", "live"])
def test_native_uninstall_refuses_before_mutation(native_install, shell, obstacle):
    home, payload, root, owned, durable = native_install
    executable = _host(shell)
    if obstacle == "legacy-lib":
        (root / "lib").mkdir()
        (root / "lib/unknown.py").write_text("operator content", encoding="utf-8")
    elif obstacle == "foreign":
        manifest = root / "deploy-manifest.json"
        value = json.loads(manifest.read_text())
        value["service"] = "other-product"
        manifest.write_text(json.dumps(value), encoding="utf-8")
    elif obstacle == "managed":
        (root / "runtime-deployment.json").write_text("{}", encoding="utf-8")
    elif obstacle == "linked":
        alias = root / "bin/hook_client.py"
        other = home / "operator.py"
        other.write_bytes(alias.read_bytes())
        alias.unlink()
        os.link(other, alias)
    before = {path: (path.read_bytes(), path.stat().st_dev, path.stat().st_ino)
              for path in root.rglob("*") if path.is_file()}
    snapshot = _snapshot(root)
    witness = home / "AppData/Local/Microsoft/Windows Terminal/Fragments/AgentWorktrees/witness"
    if obstacle == "live":
        import subprocess
        child = subprocess.Popen(
            [sys.executable, "-I", "-S", "-B", "-c",
             "import sys; sys.stdin.buffer.read()", str(root / "bin/hook_client.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    else:
        child = None
    try:
        result = _run(_native_arguments(shell, executable, payload, root), home)
        assert result.returncode != 0, result.stdout + result.stderr
        _preserved(before)
        _preserved(durable)
        assert witness.read_bytes() == b"operator witness"
        if child is not None:
            assert child.poll() is None
            _skip_unrelated_module_consumer(result, snapshot, durable, child)
            assert f"pid={child.pid}" in result.stdout + result.stderr, result.stdout + result.stderr
    finally:
        if child is not None:
            child.communicate(input=b"", timeout=10)


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_native_uninstall_refuses_runtime_owned_interpreter(native_install, shell):
    import venv

    home, payload, root, _, durable = native_install
    runtime_python = root / "activated"
    venv.EnvBuilder(with_pip=False, symlinks=False).create(runtime_python)
    candidate = runtime_python / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    arguments = _native_arguments(shell, _host(shell), payload, root)
    arguments[-1] = str(candidate)
    before = {path: (path.read_bytes(), path.stat().st_dev, path.stat().st_ino)
              for path in root.rglob("*") if path.is_file()}
    result = _run(arguments, home)
    assert result.returncode != 0
    assert "bootstrap Python" in result.stdout + result.stderr
    _preserved(before)
    _preserved(durable)


def _negative_admission_path(home: Path, shell: str, executable: str) -> Path:
    """Expose required real shell tools, never an ambient Python candidate."""
    import shlex

    directory = home / "admission-tools"
    directory.mkdir()
    names = ("dirname", "basename", "cygpath") if os.name == "nt" else ("dirname", "basename")
    if shell == "bash":
        for name in names:
            result = _run(
                [executable, "-c", 'command -v "$1"', "fixture-tool", name],
                home, timeout=10,
            )
            target = result.stdout.strip()
            assert result.returncode == 0 and target.startswith("/"), result.stderr
            wrapper = directory / name
            wrapper.write_text(
                '#!/bin/sh\nexec ' + shlex.quote(target) + ' "$@"\n', encoding="utf-8",
            )
            wrapper.chmod(0o755)
    assert {path.name for path in directory.iterdir()} == (set(names) if shell == "bash" else set())
    return directory


def test_negative_admission_path_has_tools_but_no_python(native_install) -> None:
    home, _, _, _, _ = native_install
    executable = _host("bash")
    directory = _negative_admission_path(home, "bash", executable)
    expected = bash_path(executable, home)
    result = _run([
        executable, "-c",
        'for name in python python3; do '
        'if command -v "$name" >/dev/null 2>&1; then exit 90; fi; done; dirname "$1"',
        "fixture-path", expected + "/witness",
    ], home, env={"PATH": str(directory)}, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.parametrize("shell", ["bash", "powershell", "pwsh"])
@pytest.mark.parametrize("eligible", [False, True])
def test_native_existing_python_candidates(native_install, shell, eligible):
    home, payload, root, owned, durable = native_install
    executable = _host(shell)
    candidates = home / "candidates"
    candidates.mkdir()
    broken = candidates / ("python3.exe" if os.name == "nt" else "python3")
    broken.write_bytes(b"not an executable")
    broken.chmod(0o755)
    paths = [str(candidates)]
    if eligible:
        paths.append(str(Path(sys.executable).parent))
    if os.name == "nt":
        paths.extend([
            str(Path(os.environ["SystemRoot"]) / "System32"),
            str(Path(_host("pwsh")).parent),
            r"C:\Program Files\Git\bin", r"C:\Program Files\Git\usr\bin",
        ])
    else:
        paths.extend(["/usr/bin", "/bin"])
    if not eligible:
        paths = [str(_negative_admission_path(home, shell, executable))]
    arguments = _native_arguments(shell, executable, payload, root)
    if shell != "bash":
        arguments = arguments[:-2]
    before = _snapshot(root)
    result = _run(
        arguments, home, env={"PATH": os.pathsep.join(paths)},
        timeout=60 if eligible else 15,
    )
    output = result.stdout + result.stderr
    admission_refused = output.strip() == "ERROR: Attributable runtime admission requires bootstrap Python." \
        or ("requires an existing Python" in output and "executable" in output)
    if not eligible:
        assert result.returncode != 0
        assert admission_refused, output
        _unchanged(before, durable)
    elif result.returncode:
        # A real host consumer is not exempted to qualify candidate selection.
        assert "consumer" in output, output
        assert all(path.exists() for path in owned)
    else:
        assert all(not path.exists() for path in owned)
    _preserved(durable)


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher listing adapter")
@pytest.mark.parametrize("native_install", ["cold-home"], indirect=True)
@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_native_uninstall_discovers_launcher_only_external_python(native_install, shell) -> None:
    home, payload, root, _, _ = native_install
    directory = home / "launcher-only"
    directory.mkdir()
    query = home / "launcher-query.txt"
    launcher = directory / "py.cmd"
    launcher.write_text(
        '@echo off\n'
        f'>>"{query}" echo %~1\n'
        'if not "%~1"=="-0p" exit /b 92\n'
        'if not "%~2"=="" exit /b 93\n'
        f'echo -V:3.12 * {str(sys.executable).replace("%", "%%")}\n',
        encoding="utf-8",
    )
    arguments = _native_arguments(shell, _host(shell), payload, root)[:-2]
    result = _run(arguments, home, env={"PATH": str(directory)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert query.read_text(encoding="utf-8").splitlines() == ["-0p", "-0p"]
    assert not root.exists()
    assert not (root / ".install-stage").exists()


@pytest.mark.parametrize("native_install", ["cold-home"], indirect=True)
@pytest.mark.parametrize("shell", ["bash", "powershell", "pwsh"])
@pytest.mark.parametrize("kind", ["owned", "foreign", "custom-root"])
@pytest.mark.parametrize("state", ["missing", "durable-only"])
def test_command_only_missing_runtime_has_truthful_admission(native_install, shell, kind, state) -> None:
    home, payload, default_root, _, _ = native_install
    root = home / "other-runtime" if kind == "custom-root" else default_root
    durable = {}
    if state == "durable-only":
        root.mkdir()
        config = root / "config.yaml"
        config.write_bytes(b"operator configuration\n")
        durable[config] = (config.read_bytes(), config.stat().st_dev, config.stat().st_ino)
    profile = json.loads((payload / "scripts/uninstall-profile.json").read_text())
    directory = home / ".local/bin"
    directory.mkdir(parents=True)
    for name in profile["local_bin_entries"]:
        source = payload / "bin" / name
        if name == "agent-worktrees-update-json.awk":
            source = payload / "scripts/installation-context/json-query.awk"
        target = directory / name
        target.write_bytes(
            b"operator-owned custom command\n" if kind == "foreign" else source.read_bytes(),
        )
        target.chmod(0o755)
    before = {path: (path.read_bytes(), path.stat().st_dev, path.stat().st_ino)
              for path in directory.iterdir()}
    probe = """
import importlib.util,json,pathlib,sys
payload=pathlib.Path(sys.argv[1])
spec=importlib.util.spec_from_file_location('command_only_owner',
    payload/'scripts/installer_engine_uninstall.py')
module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module
spec.loader.exec_module(module)
profile=module.profile_for(payload,'agent-worktrees',payload/'scripts/uninstall-profile.json')
print(json.dumps(module._local_bindings(payload,pathlib.Path(sys.argv[2]),profile)[1]))
"""
    evidence = _run([
        sys.executable, "-I", "-S", "-B", "-c", probe, str(payload), str(root),
    ], home)
    assert evidence.returncode == 0, evidence.stdout + evidence.stderr
    assert set(json.loads(evidence.stdout)) == (
        set(profile["local_bin_entries"]) if kind == "owned" else set()
    )
    result = _run(_native_arguments(shell, _host(shell), payload, root), home)
    if kind == "owned":
        assert result.returncode != 0, result.stdout + result.stderr
        assert "command" in (result.stdout + result.stderr).lower()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
    _preserved(before)
    _preserved(durable)
    assert root.exists() is (state == "durable-only")
    assert not (default_root / ".install-stage").exists()


@pytest.mark.parametrize("kind", ["construction", "retention", "publication", "snapshot"])
def test_native_uninstall_refuses_held_generation_custody(native_install, kind):
    home, payload, root, _, durable = native_install
    runtime = _load(payload / "scripts/versioned_runtime.py")
    leases = runtime._leases
    lease_root = root / "snapshots" if kind == "snapshot" else root
    lease_kind = "construction" if kind == "snapshot" else kind
    version = "selectors" if kind == "publication" else "1.0.0"
    executable = _host("pwsh")
    before = {path: (path.read_bytes(), path.stat().st_dev, path.stat().st_ino)
              for path in root.rglob("*") if path.is_file()}
    with leases._kernel_lease(
        lease_root, version, kind=lease_kind, shared=False, timeout=0,
    ) as descriptor:
        identity = os.fstat(descriptor)
        result = _run(_native_arguments("pwsh", executable, payload, root), home)
        assert result.returncode != 0
        assert "lease" in result.stdout + result.stderr
        _preserved(before)
        _preserved(durable)
        assert (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino) == (
            identity.st_dev, identity.st_ino,
        )


def test_uninstall_adapter_refuses_base_closure(monkeypatch, tmp_path):
    adapter = _load(SCRIPTS / "uninstall-state.py")
    root = tmp_path / "runtime"
    monkeypatch.setattr(sys, "base_prefix", str(root / "base"))
    with pytest.raises(RuntimeError, match="base closure"):
        adapter.external_python(root)


def test_profile_matches_native_publication_declarations(monkeypatch):
    profile = json.loads((SCRIPTS / "uninstall-profile.json").read_text())
    monkeypatch.syspath_prepend(str(SCRIPTS))
    publication = _load(SCRIPTS / "runtime_install_publication.py")
    assert set(publication.HOOKS) <= set(profile["bin_entries"])
    assert set(publication.SETUP) <= set(profile["script_entries"])
    assert set(publication.TOOLS) == set(profile["local_bin_entries"])
    assert "psmux-path.ps1" in profile["bin_entries"]
    assert {
        f"conduct/{path.name}" for path in (SCRIPTS / "conduct").glob("*.md")
    } <= set(profile["bin_entries"])


@pytest.mark.parametrize("suffix", ["sh", "ps1"])
def test_pre_admission_precedes_identical_standard_stage(suffix):
    source = (SCRIPTS / f"install.{suffix}").read_text(encoding="utf-8-sig")
    reference = (REPO / f"plugins/agent-bridge/scripts/install.{suffix}").read_text(
        encoding="utf-8-sig",
    )
    start = "# === install-contract:v4 self-stage"
    end = "# === end install-contract:v4 self-stage ==="

    def block(text):
        return text[text.index(start):text.index(end) + len(end)]

    assert block(source) == block(reference)
    assert source.index("--check --check-consumers") < source.index(start)
    assert source.index("absent", source.index("--check --check-consumers")) < source.index(start)
    assert "--remove-runtime" not in source[:source.index(start)]


@pytest.mark.parametrize("suffix", ["sh", "ps1"])
def test_effective_project_is_resolved_and_forwarded_before_lifecycle(suffix):
    source = (SCRIPTS / f"install.{suffix}").read_text(encoding="utf-8-sig")
    admission = source.index("# === command-lifecycle: admission before staging ===")
    if suffix == "sh":
        assert source.index(
            '__aw_project_name="$(_aw_resolve_installer_project_name "$__aw_project_name")"'
        ) < admission
        assert source.index('__aw_args+=(--project-name "$__aw_project_name")') < source.index(
            'set -- "${__aw_args[@]}"',
        ) < admission
        assert source.count('cwd_name="$(basename "$PWD")"') == 1
        assert 'PROJECT_NAME="$(_aw_resolve_installer_project_name "$PROJECT_NAME_ARG")"' in source
    else:
        assert source.index("$ProjectName = Resolve-InstallerProjectName -Name $ProjectName") < admission
        assert source.index("$PSBoundParameters['ProjectName'] = $ProjectName") < admission
        assert source.count("$candidateConf = Join-Path $env:USERPROFILE") == 1


def test_late_copied_posix_account_isolation_invalidates_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model POSIX account lookup on Windows; use the actual binding publisher."""
    from types import ModuleType, SimpleNamespace
    from typing import Any

    import runtime_install_fixture as fixture_support

    home = tmp_path / "home"
    account = tmp_path / "passwd-account"
    account.mkdir()
    payload = home / ".copilot/installed-plugins/example/agent-worktrees"
    shutil.copytree(
        SCRIPTS, payload / "scripts",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copyfile(PLUGIN / "plugin.json", payload / "plugin.json")
    (home / ".copilot/settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Example/Plugins"}},
        },
    }), encoding="utf-8")
    root = home / ".agent-worktrees"
    root.mkdir()
    monkeypatch.setitem(
        sys.modules, "pwd", SimpleNamespace(getpwuid=lambda _uid: SimpleNamespace(pw_dir=str(account))),
    )

    def posix_primitive() -> ModuleType:
        primitive = _load(payload / "scripts/installation-context/installation_context.py")
        primitive.os = SimpleNamespace(**{**vars(os), "getuid": lambda: 123})
        primitive._is_wsl_guest = lambda: False
        current = primitive._current_environment

        def current_posix(**arguments: Any) -> tuple[dict[str, Any], Path]:
            return current(**{**arguments, "platform": "posix"})

        primitive._current_environment = current_posix
        return primitive

    arguments = {
        "payload_root": payload, "plugin_id": "agent-worktrees", "legacy_root": root,
        "project_root": home, "environment": {"HOME": str(home), "USERPROFILE": str(home)},
    }
    binding = _load(payload / "scripts/command_binding.py")
    primitive = posix_primitive()
    result = binding.write_legacy_manifest(primitive, {
        "schema_version": 3, "service": "agent-worktrees",
        "source": {"kind": "marketplace", "plugin": "agent-worktrees", "path": str(payload)},
    }, **arguments)
    assert result["status"] == "published"
    manifest = root / "deploy-manifest.json"
    before = manifest.read_bytes()
    receipt = json.loads(before)["commandBinding"]
    assert receipt["environment"] == primitive.probe_legacy_entrypoint(**arguments)["environment"]
    assert receipt["environment"]["homeRealPath"] == str(account.resolve())

    monkeypatch.setattr(fixture_support, "os", SimpleNamespace(name="posix"))
    fixture_support.isolate_account_profile(payload, home, runtime_package=None)
    fresh = posix_primitive()
    observed = fresh.probe_legacy_entrypoint(**arguments)["environment"]
    assert observed == {**receipt["environment"], "homeRealPath": str(home.resolve())}
    assert fresh.resolve_payload_source(**{
        key: arguments[key] for key in ("payload_root", "plugin_id", "project_root", "environment")
    })["sourceFingerprint"] == receipt["sourceFingerprint"]
    with pytest.raises(binding.CommandOwnershipConflict, match="another source, installation, or environment"):
        binding.publish_legacy_binding(fresh, **arguments)
    assert manifest.read_bytes() == before
