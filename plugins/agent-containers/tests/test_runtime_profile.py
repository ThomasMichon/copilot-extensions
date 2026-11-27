from __future__ import annotations

from collections.abc import Iterator
import importlib
import builtins
import io
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _copy_runtime_closure(scripts: Path) -> None:
    for path in SCRIPTS.glob("versioned_runtime*.py"):
        shutil.copyfile(path, scripts / path.name)


def _materialize_construction(scripts: Path) -> None:
    _copy_runtime_closure(scripts)
    for name in (
        "runtime-construction.sh", "runtime-construction.ps1",
        "installer-bootstrap.sh", "installer-bootstrap.ps1",
    ):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    previous = sys.path[:]
    try:
        repo = SCRIPTS.parents[2]
        owner = runpy.run_path(str(repo / "tools/materialize_installer_engine.py"))
        for source, target in owner["derived_file_pairs"](
            repo, scripts.parent / "materialized", copied_consumer=(SCRIPTS.parent, scripts.parent),
        ):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    finally:
        sys.path[:] = previous


@pytest.mark.parametrize("isolated", [False, True], ids=["safe-path", "isolated"])
def test_staged_profile_loads_only_its_owned_runtime_with_safe_path(tmp_path: Path, isolated: bool) -> None:
    staged = tmp_path / "staged payload" / "scripts"
    staged.mkdir(parents=True)
    for name in (
        "runtime_profile.py", "versioned_runtime.py", "versioned_runtime_leases.py",
        "versioned_runtime_construction.py",
        "versioned_runtime_source.py",
        "versioned_runtime_publication.py",
    ):
        shutil.copyfile(SCRIPTS / name, staged / name)
    _copy_runtime_closure(staged)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "versioned_runtime.py").write_text(
        "raise AssertionError('loaded an ambient runtime helper')\n", encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, *(["-I"] if isolated else []), str(staged / "runtime_profile.py"), "--help"],
        cwd=foreign, env={**os.environ, "PYTHONSAFEPATH": "1", "PYTHONPATH": str(foreign)},
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--profile" in result.stdout
    assert result.stderr == ""


@pytest.fixture
def profiles(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("runtime_profile")


@pytest.mark.parametrize("profile", [None, "base", "provider-exec"])
def test_application_profile_cli_reads_existing_intent_without_payload_or_writes(
    profiles: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], profile: str | None,
) -> None:
    root = tmp_path / "runtime"
    if profile is not None:
        root.mkdir()
        (root / profiles.PROFILE_FILE).write_text(json.dumps({
            "schema": profiles.PROFILE_SCHEMA, "version": 1, "profile": profile,
        }))
    before = {path: path.read_bytes() for path in root.glob("*")}
    monkeypatch.setattr(profiles, "provision", lambda *a, **k: pytest.fail("profile query provisioned"))
    monkeypatch.setattr(sys, "argv", ["runtime_profile.py", "--root", str(root), "--application-profile"])
    assert profiles.main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema": "copilot-extensions.runtime-application-profile", "version": 1,
        "plugin": "agent-containers", "runtime_root": str(root),
        "profile": profile or "base", "extras": ["provider-exec"] if profile == "provider-exec" else [],
    }
    assert {path: path.read_bytes() for path in root.glob("*")} == before
    assert root.exists() == (profile is not None)


@pytest.mark.parametrize("case", ["changed-policy", "construction-argument"])
def test_application_profile_cli_refuses_unknown_policy_or_construction(
    profiles: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: str,
) -> None:
    if case == "changed-policy":
        (tmp_path / profiles.PROFILE_FILE).write_text(json.dumps({
            "schema": profiles.PROFILE_SCHEMA, "version": 1, "profile": "foreign",
        }))
    arguments = ["runtime_profile.py", "--root", str(tmp_path), "--application-profile"]
    if case == "construction-argument":
        arguments.extend(["--payload", str(tmp_path / "unread")])
    monkeypatch.setattr(sys, "argv", arguments)
    assert profiles.main() == 1
    assert capsys.readouterr().out == ""


@pytest.fixture
def payload(tmp_path: Path, profiles: ModuleType) -> Path:
    root = tmp_path / "payload"
    root.mkdir()
    (root / "pyproject.toml").write_text('version = "1.0.0"\n', encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "module.py").write_text("# fixture\n", encoding="utf-8")
    for name in profiles.LIBRARIES:
        library = root / "libs" / name
        library.mkdir(parents=True)
        (library / "pyproject.toml").write_text('version = "1.0.0"\n', encoding="utf-8")
    return root


@pytest.fixture
def commands(monkeypatch: pytest.MonkeyPatch, profiles: ModuleType) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(arguments: list[str], *, cwd: Path) -> None:
        calls.append(arguments)
        if "venv" in arguments or any(Path(arg).stem == "runtime-construction" for arg in arguments):
            slot = Path(arguments[-1])
            python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b"fixture interpreter")

    monkeypatch.setattr(profiles, "_run", run)
    return calls


