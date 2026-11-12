"""Prepared-source Manager currentness and recovery; no live installation."""

from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stderr
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from typing import Any, Iterator

import pytest

from worktree_manager import runtime_source_self_update as update
from worktree_manager import self_install as si
from worktree_manager import source_config

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_manager_runtime_install as release_fixture
from test_manager_runtime_install import (
    SOURCE, _environment, _prepare_command, _prepare_initial_construction,
    _run,
)

# Windows admission starts another interpreter; carry the fixture seam into that child.
_FIXTURE_BOOTSTRAP = """
import json,os,sys
from pathlib import Path
scripts=Path(sys.argv[1])
fixture_code=sys.argv[2]
arguments=sys.argv[3:]
sys.path.insert(0,str(scripts))
import runtime_install as installer
def activate(root,slot,**options):
    root,slot=Path(root).resolve(),Path(slot).resolve()
    assert slot.parent==root/"versions"
    assert installer.runtime.is_complete(root,slot.name)
    with (root/"source-fixture-activation.jsonl").open("a",encoding="utf-8") as output:
        output.write(json.dumps({"pid":os.getpid(),"root":str(root),"slot":str(slot)})+"\\n")
    return {"action":"fixture","slot":str(slot)}
installer._activate_after_bootstrap_update=activate
driver=installer._execution_driver(scripts.parent)
trusted_runner=driver.TrustedRunner
def runner(argv,*args,**options):
    argv=list(argv)
    script=str(scripts/"runtime_install.py")
    if script in argv:
        index=argv.index(script)
        argv[index:index+1]=["-c",fixture_code,str(scripts),fixture_code]
    return trusted_runner(tuple(argv),*args,**options)
driver.TrustedRunner=runner
raise SystemExit(installer.main(arguments))
"""

def _fixture_command(
    argv: list[str | Path], env: dict[str, str], cwd: Path,
) -> subprocess.CompletedProcess[str]:
    argv = list(map(str, argv))
    index = next(index for index, value in enumerate(argv) if Path(value).name == "runtime_install.py")
    scripts = Path(argv[index]).parent
    argv[index:index + 1] = ["-c", _FIXTURE_BOOTSTRAP, str(scripts), _FIXTURE_BOOTSTRAP]
    return _prepare_command(argv, env, cwd)


