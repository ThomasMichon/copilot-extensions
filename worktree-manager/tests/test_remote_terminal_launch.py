import argparse
import base64
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Any

import pytest
from agent_procutil import no_window_kwargs

from worktree_manager import engine_client, harness_state, remote_terminal_launch


@pytest.mark.parametrize("shell", ["bash", "sh", "zsh", "pwsh", "powershell.exe"])
@pytest.mark.parametrize("mode", ["new", "base", "resume", "bare-resume"])
def test_existing_target_syntax_preserves_literal_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shell: str, mode: str,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "machines.yaml").write_text(
        "machines:\n  target:\n    ssh:\n      environments:\n"
        f"        - name: linux\n          alias: target-ssh\n          shell: {shell}\n",
        encoding="utf-8",
    )
    registry = tmp_path / ".agent-worktrees"
    registry.mkdir()
    (registry / "repos.yaml").write_text("repos: {}\n", encoding="utf-8")
    import yaml
    (registry / "projects.yaml").write_text(
        yaml.safe_dump({"projects": {"example": {"anchor": str(root)}}}), encoding="utf-8",
    )
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(harness_state, "home", lambda: tmp_path)
    monkeypatch.setattr(engine_client, "run_json", lambda *a, **k: pytest.fail("engine invoked"))
    prompt = "-leading\nUnicode \u2603 'quotes' \"double\" $env:HOME $(oops); & | ` *"
    seeded = mode in {"new", "resume"}
    plan = engine_client.resolve_launch_plan(
        "example", new=mode == "new", base=mode == "base",
        worktree_id="wt-existing" if mode in {"resume", "bare-resume"} else None,
        bare_resume=mode == "bare-resume", target_machine="target",
        target_no_mux=True, seed=prompt if seeded else None, launch_attempt_id="a" * 32,
    )
    command = plan.raw["remote_command"]
    if shell in {"pwsh", "powershell.exe"}:
        script = base64.b64decode(command.split()[-1]).decode("utf-16le")
        # Decode PowerShell single-quoted tokens, including doubled apostrophes.
        import re
        argv = [value.replace("''", "'") for value in re.findall(r"'((?:[^']|'')*)'", script)]
        assert script.endswith("; exit $LASTEXITCODE")
    else:
        argv = shlex.split(shlex.split(command)[-1])
    assert argv.pop(0) == "example"
    parser = argparse.ArgumentParser()
    selectors = parser.add_mutually_exclusive_group(required=True)
    selectors.add_argument("--new", action="store_true")
    selectors.add_argument("--base", action="store_true")
    selectors.add_argument("--worktree-id")
    parser.add_argument("--bare-resume", action="store_true")
    parser.add_argument("--no-mux", action="store_true")
    parser.add_argument("--stage-launch-seed", action="store_true")
    parser.add_argument("--seed")
    selected = parser.parse_args(argv)
    assert selected.new == (mode == "new")
    assert selected.base == (mode == "base")
    assert selected.bare_resume == (mode == "bare-resume")
    assert selected.no_mux
    assert selected.seed == (prompt if seeded else None)
    assert selected.stage_launch_seed == seeded
    assert plan.raw["ssh_alias"] == "target-ssh"


