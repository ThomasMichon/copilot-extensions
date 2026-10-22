"""Archived updater consumer proof; all mutable state belongs to the fixture."""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import venv
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_manager_runtime_install import LEGACY_RELEASE, _LEGACY_CONTROLLER, _environment

REPO = Path(__file__).resolve().parents[3]
UPDATER_RELEASE = "f70c81f61ae809f6fe0b01a42000da7b3bb8e92c"
OLD_UPDATER = "1.24.91-dev1"
NEW_UPDATER = "1.24.92-dev1"
NEW_MANAGER = "0.6.36-dev1"


def run(argv, *, cwd, env, data=None, check=True):
    result = subprocess.run(
        list(map(str, argv)), cwd=cwd, env=env, input=data,
        capture_output=True, **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    if check and result.returncode:
        raise AssertionError(
            f"{list(map(str, argv))}: {result.returncode}\n"
            + result.stdout.decode("utf-8", errors="replace")
            + result.stderr.decode("utf-8", errors="replace")
        )
    return result


def archive(git, revision, destination, paths, env):
    destination.mkdir()
    blob = run([git, "archive", "--format=tar", revision, "--", *paths], cwd=REPO, env=env).stdout
    with tarfile.open(fileobj=io.BytesIO(blob)) as source:
        source.extractall(destination, filter="data")


def snapshot(git, destination, env):
    destination.mkdir()
    run(
        [git, "-c", "core.longpaths=true", "checkout-index", "--all",
         "--prefix", str(destination) + os.sep],
        cwd=REPO, env=env,
    )
    script = (
        "import sys;from pathlib import Path;"
        "root=Path(sys.argv[1]);sys.path.insert(0,str(root/'tools'));"
        "import accumulate_bumps as a,materialize_main as m;"
        "result=a.apply({'agent-worktrees':(a.read_consumer_version('agent-worktrees'),"
        "'1.24.92-dev1'),'worktree-manager':(a.read_consumer_version('worktree-manager'),"
        "'0.6.36-dev1')});assert set(result)=={'agent-worktrees','worktree-manager'};"
        "log=m.materialize(root,canonical_root=root);assert not any(x.startswith('SKIP ') for x in log)"
    )
    run([sys.executable, "-I", "-B", "-c", script, destination], cwd=destination, env=env)
    for args in (
        ["init", "-q", "-b", "main", "--template="],
        ["add", "."],
        ["-c", "user.name=Updater Fixture", "-c", "user.email=fixture@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "-qm",
         "Controlled published-like updater artifact\n\n"
         "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>\n"
         "Copilot-Session: 00000000-0000-4000-8000-000000000001"],
    ):
        run([git, "-c", "core.longpaths=true", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false",
             "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + str(destination / "empty-hooks"),
             *args], cwd=destination, env=env)
    return run([git, "rev-parse", "HEAD"], cwd=destination, env=env).stdout.decode().strip()


_TOOLS = r"""
import json,os,shutil,subprocess,sys
from pathlib import Path
kind,*args=sys.argv[1:]
state=Path(os.environ['UPDATER_FIXTURE'])
if kind=='git':
    if os.environ.get('FIXTURE_DENY_MANAGER_AUTH') and any('fixture/private-manager' in x for x in args):
        raise SystemExit('Controlled private-source authentication refusal')
    if 'ls-remote' in args and any(x.startswith('https://github.com/') for x in args):
        print('a'*40+'\tHEAD\n'+'a'*40+'\trefs/heads/main')
        raise SystemExit(0)
    if any(x.startswith('https://') for x in args):
        raise SystemExit('Unexpected fixture network acquisition')
    result=subprocess.run([os.environ['FIXTURE_REAL_GIT'],*args],capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW)
    sys.stdout.buffer.write(result.stdout);sys.stderr.buffer.write(result.stderr)
    raise SystemExit(result.returncode)
if kind=='gh':
    if args[:2]==['auth','status']:
        raise SystemExit(0)
    if args[:1]==['api']:
        if os.environ.get('FIXTURE_DENY_MANAGER_AUTH'):
            raise SystemExit('Controlled credential refusal')
        print(json.dumps({'login':'fixture','private':False,'visibility':'public'}))
        raise SystemExit(0)
    raise SystemExit('Unexpected fixture credential request')
if kind=='py':
    args=[x for x in args if x!='-3']
    result=subprocess.run([os.environ['FIXTURE_BASE_PYTHON'],*args],capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW)
    sys.stdout.buffer.write(result.stdout);sys.stderr.buffer.write(result.stderr)
    raise SystemExit(result.returncode)
if kind=='copilot':
    with (state/'copilot-calls.jsonl').open('a',encoding='utf-8') as stream:
        stream.write(json.dumps(args)+'\n')
    if args[:2]==['plugin','update']:
        source=state/'published'/'plugins'/'agent-worktrees'
        target=Path(os.environ['USERPROFILE'])/'.copilot'/'installed-plugins'/'copilot-extensions'/'agent-worktrees'
        shutil.rmtree(target)
        shutil.copytree(source,target)
        config=Path(os.environ['USERPROFILE'])/'.copilot'/'config.json'
        value=json.loads(config.read_text())
        value['installedPlugins'][0]['version']='1.24.92-dev1'
        config.write_text(json.dumps(value))
        print('Controlled artifact delivered')
        raise SystemExit(0)
    if args[:3]==['plugin','marketplace','browse']:
        print('agent-worktrees')
    elif args[:3] not in (['plugin','marketplace','update'],['plugin','marketplace','add']):
        raise SystemExit('Unexpected fixture Copilot operation')
    raise SystemExit(0)
raise SystemExit('Unknown fixture tool')
"""


def tools(directory, env, base_python, git):
    bin_dir = Path(env["USERPROFILE"]) / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = directory / "fixture-tools.py"
    script.write_text(_TOOLS, encoding="utf-8")
    for name in ("copilot", "git", "gh", "py"):
        (bin_dir / f"{name}.cmd").write_bytes((
            '@echo off\r\n'
            f'"{base_python}" -I -B "{script}" {name} %*\r\n'
            "exit /b %errorlevel%\r\n"
        ).encode())
    (bin_dir / "psmux.cmd").write_bytes(b"@echo off\r\necho psmux 3.3.8\r\n")
    env.update(
        PATH=str(bin_dir) + os.pathsep + env["PATH"],
        UPDATER_FIXTURE=str(directory), FIXTURE_REAL_GIT=git,
        FIXTURE_BASE_PYTHON=str(base_python), UV_NO_SYNC="1",
        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
    )


def configuration(directory, env, old_payload):
    home = Path(env["USERPROFILE"])
    state = home / ".agent-worktrees"
    state.mkdir()
    (state / "bin").mkdir()
    config = home / ".fixture"
    config.mkdir()
    project = directory / "project"
    project.mkdir()
    settings = project / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "enabledPlugins": {"agent-worktrees@copilot-extensions": True},
    }))
    (config / "config.yaml").write_text(json.dumps({
        "repo_name": "fixture", "machine": "fixture-host", "terminal_profiles": [],
        "repos": {"fixture": {"anchor": str(project), "default_branch": "main",
                              "pr": {"enabled": False}}},
    }))
    (state / "projects.yaml").write_text(json.dumps({
        "projects": {"fixture": {"config_dir": str(config), "anchor": str(project),
                                  "display_name": "Fixture"}},
    }))
    (state / "repos.yaml").write_text("schema_version: 1\nrepos: {}\n")
    copilot = home / ".copilot"
    copilot.mkdir(exist_ok=True)
    (copilot / "config.json").write_text(json.dumps({"installedPlugins": [{
        "name": "agent-worktrees", "marketplace": "copilot-extensions",
        "version": OLD_UPDATER, "enabled": True,
    }]}))
    (copilot / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"agent-worktrees@copilot-extensions": True},
        "extraKnownMarketplaces": {"copilot-extensions": {"source": {
            "source": "github", "repo": "ThomasMichon/copilot-extensions",
        }}},
    }))
    payload = copilot / "installed-plugins" / "copilot-extensions" / "agent-worktrees"
    payload.parent.mkdir(parents=True)
    shutil.copytree(old_payload, payload)
    env["AGENT_WORKTREES_PAYLOAD_ROOT"] = str(payload)
    local = directory / "local-app-data"
    local.mkdir()
    env["LOCALAPPDATA"] = str(local)
    terminal = local / "Packages" / "Microsoft.WindowsTerminal_8wekyb3d8bbwe" / "LocalState"
    terminal.mkdir(parents=True)
    (terminal / "settings.json").write_text('{"profiles":{"list":[]}}')
    (terminal / "state.json").write_text('{"generatedProfiles":[]}')
    return state, payload, project