def _bootstrap(
    root: str | Path, env: dict[str, str], directory: Path, *, snapshot: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    selection = ["--install-source", str(snapshot)] if snapshot is not None else ["--payload", str(SOURCE)]
    return _fixture_command([
        Path(getattr(sys, "_base_executable", sys.executable)), "-I", "-S", "-B",
        SOURCE / "scripts/runtime_install.py", "--root", str(root), *selection, "--", "--version",
    ], env, directory)


@contextmanager
def _source_environment(
    directory: Path, constructed: dict[str, Any],
) -> Iterator[tuple[pytest.MonkeyPatch, dict[str, str]]]:
    env = _environment(Path(constructed["directory"]))
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(directory)
        for name in ("HOME", "USERPROFILE", "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR"):
            patch.setenv(name, env[name])
        patch.delenv("WORKTREE_MANAGER_ROOT", raising=False)
        patch.setattr(
            si._runtime_installer(), "_activate_after_bootstrap_update",
            lambda actual_root, slot: {"action": "fixture", "slot": str(slot)},
        )
        yield patch, env


@contextmanager
def _selected_snapshot() -> Iterator[list[Path]]:
    snapshots: list[Path] = []
    prepare = update.prepare_applications

    def capture(*args: Any, **options: Any) -> dict[str, Path]:
        selected = prepare(*args, **options)
        snapshots.append(selected["worktree-manager"])
        return selected

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(update, "prepare_applications", capture)
        yield snapshots


def _copy_source_preparation_inputs(repo):
    for filename in ("packaged-assets.json", "launch-wrapper-assets.json"):
        declaration = json.loads((SOURCE / filename).read_text())
        for group in declaration.get("groups", [declaration]):
            for name in group["files"]:
                relative = Path(group["canonicalDir"]) / name
                target = repo / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(SOURCE.parent / relative, target)
    shutil.copytree(
        SOURCE.parent / "libs/installer-engine", repo / "libs/installer-engine",
        ignore=shutil.ignore_patterns("tests", "docs", "__pycache__", "*.pyc"),
        dirs_exist_ok=True,
    )


def _repository(directory: Path, env: dict[str, str], constructed: dict[str, Any]) -> Path:
    repo = directory / "upstream"
    repo.mkdir()
    archive = directory / "upstream-source.tar"
    result = _prepare_command([
        "git", "-C", SOURCE.parent, "archive", "--format=tar", "--output", archive,
        constructed["promotion"]["tree"],
    ], env, directory)
    assert result.returncode == 0, result.stdout + result.stderr
    with tarfile.open(archive) as bundle:
        si._safe_extract(bundle, repo)
    declaration = json.loads((repo / "worktree-manager" / "packaged-assets.json").read_text())
    for group in declaration.get("groups", [declaration]):
        for name in group["files"]:
            relative = Path(group["canonicalDir"]) / name
            assert (repo / relative).is_file(), f"Canonical preparation asset is unavailable: {relative}"
    sys.path.insert(0, str(SOURCE.parent / "plugins/agent-worktrees/tests"))
    from runtime_install_fixture import isolate_source_account_profile

    isolate_source_account_profile(repo, Path(env["USERPROFILE"]))
    result = _prepare_command(["git", "-C", repo, "init", "-q", "-b", "main"], env, directory)
    assert result.returncode == 0, result.stderr
    (repo / ".git/info/attributes").write_text("* -text -filter -ident -working-tree-encoding\n")
    for arguments in (
        ["add", "--all", "--force", "."],
        ["-c", "core.hooksPath=.git/hooks", "-c", "commit.gpgsign=false",
         "-c", "user.name=Example", "-c", "user.email=fixture@example.invalid",
         "commit", "-qm", "Isolated release source"],
        ["switch", "-qc", "dev"],
    ):
        result = _prepare_command(["git", "-C", repo, *arguments], env, directory)
        assert result.returncode == 0, result.stderr
    return repo


def test_source_repository_canonical_inputs_materialize_without_runtime_install(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SOURCE.parent / "tools"))
    import prepare_runtime_source

    repo, destination = tmp_path / "source", tmp_path / "prepared"
    consumer = repo / "worktree-manager"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname="copilot-extensions-worktree-manager"\nversion="1.0.0"\n',
        encoding="utf-8",
    )
    for filename in ("packaged-assets.json", "launch-wrapper-assets.json"):
        shutil.copy2(SOURCE / filename, consumer / filename)
    _copy_source_preparation_inputs(repo)
    before = {path.relative_to(repo): path.read_bytes()
              for path in repo.rglob("*") if path.is_file()}
    prepare_runtime_source._materialize(repo, "worktree-manager", destination)
    for filename in ("packaged-assets.json", "launch-wrapper-assets.json"):
        declaration = json.loads((consumer / filename).read_text())
        for group in declaration.get("groups", [declaration]):
            for name in group["files"]:
                assert (destination / group["payloadDir"] / name).read_bytes() == (
                    SOURCE.parent / group["canonicalDir"] / name
                ).read_bytes()
    import materialize_installer_engine

    pairs = materialize_installer_engine.derived_file_pairs(
        repo, destination, copied_consumer=(consumer, destination),
    )
    assert pairs
    for canonical, emitted in pairs:
        assert emitted.read_bytes() == canonical.read_bytes()
    assert before == {path.relative_to(repo): path.read_bytes()
                      for path in repo.rglob("*") if path.is_file()}


def _retained_runtime_identity(slot: Path) -> str:
    launcher = runpy.run_path(str(slot / ".runtime-bootstrap" / "runtime_launcher.py"))
    return launcher["runtime_identity"](slot)


def _prepare_release(directory: Path) -> dict[str, Any]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(release_fixture, "_prepare_command", _fixture_command)
        constructed = _prepare_initial_construction(directory, default_data_root=True)
    return {
        **constructed,
        "runtime_identity": _retained_runtime_identity(Path(constructed["slot"])),
    }


def _prepare_source_qualified(constructed: dict[str, Any]) -> dict[str, Any]:
    directory = Path(constructed["directory"]).parent / "source-repair"
    directory.mkdir()
    root, previous = (Path(constructed[key]) for key in ("root", "slot"))
    with _source_environment(directory, constructed) as (_patch, env):
        repo = _repository(directory, env, constructed)
        source_config.set_source(repo=os.path.relpath(repo, directory), ref="dev", root=root)
        with _selected_snapshot() as snapshots:
            planned = si.self_update(root=root, dry_run=True)
        assert planned.action == "updated" and len(snapshots) == 1, planned
        installed = _bootstrap(root, env, directory, snapshot=snapshots[0])
        assert installed.returncode == 0, installed.stdout + installed.stderr
        target = si.version_slot(si.current_version(root), root)
        assert target != previous and (target / "application.json").is_file()
        assert _retained_runtime_identity(previous) == constructed["runtime_identity"]
        assert _retained_runtime_identity(target) != constructed["runtime_identity"]
        si._runtime_installer().activate(root, previous.name, expected=target.name)
    return {**constructed, "target": str(target), "repo": str(repo)}