def _provision(profiles: ModuleType, root: Path, payload: Path, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("uv", "fixture-uv")
    return cast(dict[str, Any], profiles.provision(root, payload, python=sys.executable, **kwargs))


def _install_command(commands: list[list[str]]) -> list[str]:
    return next(
        call for call in commands
        if any(Path(argument).name == "installer_dependency_policy.py" for argument in call)
    )


@pytest.fixture(params=["repository-policy", "configuration-layers"])
def repository_policy(payload: Path, request: pytest.FixtureRequest) -> Path:
    name: str = request.param
    module_name = name.replace("-", "_")
    library = payload / "libs" / name
    library.mkdir(parents=True, exist_ok=True)
    (library / "pyproject.toml").write_text(
        f'[project]\nname="agent-{name}"\nversion="0.1.0"\n', encoding="utf-8",
    )
    module = library / "src" / module_name
    module.mkdir(parents=True)
    (module / "__init__.py").write_text('POLICY = "first"\n', encoding="utf-8")
    (payload / "pyproject.toml").write_text(
        '[project]\nname="agent-containers"\nversion="1.0.0"\n'
        f'dependencies=["agent-{name}"]\n'
        f'[tool.uv.sources]\nagent-{name}={{path="libs/{name}"}}\n',
        encoding="utf-8",
    )
    return library


@pytest.mark.parametrize("profile", ["base", "provider-exec"])
def test_declared_repository_policy_is_an_explicit_local_install_and_health_input(
    profiles: ModuleType, payload: Path, repository_policy: Path,
    commands: list[list[str]], tmp_path: Path, profile: str,
) -> None:
    result = _provision(profiles, tmp_path / "runtime", payload, requested=profile)
    install = _install_command(commands)
    assert "--no-sources" in install
    assert str(repository_policy) in install
    health = next(call for call in commands if "-c" in call)
    assert repository_policy.name.replace("-", "_") in health[health.index("-c") + 1]
    assert result["profile"] == profile


@pytest.mark.parametrize("layout", ["source", "prepared"])
def test_repository_policy_content_participates_in_each_profile_input_layout(
    profiles: ModuleType, payload: Path, repository_policy: Path, layout: str,
) -> None:
    if layout == "source":
        checkout = payload.parent / "checkout"
        original = payload
        payload = checkout / "plugins" / "agent-containers"
        payload.mkdir(parents=True)
        shutil.copyfile(original / "pyproject.toml", payload / "pyproject.toml")
        shutil.copytree(original / "src", payload / "src")
        shutil.copytree(original / "libs", checkout / "libs")
        repository_policy = checkout / "libs" / repository_policy.name
    roots = profiles._library_roots(payload, prepared=layout == "prepared")
    assert repository_policy in roots
    before = profiles._fingerprint(payload, roots)
    module = repository_policy / "src" / repository_policy.name.replace("-", "_") / "__init__.py"
    info = module.stat()
    module.write_text('POLICY = "other"\n', encoding="utf-8")
    os.utime(module, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert module.stat().st_size == info.st_size
    assert profiles._fingerprint(payload, roots) != before


@pytest.mark.guard
def test_fresh_profile_is_base_and_completed_slot_is_reused(
    profiles: ModuleType, payload: Path, commands: list[list[str]], tmp_path: Path
) -> None:
    root = tmp_path / "runtime"
    first = _provision(profiles, root, payload, uv="fixture-uv")
    assert first["profile"] == "base"
    assert first["runtimeVersion"] == "1.0.0+base"
    assert not any("[provider-exec]" in arg for call in commands for arg in call)
    install = _install_command(commands)
    assert install[:4] == [
        first["python"], "-I", str(SCRIPTS / "installer_dependency_policy.py"), "--uv",
    ]
    assert install[4:6] == ["fixture-uv", "--"]
    assert "--no-sources" in install
    assert all(str(payload / "libs" / name) in install for name in profiles.LIBRARIES)
    slot = Path(first["venv"])
    before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
              for path in (slot / "runtime-profile.json", slot / ".install-complete.json")}
    commands.clear()
    again = _provision(profiles, root, payload, uv="fixture-uv")
    assert again["reused"]
    assert all("-I" in call for call in commands)
    assert before == {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                      for path in (slot / "runtime-profile.json", slot / ".install-complete.json")}


def test_explicit_profile_switch_and_update_preserve_intent(
    profiles: ModuleType, payload: Path, commands: list[list[str]], tmp_path: Path
) -> None:
    root = tmp_path / "runtime"
    base = _provision(profiles, root, payload)
    provider = _provision(profiles, root, payload, requested="provider-exec")
    assert provider["venv"] != base["venv"]
    assert str(payload) + "[provider-exec]" in commands[-2]
    assert (root / "current-version").read_text().strip() == "1.0.0+provider-exec"
    assert Path(base["venv"]).is_dir()
    (payload / "pyproject.toml").write_text('version = "1.0.1"\n', encoding="utf-8")
    updated = _provision(profiles, root, payload)
    assert updated["runtimeVersion"] == "1.0.1+provider-exec"


@pytest.mark.skipif(os.name != "nt", reason="Windows uses signed stdlib copies")
def test_windows_uv_profile_preserves_copies_without_seeding(
    profiles: ModuleType, payload: Path, commands: list[list[str]], tmp_path: Path,
) -> None:
    _provision(profiles, tmp_path / "runtime", payload, uv="fixture-uv")
    create = next(call for call in commands if "-VenvDir" in call)
    assert Path(create[3]).name == "runtime-construction.ps1"
    assert create[4:6] == ["-Uv", "fixture-uv"]
    assert all(call[:2] != ["fixture-uv", "venv"] for call in commands)
    assert _install_command(commands)[4:6] == ["fixture-uv", "--"]


def test_missing_uv_refuses_before_runtime_or_profile_changes(
    profiles: ModuleType, payload: Path, commands: list[list[str]], tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    with pytest.raises(RuntimeError, match="unrestricted pip fallback"):
        _provision(profiles, root, payload, uv=None)
    assert not root.exists()
    assert commands == []


def test_switch_back_to_base_reuses_unchanged_slot(profiles: ModuleType, payload: Path, commands: list[list[str]], tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    base = _provision(profiles, root, payload)
    _provision(profiles, root, payload, requested="provider-exec")
    commands.clear()
    rollback = _provision(profiles, root, payload, requested="base")
    assert rollback["reused"]
    assert rollback["venv"] == base["venv"]
    assert all("-I" in call for call in commands)


def test_requested_provider_failure_never_falls_back_or_changes_active_slot(
    profiles: ModuleType, payload: Path, commands: list[list[str]], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "runtime"
    _provision(profiles, root, payload)
    previous = (root / "current-version").read_bytes()
    run = profiles._run

    def failing(arguments: list[str], *, cwd: Path) -> None:
        if str(payload) + "[provider-exec]" in arguments:
            raise RuntimeError("fixture provider install failed")
        run(arguments, cwd=cwd)

    monkeypatch.setattr(profiles, "_run", failing)
    with pytest.raises(RuntimeError, match="provider install failed"):
        _provision(profiles, root, payload, requested="provider-exec")
    assert (root / "current-version").read_bytes() == previous
    assert profiles.selected_profile(root) == "provider-exec"
    assert not (root / "versions" / "1.0.0+provider-exec").exists()


def test_native_profile_policy_refusal_retains_selected_runtime(
    profiles: ModuleType, tmp_path: Path,
) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native UV is unavailable")
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    for name in (
        "runtime_profile.py", "versioned_runtime.py", "versioned_runtime_leases.py",
        "versioned_runtime_construction.py", "installer_dependency_policy.py",
        "installer_dependency_config.py", "installer_dependency_arguments.py", "installer_retained_content.py",
        "versioned_runtime_source.py",
        "versioned_runtime_publication.py",
    ):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    _materialize_construction(scripts)
    (payload / "plugin.json").write_text(
        json.dumps({
            "name": "agent-containers", "version": "1.0.0",
            "repository": "https://github.com/ThomasMichon/copilot-extensions",
        }),
        encoding="utf-8",
    )
    (payload / "pyproject.toml").write_text(
        '[project]\nname="agent-containers"\nversion = "1.0.0"\n'
        '[project.optional-dependencies]\n'
        'provider-exec=["unseasoned @ https://example.invalid/unseasoned.whl"]\n',
        encoding="utf-8",
    )
    for name in profiles.LIBRARIES:
        library = payload / "libs" / name
        library.mkdir(parents=True)
        (library / "pyproject.toml").write_text(
            f'[project]\nname="fixture-{name}"\nversion="1.0.0"\n', encoding="utf-8",
        )
    root = tmp_path / "runtime"
    previous = profiles.runtime.slot(root, "0.9.0+base")
    profiles.runtime.mark_complete(root, "0.9.0+base")
    profiles.runtime.activate(root, "0.9.0+base", link_free=True)
    selector = (root / "current-version").read_bytes()
    marker = (previous / ".install-complete.json").read_bytes()
    completed = subprocess.run(
        [
            sys.executable, "-I", str(scripts / "runtime_profile.py"),
            "--root", str(root), "--payload", str(payload),
            "--python", sys.executable, "--uv", uv, "--profile", "provider-exec",
        ],
        capture_output=True, text=True, timeout=30, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    assert completed.returncode != 0
    assert "direct remote dependencies lack release-age evidence" in completed.stderr
    assert completed.stdout == ""
    assert (root / "current-version").read_bytes() == selector
    assert (previous / ".install-complete.json").read_bytes() == marker
    assert profiles.selected_profile(root) == "provider-exec"
    assert not (root / "versions" / "1.0.0+provider-exec").exists()


@pytest.mark.parametrize("stage", ["venv", "install", "health", "fingerprint"])
def test_failed_profile_construction_discards_before_releasing_ownership_and_retries(
    profiles: ModuleType, payload: Path, commands: list[list[str]], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stage: str,
) -> None:
    root = tmp_path / "runtime"
    previous = _provision(profiles, root, payload)
    selector = (root / "current-version").read_bytes()
    marker = Path(previous["venv"]) / ".install-complete.json"
    completed = marker.read_bytes()
    run = profiles._run

    def failing(arguments: list[str], *, cwd: Path) -> None:
        run(arguments, cwd=cwd)
        if (
            (stage == "venv" and any(Path(arg).stem == "runtime-construction" for arg in arguments))
            or (stage == "install" and any(
                Path(argument).name == "installer_dependency_policy.py" for argument in arguments
            ))
            or (stage == "health" and "-c" in arguments)
        ):
            raise RuntimeError(f"fixture {stage} failed")
        if stage == "fingerprint" and "-c" in arguments:
            (payload / "src" / "module.py").write_text("# changed\n", encoding="utf-8")

    monkeypatch.setattr(profiles, "_run", failing)
    with pytest.raises(RuntimeError, match="source changed|fixture .* failed"):
        _provision(profiles, root, payload, requested="provider-exec")
    assert not (root / "versions" / "1.0.0+provider-exec").exists()
    assert (root / "current-version").read_bytes() == selector
    assert marker.read_bytes() == completed
    assert profiles.selected_profile(root) == "provider-exec"
    with profiles.runtime.construction_lease(root, "1.0.0+provider-exec", timeout=0.1):
        pass
    monkeypatch.setattr(profiles, "_run", run)
    retry = _provision(profiles, root, payload)
    assert retry["profile"] == "provider-exec"
    assert not retry["reused"]


def test_reused_profile_health_failure_never_discards_completed_slot(
    profiles: ModuleType, payload: Path, commands: list[list[str]], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    installed = _provision(profiles, root, payload)
    marker = Path(installed["venv"]) / ".install-complete.json"
    before = marker.read_bytes()
    run = profiles._run

    def failing(arguments: list[str], *, cwd: Path) -> None:
        if "-c" in arguments:
            raise RuntimeError("fixture health failure")
        run(arguments, cwd=cwd)

    monkeypatch.setattr(profiles, "_run", failing)
    monkeypatch.setattr(
        profiles.runtime, "discard_failed_candidate",
        lambda *args, **kwargs: pytest.fail("discarded completed candidate"),
    )
    with pytest.raises(RuntimeError, match="health failure"):
        _provision(profiles, root, payload)
    assert marker.read_bytes() == before
    assert (root / "current-version").read_text().strip() == installed["runtimeVersion"]


def test_same_version_source_change_refuses_immutable_rebuild(
    profiles: ModuleType, payload: Path, commands: list[list[str]], tmp_path: Path
) -> None:
    root = tmp_path / "runtime"
    installed = _provision(profiles, root, payload)
    marker = Path(installed["venv"]) / ".install-complete.json"
    before = marker.read_bytes()
    (payload / "src" / "module.py").write_text("# changed\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="content-conflict"):
        _provision(profiles, root, payload)
    assert marker.read_bytes() == before


def _prepare_source(profiles: ModuleType, payload: Path, commit: str="a" * 40) -> Any:
    (payload / "plugin.json").write_text(
        json.dumps({"name": "agent-containers", "version": "1.0.0"}), encoding="utf-8",
    )
    digest = profiles.source.assembly_hash(payload)
    identity = profiles.source.SourceIdentity(
        "agent-containers", "1.0.0", commit, "b" * 40, digest,
        profiles.source.runtime_version("1.0.0", commit, digest),
    )
    (payload / "runtime-source.json").write_text(
        json.dumps(identity.document()), encoding="utf-8",
    )
    return identity


@pytest.mark.parametrize("profile", ["base", "provider-exec"])
def test_source_generation_preserves_profile_package_and_unchanged_reuse(
    profiles: ModuleType, payload: Path, commands: list[list[str]], tmp_path: Path, profile: str,
) -> None:
    identity = _prepare_source(profiles, payload)
    root = tmp_path / "runtime"
    first = _provision(profiles, root, payload, requested=profile)
    assert first["runtimeVersion"] == profiles.source.runtime_version(
        f"1.0.0+{profile}", identity.source_commit, identity.assembly_hash,
    )
    assert first["runtimeSource"] == identity.document()
    marker = Path(first["venv"]) / ".install-complete.json"
    before = marker.read_bytes(), marker.stat().st_mtime_ns
    again = _provision(profiles, root, payload)
    assert again["reused"]
    assert before == (marker.read_bytes(), marker.stat().st_mtime_ns)
    _prepare_source(profiles, payload, commit="c" * 40)
    changed = _provision(profiles, root, payload)
    assert changed["venv"] != first["venv"]
    assert changed["profile"] == profile
    assert marker.read_bytes() == before[0]


@pytest.mark.parametrize("stage", ["lease", "health", "activation"])
def test_source_mutation_refuses_before_selecting_candidate(
    profiles: ModuleType, payload: Path, commands: list[list[str]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    from contextlib import contextmanager

    root = tmp_path / "runtime"
    previous = _provision(profiles, root, payload)
    identity = _prepare_source(profiles, payload)
    selected = (root / "current-version").read_bytes()
    marker = Path(previous["venv"]) / ".install-complete.json"
    before = marker.read_bytes()

    def mutate() -> None:
        (payload / "runtime-source.json").unlink()

    if stage == "lease":
        lease = profiles.runtime.construction_lease

        @contextmanager
        def changed(*args: Any, **kwargs: Any) -> Iterator[Any]:
            with lease(*args, **kwargs) as held:
                mutate()
                yield held

        monkeypatch.setattr(profiles.runtime, "construction_lease", changed)
    elif stage == "health":
        health = profiles._health

        def changed_health(*args: Any, **kwargs: Any) -> None:
            health(*args, **kwargs)
            mutate()

        monkeypatch.setattr(profiles, "_health", changed_health)
    else:
        retain = profiles.runtime.retention_lease

        @contextmanager
        def changed_retention(*args: Any, **kwargs: Any) -> Iterator[Any]:
            with retain(*args, **kwargs) as held:
                mutate()
                yield held

        monkeypatch.setattr(profiles.runtime, "retention_lease", changed_retention)
    with pytest.raises(RuntimeError, match="source identity changed"):
        _provision(profiles, root, payload, expected_source_version=identity.runtime_version)
    assert (root / "current-version").read_bytes() == selected
    assert marker.read_bytes() == before


def test_prepared_source_cannot_substitute_missing_owned_library(
    profiles: ModuleType, payload: Path, commands: list[list[str]], tmp_path: Path,
) -> None:
    missing = payload / "libs" / profiles.LIBRARIES[0]
    shutil.rmtree(missing)
    ambient = payload.parent.parent / "libs" / profiles.LIBRARIES[0]
    ambient.mkdir(parents=True)
    (ambient / "pyproject.toml").write_text('version = "1.0.0"\n')
    _prepare_source(profiles, payload)
    with pytest.raises(RuntimeError, match="source not found in the owning payload"):
        _provision(profiles, tmp_path / "runtime", payload)
    assert commands == []


def test_source_stamp_retains_full_assembly_after_original_is_unavailable(
    profiles: ModuleType, payload: Path, commands: list[list[str]], tmp_path: Path,
) -> None:
    identity = _prepare_source(profiles, payload)
    snapshot = profiles.stamp_source(tmp_path / "runtime", payload, identity.runtime_version)
    assert profiles.stamp_source(tmp_path / "runtime", payload, identity.runtime_version) == snapshot
    payload.rename(tmp_path / "unavailable")
    assert profiles.source.read_source_identity(snapshot, "1.0.0") == identity
    result = _provision(profiles, tmp_path / "runtime", snapshot)
    assert result["runtimeSource"] == identity.document()
    assert all(str(snapshot / "libs" / name) in _install_command(commands)
               for name in profiles.LIBRARIES)


@pytest.mark.parametrize("operation", ["stamp", "provision"])
def test_source_handoff_refuses_valid_receipt_change_with_unchanged_generation(
    profiles: ModuleType, payload: Path, commands: list[list[str]], tmp_path: Path, operation: str,
) -> None:
    root = tmp_path / "runtime"
    _provision(profiles, root, payload)
    before = {path.relative_to(root): path.read_bytes()
              for path in root.rglob("*") if path.is_file()}
    selected = _prepare_source(profiles, payload)
    expected = json.dumps(profiles.source.manifest_fields(selected), sort_keys=True)
    document = selected.document()
    document["source_tree"] = "c" * 40
    (payload / "runtime-source.json").write_text(json.dumps(document), encoding="utf-8")
    changed = profiles.source.read_source_identity(payload, "1.0.0")
    assert changed.runtime_version == selected.runtime_version
    assert changed != selected
    commands.clear()
    with pytest.raises(RuntimeError, match="source identity changed after runtime selection"):
        if operation == "stamp":
            profiles.stamp_source(
                root, payload, selected.runtime_version, expected_identity=expected,
            )
        else:
            _provision(
                profiles, root, payload, expected_source_version=selected.runtime_version,
                expected_source_identity=expected,
            )
    assert commands == []
    assert {path.relative_to(root): path.read_bytes()
            for path in root.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("host", ["pwsh", "powershell", "bash"])
@pytest.mark.parametrize("receipt_change", [False, True])
def test_native_source_stamp_publishes_only_verified_complete_snapshot(
    profiles: ModuleType, payload: Path, tmp_path: Path, host: str, receipt_change: bool,
) -> None:
    if host == "powershell" and os.name != "nt":
        pytest.skip("Windows PowerShell unavailable")
    if host == "bash" and os.name == "nt":
        pytest.skip("Native POSIX installer requires flock or real symlinks, not Git Bash")
    shell = shutil.which(host)
    if shell is None:
        pytest.skip(f"{host} unavailable")
    shutil.copytree(SCRIPTS, payload / "scripts")
    _materialize_construction(payload / "scripts")
    if receipt_change:
        # The POSIX install-lock functions live in the sourced transaction helper.
        entry = payload / "scripts" / (
            "legacy-transaction.sh" if host == "bash" else "init.ps1"
        )
        text = entry.read_text(encoding="utf-8")
        if host == "bash":
            boundary = "_enter_install_lock() {\n"
            mutation = (
                '    "$(_source_python)" -I -c '
                "'import json,sys; from pathlib import Path; p=Path(sys.argv[1]); "
                'd=json.loads(p.read_text()); d["source_tree"]="c"*40; '
                "p.write_text(json.dumps(d))' \"$PLUGIN_DIR/runtime-source.json\"\n"
            )
        else:
            boundary = (
                "function Enter-InstallLock {\n"
                "    param([int]$TimeoutSec = (Get-InstallLockTimeoutSec))\n"
            )
            mutation = """
    $receiptPath = Join-Path $PluginDir 'runtime-source.json'
    $receipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
    $receipt.source_tree = 'c' * 40
    [IO.File]::WriteAllText($receiptPath, ($receipt | ConvertTo-Json -Compress -Depth 8),
        (New-Object Text.UTF8Encoding $false))
"""
        assert text.count(boundary) == 1
        entry.write_text(text.replace(boundary, boundary + mutation), encoding="utf-8")
    (payload / "tests").mkdir()
    (payload / "tests" / "input.txt").write_text("hashed input\n")
    identity = _prepare_source(profiles, payload)
    home = tmp_path / "home"
    home.mkdir()
    root = home / ".agent-containers"
    previous = {"current-version": b"previous", "payload-dir": b"previous-payload",
                "stamped-version": b"previous"}
    if receipt_change:
        root.mkdir()
        for name, value in previous.items():
            (root / name).write_bytes(value)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("COPILOT_", "AGENT_CONTAINERS_"))}
    env.update(HOME=home.as_posix(), USERPROFILE=str(home),
               COPILOT_EXTENSIONS_TEST_CONTAINED="1",
               COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC="2")
    if host == "bash":
        arguments = [shell, str(payload / "scripts" / "init.sh"),
                     "stamp", "--install-dir", str(root)]
    else:
        arguments = [shell, "-NoProfile", "-File", str(payload / "scripts" / "init.ps1"),
                     "stamp", "-InstallDir", str(root)]
    with tempfile.TemporaryFile(mode="w+b") as output:
        try:
            result = subprocess.run(
                arguments, cwd=home, env=env, stdout=output, stderr=subprocess.STDOUT, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired:
            output.seek(0)
            pytest.fail(output.read().decode("utf-8", errors="replace"))
        output.seek(0)
        detail = output.read().decode("utf-8", errors="replace")
        if receipt_change:
            assert result.returncode != 0, detail
            assert "Source runtime identity changed after runtime selection" in detail
            changed = profiles.source.read_source_identity(payload, "1.0.0")
            assert changed.runtime_version == identity.runtime_version
            assert changed != identity
            for name, value in previous.items():
                assert (root / name).read_bytes() == value
            assert not (root / "snapshots").exists()
            assert not (home / ".local" / "bin").exists()
            return
        assert result.returncode == 0, detail
    snapshot = Path((root / "payload-dir").read_text().strip())
    assert snapshot.name == identity.runtime_version
    assert profiles.source.read_source_identity(snapshot, "1.0.0") == identity
    assert (snapshot / "tests" / "input.txt").is_file()
    assert not (root / "current-version").exists()
    assert not (root / "versions").exists()


def test_relative_install_root_and_payload_use_the_callers_directory(
    profiles: ModuleType, payload: Path, commands: list[list[str]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    result = _provision(profiles, Path("runtime"), Path("..") / "payload")
    assert Path(result["venv"]).is_relative_to(caller / "runtime")
    create = next(call for call in commands if any(Path(arg).stem == "runtime-construction" for arg in call))
    assert Path(create[-1]).is_absolute()
    install = _install_command(commands)
    assert str(payload) in install
    assert (caller / "runtime" / "current-version").is_file()
    assert not (payload / "runtime").exists()


@pytest.mark.parametrize("paramiko", [False, True])
def test_legacy_selected_runtime_preserves_installed_ssh_capability(
    profiles: ModuleType, tmp_path: Path, paramiko: bool
) -> None:
    root = tmp_path / "runtime"
    slot = profiles.runtime.slot(root, "0.9.0")
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"legacy fixture")
    if paramiko:
        metadata = slot / "Lib" / "site-packages" / "paramiko-4.0.0.dist-info"
        metadata.mkdir(parents=True)
        (metadata / "METADATA").write_text("Name: paramiko\nVersion: 4.0.0\n")
    profiles.runtime.mark_complete(root, "0.9.0")
    profiles.runtime.activate(root, "0.9.0", link_free=True)
    assert profiles.selected_profile(root) == ("provider-exec" if paramiko else "base")
    assert profiles.application_profile(root)["extras"] == (["provider-exec"] if paramiko else [])
    assert not (root / "runtime-profile.json").exists()


@pytest.mark.parametrize("value", [
    {"schema": "foreign", "version": 1, "profile": "base"},
    {"schema": "copilot-extensions.agent-containers.runtime-profile",
     "version": 1, "profile": "unknown"},
    {"schema": "copilot-extensions.agent-containers.runtime-profile",
     "version": True, "profile": "base"},
    [],
])
def test_invalid_durable_profile_fails_closed(profiles: ModuleType, tmp_path: Path, value: object) -> None:
    (tmp_path / "runtime-profile.json").write_text(json.dumps(value))
    with pytest.raises(RuntimeError):
        profiles.selected_profile(tmp_path)


def test_package_failure_reports_only_bounded_sanitized_tail(profiles: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = (
        "import sys; "
        "print('\\n'.join('line-' + str(i) for i in range(60))); "
        "print('https://fixture:private@example.invalid/pkg token=private'); "
        "sys.exit(7)"
    )
    with pytest.raises(RuntimeError, match="exit 7"):
        profiles._run([sys.executable, "-c", script], cwd=tmp_path)
    error = capsys.readouterr().err
    assert len(error.splitlines()) == 40
    assert "line-0\n" not in error
    assert "private" not in error
    assert "line-59" in error


@pytest.mark.parametrize("missing", ["paramiko", "bcrypt"])
def test_missing_provider_dependency_is_explicit_and_does_not_hide_broken_installs(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    from agent_containers import provider_ssh

    original = builtins.__import__

    def blocked(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "paramiko":
            raise ModuleNotFoundError(f"No module named {missing}", name=missing)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    if missing == "paramiko":
        with pytest.raises(RuntimeError, match="provider-exec profile"):
            provider_ssh._serve_ssh(cast(Any, None), stdin=io.BytesIO(), stdout=io.BytesIO())
    else:
        with pytest.raises(ModuleNotFoundError, match="bcrypt"):
            provider_ssh._serve_ssh(cast(Any, None), stdin=io.BytesIO(), stdout=io.BytesIO())


@pytest.mark.parametrize("profile", ["base", "provider-exec"])
def test_native_bootstrap_accepts_current_profile_without_reconcile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    shell = shutil.which("pwsh" if os.name == "nt" else "bash")
    if shell is None:
        pytest.skip("native installer shell unavailable")
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    ext = "ps1" if os.name == "nt" else "sh"
    bootstrap = scripts / f"bootstrap-check.{ext}"
    shutil.copyfile(SCRIPTS / bootstrap.name, bootstrap)
    (payload / "plugin.json").write_text('{"name":"agent-containers"}')
    (payload / "pyproject.toml").write_text('version = "1.0.0"\n')
    home = tmp_path / "home"
    root = home / ".agent-containers"
    runtime_version = f"1.0.0+{profile}"
    python = root / "versions" / runtime_version / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    python.parent.mkdir(parents=True)
    python.write_bytes(b"bootstrap fixture")
    python.chmod(0o700)
    (root / "current-version").write_text(runtime_version)
    (root / "runtime-profile.json").write_text(json.dumps({
        "schema": "copilot-extensions.agent-containers.runtime-profile",
        "version": 1, "profile": profile,
    }))
    (root / "deploy-manifest.json").write_text(json.dumps({
        "source": {"version": "1.0.0"}, "runtime_version": runtime_version,
        "runtime_profile": profile,
    }))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    argv = [shell]
    if os.name == "nt":
        argv += ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    completed = subprocess.run(
        [*argv, str(bootstrap)], capture_output=True, text=True, timeout=15, check=False
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "{}"
    assert "reconciling" not in completed.stderr
    assert not (root / "reconcile.lock").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 native stderr contract")
def test_signed_python_probe_uses_managed_selection_without_ambient_launcher(tmp_path: Path) -> None:
    shell = Path(os.environ["SystemRoot"]) / (
        "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    if not shell.is_file():
        pytest.skip("Windows PowerShell 5.1 unavailable")
    engine = SCRIPTS.parents[2] / "libs/installer-engine/installer-engine.ps1"
    def quote(value: object) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    command = (
        "$ErrorActionPreference = 'Stop';\n"
        f". {quote(engine)}\n"
        "function py { throw 'ambient launcher was invoked' }\n"
        "function fixture-uv { $script:arguments=@($args) }\n"
        "function Invoke-NativeCapture { param([scriptblock]$Command)\n"
        " & $Command\n"
        f" [pscustomobject]@{{ExitCode=0;Output={quote(sys.executable)}}}\n"
        "}\n"
        "function Get-AuthenticodeSignature { [pscustomobject]@{Status='Valid'} }; "
        "$selected=Get-InstallerManagedSignedBasePython -MinimumVersion ([version]'3.11') "
        "-PythonVersion '3.11' -UvCommand 'fixture-uv'\n"
        "if (($script:arguments -join ' ') -ne "
        "'python find --managed-python --no-project --system --no-python-downloads 3.11') "
        "{ throw 'managed application selection was not used' }\n"
        "if ($ErrorActionPreference -ne 'Stop') { throw 'preference not restored' }; "
        f"if ($selected -ne {quote(sys.executable)}) {{ throw 'candidate not selected' }}"
    )
    result = subprocess.run(
        [str(shell), "-NoProfile", "-Command", command],
        capture_output=True, text=True, check=False, timeout=15,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture(scope="module")
def native_docker_probe(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if os.name != "nt":
        pytest.skip("Windows native executable output contract")
    compiler = Path(os.environ["SystemRoot"]) / (
        "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    )
    if not compiler.is_file():
        pytest.skip("Framework C# compiler unavailable")
    directory = tmp_path_factory.mktemp("native-docker-probe")
    source = directory / "docker.cs"
    source.write_text(
        r"""
using System;
using System.IO;
using System.Text;
class DockerProbe {
    static int Main(string[] args) {
        File.WriteAllLines(Environment.GetEnvironmentVariable("PROBE_ARGV"), args);
        var encoding = Encoding.GetEncoding(1252);
        var stdout = encoding.GetBytes("Docker version caf\u00e9\n");
        Console.OpenStandardOutput().Write(stdout, 0, stdout.Length);
        var stderr = Console.OpenStandardError();
        var diagnostic = encoding.GetBytes("native diagnostic caf\u00e9\n");
        var count = int.Parse(Environment.GetEnvironmentVariable("PROBE_LINES"));
        for (var i = 0; i < count; i++) {
            stderr.Write(diagnostic, 0, diagnostic.Length);
        }
        return int.Parse(Environment.GetEnvironmentVariable("PROBE_EXIT"));
    }
}
""",
        encoding="ascii",
    )
    executable = directory / "docker.exe"
    result = subprocess.run(
        [str(compiler), "/nologo", f"/out:{executable}", str(source)],
        capture_output=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return executable


@pytest.mark.parametrize("shell_name", ["pwsh", "powershell"])
@pytest.mark.parametrize("human_utf8", [False, True], ids=["legacy", "human"])
@pytest.mark.parametrize("exit_code", [0, 23], ids=["success", "failure"])
@pytest.mark.parametrize("lines", [1, 4096], ids=["small", "pipe-capacity"])
def test_docker_preflight_preserves_native_diagnostics_and_outcome(
    tmp_path: Path, native_docker_probe: Path, shell_name: str, human_utf8: bool, exit_code: int, lines: int,
) -> None:
    shell = shutil.which(shell_name)
    if shell is None:
        pytest.skip(f"{shell_name} unavailable")
    source = (SCRIPTS / "init.ps1").read_text(encoding="utf-8")
    start = source.index("if (Get-Command docker -ErrorAction SilentlyContinue) {")
    end = source.index("\n# -- 1. Create directories", start)
    helpers = "\n".join(
        line for line in source.splitlines()
        if line.startswith(("function Write-Ok ", "function Write-Warn ", "function Write-Step "))
    )
    for name in ("installer-human-output.ps1", "versioned_runtime_native.ps1"):
        shutil.copyfile(SCRIPTS / name, tmp_path / name)
    setup_start = source.index("Set-StrictMode -Version 2.0")
    setup_end = source.index("\n. (Join-Path $PSScriptRoot 'legacy-transaction.ps1')", setup_start)
    harness = tmp_path / "docker-preflight.ps1"
    harness.write_text(
        source[source.index("[CmdletBinding()]"):setup_start]
        + "[Console]::OutputEncoding = [Text.Encoding]::GetEncoding(1252)\n"
        "$OutputEncoding = [Text.ASCIIEncoding]::new()\n"
        + source[setup_start:setup_end] + "\n"
        + helpers + "\n"
        + "function Get-Command { [pscustomobject]@{Source=$env:PROBE_DOCKER} }\n"
        + source[start:end] + "\n"
        + "if ([Console]::OutputEncoding.CodePage -ne 1252 -or "
        "$OutputEncoding.CodePage -ne 20127) { throw 'native encodings changed' }\n"
        + "[Console]::Out.WriteLine('continued')\n",
        encoding="ascii",
    )
    receipt = tmp_path / "argv.txt"
    env = {
        **os.environ,
        "PROBE_DOCKER": str(native_docker_probe),
        "PROBE_ARGV": str(receipt),
        "PROBE_EXIT": str(exit_code),
        "PROBE_LINES": str(lines),
        "COPILOT_EXTENSIONS_HUMAN_UTF8": "1" if human_utf8 else "0",
    }
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        env=env, capture_output=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert receipt.read_text(encoding="utf-8-sig").splitlines() == ["--version"]
    encoding = "utf-8" if human_utf8 else "cp1252"
    assert result.stderr.decode(encoding) == "native diagnostic caf\u00e9\n" * lines
    output = result.stdout.decode(encoding).replace("\r\n", "\n")
    assert output.endswith("continued\n")
    if exit_code == 0:
        assert "[OK]   Docker: Docker version caf\u00e9\n" in output
        assert "[WARN]" not in output
    else:
        assert "[OK]" not in output
        assert "Docker: Docker version caf\u00e9\n" in output
        assert "[WARN]" in output
        assert "docker --version failed (exit 23)" in output
        assert "non-fatal" in output


@pytest.mark.skipif(os.name == "nt", reason="native POSIX installer boundary")
@pytest.mark.parametrize("exit_code", [0, 23], ids=["success", "failure"])
@pytest.mark.parametrize("lines", [1, 4096], ids=["small", "pipe-capacity"])
def test_posix_docker_preflight_preserves_diagnostics_and_outcome(tmp_path: Path, exit_code: int, lines: int) -> None:
    shell = shutil.which("bash")
    if shell is None:
        pytest.skip("Bash unavailable")
    source = (SCRIPTS / "init.sh").read_text(encoding="utf-8")
    start = source.index("if command -v docker >/dev/null 2>&1; then")
    end = source.index("\n_ensure_uv_index", start)
    helpers = "\n".join(
        line for line in source.splitlines()
        if line.startswith(("_ok()", "_warn()", "_step()"))
    )
    commands = tmp_path / "commands"
    commands.mkdir()
    docker = commands / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$@" > "$PROBE_ARGV"\n'
        "printf 'Docker version caf\\303\\251\\n'\n"
        "i=0\n"
        'while [ "$i" -lt "$PROBE_LINES" ]; do\n'
        "    printf 'native diagnostic caf\\303\\251\\n' >&2\n"
        "    i=$((i + 1))\n"
        "done\n"
        'exit "$PROBE_EXIT"\n',
        encoding="ascii",
    )
    docker.chmod(0o700)
    harness = tmp_path / "preflight.sh"
    harness.write_text(
        "set -euo pipefail\n" + helpers + "\n" + source[start:end]
        + "\nprintf 'continued\\n'\n", encoding="ascii",
    )
    receipt = tmp_path / "argv.txt"
    result = subprocess.run(
        [shell, str(harness)], capture_output=True, timeout=10,
        env={
            **os.environ, "PATH": str(commands) + os.pathsep + os.environ.get("PATH", ""),
            "PROBE_ARGV": str(receipt), "PROBE_EXIT": str(exit_code), "PROBE_LINES": str(lines),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert receipt.read_text(encoding="utf-8").splitlines() == ["--version"]
    output = result.stdout.decode("utf-8")
    error = result.stderr.decode("utf-8")
    assert error.count("native diagnostic caf\u00e9\n") == lines
    assert output.endswith("continued\n")
    if exit_code == 0:
        assert "[OK]   Docker: Docker version caf\u00e9\n" in output
        assert "[WARN]" not in error
    else:
        assert "[OK]" not in output
        assert "Docker: Docker version caf\u00e9\n" in error
        assert "docker --version failed (exit 23)" in error
        assert "non-fatal" in error


@pytest.mark.parametrize("extension", ["ps1", "sh"])
def test_marketplace_self_stage_preserves_caller_relative_root_and_profile(tmp_path: Path, extension: str) -> None:
    shell: str
    if extension == "ps1":
        if os.name != "nt":
            pytest.skip("native Windows PowerShell staging contract")
        shell = str(
            Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        if not Path(shell).is_file():
            pytest.skip("Windows PowerShell 5.1 unavailable")
    else:
        found = shutil.which("bash")
        if os.name == "nt" or found is None:
            pytest.skip("native POSIX staging contract")
        shell = found
    home = tmp_path / "home"
    home.mkdir()
    caller = tmp_path / "caller"
    caller.mkdir()
    payload = home / ".copilot" / "installed-plugins" / "example" / "agent-containers"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    (payload / "plugin.json").write_text('{"name":"agent-containers"}\n', encoding="utf-8")
    source = (SCRIPTS / f"init.{extension}").read_text(encoding="utf-8")
    marker = "# === end install-contract:v4 self-stage ==="
    prefix = source[:source.index(marker) + len(marker)]
    receipt = tmp_path / "staged-arguments.txt"
    if extension == "ps1":
        for name in ("versioned_runtime_native.ps1", "legacy-transaction.ps1"):
            shutil.copy2(SCRIPTS / name, scripts / name)
        body = (
            '[System.IO.File]::WriteAllLines($env:PROFILE_STAGE_RECEIPT, '
            '[string[]]@($InstallDir, $Profile, $Action, [string]$Force.IsPresent, '
            '$env:COPILOT_PLUGIN_STAGED_FROM))\n'
        )
        arguments = [
            str(shell), "-NoProfile", "-File", str(scripts / "init.ps1"),
            "-Action", "provision", "-InstallDir", ".\\new runtime\\nested",
            "-Profile", "provider-exec", "-Force",
        ]
    else:
        body = (
            'printf "%s\\n" "$INSTALL_DIR" "$PROFILE" "$ACTION" "$FORCE" '
            '"$COPILOT_PLUGIN_STAGED_FROM" > "$PROFILE_STAGE_RECEIPT"\n'
        )
        arguments = [
            shell, str(scripts / "init.sh"), "provision",
            "--install-dir", "./new runtime/nested", "--profile", "provider-exec", "--force",
        ]
    (scripts / f"init.{extension}").write_text(prefix + "\n" + body, encoding="utf-8")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("COPILOT_", "AGENT_CONTAINERS_"))}
    env.update(HOME=str(home), USERPROFILE=str(home), PROFILE_STAGE_RECEIPT=str(receipt),
               COPILOT_EXTENSIONS_TEST_CONTAINED="1", COPILOT_PLUGIN_INSTALL_DEADLINE_SEC="20")
    completed = subprocess.run(
        arguments, cwd=caller, env=env, capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    root, profile, action, force, origin = receipt.read_text(encoding="utf-8-sig").splitlines()
    assert Path(root).is_absolute()
    assert Path(root).resolve() == caller / "new runtime" / "nested"
    assert (profile, action, force) == (
        "provider-exec", "provision", "True" if extension == "ps1" else "1",
    )
    assert Path(origin).resolve() == payload
    assert not (home / "new runtime").exists()


@pytest.mark.parametrize("escape", [False, True])
def test_native_health_requires_all_helper_code_inside_the_immutable_slot(
    profiles: ModuleType, tmp_path: Path, escape: bool,
) -> None:
    root = tmp_path / "runtime"
    subprocess.run(
        [sys.executable, "-m", "venv", "--copies", "--without-pip", str(root)],
        check=True, capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = root / (
        "Lib/site-packages" if os.name == "nt"
        else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )
    for name in ["agent_containers", *(name.replace("-", "_") for name in profiles.LIBRARIES)]:
        (site / f"{name}.py").write_text("", encoding="utf-8")
    if escape:
        external = tmp_path / "mutable-source"
        external.mkdir()
        (site / "credential_relay.py").rename(external / "credential_relay.py")
        (site / "editable-helper.pth").write_text(str(external) + "\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="Runtime command failed"):
            profiles._health(python, "base", cwd=tmp_path)
    else:
        profiles._health(python, "base", cwd=tmp_path)
