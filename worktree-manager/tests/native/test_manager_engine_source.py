"""Real native engine repair followed by exact-pin suite preparation."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
from types import SimpleNamespace

import pytest
from agent_procutil import no_window_kwargs

from worktree_manager import __main__ as manager
from worktree_manager import agent_plugin_runtime, engine_client, runtime_source_config
from worktree_manager import self_install, update_bootstrap

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_update_engine_repair import REPO, REAL_RUN, repair_case


def _git(root: Path, *arguments: str) -> str:
    result = REAL_RUN(
        ["git", "-c", "core.longpaths=true", "-C", str(root), *arguments],
        capture_output=True, text=True, timeout=20, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def _commit(root: Path, message: str) -> str:
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Example", "-c", "user.email=fixture@example.invalid",
         "commit", "-qm", message)
    return _git(root, "rev-parse", "HEAD")


def _release_git_files(root: Path) -> None:
    if os.name == "nt":
        for directory in root.rglob(".git"):
            if directory.is_dir() and not directory.is_symlink():
                for path in directory.rglob("*"):
                    if path.is_file() and not path.is_symlink():
                        path.chmod(path.stat().st_mode | stat.S_IWRITE)


def _prepare(directory: Path) -> dict:
    directory.mkdir()
    with ExitStack() as ownership:
        patch = ownership.enter_context(pytest.MonkeyPatch.context())
        request = SimpleNamespace(addfinalizer=ownership.callback, param="copilot-extensions")
        # Reuse the existing account-isolated native ownership fixture. Its
        # construction fence remains in the marketplace owner, not our artifact.
        fixture = repair_case.__wrapped__(directory, patch, request)
        case = next(fixture)
        ownership.callback(fixture.close)
        ownership.callback(_release_git_files, case.home.parent)
        shutil.copy2(
            case.fresh / "scripts/runtime_install_real.py",
            case.fresh / "scripts/runtime_install.py",
        )
        shutil.copytree(REPO / "worktree-manager/bin", case.source / "worktree-manager/bin")
        documentation = case.source / "docs/patterns"
        documentation.mkdir(parents=True)
        shutil.copy2(
            REPO / "docs/patterns/entity-relationship-model.md",
            documentation / "entity-relationship-model.md",
        )
        _git(case.source, "init", "-q", "-b", "main")
        _commit(case.source, "Isolated engine release source")
        _git(case.source, "switch", "-qc", "dev")
        (case.fresh / "source-fixture.txt").write_text("original merged development source\n")
        selected = _commit(case.source, "Isolated merged development source")
        _git(case.source, "switch", "-q", "main")
        project = case.home / ".example"
        project.mkdir()
        (project / "config.yaml").write_text(json.dumps({
            "repo_name": "example", "repos": {"example": {"anchor": str(case.source)}},
        }))
        runtime_source_config.set_source(runtime_source_config.RuntimeSource(
            "dev", repo=str(case.source), allow_dev=True,
        ))
        config = self_install.default_root() / "config.toml"
        database = case.runtime / "continuity.sqlite"
        with closing(sqlite3.connect(database)) as connection:
            with connection:
                connection.executescript(
                    "CREATE TABLE witness(value TEXT); INSERT INTO witness VALUES('retained');"
                    "PRAGMA user_version = 7;"
                )
        before = {path: (path.read_bytes(), path.stat().st_dev, path.stat().st_ino)
                  for path in (config, database)}
        original_owner = case.receipt
        path = os.environ["PATH"].split(os.pathsep)
        real_uv = shutil.which("uv", path=os.pathsep.join(path[1:]))
        assert real_uv is not None
        # Use the actual constructor rather than the fixture's forbidden-UV
        # fence. The group supervisor still owns every descendant and deadline.
        patch.setenv("PATH", str(Path(real_uv).parent) + os.pathsep + os.environ["PATH"])
        patch.delenv("UV_OFFLINE", raising=False)
        patch.delenv("UV_DEFAULT_INDEX", raising=False)
        patch.setattr(agent_plugin_runtime, "_load_installation_context", lambda: case.primitive)
        patch.setattr(engine_client, "_INHERITED_ENGINE_COMMAND", None)
        patch.setattr(engine_client, "_ENGINE_CMD_OVERRIDE", None)
        patch.setattr(
            self_install, "self_update", lambda **kw: pytest.fail("fresh Manager is independent"),
        )
        native = []
        advanced = []

        def invoke(command, environment, cwd):
            def run_native():
                result = REAL_RUN(
                    command, env=environment, cwd=cwd, capture_output=True,
                    text=True, encoding="utf-8", **no_window_kwargs(),
                )
                assert result.returncode == 0, result.stdout + result.stderr
                return result.returncode

            run_native()
            original = (case.runtime / "current-version").read_text().strip()
            slot = case.runtime / "versions" / original
            python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            observed = REAL_RUN(
                [str(python), "-I", "-c",
                 "import agent_worktrees.__main__;print(agent_worktrees.__main__.__file__)"],
                capture_output=True, text=True, env=environment, timeout=20, **no_window_kwargs(),
            )
            assert observed.returncode == 0, observed.stdout + observed.stderr
            damaged = Path(observed.stdout.strip())
            damaged.write_text('raise RuntimeError("controlled same-source engine health failure")\n')
            retained = {path: path.read_bytes() for path in (
                damaged, slot / ".install-complete.json", slot / ".runtime-input-origins.json",
            )}
            run_native()
            repaired = (case.runtime / "current-version").read_text().strip()
            assert repaired != original and repaired.startswith(original + ".repair.")
            rc = run_native()
            assert (case.runtime / "current-version").read_text().strip() == repaired
            assert all(path.read_bytes() == content for path, content in retained.items())
            native.append({
                "artifact": str(cwd),
                "pin": json.loads(environment["COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN"]),
                "owner": environment["COPILOT_PLUGIN_STAGED_FROM"],
                "runtime_only": environment["AGENT_WORKTREES_TARGETED_UPDATE"],
                "original": original, "repaired": repaired, "healthy_reuse": True,
            })
            _git(case.source, "switch", "-q", "dev")
            (case.fresh / "source-fixture.txt").write_text("later merged development source\n")
            advanced.append(_commit(case.source, "Advance source after repair"))
            _git(case.source, "switch", "-q", "main")
            return rc

        patch.setattr(update_bootstrap, "_wait", invoke)
        suite = []

        def execute(project, arguments, **kwargs):
            assert project == "example"
            packet = json.loads(arguments[arguments.index("--runtime-sources") + 1])
            assert set(packet["sources"]) == {"agent-worktrees"}
            source = packet["sources"]["agent-worktrees"]
            assert source["identity"] == native[0]["pin"]
            assert source["identity"]["runtime_source"]["source_commit"] == selected
            assert advanced[0] != selected
            assert Path(source["payload"]) == Path(native[0]["artifact"])
            command = kwargs["engine_command"]
            result = REAL_RUN(
                [*command, "--version"], capture_output=True, text=True,
                env=kwargs["environment"], timeout=20, **no_window_kwargs(),
            )
            assert result.returncode == 0, result.stdout + result.stderr
            observed = REAL_RUN(
                [command[0], "-I", "-c",
                 "import json,sqlite3,sys,agent_worktrees;"
                 "from agent_worktrees.config import legacy_install_dir;"
                 "r=legacy_install_dir();c=sqlite3.connect(r/'continuity.sqlite');"
                 "print(json.dumps([str(r),sys.prefix,agent_worktrees.__file__,"
                 "c.execute('SELECT value FROM witness').fetchall(),"
                 "c.execute('PRAGMA user_version').fetchone()[0]]));c.close()"],
                capture_output=True, text=True, env=kwargs["environment"], timeout=20,
                **no_window_kwargs(),
            )
            assert observed.returncode == 0, observed.stdout + observed.stderr
            root, prefix, module, rows, schema = json.loads(observed.stdout)
            manifest = json.loads(case.manifest.read_text(encoding="utf-8-sig"))
            assert Path(root) == case.runtime and Path(prefix) == Path(manifest["venv"])
            assert Path(module).resolve().is_relative_to(Path(prefix).resolve())
            assert rows == [["retained"]] and schema == 7
            binstub = case.home / ".local/bin" / (
                "agent-worktrees.ps1" if os.name == "nt" else "agent-worktrees"
            )
            argv = (
                [shutil.which("pwsh") or shutil.which("powershell.exe"), "-NoProfile", "-File", str(binstub)]
                if os.name == "nt" else [str(binstub)]
            )
            dispatched = REAL_RUN(
                [*argv, "--version"], capture_output=True, text=True,
                env=kwargs["environment"], timeout=20, **no_window_kwargs(),
            )
            assert dispatched.returncode == 0, dispatched.stdout + dispatched.stderr
            assert "agent-worktrees" in dispatched.stdout
            suite.append({"packet": packet, "command": command})
            return 0

        patch.setattr(engine_client, "run_engine_passthrough", execute)
        assert manager._cmd_update([
            "--repair-engine", str(case.source), "--project", "example",
        ]) == 0
        manifest = json.loads(case.manifest.read_text(encoding="utf-8-sig"))
        assert manifest["commandBinding"] == original_owner
        assert native[0]["owner"] == str(case.incumbent) and native[0]["runtime_only"] == "1"
        assert manifest["runtime_source"]["source_commit"] == selected
        assert not case.effect.exists()
        assert len(case.acquisitions) == 1 and len(native) == len(suite) == 1
        assert all((path.read_bytes(), path.stat().st_dev, path.stat().st_ino) == record
                   for path, record in before.items())
        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute("SELECT value FROM witness").fetchall() == [("retained",)]
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 7
        return {
            "selected": selected, "advanced": advanced[0],
            "installed": manifest["runtime_source"]["source_commit"],
            "suite": suite[0]["packet"]["sources"]["agent-worktrees"]["identity"],
            "owner_retained": manifest["commandBinding"] == original_owner,
            "data_preserved": True, "repair": native[0],
        }


def test_engine_repair_retains_one_commit_and_incumbent_data() -> None:
    path = os.environ.get("MANAGER_RUNTIME_ACCEPTANCE_DATA")
    if path is None:
        pytest.skip("run tools/run-manager-runtime-acceptance.py --contract engine-source")
    observed = json.loads(Path(path).read_text())
    assert observed["selected"] == observed["installed"] != observed["advanced"]
    assert observed["suite"]["runtime_source"]["source_commit"] == observed["selected"]
    assert observed["owner_retained"] and observed["data_preserved"]
    assert observed["repair"]["original"] != observed["repair"]["repaired"]
    assert observed["repair"]["healthy_reuse"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", type=Path, required=True)
    args = parser.parse_args()
    Path(os.environ["MANAGER_RUNTIME_ACCEPTANCE_DATA"]).write_text(
        json.dumps(_prepare(args.prepare / "engine-source")),
    )