def _advance_repository(repo: Path, env: dict[str, str], directory: Path) -> None:
    payload = repo / "worktree-manager"
    (payload / "source-fixture.txt").write_text("Merged development acquisition witness\n")
    for arguments in (
        ["add", "."],
        ["-c", "core.hooksPath=.git/hooks", "-c", "commit.gpgsign=false",
         "-c", "user.name=Example", "-c", "user.email=fixture@example.invalid",
         "commit", "-qm", "Merged development source"],
    ):
        result = _run(["git", "-C", repo, *arguments], env, directory)
        assert result.returncode == 0, result.stderr


def _prepare_source_candidate(directory, constructed):
    env = _environment(Path(constructed["directory"]))
    root, previous = (
        Path(constructed[key]) for key in ("root", "slot")
    )
    repo = Path(constructed["repo"])
    _advance_repository(repo, env, directory)
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
    with _source_environment(directory, constructed) as (_patch, env):
        si._runtime_installer().activate(root, Path(constructed["target"]).name, expected=previous.name)
        with _selected_snapshot() as snapshots:
            planned = si.self_update(root=root, dry_run=True)
        assert planned.action == "updated", planned
        snapshot, = snapshots
        with si._runtime_installer().prepared_manager_inputs(snapshot) as (selected, options):
            payload, identity = selected.payload, selected.identity
            assert "application" in options
        identity = source.read_source_identity(payload, si.payload_version(payload), plugin="worktree-manager")
        assert identity is not None
        assert identity.source_commit == _run(["git", "-C", repo, "rev-parse", "dev"], env, directory).stdout.strip()
        assert (payload / "source-fixture.txt").read_text() == "Merged development acquisition witness\n"
        assert si.current_version(root) == Path(constructed["target"]).name
        selected = _bootstrap(os.path.relpath(root, directory), env, directory, snapshot=snapshot)
        assert selected.returncode == 0, selected.stdout + selected.stderr
        target = si.version_slot(si.current_version(root), root)
        assert target.name.startswith(identity.runtime_version + ".profile.")
        assert (target / "application.json").is_file()
        runtime_identity = _retained_runtime_identity(target)
        assert runtime_identity != constructed["runtime_identity"]
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
    return {
        "repo": str(repo), "snapshot": str(snapshot), "target": str(target),
        "runtime_identity": runtime_identity,
        "acquired_commit": identity.source_commit,
        "before": {str(path): [content.hex(), device, inode]
                   for path, (content, device, inode) in before.items()},
        "commands": {str(path): content.hex() for path, content in commands.items()},
        "retained": {str(path): content.hex() for path, content in retained.items()},
    }


