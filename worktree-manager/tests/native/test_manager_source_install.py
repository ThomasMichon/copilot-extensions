"""Prepared-source Manager currentness and recovery; no live installation."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys

import pytest

from worktree_manager import self_install as si
from worktree_manager import source_config

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_manager_runtime_install import (
    SOURCE, _environment, _prepare_command, _prepare_constructed, _run,
)


def _repository(directory, env):
    repo = directory / "upstream"
    payload = repo / "worktree-manager"
    si._copy_payload(SOURCE, payload, mark_complete=False)
    tools = repo / "tools"
    tools.mkdir()
    for name in json.loads((SOURCE / "packaged-assets.json").read_text())["files"]:
        shutil.copy2(SOURCE.parent / "tools" / name, tools / name)
    (payload / "plugin.json").write_text(json.dumps({
        "name": "worktree-manager", "version": si.payload_version(payload),
    }))
    for arguments in (
        ["init", "-q", "-b", "main"], ["add", "."],
        ["-c", "user.name=Example", "-c", "user.email=fixture@example.invalid",
         "commit", "-qm", "Isolated release source"],
        ["switch", "-qc", "dev"],
    ):
        result = _run(["git", "-C", repo, *arguments], env, directory)
        assert result.returncode == 0, result.stderr
    (payload / "source-fixture.txt").write_text("Merged development acquisition witness\n")
    for arguments in (
        ["add", "."],
        ["-c", "user.name=Example", "-c", "user.email=fixture@example.invalid",
         "commit", "-qm", "Merged development source"],
        ["switch", "-q", "main"],
    ):
        result = _run(["git", "-C", repo, *arguments], env, directory)
        assert result.returncode == 0, result.stderr
    return repo


def _prepare_source_repair(directory, constructed):
    directory.mkdir()
    env = _environment(Path(constructed["directory"]))
    root, previous = (
        Path(constructed[key]) for key in ("root", "slot")
    )
    repo = _repository(directory, env)
    source = si._runtime_installer().runtime_source
    config = root / "config.toml"
    config.write_text('[runtime_source]\nchannel = "main"\nrepo = "https://example.invalid/service.git"\n')
    source_config.set_source(repo=os.path.relpath(repo, directory), ref="dev", root=root)
    database = root / "continuity.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE witness (value TEXT); INSERT INTO witness VALUES ('retained');"
            "PRAGMA user_version = 7;"
        )
    before = {path: (path.read_bytes(), path.stat().st_dev, path.stat().st_ino)
              for path in (config, database)}
    commands = {path: path.read_bytes()
                for path in (Path(env["USERPROFILE"]) / ".local" / "bin").glob("worktree-manager*")}
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(directory)
        for name in ("HOME", "USERPROFILE", "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR"):
            patch.setenv(name, env[name])
        # No alternate data-root override: both installed interpreters resolve
        # the fixture's existing default root, as the global binstub does.
        patch.delenv("WORKTREE_MANAGER_ROOT", raising=False)
        patch.setattr("worktree_manager.mux_daemon_cutover.activate_after_update",
                      lambda **kw: {"action": "fixture", "slot": str(kw["slot"])})
        si._runtime_installer().activate(root, Path(constructed["target"]).name, expected=previous.name)
        planned = si.self_update(root=root, dry_run=True)
        assert planned.action == "updated", planned
        receipt, = (root / "manager-source/runtime-sources/prepared/worktree-manager").glob("*/runtime-source.json")
        payload = receipt.parent
        identity = source.read_source_identity(payload, si.payload_version(payload), plugin="worktree-manager")
        assert identity is not None
        assert identity.source_commit == _run(["git", "-C", repo, "rev-parse", "dev"], env, directory).stdout.strip()
        assert (payload / "source-fixture.txt").read_text() == "Merged development acquisition witness\n"
        target = si.version_slot(identity.runtime_version, root)
        assert si.current_version(root) == Path(constructed["target"]).name
        selected = _prepare_command([
            Path(getattr(sys, "_base_executable", sys.executable)), "-I", "-S", "-B",
            str(SOURCE / "scripts/runtime_install.py"),
            "--root", os.path.relpath(root, directory), "--payload", str(SOURCE), "--", "--version",
        ], env, directory)
        assert selected.returncode == 0, selected.stdout + selected.stderr
        assert si.current_version(root) == target.name
        initial = si.self_update(root=root, dry_run=True)
        assert initial.action == "already-current", initial
        python = si._runtime_installer().runtime.slot_python(root, target.name)
        module = _run([
            python, "-I", "-c", "import worktree_manager.__main__;print(worktree_manager.__main__.__file__)",
        ], env, directory)
        assert module.returncode == 0, module.stderr
        damaged = Path(module.stdout.strip())
        damaged.write_text('raise RuntimeError("controlled installed Manager health failure")\n')
        retained = {path: path.read_bytes() for path in (
            damaged, target / ".install-complete.json", target / ".runtime-input-origins.json",
        )}
        diagnostics = io.StringIO()
        with redirect_stderr(diagnostics):
            repaired = si.self_update(root=root, dry_run=False)
        assert repaired.action == "updated", repaired
        assert "controlled installed Manager health failure" in diagnostics.getvalue()
        repair = si.version_slot(si.current_version(root), root)
        assert repair.name.startswith(target.name + ".repair.") and repair != target
        again = si.self_update(root=root, dry_run=False)
        assert again.action == "already-current" and si.current_version(root) == repair.name, again
        assert repaired.cutover == {"action": "fixture", "slot": str(repair)}
        checkout, = (root / "manager-source/runtime-sources").glob("*/checkout")
        assert _run(["git", "-C", checkout, "rev-parse", "HEAD"], env, directory).stdout == _run([
            "git", "-C", repo, "rev-parse", "main",
        ], env, directory).stdout
        (checkout / "untracked-refusal.txt").write_text("Acquisition must refuse this dirty checkout\n")
        refused = si.self_update(root=root, dry_run=True)
        assert refused.action == "error" and "untracked or ignored" in refused.reason, refused
        assert si.current_version(root) == repair.name
        bootstrap_refused = _prepare_command([
            Path(getattr(sys, "_base_executable", sys.executable)), "-I", "-S", "-B",
            str(SOURCE / "scripts/runtime_install.py"),
            "--root", str(root), "--payload", str(SOURCE), "--", "--version",
        ], env, directory)
        assert bootstrap_refused.returncode == 1, bootstrap_refused.stdout + bootstrap_refused.stderr
        assert "untracked or ignored" in bootstrap_refused.stderr
        assert si.current_version(root) == repair.name
        exact = _prepare_command([
            Path(getattr(sys, "_base_executable", sys.executable)), "-I", "-S", "-B",
            str(SOURCE / "scripts/runtime_install.py"),
            "--root", str(root), "--payload", str(payload), "--", "--version",
        ], env, directory)
        assert exact.returncode == 0, exact.stdout + exact.stderr
        assert si.current_version(root) == repair.name
        plan = si.self_install(payload, root=root, dry_run=True)
        assert plan.action == "already-current" and plan.marker == repair.name, plan
        assert all(path.read_bytes() == content for path, content in retained.items())
        assert all((path.read_bytes(), path.stat().st_dev, path.stat().st_ino) == record
                   for path, record in before.items())
        for slot in (repair, previous):
            si._runtime_installer().activate(root, slot.name, expected=si.current_version(root))
            python = si._runtime_installer().runtime.slot_python(root, slot.name)
            read = _run([
                python, "-I", "-c",
                "import json,sqlite3,sys,worktree_manager;"
                "from worktree_manager.runtime_identity import running_generation;"
                "from worktree_manager.self_install import default_root;"
                "r=default_root();c=sqlite3.connect(r/'continuity.sqlite');"
                "print(json.dumps([str(r),c.execute('SELECT value FROM witness').fetchall(),"
                "c.execute('PRAGMA user_version').fetchone()[0],sys.prefix,"
                "worktree_manager.__file__,running_generation()]));c.close()",
            ], env, directory)
            assert read.returncode == 0, read.stderr
            resolved, rows, schema, prefix, module, generation = json.loads(read.stdout)
            assert Path(resolved).resolve() == root.resolve()
            assert rows == [["retained"]] and schema == 7
            assert Path(prefix).resolve() == slot.resolve()
            assert Path(module).resolve().is_relative_to(slot.resolve())
            assert generation == slot.name
        assert all((path.read_bytes(), path.stat().st_dev, path.stat().st_ino) == record
                   for path, record in before.items())
        assert commands and all(path.read_bytes() == content for path, content in commands.items())
    return {
        "repair": str(repair), "target": str(target), "previous": str(previous),
        "acquired_commit": identity.source_commit, "acquisition_refused": refused.action == "error",
        "reused": again.action == "already-current", "selection": si.current_version(root),
        "retained": all(path.read_bytes() == content for path, content in retained.items()),
        "data_preserved": all((path.read_bytes(), path.stat().st_dev, path.stat().st_ino) == record
                              for path, record in before.items()),
    }


def test_source_generation_repair_reuses_exact_selection_and_preserves_default_data():
    result = json.loads(Path(os.environ["MANAGER_RUNTIME_ACCEPTANCE_DATA"]).read_text())["source_repair"]
    assert result["reused"] and result["retained"] and result["data_preserved"]
    assert result["acquisition_refused"] and len(result["acquired_commit"]) == 40
    assert result["selection"] == Path(result["previous"]).name
    assert Path(result["repair"]).name.startswith(Path(result["target"]).name + ".repair.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", type=Path, required=True)
    destination = parser.parse_args().prepare
    print("Manager source acceptance: installed same-package generations", flush=True)
    constructed = _prepare_constructed(destination / "first", default_data_root=True)
    print("Manager source acceptance: exact source repair and default-data recovery", flush=True)
    data = {"source_repair": _prepare_source_repair(destination / "source-repair", constructed)}
    (destination / "fixtures.json").write_text(json.dumps(data), encoding="utf-8")