def environment(directory):
    env = _environment(directory)
    for key in list(env):
        if key.startswith("GIT_") or key in (
            "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "AGENT_HOME",
            "AGENT_PROJECT", "AGENT_WORKTREES_PROJECT", "WORKTREE_MANAGER_ENGINE_ARGV",
            "AGENT_WORKTREES_UPDATE_CONTEXT", "COPILOT_PROJECT_DIR",
            "AGENT_WORKTREES_OWNER_REF", "AGENT_WORKTREES_CONFIG_ROOT",
        ):
            env.pop(key, None)
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
    env["CE_UPDATE_AUTH"] = "on"
    env["LOCALAPPDATA"] = str(directory / "local-app-data")
    return env


def prepare(directory):
    if os.name != "nt":
        raise RuntimeError("This explicit native contract requires Windows and its COM profile writer")
    env = environment(directory)
    git = shutil.which("git")
    uv = shutil.which("uv")
    assert git and uv
    base = Path(getattr(sys, "_base_executable", sys.executable))
    archive(git, UPDATER_RELEASE, directory / "old-updater", ["plugins/agent-worktrees"], env)
    archive(git, LEGACY_RELEASE, directory / "old-manager", ["worktree-manager"], env)
    published = directory / "published"
    commit = snapshot(git, published, env)
    tools(directory, env, base, git)
    aw_root, payload, project = configuration(
        directory, env, directory / "old-updater" / "plugins" / "agent-worktrees",
    )
    installed = run([
        base, "-I", "-B", payload / "scripts" / "runtime_install.py",
        "--root", aw_root, "--plugin", payload, "--payload", payload, "--uv", uv,
        "--base-python", base, "--result", directory / "old-updater-installed.json",
    ], cwd=directory, env=env)
    (directory / "old-updater-construction.log").write_bytes(installed.stdout + installed.stderr)
    assert (aw_root / "current-version").read_text().strip() == OLD_UPDATER
    manager_root = Path(env["USERPROFILE"]) / ".worktree-manager"
    legacy = directory / "old-manager" / "worktree-manager"
    run([base, "-I", "-B", "-c", _LEGACY_CONTROLLER, legacy, legacy, manager_root],
        cwd=directory, env=env)
    legacy_slot = manager_root / "versions" / "0.6.35-dev1"
    venv.EnvBuilder(with_pip=False).create(legacy_slot / ".venv")
    (directory / "legacy-manager.cmd").write_bytes(
        (Path(env["USERPROFILE"]) / ".local" / "bin" / "worktree-manager.cmd").read_bytes(),
    )
    (manager_root / "config.toml").write_text(
        f"[source]\nrepo={json.dumps(str(published))}\nref={json.dumps(commit)}\n",
    )
    entry = [shutil.which("pwsh"), "-NoProfile", "-File", payload / "bin" / "payload" / "agent-worktrees.ps1"]
    version = run([*entry, "--version"], cwd=project, env=env)
    assert OLD_UPDATER in version.stdout.decode(), version.stdout + version.stderr
    return {
        "git": git, "base_python": str(base), "aw_root": str(aw_root),
        "payload": str(payload), "project": str(project), "manager_root": str(manager_root),
        "legacy_slot": str(legacy_slot), "source_commit": commit,
    }