def _prepare_source_repair(directory, constructed, prepared):
    root, previous = (Path(constructed[key]) for key in ("root", "slot"))
    repo, snapshot, target = (Path(prepared[key]) for key in ("repo", "snapshot", "target"))
    before = {Path(path): (bytes.fromhex(record[0]), *record[1:])
              for path, record in prepared["before"].items()}
    commands, retained = (
        {Path(path): bytes.fromhex(content) for path, content in prepared[key].items()}
        for key in ("commands", "retained")
    )
    with _source_environment(directory, constructed) as (_patch, env):
        diagnostics = io.StringIO()
        with redirect_stderr(diagnostics):
            repaired = si.self_update(root=root, dry_run=False)
        assert repaired.action == "updated", repaired
        assert "controlled installed Manager health failure" in diagnostics.getvalue()
        repair = si.version_slot(si.current_version(root), root)
        assert repair.name.startswith(target.name + ".repair.") and repair != target
        assert _retained_runtime_identity(target) == prepared["runtime_identity"]
        repair_identity = _retained_runtime_identity(repair)
        assert len({
            constructed["runtime_identity"], prepared["runtime_identity"], repair_identity,
        }) == 3
        completion = {
            path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in (
                repair / ".install-complete.json", repair / ".runtime-input-origins.json",
                repair / ".runtime-bootstrap" / "runtime-generation",
            )
        }
        again = si.self_update(root=root, dry_run=False)
        assert again.action == "already-current" and si.current_version(root) == repair.name, again
        assert _retained_runtime_identity(repair) == repair_identity
        assert all((path.read_bytes(), path.stat().st_mtime_ns) == record
                   for path, record in completion.items())
        assert repaired.cutover == {"action": "fixture", "slot": str(repair)}
        checkout, = (root / "manager-source/runtime-sources").glob("*/checkout")
        assert _run(["git", "-C", checkout, "rev-parse", "HEAD"], env, directory).stdout == _run([
            "git", "-C", repo, "rev-parse", "main",
        ], env, directory).stdout
        (checkout / "untracked-refusal.txt").write_text("Acquisition must refuse this dirty checkout\n")
        refused = si.self_update(root=root, dry_run=True)
        assert refused.action == "error" and "untracked or ignored" in refused.reason, refused
        assert si.current_version(root) == repair.name
        bootstrap_refused = _bootstrap(root, env, directory)
        assert bootstrap_refused.returncode == 1, bootstrap_refused.stdout + bootstrap_refused.stderr
        assert "untracked or ignored" in bootstrap_refused.stderr
        assert si.current_version(root) == repair.name
        exact = _bootstrap(root, env, directory, snapshot=snapshot)
        assert exact.returncode == 0, exact.stdout + exact.stderr
        assert si.current_version(root) == repair.name
        assert _retained_runtime_identity(repair) == repair_identity
        assert all((path.read_bytes(), path.stat().st_mtime_ns) == record
                   for path, record in completion.items())
        with si._runtime_installer().prepared_manager_inputs(snapshot) as (selected, options):
            plan = si.self_install(
                selected.payload, root=root, dry_run=True,
                **{"_" + name: value for name, value in options.items()},
            )
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
            assert Path(prefix).resolve() == (slot / ".venv" if slot == repair else slot).resolve()
            assert Path(module).resolve().is_relative_to(slot.resolve())
            assert generation == slot.name
        assert all((path.read_bytes(), path.stat().st_dev, path.stat().st_ino) == record
                   for path, record in before.items())
        assert commands and all(path.read_bytes() == content for path, content in commands.items())
    return {
        "repair": str(repair), "target": str(target), "previous": str(previous),
        "runtime_identities": [
            constructed["runtime_identity"], prepared["runtime_identity"], repair_identity,
        ],
        "exact_completion_reused": all(
            (path.read_bytes(), path.stat().st_mtime_ns) == record
            for path, record in completion.items()
        ),
        "acquired_commit": prepared["acquired_commit"], "acquisition_refused": refused.action == "error",
        "reused": again.action == "already-current", "selection": si.current_version(root),
        "retained": all(path.read_bytes() == content for path, content in retained.items()),
        "data_preserved": all((path.read_bytes(), path.stat().st_dev, path.stat().st_ino) == record
                              for path, record in before.items()),
    }


def test_source_generation_repair_reuses_exact_selection_and_preserves_default_data():
    result = json.loads(Path(os.environ["MANAGER_RUNTIME_ACCEPTANCE_DATA"]).read_text())["source_repair"]
    assert result["reused"] and result["retained"] and result["data_preserved"]
    assert len(set(result["runtime_identities"])) == 3
    assert result["exact_completion_reused"]
    assert result["acquisition_refused"] and len(result["acquired_commit"]) == 40
    assert result["selection"] == Path(result["previous"]).name
    assert Path(result["repair"]).name.startswith(Path(result["target"]).name + ".repair.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", type=Path, required=True)
    steps = ("release", "qualified", "source", "repair")
    parser.add_argument("--prepare-step", choices=steps)
    args = parser.parse_args()
    destination = args.prepare
    receipt = destination / "fixtures.json"
    data = json.loads(receipt.read_text(encoding="utf-8")) if args.prepare_step not in (None, "release") else {}
    for step in (args.prepare_step,) if args.prepare_step else steps:
        print(f"Manager source acceptance: {step} construction", flush=True)
        if step == "release":
            data["constructed"] = _prepare_release(destination / "first")
        elif step == "qualified":
            data["constructed"] = _prepare_source_qualified(data["constructed"])
        elif step == "source":
            data["source_candidate"] = _prepare_source_candidate(destination / "source-repair", data["constructed"])
        else:
            data = {"source_repair": _prepare_source_repair(
                destination / "source-repair", data["constructed"], data["source_candidate"],
            )}
        receipt.write_text(json.dumps(data), encoding="utf-8")