def test_cell_remote_plan_uses_prepared_binding_without_engine_or_legacy_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worktree_manager.production_picker import context, project_config

    root = tmp_path / "cell-source"
    root.mkdir()
    (root / "machines.yaml").write_text(
        "machines:\n  target:\n    ssh:\n      environments:\n"
        "        - name: linux\n          alias: cell-ssh\n          shell: bash\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(tmp_path / "context.json"))
    monkeypatch.setattr(engine_client, "run_json", lambda *a, **k: pytest.fail("engine invoked"))
    monkeypatch.setattr(
        harness_state, "project_anchor_selection",
        lambda *a, **k: pytest.fail("legacy registry read"),
    )
    context.set_project("example")
    project_config.bind_bootstrap("example", {
        "repo_dir": str(root), "config_dir": str(tmp_path / "cell-config"),
    })
    try:
        plan = engine_client.resolve_launch_plan("example", new=True, target_machine="target")
        assert plan.raw["ssh_alias"] == "cell-ssh"
    finally:
        project_config.clear_caches(reset_binding=True)
        context.reset()


def test_unsupported_shell_does_not_get_guessed() -> None:
    with pytest.raises(ValueError, match="explicit shell"):
        remote_terminal_launch.shell_command("fish", ["example", "--new"])


@pytest.mark.parametrize("shell", ["pwsh", "bash"])
def test_real_shell_preserves_prompt_at_strict_legacy_parser(tmp_path: Path, shell: str) -> None:
    executable = shutil.which(shell)
    if not executable or (shell == "bash" and os.name == "nt"):
        pytest.skip("native shell unavailable")
    receiver = tmp_path / "legacy parser.py"
    receiver.write_text(
        "import argparse,json\n"
        "p=argparse.ArgumentParser()\n"
        "p.add_argument('--new',action='store_true')\n"
        "p.add_argument('--stage-launch-seed',action='store_true')\n"
        "p.add_argument('--seed')\n"
        "print(json.dumps(vars(p.parse_args()),ensure_ascii=True))\n",
        encoding="utf-8",
    )
    prompt = "-leading\n\u2603 quotes ' \" $(throw 'executed') ; & | `\n--require-foreground-completion"
    command = remote_terminal_launch.shell_command(
        shell, [sys.executable, str(receiver), "--new", "--stage-launch-seed", "--seed=" + prompt],
    )
    argv = (
        [executable, "-NoProfile", "-EncodedCommand", command.split()[-1]]
        if shell == "pwsh" else [executable, "-c", command]
    )
    result = subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"new": True, "stage_launch_seed": True, "seed": prompt}


@pytest.mark.parametrize("scoped", [True, False, None])
def test_cell_supplier_never_reads_conflicting_legacy_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scoped: bool | None,
) -> None:
    import yaml
    from worktree_manager import agent_plugin_runtime
    from worktree_manager.production_picker import context, project_config

    source, config, registry, legacy = (tmp_path / name for name in (
        "source", "config", "scoped-registry", ".agent-worktrees",
    ))
    for path in (source, config, registry, legacy):
        path.mkdir()
    (config / "config.yaml").write_text("knowledge_repo: supplier\n", encoding="utf-8")
    for target, label, available in ((registry, "cell", scoped), (legacy, "legacy", True)):
        supplier = tmp_path / label
        supplier.mkdir()
        (supplier / "machines.yaml").write_text(
            "machines:\n  target:\n    ssh:\n      environments:\n"
            f"        - name: linux\n          alias: {label}-ssh\n          shell: bash\n",
            encoding="utf-8",
        )
        (target / "repos.yaml").write_text(yaml.safe_dump({
            "repos": {"supplier": {"linux": str(supplier), "windows": str(supplier)}} if available else {},
        }), encoding="utf-8")
        (target / "projects.yaml").write_text("projects: {}\n", encoding="utf-8")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(tmp_path / "context.json"))
    monkeypatch.setattr(harness_state, "home", lambda: tmp_path)
    monkeypatch.setattr(
        agent_plugin_runtime, "_namespaced_plugin_root", lambda plugin: registry if scoped is not None else None,
    )
    monkeypatch.setattr(engine_client, "run_json", lambda *a, **k: pytest.fail("engine invoked"))
    original = harness_state._read_yaml

    def read(path: Path, **kwargs: object) -> dict[str, Any]:
        assert not path.is_relative_to(legacy), "cross-installation registry read"
        return original(path, strict=True)

    monkeypatch.setattr(harness_state, "_read_yaml", read)
    context.set_project("example")
    project_config.bind_bootstrap("example", {"repo_dir": str(source), "config_dir": str(config)})
    try:
        if scoped:
            plan = engine_client.resolve_launch_plan("example", new=True, target_machine="target")
            assert plan.raw["ssh_alias"] == "cell-ssh"
        else:
            message = "supplier is unavailable" if scoped is False else "no scoped supplier registry"
            with pytest.raises(engine_client.EngineError, match=message):
                engine_client.resolve_launch_plan("example", new=True, target_machine="target")
    finally:
        project_config.clear_caches(reset_binding=True)
        context.reset()
