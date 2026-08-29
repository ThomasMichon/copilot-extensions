"""Guards for the wholesale production Picker transplant."""

from __future__ import annotations

import importlib.util
import json
import binascii
import zlib
from pathlib import Path

import pytest

from worktree_manager import __main__ as entrypoint
from worktree_manager.production_picker import runner


def test_transplanted_picker_sources_match_production_copy():
    root = Path(__file__).resolve().parents[2]
    source = root / "plugins" / "agent-worktrees" / "src" / "agent_worktrees"
    transplanted = (
        root / "worktree-manager" / "src" / "worktree_manager" / "production_picker"
    )
    relative_paths = [
        Path("picker.py"),
        *(
            path.relative_to(source)
            for path in sorted((source / "picker_tui").glob("*.py"))
            if path.name != "pivots.py"
        ),
    ]

    assert relative_paths
    for relative in relative_paths:
        assert (transplanted / relative).read_bytes() == (source / relative).read_bytes()
    assert "WORKTREE_MANAGER_PICKER_NO_PIVOT_MATERIALIZE" in (
        transplanted / "picker_tui" / "pivots.py"
    ).read_text(encoding="utf-8")


def test_production_runner_activates_project_and_uses_transplanted_ui(monkeypatch):
    calls = []

    class ImmediateThread:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    class Config:
        @staticmethod
        def set_active_project(project):
            calls.append(("active", project))

    class Cli:
        @staticmethod
        def _resolve_active_project(project):
            return project, None

        @staticmethod
        def _in_ssh_session():
            return False

        @staticmethod
        def _heal_stale_anchor_if_self_missing(config):
            calls.append(("heal", config))
            return config

        @staticmethod
        def reap_orphan_mux_sessions():
            calls.append(("reap",))

        @staticmethod
        def _sweep_managed_on_exit():
            calls.append(("managed",))

        @staticmethod
        def _sweep_launcher_shells_on_exit():
            calls.append(("shells",))

        @staticmethod
        def _sweep_finished_sessions_on_cadence():
            calls.append(("finished",))

        @staticmethod
        def _start_picker_monitor_root():
            return None

    Config.load_config = staticmethod(lambda: "config")

    monkeypatch.setattr(
        runner,
        "engine_module",
        lambda name: Config if name == "config" else Cli,
    )
    monkeypatch.setattr(
        runner,
        "run_tui_picker",
        lambda *, live: calls.append(("picker", live)) or {"action": "new"},
    )
    monkeypatch.setattr(runner.threading, "Thread", ImmediateThread)

    assert runner.run("demo") == {"action": "new"}
    assert calls == [
        ("active", "demo"),
        ("heal", "config"),
        ("reap",),
        ("managed",),
        ("shells",),
        ("finished",),
        ("picker", True),
    ]


def test_production_runner_mock_skips_mutating_startup(monkeypatch):
    calls = []

    class Config:
        @staticmethod
        def set_active_project(project):
            calls.append(("active", project))

        @staticmethod
        def load_config():
            return "config"

    class Cli:
        @staticmethod
        def _resolve_active_project(project):
            return project, None

        @staticmethod
        def _in_ssh_session():
            return False

        @staticmethod
        def _heal_stale_anchor_if_self_missing(config):
            calls.append(("heal", config))

        @staticmethod
        def _start_picker_monitor_root():
            calls.append(("monitor",))
            return None

    monkeypatch.setattr(
        runner,
        "engine_module",
        lambda name: Config if name == "config" else Cli,
    )
    monkeypatch.setattr(
        runner,
        "_start_housekeeping",
        lambda cli: calls.append(("housekeeping",)),
    )
    monkeypatch.setattr(
        runner,
        "run_tui_picker",
        lambda *, live, mock_mode: calls.append(("picker", live, mock_mode))
        or None,
    )

    assert runner.run("demo", mock_mode=True, local=True) is None
    assert calls == [
        ("active", "demo"),
        ("picker", False, True),
    ]


def test_production_capture_uses_read_only_prepare(monkeypatch):
    from worktree_manager.production_picker.picker_tui import capture as picker_capture

    calls = []
    monkeypatch.setattr(
        runner,
        "_prepare",
        lambda project, *, heal: calls.append((project, heal)) or (object(), False),
    )
    monkeypatch.setattr(
        picker_capture,
        "capture",
        lambda source, **kwargs: {
            "text": "GRID\n",
            "ansi": "ANSI\n",
            "svg": "<svg />",
        },
    )

    assert runner.capture("demo")["text"] == "GRID\n"
    assert calls == [("demo", False)]


def test_manager_acts_on_production_picker_new_decision(monkeypatch):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "new",
            "is_local": True,
            "options": {"no_mux": False},
        },
    )
    requests = []
    monkeypatch.setattr(
        entrypoint,
        "_run_launch",
        lambda request: requests.append(request) or 23,
    )

    assert entrypoint._run_production_picker("demo") == 23
    assert requests[0].project == "demo"
    assert requests[0].mode == "new"
    assert requests[0].worktree_id is None
    assert requests[0].no_mux is False