def exercise(directory):
    data = json.loads((directory / "updater-setup.json").read_text())
    env = environment(directory)
    tools(directory, env, Path(data["base_python"]), data["git"])
    aw_root, payload, project, manager_root, legacy_slot = (
        Path(data[key]) for key in ("aw_root", "payload", "project", "manager_root", "legacy_slot")
    )
    entry = [shutil.which("pwsh"), "-NoProfile", "-File", payload / "bin" / "payload" / "agent-worktrees.ps1"]
    outcome = run([*entry, "-p", "fixture", "update"], cwd=project, env=env, check=False)
    (directory / "normal-update.stdout").write_bytes(outcome.stdout)
    (directory / "normal-update.stderr").write_bytes(outcome.stderr)
    assert outcome.returncode == 0, (
        outcome.stdout.decode("utf-8", errors="replace") + outcome.stderr.decode("utf-8", errors="replace")
    )
    assert (aw_root / "current-version").read_text().strip() == NEW_UPDATER
    manager_slot = manager_root / "versions" / (manager_root / "current-version").read_text().strip()
    assert manager_slot != legacy_slot and (manager_slot / ".install-complete.json").is_file()
    manager = Path(env["USERPROFILE"]) / ".local" / "bin" / "worktree-manager.cmd"
    profiles = run([manager, "refresh-profiles", "fixture", "--machine", "fixture-host"],
                   cwd=project, env={**env, "WORKTREE_MANAGER_ROOT": str(manager_root)})
    report = json.loads(profiles.stdout)
    assert report["status"] == "verified_current"
    assert Path(report["fragment"]).is_relative_to(Path(env["LOCALAPPDATA"]))
    return {
        "old_updater": OLD_UPDATER, "new_updater": NEW_UPDATER,
        "old_manager": "0.6.35-dev1", "new_manager": NEW_MANAGER,
        "source_commit": data["source_commit"], "normal_update_exit": outcome.returncode,
        "profile_status": report["status"], "manager_slot": str(manager_slot),
        "scope": "archived normal CLI, controlled payload/auth transport, actual canonical construction and private profile writer",
    }


