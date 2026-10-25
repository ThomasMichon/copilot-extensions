"""Prepared-source Manager currentness and recovery; no live installation."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import sqlite3
import sys

import pytest

from worktree_manager import self_install as si

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_manager_runtime_install import _environment, _prepare_constructed, _run


def _prepare_source_repair(directory, constructed):
    directory.mkdir()
    env = _environment(Path(constructed["directory"]))
    root, previous, original = (
        Path(constructed[key]) for key in ("root", "slot", "qualified")
    )
    payload = directory / "payload"
    si._copy_payload(original, payload, mark_complete=False)
    source = si._runtime_installer().runtime_source
    identity = source.read_source_identity(payload, si.payload_version(payload), plugin="worktree-manager")
    (payload / "source-fixture.txt").write_text("Independent prepared-assembly repair fixture\n")
    digest = source.assembly_hash(payload)
    identity = source.SourceIdentity(
        identity.plugin, identity.package_version, identity.source_commit, identity.source_tree,
        digest, source.runtime_version(identity.package_version, identity.source_commit, digest),
    )
    (payload / "runtime-source.json").write_text(json.dumps(identity.document()))
    target = si.version_slot(identity.runtime_version, root)
    config = root / "config.toml"
    config.write_text('[source]\nref = "fixture-canary"\n[runtime_source]\nkind = "marketplace"\n')
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
        for name in ("HOME", "USERPROFILE", "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR"):
            patch.setenv(name, env[name])
        # No alternate data-root override: both installed interpreters resolve
        # the fixture's existing default root, as the global binstub does.
        patch.delenv("WORKTREE_MANAGER_ROOT", raising=False)
        si._runtime_installer().activate(root, Path(constructed["target"]).name, expected=previous.name)
        planned = si.self_install(payload, root=root, dry_run=True)
        assert planned.action == "planned" and planned.slot == str(target), planned
        selected = si.self_install(payload, root=root, dry_run=False)
        assert selected.action == "installed" and selected.slot == str(target), selected
        initial = si.self_install(payload, root=root, dry_run=True)
        assert initial.action == "already-current" and initial.marker == target.name, initial
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
            repaired = si.self_install(payload, root=root, dry_run=False)
        assert repaired.action == "installed", repaired
        assert "controlled installed Manager health failure" in diagnostics.getvalue()
        repair = Path(repaired.slot)
        assert repair.name.startswith(target.name + ".repair.") and repair != target
        again = si.self_install(payload, root=root, dry_run=False)
        assert again.action == "already-current" and again.slot == repaired.slot, again
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
        "reused": again.slot == repaired.slot, "selection": si.current_version(root),
        "retained": all(path.read_bytes() == content for path, content in retained.items()),
        "data_preserved": all((path.read_bytes(), path.stat().st_dev, path.stat().st_ino) == record
                              for path, record in before.items()),
    }


def test_source_generation_repair_reuses_exact_selection_and_preserves_default_data():
    result = json.loads(Path(os.environ["MANAGER_RUNTIME_ACCEPTANCE_DATA"]).read_text())["source_repair"]
    assert result["reused"] and result["retained"] and result["data_preserved"]
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