def test_manager_acts_on_production_picker_resume_decision(monkeypatch):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "resume",
            "worktree_id": "demo-1234",
            "title": "Resume me",
            "is_local": True,
            "options": {"bare_resume": True, "no_mux": True},
        },
    )
    requests = []
    monkeypatch.setattr(
        entrypoint,
        "_run_launch",
        lambda request: requests.append(request) or 0,
    )

    assert entrypoint._run_production_picker("demo") == 0
    assert requests[0].worktree_id == "demo-1234"
    assert requests[0].mode == "bare-resume"
    assert requests[0].no_mux is True


def test_manager_acts_on_remote_production_picker_decision(monkeypatch):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "resume",
            "worktree_id": "demo-1234",
            "title": "Resume remotely",
            "is_local": False,
            "machine": "Example",
            "env": "WSL",
            "options": {"bare_resume": True, "no_mux": True},
        },
    )
    requests = []
    monkeypatch.setattr(
        entrypoint,
        "_run_launch",
        lambda request: requests.append(request) or 0,
    )

    assert entrypoint._run_production_picker("demo") == 0
    assert requests[0].worktree_id == "demo-1234"
    assert requests[0].mode == "bare-resume"
    assert requests[0].machine == "Example"
    assert requests[0].environment == "WSL"
    assert requests[0].no_mux is True


def test_manager_acts_on_base_repo_production_picker_decision(monkeypatch):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "new",
            "is_local": True,
            "options": {"anchor": True, "no_mux": False},
        },
    )
    requests = []
    monkeypatch.setattr(
        entrypoint,
        "_run_launch",
        lambda request: requests.append(request) or 0,
    )

    assert entrypoint._run_production_picker("demo") == 0
    assert requests[0].mode == "base"
    assert requests[0].worktree_id is None


def test_run_launch_executes_remote_plan(monkeypatch):
    plan = type("Plan", (), {"action": "remote", "exit_code": 0})()
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda request: (plan, 0))
    calls = []
    from worktree_manager import launcher

    monkeypatch.setattr(
        launcher,
        "launch",
        lambda resolved, *, want_mux: calls.append((resolved, want_mux)) or 17,
    )
    request = type("Request", (), {"no_mux": True})()

    assert entrypoint._run_launch(request) == 17
    assert calls == [(plan, False)]


def test_resolve_for_uses_remote_compatibility_on_older_engine(monkeypatch):
    from worktree_manager import engine_client

    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "worktree_id": "demo-1234",
            "mode": "bare-resume",
            "machine": "Example",
            "environment": "WSL",
            "no_mux": True,
        },
    )()
    monkeypatch.setattr(
        engine_client,
        "resolve_launch_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            engine_client.EngineFeatureUnavailable("older engine")
        ),
    )
    monkeypatch.setattr(
        runner,
        "compatibility_remote_plan",
        lambda *args, **kwargs: {
            "action": "remote",
            "ssh_alias": "example-wsl",
            "remote_command": "demo --worktree-id demo-1234 --bare-resume --no-mux",
        },
    )

    plan, code = entrypoint._resolve_for(request)
    assert code == 0
    assert plan.action == "remote"
    assert plan.raw["ssh_alias"] == "example-wsl"


def test_normal_picker_command_uses_production_transplant(monkeypatch):
    monkeypatch.setattr(entrypoint, "engine_available", lambda: True)
    monkeypatch.setattr(entrypoint, "build_projects", lambda: [])
    calls = []
    monkeypatch.setattr(
        entrypoint,
        "_run_production_picker",
        lambda project: calls.append(project) or 19,
    )

    assert entrypoint._cmd_picker(["demo"]) == 19
    assert calls == ["demo"]


def test_picker_mock_uses_production_transplant_without_acting(monkeypatch, capsys):
    monkeypatch.setattr(entrypoint, "engine_available", lambda: False)
    monkeypatch.setattr(
        entrypoint,
        "build_projects",
        lambda: [type("Project", (), {"name": "demo"})()],
    )
    calls = []
    monkeypatch.setattr(
        runner,
        "run",
        lambda project, **kwargs: calls.append((project, kwargs))
        or {"action": "new"},
    )
    monkeypatch.setattr(
        entrypoint,
        "_run_launch",
        lambda request: (_ for _ in ()).throw(
            AssertionError("mock mode must not launch")
        ),
    )

    assert entrypoint._cmd_picker(["mock", "demo", "--local", "--json"]) == 0
    assert calls == [("demo", {"mock_mode": True, "local": True})]
    assert json.loads(capsys.readouterr().out) == {
        "mock": True,
        "decision": {"action": "new"},
    }