def exercise_safety(directory):
    data = json.loads((directory / "updater-setup.json").read_text())
    witness = json.loads((directory / "fixtures.json").read_text())
    env = environment(directory)
    tools(directory, env, Path(data["base_python"]), data["git"])
    root, aw_root, payload, project = (
        Path(data[key]) for key in ("manager_root", "aw_root", "payload", "project")
    )
    manager = Path(env["USERPROFILE"]) / ".local" / "bin" / "worktree-manager.cmd"
    registry = Path(env["AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR"]) / "worktree-manager.json"
    config = root / "config.toml"
    baseline = {path: path.read_bytes() if path.exists() else None for path in (
        root / "current-version", root / "last-known-good", manager, registry, config,
    )}
    slot = aw_root / "versions" / NEW_UPDATER
    python = slot / "Scripts" / "python.exe"
    entry = [shutil.which("pwsh"), "-NoProfile", "-File", payload / "bin" / "payload" / "agent-worktrees.ps1"]

    def restore():
        for path, value in baseline.items():
            if value is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(value)

    def broken():
        restore()
        (root / "current-version").write_text("0.6.35-dev1\n")
        manager.write_bytes((directory / "legacy-manager.cmd").read_bytes())

    def unchanged():
        assert (root / "current-version").read_text().strip() == "0.6.35-dev1"
        assert (root / "last-known-good").read_bytes() == baseline[root / "last-known-good"]
        assert manager.read_bytes() == (directory / "legacy-manager.cmd").read_bytes()
        assert Path(data["legacy_slot"]).is_dir()

    def normal(label, *, arguments=None, extra=None):
        result = run(
            [*(arguments or entry), "-p", "fixture", "update"],
            cwd=project, env={**env, **(extra or {})}, check=False,
        )
        (directory / f"{label}.log").write_bytes(result.stdout + result.stderr)
        return result

    try:
        config.write_text("malformed = [")
        health = run([
            python, "-I", "-B", "-c",
            "import json;from agent_worktrees.manager_recovery import recover;print(json.dumps(recover()))",
        ], cwd=project, env=env)
        assert json.loads(health.stdout)["status"] == "current"
        assert (root / "current-version").read_bytes() == baseline[root / "current-version"]
        witness["healthy_reused_without_source"] = True

        broken()
        registry.unlink()
        unadopted = normal("unadopted")
        assert unadopted.returncode == 0, unadopted.stdout + unadopted.stderr
        unchanged()
        assert not registry.exists()
        witness["unadopted_untouched"] = True

        broken()
        config.write_text(
            f"[source]\nrepo={json.dumps(str(directory / 'published'))}\nref=\"missing-selected-pin\"\n",
        )
        pinned = normal("pinned-source-refusal")
        assert pinned.returncode != 0
        assert b"source fetch failed" in pinned.stdout + pinned.stderr
        unchanged()
        witness["source_pin_failure_preserved"] = True

        broken()
        config.write_text(
            '[source]\nrepo="https://github.com/fixture/private-manager"\nref="selected-pin"\n',
        )
        denied = normal("credential-refusal", extra={"FIXTURE_DENY_MANAGER_AUTH": "1"})
        assert denied.returncode != 0
        assert b"authentication is blocked" in denied.stdout + denied.stderr
        unchanged()
        witness["credential_admission_preserved"] = True

        broken()
        legacy_python = Path(data["legacy_slot"]) / ".venv" / "Scripts" / "python.exe"
        interpreter = legacy_python.read_bytes()
        try:
            legacy_python.write_bytes(b"not an executable")
            repaired = normal("unexecutable-interpreter")
            assert repaired.returncode == 0, repaired.stdout + repaired.stderr
            assert (root / "current-version").read_bytes() == baseline[root / "current-version"]
            witness["unexecutable_interpreter_recovers"] = True
        finally:
            legacy_python.write_bytes(interpreter)

        restore()
        canonical_slot = Path(witness["manager_slot"])
        canonical_python = canonical_slot / "Scripts" / "python.exe"
        interpreter = canonical_python.read_bytes()
        try:
            canonical_python.write_bytes(b"not an executable")
            repaired = normal("unexecutable-completed-canonical-interpreter")
            assert repaired.returncode == 0, repaired.stdout + repaired.stderr
            selected = root / "versions" / (root / "current-version").read_text().strip()
            assert selected != canonical_slot and (selected / ".install-complete.json").is_file()
            assert canonical_python.read_bytes() == b"not an executable"
            witness["completed_canonical_interpreter_recovers"] = True
        finally:
            canonical_python.write_bytes(interpreter)

        broken()
        old = aw_root / "versions" / OLD_UPDATER
        # A caller already running when a peer publishes cannot reload itself.
        old_entry = [
            old / "Scripts" / "python.exe", "-I", "-S", "-B",
            old / ".runtime-bootstrap" / "runtime_launcher.py", "-m", "agent_worktrees",
        ]
        skipped = normal("old-caller-skipped-installer", arguments=old_entry)
        assert b"skipping installer" in skipped.stdout + skipped.stderr
        unchanged()
        retried = normal("next-normal-invocation")
        assert retried.returncode == 0, retried.stdout + retried.stderr
        assert (root / "current-version").read_bytes() == baseline[root / "current-version"]
        assert manager.read_bytes() == baseline[manager]
        witness["skipped_installer_next_invocation_recovers"] = True
        return witness
    finally:
        restore()


