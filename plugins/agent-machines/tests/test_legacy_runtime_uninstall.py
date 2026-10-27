"""Real native legacy removal, refusal and whole-transaction contracts."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
ROOT = PLUGIN.parents[1]
HOSTS = [
    path for path in (
        shutil.which("pwsh"),
        str(Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32/WindowsPowerShell/v1.0/powershell.exe") if os.name == "nt" else None,
    ) if path and Path(path).is_file()
]
if os.name != "nt" and shutil.which("bash"):
    HOSTS.append(shutil.which("bash"))


def _environment(home: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("COPILOT_", "AGENT_MACHINES_", "AGENT_CONTAINERS_"))}
    home.mkdir(exist_ok=True)
    env.update(HOME=str(home), USERPROFILE=str(home), COPILOT_EXTENSIONS_TEST_CONTAINED="1",
               COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC="1", GIT_OPTIONAL_LOCKS="0")
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    return env


def _run(
    host: str, root: str | Path | None, env: dict[str, str], *, action: str = "uninstall",
    payload: Path | None = None, extra: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    payload = payload or PLUGIN
    if Path(host).name in {"bash", "bash.exe"}:
        command = [host, str(payload / "scripts/init.sh"), action]
        if root is not None:
            command += ["--install-dir", str(root)]
    else:
        command = [host, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-File", str(payload / "scripts/init.ps1"), "-Action", action]
        if root is not None:
            command += ["-InstallDir", str(root)]
    return subprocess.run(  # noqa: S603 -- real native entrypoint, fixture-only root
        [*command, *extra], env=env, cwd=ROOT, capture_output=True, text=True, timeout=35,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _fixture(root: Path, *, payload: Path | None = None) -> dict[str, bytes]:
    payload = payload or PLUGIN
    root.mkdir()
    (root / "versions/1.2.3").mkdir(parents=True)
    (root / "versions/1.2.3/runtime.txt").write_bytes(b"runtime")
    (root / "current-version").write_text("1.2.3", encoding="utf-8")
    (root / "deploy-manifest.json").write_text(json.dumps({
        "schema_version": 3, "service": PLUGIN.name,
        "source": {
            "kind": "local", "path": str(payload), "repo": "copilot-extensions",
            "plugin": PLUGIN.name, "version": "1.2.3", "commit": None,
            "branch": None, "dirty": False,
        },
        "runtime": {
            "kind": "python", "version": "1.2.3",
            "path": str(root / "versions/1.2.3"),
        },
    }), encoding="utf-8")
    durable = {"config.yaml": b"config\x00bytes", "unknown": b"custom",
               "state.db": b"database", "auth.json": b"private",
               "config/requirements.json": b"requirements",
               "state/nested.db": b"nested database", "private/session.key": b"fixture-private",
               "logs/runtime.log": b"historical log"}
    if PLUGIN.name == "agent-containers":
        durable.update({"containers.yaml": b"fleet", "leases.json": b"lease state"})
    for name, content in durable.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(content)
    return durable


def _finite_assets(payload: Path | None = None) -> bool:
    payload = payload or PLUGIN
    engine = payload / "scripts/installer-engine.ps1"
    helper = payload / "scripts/installer_engine_uninstall.py"
    return (
        helper.is_file() and engine.is_file()
        and "[string[]]$EntryNames" in engine.read_text(encoding="utf-8")
    )


def _isolated_payload(directory: Path) -> Path:
    """Assemble a test-owned local release from this worktree's provided assets."""
    payload = directory / "payload"
    shutil.copytree(
        PLUGIN, payload,
        ignore=shutil.ignore_patterns("tests", "__pycache__", ".venv", "runtime-source.json"),
    )
    # The production module remains fail-closed against real ambient consumers.
    # This trusted fixture declares its own module instead of pretending those
    # unrelated production processes are idle or stopping them to make a test pass.
    module = "finite_fixture_" + hashlib.sha256(str(payload).encode()).hexdigest()[:16]
    profile_path = payload / "scripts/uninstall-profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    profile["module"] = module
    profile_path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    package = payload / "src" / module
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    return payload