def test_picker_screenshot_uses_production_capture(monkeypatch, tmp_path):
    monkeypatch.setattr(entrypoint, "engine_available", lambda: False)
    monkeypatch.setattr(
        entrypoint,
        "build_projects",
        lambda: [type("Project", (), {"name": "demo"})()],
    )
    calls = []
    monkeypatch.setattr(
        runner,
        "capture",
        lambda project, **kwargs: calls.append((project, kwargs))
        or {"text": "GRID\n", "ansi": "ANSI\n", "svg": "<svg />"},
    )
    out = tmp_path / "picker.txt"

    assert entrypoint._cmd_picker([
        "screenshot",
        "demo",
        "--format",
        "text",
        "--pivot",
        "Tasks",
        "--wait",
        "1.5",
        "--out",
        str(out),
    ]) == 0
    assert calls == [(
        "demo",
        {"live": False, "pivot": "Tasks", "wait_pivot": 1.5},
    )]
    assert out.read_text(encoding="utf-8") == "GRID\n"


def test_picker_screenshot_keeps_relative_output_at_caller_cwd(monkeypatch, tmp_path):
    caller = tmp_path / "caller"
    project = tmp_path / "project"
    caller.mkdir()
    project.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setattr(entrypoint, "engine_available", lambda: False)
    monkeypatch.setattr(
        entrypoint,
        "build_projects",
        lambda: [type("Project", (), {"name": "demo"})()],
    )

    def capture(*args, **kwargs):
        monkeypatch.chdir(project)
        return {"text": "GRID\n", "ansi": "ANSI\n", "svg": "<svg />"}

    monkeypatch.setattr(runner, "capture", capture)

    assert entrypoint._cmd_picker([
        "screenshot",
        "demo",
        "--format",
        "text",
        "--out",
        "picker.txt",
    ]) == 0
    assert (caller / "picker.txt").read_text(encoding="utf-8") == "GRID\n"
    assert not (project / "picker.txt").exists()


def test_legacy_screenshot_flag_uses_production_capture(monkeypatch, tmp_path):
    monkeypatch.setattr(entrypoint, "engine_available", lambda: True)
    monkeypatch.setattr(
        entrypoint,
        "build_projects",
        lambda: [type("Project", (), {"name": "demo"})()],
    )
    monkeypatch.setattr(
        runner,
        "capture",
        lambda project, **kwargs: {
            "text": "GRID\n",
            "ansi": "ANSI\n",
            "svg": "<svg>production</svg>",
        },
    )
    out = tmp_path / "picker.svg"

    assert entrypoint._cmd_picker(["demo", "--screenshot", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == "<svg>production</svg>"


def test_picker_validation_assets_are_manager_owned():
    root = Path(__file__).resolve().parents[2]
    manager = root / "worktree-manager"
    corpus = manager / "tests" / "production_picker"
    snapshots = manager / "scripts" / "picker-snapshot"

    assert (corpus / "test_picker_tui.py").is_file()
    assert (corpus / "goldens" / "picker" / "worktrees_list.txt").is_file()
    assert (manager / "scripts" / "preview-picker.ps1").is_file()
    assert (manager / "scripts" / "preview-picker.sh").is_file()
    assert (manager / "scripts" / "picker-shot.py").is_file()
    assert (snapshots / "render.py").is_file()
    assert (snapshots / "svg2png.mjs").is_file()
    assert "worktree_manager.production_picker" in (
        snapshots / "render.py"
    ).read_text(encoding="utf-8")


def _picker_shot_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "picker-shot.py"
    spec = importlib.util.spec_from_file_location("manager_picker_shot", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = binascii.crc32(chunk_type + data) & 0xFFFFFFFF
    return (
        len(data).to_bytes(4, "big")
        + chunk_type
        + data
        + crc.to_bytes(4, "big")
    )


def _test_png(width: int, height: int) -> bytes:
    ihdr = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + bytes([8, 6, 0, 0, 0])
    )
    pixels = b"".join(b"\x00" + bytes(width * 4) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(pixels))
        + _png_chunk(b"IEND", b"")
    )


def test_png_validator_accepts_expected_signature_and_dimensions(tmp_path):
    module = _picker_shot_module()
    png = tmp_path / "picker.png"
    png.write_bytes(_test_png(4, 2))

    module._validate_png(str(png), expected_size=(4, 2))


def test_png_validator_rejects_wrong_dimensions(tmp_path):
    module = _picker_shot_module()
    png = tmp_path / "picker.png"
    png.write_bytes(_test_png(4, 2))

    with pytest.raises(RuntimeError, match="expected 8x4"):
        module._validate_png(str(png), expected_size=(8, 4))


def test_png_validator_rejects_truncated_png(tmp_path):
    module = _picker_shot_module()
    png = tmp_path / "picker.png"
    png.write_bytes(_test_png(4, 2)[:24])

    with pytest.raises(RuntimeError, match="invalid PNG"):
        module._validate_png(str(png), expected_size=(4, 2))
