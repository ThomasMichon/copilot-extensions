"""The real runtime fan-out entry point never follows destination links."""

import importlib.util
import os
from pathlib import Path
import stat
import sys

import pytest


SCRIPT = Path(__file__).resolve().with_name("sync-versioned-runtime.py")
SPEC = importlib.util.spec_from_file_location("sync_runtime_destination_tests", SCRIPT)
assert SPEC and SPEC.loader
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)
NAMES = (
    sync.VRT_NAME, *sync.COMPONENT_NAMES,
    sync.DEPENDENCY_POLICY.name, sync.DEPENDENCY_CONFIG.name, *sync.RESOLVER_NAMES,
    *sync.INSTALLER_INSTANCE_NAMES,
)


@pytest.fixture
def repository(tmp_path, monkeypatch):
    runtime = tmp_path / "libs" / "versioned-runtime"
    engine = tmp_path / "libs" / "installer-engine"
    scripts = tmp_path / "plugins" / "example" / "scripts"
    for directory in (runtime, engine, scripts):
        directory.mkdir(parents=True)
    (scripts / "install.sh").write_text("# fixture\n")
    (scripts.parent / "pyproject.toml").write_text("[project]\nname='example'\n")
    for name in NAMES:
        source = (
            sync.installer_instance_sources(tmp_path)[name]
            if name in sync.INSTALLER_INSTANCE_NAMES
            else (engine if name.startswith("installer_dependency_") else runtime) / name
        )
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f"canonical {name}\n")
        (scripts / name).write_bytes(source.read_bytes())
    monkeypatch.setattr(sync, "REPO", tmp_path)
    monkeypatch.setattr(sync, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(sync, "CANONICAL", runtime / sync.VRT_NAME)
    monkeypatch.setattr(sync, "DEPENDENCY_POLICY", engine / sync.DEPENDENCY_POLICY.name)
    monkeypatch.setattr(sync, "DEPENDENCY_CONFIG", engine / sync.DEPENDENCY_CONFIG.name)
    monkeypatch.setattr(sync, "RESOLVER_DIR", runtime)
    return scripts


@pytest.mark.parametrize("check", [False, True], ids=["sync", "check"])
@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("target_state", ["different", "matching", "dangling"])
def test_main_refuses_linked_destinations_without_any_mutation(
    repository, monkeypatch, capsys, check, name, target_state,
):
    scripts = repository
    destination = scripts / name
    canonical = destination.read_bytes()
    destination.unlink()
    target = scripts.parents[2] / "unrelated"
    if target_state != "dangling":
        target.write_bytes(canonical if target_state == "matching" else b"unrelated bytes\n")
        target.chmod(0o600)
    destination.symlink_to(target)
    original_link = os.readlink(destination)
    before = (
        target.read_bytes(), stat.S_IMODE(target.stat().st_mode), target.stat().st_mtime_ns,
    ) if target.exists() else None
    sentinel = next(scripts / item for item in NAMES if item != name)
    sentinel.write_bytes(b"earlier stale destination\n")
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), *(["--check"] if check else [])])

    assert sync.main() == 1
    assert "symlink -- refusing" in capsys.readouterr().err
    assert sentinel.read_bytes() == b"earlier stale destination\n"
    assert destination.is_symlink()
    assert os.readlink(destination) == original_link
    if before is None:
        assert not target.exists()
    else:
        assert (
            target.read_bytes(), stat.S_IMODE(target.stat().st_mode), target.stat().st_mtime_ns,
        ) == before


@pytest.mark.parametrize("check", [False, True], ids=["sync", "check"])
def test_main_refuses_linked_destination_ancestor(repository, monkeypatch, capsys, check):
    scripts = repository
    actual = scripts.with_name("actual-scripts")
    scripts.rename(actual)
    scripts.symlink_to(actual, target_is_directory=True)
    before = {path.name: path.read_bytes() for path in actual.iterdir()}
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), *(["--check"] if check else [])])

    assert sync.main() == 1
    assert "symlink -- refusing" in capsys.readouterr().err
    assert scripts.is_symlink()
    assert {path.name: path.read_bytes() for path in actual.iterdir()} == before


def test_main_repairs_ordinary_missing_and_stale_files_and_is_idempotent(repository, monkeypatch):
    scripts = repository
    (scripts / sync.VRT_NAME).write_bytes(b"stale runtime\n")
    (scripts / sync.DEPENDENCY_CONFIG.name).unlink()
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--check"])
    assert sync.main() == 1
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])
    assert sync.main() == 0
    assert sync.main() == 0
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--check"])
    assert sync.main() == 0
    for name in NAMES:
        source = (
            sync.installer_instance_sources(sync.REPO)[name]
            if name in sync.INSTALLER_INSTANCE_NAMES else (
                sync.DEPENDENCY_POLICY.parent if name.startswith("installer_dependency_")
                else sync.CANONICAL.parent
            ) / name
        )
        assert (scripts / name).read_bytes() == source.read_bytes()


def test_instance_only_sync_preserves_unrelated_runtime_bytes(repository, monkeypatch):
    scripts = repository
    unrelated = scripts / sync.VRT_NAME
    unrelated.write_bytes(b"concurrent runtime edit\n")
    for name in sync.INSTALLER_INSTANCE_NAMES:
        (scripts / name).unlink()
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--only-installer-instances"])

    assert sync.main() == 0
    assert unrelated.read_bytes() == b"concurrent runtime edit\n"
    for name, source in sync.installer_instance_sources(sync.REPO).items():
        assert (scripts / name).read_bytes() == source.read_bytes()


def test_instance_only_sync_ships_only_native_ps_pair_to_wsl_payload(repository, monkeypatch):
    references = sync.REPO / "plugins" / "wsl-setup" / "skills" / "setting-up-wsl" / "references"
    references.mkdir(parents=True)
    (references / "wsl-keepalive.ps1").write_text("# explicit installer\n")
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--only-installer-instances"])

    assert sync.main() == 0
    sources = sync.installer_instance_sources(sync.REPO)
    for name in ("daemon_instances.ps1", "installer-instance-reconcile.ps1"):
        assert (references / name).read_bytes() == sources[name].read_bytes()
    assert {path.name for path in references.iterdir()} == {
        "wsl-keepalive.ps1", "daemon_instances.ps1", "installer-instance-reconcile.ps1",
    }


def test_explicit_native_wheel_fanout_preserves_the_built_executable(repository, monkeypatch):
    import native_daemon_artifacts

    relative = Path("native") / "linux-x86_64" / "daemon-instance"
    monkeypatch.setattr(
        native_daemon_artifacts, "wheel_assets",
        lambda *args, **kwargs: {relative: b"verified built native fixture\n"},
    )
    monkeypatch.setattr(sys, "argv", [
        str(SCRIPT), "--only-installer-instances", "--native-wheel", str(sync.REPO / "built.whl"),
    ])
    assert sync.main() == 0
    executable = repository / relative
    assert executable.read_bytes() == b"verified built native fixture\n"
    if os.name != "nt":
        assert executable.stat().st_mode & 0o111