def _component(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "legacy_native_test_" + name, PLUGIN / "scripts" / (name + ".py"),
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _snapshot(root: Path) -> Path:
    snapshot = root / "snapshots/1.2.3"
    snapshot.mkdir(parents=True)
    (snapshot / "pyproject.toml").write_text(
        f'[project]\nname = "{PLUGIN.name}"\nversion = "1.2.3"\n', encoding="utf-8",
    )
    (snapshot / "payload.txt").write_bytes(b"owned snapshot")
    (root / "payload-dir").write_text(str(snapshot), encoding="utf-8")
    (root / "stamped-version").write_text("1.2.3", encoding="utf-8")
    return snapshot


@pytest.mark.parametrize("host", HOSTS)
def test_native_absent_uninstall_creates_nothing(tmp_path: Path, host: str) -> None:
    env = _environment(tmp_path / "home")
    root = tmp_path / "absent"
    result = _run(host, root, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not root.exists()


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("option", ["dry-run", "check", "force", "context", "profile"])
def test_native_uninstall_rejects_unsupported_options_without_mutation(
    tmp_path: Path, host: str, option: str,
) -> None:
    env = _environment(tmp_path / "home")
    payload = _isolated_payload(tmp_path)
    root = tmp_path / "runtime"
    _fixture(root, payload=payload)
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    flag = ("--" if Path(host).name in {"bash", "bash.exe"} else "-") + option
    arguments = (flag, "fixture") if option in {"context", "profile"} else (flag,)
    result = _run(host, root, env, payload=payload, extra=arguments)
    assert result.returncode != 0, result.stdout + result.stderr
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
    assert not (root / ".install.lock").exists()
    assert not (root / ".install-stage").exists()
    assert not (tmp_path / "home/.local/bin").exists()


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("option", ["dry-run", "check"])
def test_native_uninstall_helper_rejects_trailing_options(
    tmp_path: Path, host: str, option: str,
) -> None:
    env = _environment(tmp_path / "home")
    payload = _isolated_payload(tmp_path)
    root = tmp_path / "runtime"
    _fixture(root, payload=payload)
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    if Path(host).name in {"bash", "bash.exe"}:
        command = [host, str(payload / "scripts/legacy-uninstall.sh"), str(root), "--" + option]
    else:
        command = [
            host, "-NoProfile", "-NonInteractive", "-File",
            str(payload / "scripts/legacy-uninstall.ps1"), "-InstallDir", str(root), "-" + option,
        ]
    result = subprocess.run(
        command, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=35,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
    assert not (root / ".install.lock").exists()


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("kind", ["foreign", "malformed", "cell", "managed", "raw-parent"])
def test_native_refusal_precedes_staging_and_lock(
    tmp_path: Path, host: str, kind: str,
) -> None:
    env = _environment(tmp_path / "home")
    root = tmp_path / "runtime"
    root.mkdir()
    if kind in {"foreign", "malformed"}:
        (root / "deploy-manifest.json").write_text(
            "{" if kind == "malformed" else '{"schema_version":3,"service":"another-plugin"}',
            encoding="utf-8",
        )
    elif kind == "cell":
        (root / "install.json").write_text("{}", encoding="utf-8")
    elif kind == "managed":
        (root / "runtime-deployment.json").write_text("{}", encoding="utf-8")
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    argument = str(root) + os.sep + ".." + os.sep + root.name if kind == "raw-parent" else root
    result = _run(host, argument, env)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "helper is unavailable" not in result.stdout + result.stderr
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before
    assert not (Path(env["HOME"]) / ("." + PLUGIN.name) / ".install-stage").exists()


@pytest.mark.parametrize("host", HOSTS)
def test_native_idle_cleanup_preserves_durable_bytes_and_is_idempotent(
    tmp_path: Path, host: str,
) -> None:
    env = _environment(tmp_path / "home")
    payload = _isolated_payload(tmp_path)
    root = tmp_path / "runtime"
    durable = _fixture(root, payload=payload)
    snapshot = _snapshot(root)
    leases = _component("versioned_runtime_leases")
    # Release real kernel custody, retaining its exact registry inodes. Their
    # existence must never be treated as a live owner or as a generation.
    with leases.batch_removal_custody(root, ["1.2.3"]):
        pass
    with leases.batch_removal_custody(root / "snapshots", ["1.2.3"]):
        pass
    persistent = {
        path: (path.stat().st_dev, path.stat().st_ino)
        for directory in (root, root / "snapshots")
        for path in directory.glob(".*-leases/*.lock")
    }
    assert persistent
    if PLUGIN.name == "agent-containers":
        (root / "ssh").mkdir()
    result = _run(host, root, env, payload=payload)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (root / "versions/1.2.3").exists()
    assert not snapshot.exists()
    assert not (root / "deploy-manifest.json").exists()
    assert {name: (root / name).read_bytes() for name in durable} == durable
    again = _run(host, root, env, payload=payload)
    assert again.returncode == 0, again.stdout + again.stderr
    assert {name: (root / name).read_bytes() for name in durable} == durable
    assert {
        path: (path.stat().st_dev, path.stat().st_ino) for path in persistent
    } == persistent


@pytest.mark.parametrize("host", HOSTS)
def test_native_finite_assets_never_fall_back_to_purge(
    tmp_path: Path, host: str,
) -> None:
    env = _environment(tmp_path / "home")
    payload = _isolated_payload(tmp_path)
    root = tmp_path / "runtime"
    durable = _fixture(root, payload=payload)
    result = _run(host, root, env, payload=payload)
    assert {name: (root / name).read_bytes() for name in durable} == durable
    if _finite_assets(payload):
        assert result.returncode == 0, result.stdout + result.stderr
        assert not (root / "versions/1.2.3").exists()
    else:
        assert result.returncode != 0
        assert "finite" in result.stdout + result.stderr
        assert (root / "versions/1.2.3/runtime.txt").read_bytes() == b"runtime"
        assert (root / "deploy-manifest.json").is_file()


@pytest.mark.parametrize(
    "host", [host for host in HOSTS if "powershell" in host.lower() or "pwsh" in host.lower()],
)
@pytest.mark.parametrize(
    "action", ["uninstall", "install", "stamp", "stamp-binstub", "init", "provision"],
)
def test_every_native_publisher_refuses_held_transaction(
    tmp_path: Path, host: str, action: str,
) -> None:
    env = _environment(tmp_path / "home")
    root = Path(env["HOME"]) / ".agent-machines" if action == "stamp-binstub" else (
        tmp_path / "runtime"
    )
    durable = _fixture(root)
    lock = root / ".install.lock"
    # The holder exits on stdin EOF; no unknown process or process-name killing.
    script = (
        f"$f=[IO.File]::Open('{str(lock).replace(chr(39), chr(39)*2)}',"
        "'OpenOrCreate','ReadWrite','None');"
        "[Console]::Out.WriteLine('held');[Console]::Out.Flush();"
        "[Console]::In.ReadLine() | Out-Null; $f.Dispose()"
    )
    holder = subprocess.Popen(  # noqa: S603 -- exact native host and fixture-owned lock
        [host, "-NoProfile", "-NonInteractive", "-Command", script], env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        result = _run(host, root, env, action=action)
        assert result.returncode != 0, result.stdout + result.stderr
        assert "transaction lock" in result.stdout + result.stderr
        assert {name: (root / name).read_bytes() for name in durable} == durable
        assert not (root / "snapshots").exists()
    finally:
        holder.communicate("\n", timeout=10)


@pytest.mark.guard
def test_whole_gate_precedes_every_legacy_mutation() -> None:
    ps = (PLUGIN / "scripts/init.ps1").read_text(encoding="utf-8")
    acquired = ps.index("$legacyTransaction = Enter-LegacyTransaction")
    assert acquired < ps.index("[void](Invoke-VersionedSlotClean)")
    assert acquired < ps.index("if ($Action -eq 'stamp-binstub') { Invoke")
    assert acquired < ps.index("if ($Action -eq 'stamp') { Invoke")
    assert "$legacyTransaction.Dispose()" in ps
    sh = (PLUGIN / "scripts/init.sh").read_text(encoding="utf-8")
    assert sh.index("_enter_install_lock ||") < sh.index('_runtime_construction_begin "$py"')

    # Helper entry paths dispatch into the same gated native entrypoint.
    bootstrap = (PLUGIN / "scripts/bootstrap-check.ps1").read_text(encoding="utf-8")
    assert '-File $payloadInit stamp-binstub' in bootstrap
    assert '"& `"$payloadInit`" stamp' in bootstrap
    assert "$transactionArgs.MaximumWaitSec = 1" in ps
    tail = ps[ps.index("try {\n", acquired):]
    assert tail.rfind("Stop-RuntimeConstruction") < tail.rfind("$legacyTransaction.Dispose()")


@pytest.mark.guard
def test_native_adapter_uses_one_canonical_finite_owner() -> None:
    profile = json.loads((PLUGIN / "scripts/uninstall-profile.json").read_text(encoding="utf-8"))
    assert profile["version"] == 1
    assert profile["module"] == PLUGIN.name.replace("-", "_")
    assert "deploy-manifest.json" in profile["root_entries"]
    assert not {"versions", "snapshots", "config", "state", "ssh"} & set(profile["root_entries"])
    for extension in ("ps1", "sh"):
        text = (PLUGIN / ("scripts/legacy-uninstall." + extension)).read_text(encoding="utf-8")
        assert "installer_engine_uninstall.py" in text
        assert "--check" in text and "--remove-runtime" in text and "--profile" in text
        assert "Remove-InstallRuntimeContent" not in text
        assert "remove_install_runtime_content" not in text
        assert "command_lifecycle" not in text
        assert "libs/installer-engine" not in text
        assert "libs\\installer-engine" not in text


@pytest.mark.parametrize(
    "kind", [
        "construction", "retention", "publication",
        "snapshot-construction", "snapshot-retention", "redirects",
    ],
)
def test_real_held_custody_refuses_concurrent_uninstall(tmp_path: Path, kind: str) -> None:
    module = _component("versioned_runtime_leases")
    env = _environment(tmp_path / "home")
    payload = _isolated_payload(tmp_path)
    root = tmp_path / "runtime"
    _fixture(root, payload=payload)
    snapshot = _snapshot(root)
    if kind == "construction":
        custody = module.construction_lease(root, "1.2.3", timeout=0)
    elif kind == "retention":
        custody = module._kernel_lease(
            root, "1.2.3", kind="retention", shared=True, timeout=0,
        )
    elif kind == "publication":
        custody = module._publication_lease(root, timeout=0)
    elif kind == "snapshot-construction":
        custody = module.construction_lease(root / "snapshots", "1.2.3", timeout=0)
    elif kind == "snapshot-retention":
        custody = module._kernel_lease(
            root / "snapshots", "1.2.3", kind="retention", shared=True, timeout=0,
        )
    else:
        publication = _component("versioned_runtime_publication")
        custody = publication.target_leases(root, [root / "payload-dir"])
    manifest_before = (root / "deploy-manifest.json").read_bytes()
    with custody:
        for host in HOSTS:
            result = _run(host, root, env, payload=payload)
            assert result.returncode != 0
            diagnostic = result.stdout + result.stderr
            assert any(word in diagnostic.lower() for word in (
                "custody", "lease", "construction", "retention", "publication",
            )), diagnostic
            assert "helper is unavailable" not in diagnostic
            assert (root / "versions/1.2.3/runtime.txt").read_bytes() == b"runtime"
            assert (snapshot / "payload.txt").read_bytes() == b"owned snapshot"
            assert (root / "deploy-manifest.json").read_bytes() == manifest_before
    # The next cleanup must be admitted after the real holder exits; persistent
    # registries alone must not produce a false busy result.
    for host in HOSTS:
        result = _run(host, root, env, payload=payload)
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("host", HOSTS)
def test_native_linked_root_refuses_without_following_target(tmp_path: Path, host: str) -> None:
    env = _environment(tmp_path / "home")
    target = tmp_path / "target"
    durable = _fixture(target)
    alias = tmp_path / "alias"
    if os.name == "nt":
        script = f"New-Item -ItemType Junction -Path '{alias}' -Value '{target}' | Out-Null"
        linked = subprocess.run(  # noqa: S603 -- fixture junction, exact native host
            [host, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert linked.returncode == 0, linked.stderr
    else:
        alias.symlink_to(target, target_is_directory=True)
    result = _run(host, alias, env)
    assert result.returncode != 0
    assert not (target / ".install.lock").exists()
    assert {name: (target / name).read_bytes() for name in durable} == durable


@pytest.mark.parametrize("host", HOSTS)
def test_native_unknown_runtime_bin_is_preserved(tmp_path: Path, host: str) -> None:
    env = _environment(tmp_path / "home")
    payload = _isolated_payload(tmp_path)
    root = tmp_path / "runtime"
    _fixture(root, payload=payload)
    (root / "bin").mkdir()
    unknown = root / "bin/custom-tool"
    unknown.write_bytes(b"operator-owned")
    result = _run(host, root, env, payload=payload)
    assert result.returncode == 0, result.stdout + result.stderr
    assert unknown.read_bytes() == b"operator-owned"
    assert not (root / "versions/1.2.3").exists()


@pytest.mark.skipif(os.name == "nt", reason="Requires a native POSIX shell and lock filesystem")
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("action", ["uninstall", "stamp", "init", "provision"])
def test_posix_publishers_refuse_real_held_transaction(
    tmp_path: Path, fallback: bool, action: str,
) -> None:
    import shlex

    env = _environment(tmp_path / "home")
    env["COPILOT_EXT_NO_FLOCK"] = "1" if fallback else "0"
    root = tmp_path / "runtime"
    _fixture(root)
    host = shutil.which("bash")
    helper = PLUGIN / "scripts/legacy-transaction.sh"
    script = (
        f". {shlex.quote(str(helper))}; INSTALL_DIR={shlex.quote(str(root))}; "
        "_enter_install_lock || exit 1; trap _exit_install_lock EXIT; "
        "printf 'held\\n'; IFS= read -r acknowledgement"
    )
    holder = subprocess.Popen(  # noqa: S603 -- fixture-owned transaction holder
        [host, "-c", script], env=env, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        result = _run(host, root, env, action=action)
        assert result.returncode != 0
        assert "transaction lock" in result.stdout + result.stderr
        assert not (root / "snapshots").exists()
    finally:
        holder.communicate("\n", timeout=10)
