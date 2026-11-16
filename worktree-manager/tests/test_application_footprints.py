"""Manager orchestration uses real slot-local shell boundaries, never Worktrees."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from worktree_manager import application_footprints as footprints


@pytest.fixture(autouse=True)
def isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


def application_set(
    root: Path, rows: list[tuple[str, str, list[str]]],
) -> Path:
    source = root / "footprint"
    source.mkdir()
    (source / "footprint.json").write_text(json.dumps({
        "schema": "copilot-extensions.application-footprint", "version": 1,
        "apps": [
            {"name": name, "version": version, "dependencies": dependencies}
            for name, version, dependencies in rows
        ],
    }), encoding="utf-8")
    for name, version, _dependencies in rows:
        app = source / "apps" / name
        (app / "bin").mkdir(parents=True)
        variable = name.upper().replace("-", "_") + "_ROOT"
        (app / "application.json").write_text(json.dumps({
            "name": name, "commands": [name], "root_environment": variable,
            "construction": {"runtime_version": version},
        }), encoding="utf-8")
        (app / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "{version}"\n', encoding="utf-8",
        )
        (app / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        (app / "bin" / "setup.ps1").write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f"$root = $env:{variable}\n"
            "$staging = $args -contains '--stage'\n"
            "$phase = if ($staging) { 'stage' } else { 'activate' }\n"
            f'Add-Content -LiteralPath $env:FOOTPRINT_TEST_LOG -Value "{name}:$phase"\n'
            "if ($staging -and (Test-Path -LiteralPath (Join-Path $root 'fail-stage'))) { exit 7 }\n"
            f"if (-not $staging) {{ Set-Content -LiteralPath (Join-Path $root 'current-version') -Value '{version}' }}\n",
            encoding="utf-8",
        )
        (app / "bin" / "setup.sh").write_text(
            "#!/bin/sh\nset -eu\n"
            f'root="${variable}"\n'
            'phase=activate\nif [ "${1:-}" = "--stage" ]; then phase=stage; fi\n'
            f'printf "%s\\n" "{name}:$phase" >> "$FOOTPRINT_TEST_LOG"\n'
            'if [ "$phase" = stage ] && [ -f "$root/fail-stage" ]; then exit 7; fi\n'
            f'if [ "$phase" = activate ]; then printf "%s\\n" "{version}" > "$root/current-version"; fi\n',
            encoding="utf-8",
        )
    return source


def test_plan_selects_dependency_closure_without_touching_installation(tmp_path: Path) -> None:
    source = application_set(tmp_path, [
        ("consumer", "2.0", ["provider"]), ("unrelated", "1.0", []),
        ("provider", "1.0", []),
    ])
    target = tmp_path / "installed"
    planned = footprints.plan(source, target, selected=("consumer",))
    assert [app.name for app in planned] == ["provider", "consumer"]
    assert planned[1].slot == target / "consumer" / "versions" / "2.0"
    assert not target.exists()


@pytest.mark.parametrize("dependencies", [["missing"], ["consumer"]])
def test_missing_or_cyclic_dependency_refuses_before_copy(
    tmp_path: Path, dependencies: list[str],
) -> None:
    source = application_set(tmp_path, [("consumer", "1.0", dependencies)])
    target = tmp_path / "installed"
    with pytest.raises(ValueError, match="dependency"):
        footprints.plan(source, target)
    assert not target.exists()


@pytest.mark.parametrize("member", [".venv", "state"])
def test_footprint_cannot_copy_runtime_state(tmp_path: Path, member: str) -> None:
    source = application_set(tmp_path, [("example", "1.0", [])])
    (source / "apps" / "example" / member).mkdir()
    with pytest.raises(ValueError, match="prepared environment or runtime state"):
        footprints.plan(source, tmp_path / "installed")


def test_root_environment_conflict_refuses_before_copy(tmp_path: Path) -> None:
    source = application_set(tmp_path, [("consumer", "1.0", ["provider"]), ("provider", "1.0", [])])
    declaration = source / "apps" / "consumer" / "application.json"
    record = json.loads(declaration.read_text(encoding="utf-8"))
    record["root_environment"] = "PROVIDER_ROOT"
    declaration.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="root-environment declarations conflict"):
        footprints.plan(source, tmp_path / "installed")
    assert not (tmp_path / "installed").exists()


@pytest.mark.parametrize("relative", ["src/example/state/link", "src/example/.venv/link", "state"])
def test_copy_verifies_entries_before_pruning_only_installed_runtime_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str,
) -> None:
    source = application_set(tmp_path, [("example", "1.0", [])])
    application = footprints.plan(source, tmp_path / "installed")[0]
    copied = application.source
    if relative == "state":
        shutil.copytree(application.source, application.slot)
        copied = application.slot
    target = copied / relative
    target.mkdir(parents=True)
    original = Path.lstat
    def indirect(path: Path, *args: object, **kwargs: object) -> object:
        info = original(path)
        if path == target:
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_file_attributes=getattr(info, "st_file_attributes", 0) | 0x400,
            )
        return info
    monkeypatch.setattr(Path, "lstat", indirect)
    with pytest.raises(ValueError, match="must not be indirect"):
        footprints._verify_copy(copied, application)


def test_real_setup_stages_entire_closure_before_dependency_ordered_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = application_set(tmp_path, [
        ("consumer", "2.0", ["provider"]), ("provider", "1.0", []),
    ])
    log = tmp_path / "setup-events"
    monkeypatch.setenv("FOOTPRINT_TEST_LOG", str(log))
    # Ambient selectors cannot redirect setup into an incumbent installation.
    incumbent = tmp_path / "legacy"
    incumbent.mkdir()
    (incumbent / "current-version").write_text("old\n", encoding="utf-8")
    monkeypatch.setenv("CONSUMER_ROOT", str(incumbent))
    monkeypatch.setenv("PROVIDER_ROOT", str(incumbent))
    planned = footprints.plan(source, tmp_path / "installed")
    consumer = planned[1].source / "bin"
    with (consumer / "setup.ps1").open("a", encoding="utf-8") as script:
        script.write(
            "if (-not $staging) { Set-Content -LiteralPath (Join-Path $root 'provider-root') "
            "-Value $env:PROVIDER_ROOT }\n",
        )
    with (consumer / "setup.sh").open("a", encoding="utf-8") as script:
        script.write(
            'if [ "$phase" = activate ]; then printf "%s\\n" "$PROVIDER_ROOT" > "$root/provider-root"; fi\n',
        )
    footprints.install(planned, stage=False, deadline=time.monotonic() + 60)
    assert log.read_text(encoding="utf-8").splitlines() == [
        "provider:stage", "consumer:stage", "provider:activate", "consumer:activate",
    ]
    assert (planned[1].root / "current-version").read_text(encoding="utf-8").strip() == "2.0"
    assert (incumbent / "current-version").read_text(encoding="utf-8") == "old\n"
    assert (planned[1].root / "provider-root").read_text(encoding="utf-8").strip() == str(planned[0].root)
    assert not (planned[0].root / "state").exists()


def test_stage_failure_never_activates_any_application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = application_set(tmp_path, [
        ("consumer", "2.0", ["provider"]), ("provider", "1.0", []),
    ])
    log = tmp_path / "setup-events"
    monkeypatch.setenv("FOOTPRINT_TEST_LOG", str(log))
    planned = footprints.plan(source, tmp_path / "installed")
    planned[1].root.mkdir(parents=True)
    (planned[1].root / "fail-stage").touch()
    with pytest.raises(subprocess.CalledProcessError) as error:
        footprints.install(planned, stage=False, deadline=time.monotonic() + 60)
    assert error.value.returncode == 7
    assert log.read_text(encoding="utf-8").splitlines() == ["provider:stage", "consumer:stage"]
    assert all(not (app.root / "current-version").exists() for app in planned)


def test_repeated_staging_preserves_active_selection_and_disposable_slot_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = application_set(tmp_path, [("example", "2.0", [])])
    log = tmp_path / "setup-events"
    monkeypatch.setenv("FOOTPRINT_TEST_LOG", str(log))
    planned = footprints.plan(source, tmp_path / "installed")
    planned[0].root.mkdir(parents=True)
    marker = planned[0].root / "current-version"
    marker.write_text("1.0\n", encoding="utf-8")
    footprints.install(planned, stage=True, deadline=time.monotonic() + 30)
    state = planned[0].slot / "state"
    state.mkdir()
    (state / "temporary.db").write_bytes(b"discardable")
    footprints.install(planned, stage=True, deadline=time.monotonic() + 30)
    assert marker.read_text(encoding="utf-8") == "1.0\n"
    assert (state / "temporary.db").read_bytes() == b"discardable"
    assert log.read_text(encoding="utf-8").splitlines() == ["example:stage", "example:stage"]


def test_cli_plan_never_submits_or_loads_worktrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    import update_driver
    from worktree_manager import engine_client

    source = application_set(tmp_path, [("example", "1.0", [])])
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("planning reached an effectful or Worktrees boundary")
    monkeypatch.setattr(update_driver, "run_explicit", forbidden)
    monkeypatch.setattr(engine_client, "run_engine_passthrough", forbidden)
    assert footprints.main([str(source), "--apps-root", str(tmp_path / "installed")]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "planned"
    assert not (tmp_path / "installed").exists()


def test_apply_uses_existing_finite_driver_not_a_worktrees_installer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import update_driver
    from worktree_manager import engine_client

    source = application_set(tmp_path, [("example", "1.0", [])])
    seen: list[tuple[update_driver.RequestSpec, update_driver.TrustedRunner]] = []
    def explicit(
        spec: update_driver.RequestSpec, *, runner: update_driver.TrustedRunner,
    ) -> SimpleNamespace:
        seen.append((spec, runner))
        return SimpleNamespace(id="fixture-request")
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Manager reached the Worktrees installer")
    monkeypatch.setattr(update_driver, "borrowed_custody", lambda: None)
    monkeypatch.setattr(update_driver, "run_explicit", explicit)
    monkeypatch.setattr(update_driver, "wait", lambda *_args, **_kwargs: SimpleNamespace(
        state="completed", exit_code=7,
    ))
    monkeypatch.setattr(engine_client, "run_engine_passthrough", forbidden)
    assert footprints.main([
        str(source), "--apps-root", str(tmp_path / "installed"), "--apply",
    ]) == 7
    spec, _runner = seen[0]
    assert spec.backend == "manager-application-footprints"
    assert spec.source.startswith("footprint:")
    assert "--apply" in spec.argv
    assert not (tmp_path / "installed").exists()


@pytest.mark.parametrize("rejected_input", ["source", "versions", "slot", "trailing-dot"])
def test_rejected_input_does_not_fence_a_corrected_request_on_the_same_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rejected_input: str,
) -> None:
    import update_driver

    source = application_set(tmp_path, [("example", "1.0", [])])
    log = tmp_path / "setup-events"
    monkeypatch.setenv("FOOTPRINT_TEST_LOG", str(log))
    target = tmp_path / "installed"
    if rejected_input == "source":
        rejected = source / "apps" / "example" / "src" / "example" / "state" / "linked"
        rejected.mkdir(parents=True)
    elif rejected_input == "versions":
        rejected = target / "example" / "versions"
        rejected.mkdir(parents=True)
    elif rejected_input == "slot":
        rejected = target / "example" / "versions" / "1.0"
        shutil.copytree(source / "apps" / "example", rejected)
    else:
        rejected = None
    declarations = [source / "footprint.json", source / "apps" / "example" / "application.json"]
    originals = [path.read_bytes() for path in declarations]
    if rejected_input == "trailing-dot":
        for path in declarations:
            path.write_bytes(path.read_bytes().replace(b'"1.0"', b'"1.0."'))
    before = sorted(path.relative_to(target) for path in target.rglob("*")) if target.exists() else None
    original = Path.lstat
    def indirect(path: Path, *args: object, **kwargs: object) -> object:
        info = original(path)
        if path == rejected:
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_file_attributes=getattr(info, "st_file_attributes", 0) | 0x400,
            )
        return info
    arguments = [str(source), "--apps-root", str(target), "--apply", "--stage"]
    with monkeypatch.context() as rejection:
        rejection.setattr(Path, "lstat", indirect)
        assert footprints.main(arguments) == 1
    after = sorted(path.relative_to(target) for path in target.rglob("*")) if target.exists() else None
    assert after == before
    assert not (update_driver.default_root() / "queue.sqlite3").exists()
    assert not log.exists()
    for path, content in zip(declarations, originals):
        path.write_bytes(content)
    assert footprints.main(arguments) == 0
    requests = update_driver.status()
    assert isinstance(requests, tuple)
    assert len(requests) == 1
    assert requests[0].state == "completed" and requests[0].exit_code == 0
    assert log.read_text(encoding="utf-8").splitlines() == ["example:stage"]


@pytest.mark.parametrize("entry,fail_stage", [("console", False), ("console", True), ("module", False)])
def test_real_cli_driver_and_slot_setup_complete_without_worktrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str, fail_stage: bool,
) -> None:
    import update_driver
    from agent_procutil.owned_process import run_installation_owned

    source = application_set(tmp_path, [("example", "1.0", [])])
    log = tmp_path / "setup-events"
    monkeypatch.setenv("FOOTPRINT_TEST_LOG", str(log))
    target = tmp_path / "installed"
    if fail_stage:
        (target / "example").mkdir(parents=True)
        (target / "example" / "fail-stage").touch()
    command = Path(sys.executable).parent / (
        "worktree-manager-install.exe" if os.name == "nt" else "worktree-manager-install"
    )
    invocation = [str(command)] if entry == "console" else [
        sys.executable, "-I", "-B", "-m", "worktree_manager.application_footprints",
    ]
    result = run_installation_owned([
        *invocation, str(source), "--apps-root", str(target),
        "--apply", "--stage", "--timeout", "45",
    ], timeout=50, max_stdout_bytes=65536, max_stderr_bytes=65536)
    expected = 7 if fail_stage else 0
    assert result.returncode == expected, (result.stdout, result.stderr)
    assert log.read_text(encoding="utf-8").splitlines() == ["example:stage"]
    assert not (target / "example" / "current-version").exists()
    requests = update_driver.status()
    assert isinstance(requests, tuple)
    assert requests[-1].spec.backend == "manager-application-footprints"
    assert requests[-1].state == "completed" and requests[-1].exit_code == expected
