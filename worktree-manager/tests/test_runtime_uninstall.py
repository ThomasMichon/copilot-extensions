"""Synthetic ownership/parser controls; real installed acceptance is separate."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs
from agent_procutil.owned_process import run_owned

from worktree_manager import self_install as si

SOURCE = Path(__file__).resolve().parents[1]
INSTALLER = SOURCE / "scripts" / "runtime_install.py"
si._runtime_installer()
import runtime_uninstall as removal  # noqa: E402


@pytest.fixture(autouse=True)
def private_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR", str(home / "providers"))
    monkeypatch.delenv("AGENT_WORKTREES_CONTROL_PLANE_PROVIDER", raising=False)
    monkeypatch.delenv("WORKTREE_MANAGER_ROOT", raising=False)


def fixture_slot(root, version="1.2.3"):
    """Seal real fixture files through the production construction inventory."""
    runtime = removal.runtime
    slot = root / "versions" / version
    with runtime.construction_lease(root, version):
        payload = slot / "payload"
        (payload / "references").mkdir(parents=True)
        (payload / "pyproject.toml").write_text(
            f'[project]\nname="copilot-extensions-worktree-manager"\nversion="{version}"\n',
            encoding="utf-8",
        )
        shutil.copyfile(SOURCE / "references" / "uninstall-contract.json",
                        payload / "references" / "uninstall-contract.json")
        python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python.parent.mkdir()
        shutil.copyfile(sys.executable, python)
        stubs = si._expected_binstub_contents()
        provider = si._expected_control_plane_provider_manifest(root=root)
        removal.seal_inventory(slot, frontdoors=stubs, provider=provider)
        runtime.mark_complete(root, version, payload_hash="a" * 64)
    (root / "current-version").write_text(version + "\n", encoding="utf-8")
    (root / "last-known-good").write_text(version + "\n", encoding="utf-8")
    si._deploy_binstubs()
    si._write_control_plane_provider_manifest(root=root)
    return slot


def tree_bytes(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def invoke(*args, installer=INSTALLER):
    return run_owned(
        [getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-B",
         str(installer), *map(str, args)], timeout=15,
    )


@pytest.mark.parametrize("apply", [False, True])
def test_absent_target_never_constructs_or_creates_root(tmp_path, apply):
    root = tmp_path / "absent"
    result = invoke("--root", root, "--uninstall", "--json", *(["--apply"] if apply else []))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "absent"
    assert not root.exists()


@pytest.mark.parametrize("arguments", [
    ["--apply"], ["--uninstall", "--typo"],
    ["--uninstall", "--repair-update"], ["--uninstall", "--recover-for-repair"],
    ["--uninstall", "--", "--version"], ["--uninstall", "update"],
])
def test_strict_parser_refuses_before_construction(tmp_path, arguments):
    root = tmp_path / "absent"
    result = invoke("--root", root, *arguments)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert not root.exists()


@pytest.mark.parametrize("declaration", [
    None,
    dict(removal.CONTRACT, generation=2),
    dict(removal.CONTRACT, supported_callers=[2]),
])
def test_selected_provider_expansion_and_rollback_preserve_legacy_syntax(tmp_path, declaration):
    provider = tmp_path / "provider"
    scripts = provider / "scripts"
    scripts.mkdir(parents=True)
    for source in (SOURCE / "scripts").glob("*.py"):
        shutil.copyfile(source, scripts / source.name)
    references = provider / "references"
    references.mkdir()
    contract = references / "uninstall-contract.json"
    if declaration is not None:
        contract.write_text(json.dumps(declaration), encoding="utf-8")
    installer = scripts / "runtime_install.py"
    root = tmp_path / "absent"
    legacy = invoke("--describe-recovery", installer=installer)
    assert legacy.returncode == 0, legacy.stderr
    assert json.loads(legacy.stdout)["schema"] == "copilot-extensions.manager-recovery-entry"
    refused = invoke("--root", root, "--uninstall", "--apply", "--json", installer=installer)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert json.loads(refused.stdout)["status"] == "refused"
    assert not root.exists()
    contract.write_text(json.dumps(removal.CONTRACT), encoding="utf-8")
    admitted = invoke("--root", root, "--uninstall", "--json", installer=installer)
    assert admitted.returncode == 0, admitted.stderr
    assert json.loads(admitted.stdout)["status"] == "absent"
    contract.unlink()
    rollback = invoke("--root", root, "--uninstall", "--apply", "--json", installer=installer)
    assert rollback.returncode == 1 and not root.exists()
    assert json.loads(rollback.stdout)["status"] == "refused"


def test_preview_and_removal_preserve_durable_bytes(tmp_path, capsys):
    root = tmp_path / "manager"
    slot = fixture_slot(root)
    (root / "config.toml").write_text("[source]\nref='custom'\n", encoding="utf-8")
    (root / "projects.json").write_text('{"retained":true}', encoding="utf-8")
    before = tree_bytes(tmp_path)
    assert removal.uninstall(root, apply=False, as_json=True) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "planned"
    assert tree_bytes(tmp_path) == before
    assert removal.uninstall(root, apply=True, as_json=True) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "removed"
    assert not slot.exists()
    assert not (root / "current-version").exists()
    assert not (root / "last-known-good").exists()
    assert (root / "config.toml").read_bytes() == before["manager\\config.toml" if os.name == "nt" else "manager/config.toml"]
    assert (root / "projects.json").read_text(encoding="utf-8") == '{"retained":true}'
    assert not si.binstub_present()
    after = tree_bytes(tmp_path)
    assert removal.uninstall(root, apply=True, as_json=True) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "absent"
    assert tree_bytes(tmp_path) == after


@pytest.mark.parametrize("absolute", [False, True])
def test_cli_normalizes_parent_components_before_ownership_comparison(tmp_path, monkeypatch, absolute):
    root = tmp_path / "manager"
    slot = fixture_slot(root)
    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    target = (caller if absolute else Path()) / ".." / "manager"
    before = tree_bytes(tmp_path)
    preview = invoke("--root", target, "--uninstall", "--json")
    assert preview.returncode == 0, preview.stdout + preview.stderr
    planned = json.loads(preview.stdout)
    assert planned["status"] == "planned" and planned["root"] == str(root.resolve())
    assert tree_bytes(tmp_path) == before
    applied = invoke("--root", target, "--uninstall", "--apply", "--json")
    assert applied.returncode == 0, applied.stdout + applied.stderr
    removed = json.loads(applied.stdout)
    assert removed["status"] == "removed" and removed["root"] == str(root.resolve())
    assert not slot.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory symlink")
def test_root_indirection_is_refused_before_canonicalization(tmp_path, capsys):
    root = tmp_path / "manager"
    fixture_slot(root)
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    before = tree_bytes(tmp_path)
    assert removal.uninstall(alias, apply=True, as_json=True) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "refused" and result["removed"] == []
    assert "Indirect removal path" in result["reason"]
    assert tree_bytes(tmp_path) == before


@pytest.mark.parametrize("apply", [False, True])
def test_unreliable_process_enumeration_refuses_without_effects(tmp_path, monkeypatch, capsys, apply):
    root = tmp_path / "manager"
    fixture_slot(root)
    monkeypatch.setattr(removal.runtime, "_reliable_process_enumeration", lambda: False)
    before = tree_bytes(tmp_path)
    assert removal.uninstall(root, apply=apply, as_json=True) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "refused" and result["removed"] == []
    assert "process enumeration" in result["reason"]
    assert tree_bytes(tmp_path) == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX file creation modes and umask")
@pytest.mark.parametrize("mask", [0o002, 0o000])
def test_permissive_umask_still_creates_private_removable_inventory(tmp_path, capsys, mask):
    root = tmp_path / "manager"
    previous = os.umask(mask)
    try:
        slot = fixture_slot(root)
    finally:
        os.umask(previous)
    assert (slot / removal.INVENTORY).stat().st_mode & 0o777 == 0o600
    before = tree_bytes(tmp_path)
    assert removal.uninstall(root, apply=False, as_json=True) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "planned"
    assert tree_bytes(tmp_path) == before
    assert removal.uninstall(root, apply=True, as_json=True) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "removed"
    assert not slot.exists()


@pytest.mark.parametrize("change", ["artifact", "extra", "inventory", "selector", "frontdoor", "service", "legacy"])
def test_unknown_or_changed_ownership_refuses_without_effects(tmp_path, capsys, change):
    root = tmp_path / "manager"
    slot = fixture_slot(root)
    if change == "artifact":
        next((slot / ("Scripts" if os.name == "nt" else "bin")).iterdir()).write_bytes(b"changed")
    elif change == "extra":
        (slot / "foreign.txt").write_bytes(b"preserve")
    elif change == "inventory":
        (slot / removal.INVENTORY).write_text("{}", encoding="utf-8")
    elif change == "selector":
        (root / "last-known-good").write_text("foreign", encoding="utf-8")
    elif change == "frontdoor":
        si.binstub_present().write_bytes(b"foreign")
    elif change == "service":
        (root / "mux-daemon.lock").write_text("{}", encoding="utf-8")
    else:
        (slot / removal.INVENTORY).unlink()
    before = tree_bytes(tmp_path)
    assert removal.uninstall(root, apply=True, as_json=True) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "refused"
    assert tree_bytes(tmp_path) == before


def test_target_interpreter_refuses_before_lock_or_frontdoor_effects(tmp_path, monkeypatch, capsys):
    root = tmp_path / "manager"
    slot = fixture_slot(root)
    before = tree_bytes(tmp_path)
    monkeypatch.setattr(removal.sys, "executable", str(slot / "Scripts" / "python.exe"))
    assert removal.uninstall(root, apply=True, as_json=True) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "refused"
    assert str(INSTALLER) in result["reason"]
    assert tree_bytes(tmp_path) == before


@pytest.mark.parametrize("missing", [False, True])
def test_shared_frontdoors_require_current_registration(tmp_path, capsys, missing):
    root = tmp_path / "manager"
    fixture_slot(root)
    fixture_slot(tmp_path / "other-manager")
    if missing:
        si._control_plane_provider_manifest_path(root=root).unlink()
    before = tree_bytes(tmp_path)
    assert removal.uninstall(root, apply=True, as_json=True) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "refused"
    assert tree_bytes(tmp_path) == before


@pytest.mark.parametrize("directory", ["", "~/providers", "~/provider registrations"])
def test_home_relative_provider_override_matches_installer(tmp_path, monkeypatch, capsys, directory):
    monkeypatch.setenv("AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR", directory)
    root = tmp_path / "manager"
    slot = fixture_slot(root)
    registry = Path(directory).expanduser() if directory else (
        Path(os.environ["USERPROFILE"]) / ".agent-worktrees" / "control-plane-providers.d"
    )
    provider = registry / "worktree-manager.json"
    assert provider.is_file()
    before = tree_bytes(tmp_path)
    assert removal.uninstall(root, apply=False, as_json=True) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "planned"
    assert tree_bytes(tmp_path) == before
    assert removal.uninstall(root, apply=True, as_json=True) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "removed"
    assert not slot.exists() and not provider.exists()


@pytest.mark.parametrize("selected", [
    "", " ", "worktree-manager", " worktree-manager ", "different-provider", " different-provider ",
])
def test_provider_selection_preserves_empty_and_foreign_semantics(tmp_path, monkeypatch, capsys, selected):
    root = tmp_path / "manager"
    slot = fixture_slot(root)
    monkeypatch.setenv("AGENT_WORKTREES_CONTROL_PLANE_PROVIDER", selected)
    before = tree_bytes(tmp_path)
    refused = selected.strip() == "different-provider"
    assert removal.uninstall(root, apply=True, as_json=True) == int(refused)
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == ("refused" if refused else "removed")
    if refused:
        assert result["removed"] == [] and tree_bytes(tmp_path) == before
    else:
        assert not slot.exists()


def test_settled_daemon_routing_history_is_preserved(tmp_path, capsys):
    from worktree_manager import mux_daemon_process
    from zdd import routing

    root = tmp_path / "manager"
    fixture_slot(root)
    lease = mux_daemon_process.acquire_daemon_lease(root)
    assert lease is not None
    try:
        directory = root / "mux-daemon-routing"
        routing.publish_active(directory, bind="127.0.0.1", port=45678, pid=os.getpid())
        assert routing.clear_if_owner(directory, pid=os.getpid())
    finally:
        mux_daemon_process.release_daemon_lease(lease)
    before = tree_bytes(directory)
    snapshot = tree_bytes(tmp_path)
    assert removal.uninstall(root, apply=False, as_json=True) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "planned"
    assert tree_bytes(tmp_path) == snapshot
    assert removal.uninstall(root, apply=True, as_json=True) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "removed"
    assert tree_bytes(directory) == before
    assert (root / "mux-daemon.spawn.lock").is_file()


def test_real_daemon_spawn_lease_refuses_removal(tmp_path, capsys):
    root = tmp_path / "manager"
    fixture_slot(root)
    code = (
        "import importlib.util,pathlib,sys;"
        "spec=importlib.util.spec_from_file_location('locks',sys.argv[1]);"
        "owner=importlib.util.module_from_spec(spec);spec.loader.exec_module(owner);"
        "stream=open(sys.argv[2],'a+b');"
        "assert owner._try_lock_file_once(stream);print('held',flush=True);"
        "sys.stdin.read();owner._unlock_file(stream);stream.close()"
    )
    with subprocess.Popen(
        [sys.executable, "-I", "-S", "-B", "-c", code,
         str(SOURCE / "src" / "worktree_manager" / "mux_file_lock.py"),
         str(root / "mux-daemon.spawn.lock")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        **no_window_kwargs(),
    ) as child:
        try:
            assert child.stdout.readline().strip() == "held"
            assert removal.uninstall(root, apply=True, as_json=True) == 1
            assert json.loads(capsys.readouterr().out)["status"] == "refused"
            assert (root / "current-version").is_file()
        finally:
            child.communicate("", timeout=5)
        assert child.returncode == 0


@pytest.mark.parametrize("kind", ["construction", "retention", "publication"])
def test_real_competing_kernel_custody_refuses(tmp_path, capsys, kind):
    root = tmp_path / "manager"
    fixture_slot(root)
    code = (
        "import pathlib,sys;sys.path.insert(0,sys.argv[1]);import versioned_runtime as r;"
        "root=pathlib.Path(sys.argv[2]);kind=sys.argv[3];"
        "lease=r._kernel_lease(root,'selectors' if kind=='publication' else '1.2.3',"
        "kind=kind,shared=False,timeout=0);"
        "lease.__enter__();print('held',flush=True);sys.stdin.read();lease.__exit__(None,None,None)"
    )
    with subprocess.Popen(
        [sys.executable, "-I", "-S", "-B", "-c", code, str(SOURCE / "scripts"), str(root), kind],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        **no_window_kwargs(),
    ) as child:
        try:
            assert child.stdout.readline().strip() == "held"
            assert removal.uninstall(root, apply=True, as_json=True) == 1
            assert json.loads(capsys.readouterr().out)["status"] == "refused"
            assert (root / "current-version").read_text(encoding="utf-8").strip() == "1.2.3"
        finally:
            child.communicate("", timeout=5)
        assert child.returncode == 0


def test_partial_failure_is_not_success_or_automatic_rollback(tmp_path, monkeypatch, capsys):
    root = tmp_path / "manager"
    slot = fixture_slot(root)
    original = Path.unlink

    def fail(path, *args, **kwargs):
        if path.is_relative_to(slot):
            raise PermissionError("fixture sharing violation")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail)
    assert removal.uninstall(root, apply=True, as_json=True) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "incomplete" and result["removed"]
    assert slot.exists() and (slot / removal.INVENTORY).exists()
    assert not (root / "current-version").exists()


def test_new_provider_missing_declaration_has_no_effects(tmp_path, monkeypatch, capsys):
    root = tmp_path / "manager"
    fixture_slot(root)
    external = tmp_path / "external" / "scripts"
    external.mkdir(parents=True)
    monkeypatch.setattr(removal, "SCRIPTS", external)
    before = tree_bytes(tmp_path)
    assert removal.uninstall(root, apply=True, as_json=True) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "refused"
    assert tree_bytes(tmp_path) == before


def test_publication_callback_runs_inside_existing_selector_custody(tmp_path):
    root = tmp_path / "manager"
    fixture_slot(root)
    runtime = removal.runtime
    called = []

    def publish():
        with pytest.raises(runtime.RetentionTimeout):
            with runtime._publication_lease(root, timeout=0):
                pytest.fail("frontdoor callback was outside selector custody")
        called.append(True)

    si._runtime_installer().activate(root, "1.2.3", expected="1.2.3", before_publish=publish)
    assert called == [True]


def test_slot_replacement_does_not_inherit_inventory_authority(tmp_path, capsys):
    root = tmp_path / "manager"
    slot = fixture_slot(root)
    retired = tmp_path / "retired"
    slot.rename(retired)
    shutil.copytree(retired, slot)
    before = tree_bytes(tmp_path)
    assert removal.uninstall(root, apply=True, as_json=True) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "refused"
    assert tree_bytes(tmp_path) == before


def test_batch_removes_distinct_current_and_last_known_good(tmp_path, capsys):
    root = tmp_path / "manager"
    previous = fixture_slot(root, "1.2.3")
    selected = fixture_slot(root, "1.2.4")
    (root / "last-known-good").write_text("1.2.3\n", encoding="utf-8")
    assert removal.uninstall(root, apply=True, as_json=True) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "removed"
    assert not previous.exists() and not selected.exists()
    assert not list((root / "versions").iterdir())


def test_missing_root_and_additive_invalid_contract_are_explicit(tmp_path, monkeypatch, capsys):
    result = invoke("--uninstall")
    assert result.returncode == 2
    external = tmp_path / "external"
    scripts = external / "scripts"
    scripts.mkdir(parents=True)
    references = external / "references"
    references.mkdir()
    declaration = dict(removal.CONTRACT, generation=True)
    (references / "uninstall-contract.json").write_text(json.dumps(declaration), encoding="utf-8")
    monkeypatch.setattr(removal, "SCRIPTS", scripts)
    root = tmp_path / "absent"
    assert removal.uninstall(root, apply=False, as_json=True) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "refused"
    assert not root.exists()