@pytest.fixture(scope="module")
def witness():
    return json.loads(Path(os.environ["MANAGER_RUNTIME_ACCEPTANCE_DATA"]).read_text())


def test_archived_normal_update_recovers_manager_without_operator_bootstrap(witness):
    assert witness["old_updater"] == OLD_UPDATER
    assert witness["new_updater"] == NEW_UPDATER
    assert witness["normal_update_exit"] == 0
    assert witness["profile_status"] == "verified_current"


@pytest.mark.parametrize("case", [
    "healthy_reused_without_source", "unadopted_untouched", "source_pin_failure_preserved",
    "credential_admission_preserved", "unexecutable_interpreter_recovers",
    "completed_canonical_interpreter_recovers", "skipped_installer_next_invocation_recovers",
])
def test_installed_recovery_admission_and_concurrent_caller(witness, case):
    assert witness[case] is True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    stage = parser.add_mutually_exclusive_group(required=True)
    stage.add_argument("--prepare", type=Path)
    stage.add_argument("--exercise", type=Path)
    stage.add_argument("--safety", type=Path)
    args = parser.parse_args()
    destination = args.prepare or args.exercise or args.safety
    result = prepare(destination) if args.prepare else (
        exercise(destination) if args.exercise else exercise_safety(destination)
    )
    filename = "updater-setup.json" if args.prepare else "fixtures.json"
    (destination / filename).write_text(json.dumps(result), encoding="utf-8")
