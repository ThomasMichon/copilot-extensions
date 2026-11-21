"""Independent engine repair retains its owner and one exact service selection."""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from worktree_manager import __main__ as manager
from worktree_manager import runtime_source_config as config
from worktree_manager import runtime_source_repair as repair
from worktree_manager import runtime_source_update as update
from worktree_manager import update_bootstrap as bootstrap
from worktree_manager import update_events
from worktree_manager import engine_client
from worktree_manager.runtime_source import ResolvedSource, SourceAcquisitionError


@pytest.fixture
def selected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ResolvedSource:
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "manager"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    config.set_source(config.RuntimeSource("dev", allow_dev=True))
    return ResolvedSource(
        config.configured_source(), tmp_path / "checkout", "a" * 40, "b" * 40,
        "a" * 40, "e" * 40,
    )


def test_one_acquisition_lease_covers_repair_and_suite(
    selected: ResolvedSource, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    @contextmanager
    def acquire(selection: config.RuntimeSource) -> Iterator[ResolvedSource]:
        assert selection == selected.selection and selection.branch == "dev"
        calls.append("acquired")
        yield selected
        calls.append("released")

    def prepare(arguments: list[str], *, runtime_source: ResolvedSource) -> bootstrap.PreparedUpdate:
        assert runtime_source is selected and calls == ["acquired"]
        calls.append("repair")
        return bootstrap.PreparedUpdate(arguments[2:], True)

    def execute(arguments: list[str], run: update_events.UpdateRun, **kwargs: Any) -> int:
        assert kwargs["runtime_source"] is selected and kwargs["fresh_manager"]
        assert arguments == ["--force"] and calls == ["acquired", "repair"]
        calls.append("suite")
        return 17

    monkeypatch.setattr(update, "acquire", acquire)
    monkeypatch.setattr(bootstrap, "prepare_update", prepare)
    monkeypatch.setattr(manager, "_cmd_update_steps", execute)
    monkeypatch.setattr(update_events, "_ut", None)
    assert manager._cmd_update(["--repair-engine", "/fresh/source", "--force"]) == 17
    assert calls == ["acquired", "repair", "suite", "released"]


def test_acquisition_refusal_precedes_repair_and_aggregate(
    selected: ResolvedSource, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    @contextmanager
    def acquire(selection: config.RuntimeSource) -> Iterator[ResolvedSource]:
        raise SourceAcquisitionError("controlled unavailable source")
        yield  # pragma: no cover

    monkeypatch.setattr(update, "acquire", acquire)
    monkeypatch.setattr(bootstrap, "prepare_update", lambda *a, **k: pytest.fail("no repair"))
    monkeypatch.setattr(update_events, "UpdateRun", lambda: pytest.fail("no aggregate"))
    assert manager._cmd_update(["--repair-engine", "/fresh/source"]) == 1
    assert "controlled unavailable source" in capsys.readouterr().err


def test_second_packet_refused_before_source_acquisition_or_repair(
    selected: ResolvedSource, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(update, "acquire", lambda *a: pytest.fail("no acquisition"))
    monkeypatch.setattr(bootstrap, "prepare_update", lambda *a, **k: pytest.fail("no repair"))
    assert manager._cmd_update([
        "--repair-engine", "/fresh/source", "--runtime-sources", "{}",
    ]) == 1
    assert "second source packet" in capsys.readouterr().err


def test_exit_only_repair_cannot_silently_substitute_marketplace(
    selected: ResolvedSource, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(bootstrap, "_refresh_source", lambda *a: pytest.fail("no marketplace refresh"))
    assert bootstrap.repair_engine(Path("/fresh/source")) == 1
    assert "No update was run" in capsys.readouterr().err


@pytest.mark.parametrize("trigger,automatic_trigger", [("manual", None), ("schedule", "manager")])
def test_source_validation_on_release_precedes_success_settlement(
    selected: ResolvedSource, monkeypatch: pytest.MonkeyPatch, trigger: str,
    automatic_trigger: str | None,
) -> None:
    calls: list[tuple[str, str | None] | str | int] = []

    @contextmanager
    def acquire(selection: config.RuntimeSource) -> Iterator[ResolvedSource]:
        yield selected
        raise SourceAcquisitionError("checkout changed while consuming selection")

    class Run:
        def __init__(self, *, trigger: str, automatic_trigger: str | None) -> None:
            calls.append((trigger, automatic_trigger))

        def say(self, *a: Any, **k: Any) -> None:
            calls.append("diagnostic")

        def record_engine_error(self, *a: Any, **k: Any) -> None:
            calls.append("recorded")

        def finish(self, rc: int) -> int:
            calls.append(rc)
            return rc

    monkeypatch.setattr(update, "acquire", acquire)
    monkeypatch.setattr(bootstrap, "prepare_update", lambda *a, **k: bootstrap.PreparedUpdate([], True))
    monkeypatch.setattr(manager, "_cmd_update_steps", lambda *a, **k: 0)
    monkeypatch.setattr(update_events, "UpdateRun", Run)
    assert manager._cmd_update_recorded(
        ["--repair-engine", "/fresh/source"], trigger=trigger, automatic_trigger=automatic_trigger,
    ) == 1
    assert calls == [(trigger, automatic_trigger), "diagnostic", "recorded", 1]


def test_suite_reuses_original_dev_selection_without_acquisition(
    selected: ResolvedSource, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine_client, "engine_base_command", lambda: ["repaired-engine"])
    monkeypatch.setattr(engine_client, "run_json", lambda *a, **k: {
        "runtime_sources": {"application_profiles": {}},
    })
    monkeypatch.setattr(update, "_targets", lambda *a: ["agent-worktrees", "agent-bridge"])
    monkeypatch.setattr(update, "_preparer", lambda: selected.checkout / "trusted-preparer")
    monkeypatch.setattr(update, "acquire", lambda *a: pytest.fail("no second moving fetch"))
    calls: list[str] = []

    def prepare(
        tool: Path, resolved: ResolvedSource, names: tuple[str, ...], output: Path,
        **kwargs: Any,
    ) -> dict[str, Path]:
        assert resolved is selected and resolved.selection.branch == "dev"
        assert tool == selected.checkout / "trusted-preparer"
        assert kwargs["environment"] is None and kwargs["extras"] == {}
        calls.extend(names)
        return {name: output / name for name in names}

    monkeypatch.setattr(update, "prepare_applications", prepare)
    result = update.prepare_suite(None, ["--force"], runtime_source=selected)
    packet = json.loads(result.arguments[-1])
    assert calls == ["agent-worktrees", "agent-bridge"]
    assert packet["source"] == {
        "commit": selected.commit, "tree": selected.tree, "main_seed": selected.main_head,
    }
    assert set(packet["sources"]) == set(calls)
    assert all(
        Path(row["snapshot"]).name == name and row["profile"] is None
        for name, row in packet["sources"].items()
    )
    config.set_source(config.RuntimeSource())
    with pytest.raises(ValueError, match="policy changed"):
        update.prepare_suite(None, [], runtime_source=selected)


def test_native_artifact_and_pin_do_not_replace_owner_or_runtime(
    selected: ResolvedSource, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, artifact, root, sdk = (tmp_path / name for name in ("owner", "artifact", "runtime", "sdk"))
    pin = '{"exact":"service"}'
    installation = bootstrap._Installation(
        root, owner, {}, sdk, artifact=artifact, source_pin=pin,
    )
    calls = []
    def wait(*args: Any) -> int:
        calls.append(args)
        return 17

    monkeypatch.setattr(bootstrap, "_wait", wait)
    assert bootstrap._invoke(installation, "update", {"PATH": ""}) == 17
    command, environment, cwd = calls[0]
    assert command[command.index("--source-payload-root") + 1] == str(owner)
    assert command[command.index("--payload-root") + 1] == str(sdk)
    native = command[command.index("--") + 1:]
    extension = "ps1" if os.name == "nt" else "sh"
    assert str(artifact / f"scripts/install.{extension}") in native
    assert str(root) in native and cwd == artifact
    assert environment[repair.PIN_ENV] == pin
    assert environment["COPILOT_PLUGIN_STAGED_FROM"] == str(owner)
    assert environment["COPILOT_PLUGIN_ROOT"] == str(owner)
    assert environment["COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT"] == str(Path.cwd())
    assert environment["AGENT_WORKTREES_TARGETED_UPDATE"] == "1"


@pytest.mark.parametrize("changed", ["", "initial", "after-preparation"])
def test_repair_qualifies_after_incumbent_identity_and_verifies_before_success(
    selected: ResolvedSource, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str,
) -> None:
    root = tmp_path / "runtime"
    (root / "bin").mkdir(parents=True)
    installation = bootstrap._Installation(root, tmp_path / "owner", {}, tmp_path / "sdk")
    expected = {"incumbent": "owner"}
    calls = []

    def refresh(
        source: Path, environment: dict[str, str], **kwargs: Any,
    ) -> bootstrap._RefreshedSource:
        assert kwargs == {"independent": True}
        calls.append("refresh")
        return bootstrap._RefreshedSource(expected, installation.payload)

    monkeypatch.setattr(bootstrap, "_refresh_source", refresh)
    monkeypatch.setattr(bootstrap, "_incumbent", lambda *a: installation)
    def identity(row: bootstrap._Installation) -> dict[str, str]:
        if changed == "initial" or (changed == "after-preparation" and "prepare" in calls):
            return {}
        return expected

    monkeypatch.setattr(bootstrap, "_identity", identity)

    def prepare(resolved: ResolvedSource) -> tuple[Path, str]:
        assert resolved is selected
        calls.append("prepare")
        return tmp_path / "artifact", "pin"

    def invoke(current: bootstrap._Installation, action: str, environment: dict[str, str]) -> int:
        assert current.payload == installation.payload and current.root == root
        assert current.artifact == tmp_path / "artifact" and current.source_pin == "pin"
        calls.append("native")
        return 0

    monkeypatch.setattr(repair, "prepare_engine", prepare)
    monkeypatch.setattr(repair, "verify_engine", lambda *a: calls.append("verified"))
    monkeypatch.setattr(bootstrap, "_invoke", invoke)
    result = bootstrap._repair_installation(tmp_path / "fresh", runtime_source=selected)
    assert result.exit_code == (1 if changed else 0)
    assert calls == (
        ["refresh"] if changed == "initial" else
        ["refresh", "prepare"] if changed else
        ["refresh", "prepare", "native", "verified"]
    )


@pytest.mark.parametrize("supports_pin", [False, True])
def test_prepare_engine_reads_capability_without_importing_prepared_code(
    selected: ResolvedSource, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, supports_pin: bool,
) -> None:
    payload = tmp_path / "prepared"
    (payload / "scripts").mkdir(parents=True)
    (payload / "scripts/versioned_runtime_source.py").write_text(
        (f"SOURCE_PIN_ENV = {repair.PIN_ENV!r}\n" if supports_pin else "")
        + "raise RuntimeError('prepared code must not be imported')\n",
    )
    (payload / "scripts" / ("install.ps1" if os.name == "nt" else "install.sh")).write_text(
        "# AGENT_WORKTREES_TARGETED_UPDATE\n",
    )
    monkeypatch.setattr(repair, "preparer", lambda: tmp_path / "trusted-preparer")
    monkeypatch.setattr(
        repair, "prepare", lambda *a: {"payload": str(payload), "identity": {"exact": "pin"}},
    )
    if not supports_pin:
        with pytest.raises(ValueError, match="does not support"):
            repair.prepare_engine(selected)
    else:
        assert repair.prepare_engine(selected) == (payload, '{"exact": "pin"}')


@pytest.mark.parametrize("fault", [
    "", "repair", "repair-incomplete", "manifest", "selector", "slot", "incomplete", "venv", "service",
])
def test_repair_requires_actual_selected_complete_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    root = tmp_path / "runtime"
    base_version = "1.0.0.src.fixture"
    version = base_version + ".repair." + "d" * 32 if fault.startswith("repair") else base_version
    slot = root / "versions" / version
    slot.mkdir(parents=True)
    pin = {"runtime_version": base_version, "runtime_source": {"source_commit": "a" * 40}}
    manifest = {**pin, "runtime_version": version, "service": "agent-worktrees", "venv": str(slot)}
    if fault == "manifest":
        manifest["runtime_source"] = {"source_commit": "b" * 40}
    if fault in {"venv", "service"}:
        manifest[fault] = "foreign"
    (root / "deploy-manifest.json").write_text(json.dumps(manifest))
    (root / "current-version").write_text("foreign" if fault == "selector" else version)
    if fault == "slot":
        slot.rename(slot.with_name("retained-slot"))
    monkeypatch.setattr(repair, "_runtime_installer", lambda: SimpleNamespace(runtime=SimpleNamespace(
        version_dir=lambda r, v: r / "versions" / v,
        is_complete=lambda *a: fault not in {"incomplete", "repair-incomplete"},
    )))
    if fault not in {"", "repair"}:
        with pytest.raises(ValueError, match="did not select"):
            repair.verify_engine(root, json.dumps(pin))
    else:
        repair.verify_engine(root, json.dumps(pin))
