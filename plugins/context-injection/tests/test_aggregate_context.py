from __future__ import annotations

import json
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN / "scripts" / "aggregate_context.py"
BASH_WRAPPER = PLUGIN / "scripts" / "emit-context.sh"
POWERSHELL_WRAPPER = PLUGIN / "scripts" / "emit-context.ps1"
PRODUCER_BASH_WRAPPER = PLUGIN / "scripts" / "invoke-context-contributor.sh"
PRODUCER_POWERSHELL_WRAPPER = (
    PLUGIN / "scripts" / "invoke-context-contributor.ps1"
)
SCHEMA = "copilot-extensions.session-context-contributors"

SPEC = importlib.util.spec_from_file_location("aggregate_context", SCRIPT)
assert SPEC and SPEC.loader
AGGREGATE_CONTEXT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AGGREGATE_CONTEXT
SPEC.loader.exec_module(AGGREGATE_CONTEXT)


def test_hook_timeout_meets_rendezvous_deadline() -> None:
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    session_start = hooks["hooks"]["sessionStart"]

    assert session_start
    assert all(hook["timeoutSec"] >= 25 for hook in session_start)


def test_native_process_ancestry_reader_returns_current_chain(
    tmp_path: Path,
) -> None:
    reader = tmp_path / "read_ancestry.py"
    reader.write_text(
        "import importlib.util,json,pathlib,sys\n"
        f"path=pathlib.Path({str(SCRIPT)!r})\n"
        "spec=importlib.util.spec_from_file_location('native_ancestry',path)\n"
        "module=importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name]=module\n"
        "spec.loader.exec_module(module)\n"
        "print(json.dumps(module._process_ancestry_argv()))\n",
        encoding="utf-8",
    )
    host = tmp_path / "host.py"
    host.write_text(
        "import subprocess,sys\n"
        f"command=[sys.executable,{str(reader)!r}]\n"
        "raise SystemExit(subprocess.run(command,check=False).returncode)\n",
        encoding="utf-8",
    )
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    environment = os.environ.copy()
    environment.pop("COPILOT_CONTEXT_INJECTION_TEST_NO_STAGED_PLUGINS", None)
    environment.pop("COPILOT_CONTEXT_INJECTION_TEST_ANCESTRY", None)

    result = subprocess.run(
        [
            sys.executable,
            str(host),
            "--acp",
            "--plugin-dir",
            str(plugin_root),
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=True,
    )
    ancestry = json.loads(result.stdout)

    assert ancestry
    assert any(
        "--acp" in command
        and command[command.index("--plugin-dir") + 1] == str(plugin_root)
        for command in ancestry
    )


def _bash_path(path: Path) -> str:
    resolved = path.resolve()
    if os.name != "nt":
        return resolved.as_posix()
    relative = resolved.relative_to(resolved.anchor).as_posix()
    return f"/mnt/{resolved.drive[0].lower()}/{relative}"


def _plugin(
    root: Path,
    marketplace: str,
    name: str,
    *,
    context: str | None,
    complete: bool = True,
) -> Path:
    plugin = root / ".copilot" / "installed-plugins" / marketplace / name
    plugin.mkdir(parents=True)
    manifest = {
        "name": name,
        "version": "1.0.0",
        "hooks": "hooks.json",
        "sessionContext": "session-context.json",
    }
    (plugin / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (plugin / "hooks.json").write_text(
        json.dumps({"version": 1, "hooks": {"sessionStart": [{"bash": "true"}]}}),
        encoding="utf-8",
    )
    scripts = plugin / "scripts"
    scripts.mkdir()
    output = "{}" if context is None else json.dumps({"additionalContext": context})
    (scripts / "emit.sh").write_text(
        f"#!/usr/bin/env bash\nprintf '%s' '{output}'\n",
        encoding="utf-8",
    )
    powershell_output = output.replace("'", "''")
    (scripts / "emit.ps1").write_text(
        f"[Console]::Out.Write('{powershell_output}')\n",
        encoding="utf-8",
    )
    shutil.copy2(PRODUCER_BASH_WRAPPER, scripts / PRODUCER_BASH_WRAPPER.name)
    shutil.copy2(
        PRODUCER_POWERSHELL_WRAPPER,
        scripts / PRODUCER_POWERSHELL_WRAPPER.name,
    )
    declaration = {
        "schema": SCHEMA,
        "version": 1,
        "complete": complete,
        "sessionStart": {
            "sideEffects": "none",
            "context": "authority-aware",
        },
        "contributors": [
            {
                "id": "main",
                "pure": True,
                "order": 100,
                "timeoutSeconds": 5,
                "maxBytes": 4096,
                "bash": ["scripts/emit.sh"],
                "powershell": ["scripts/emit.ps1"],
            }
        ],
    }
    (plugin / "session-context.json").write_text(
        json.dumps(declaration), encoding="utf-8"
    )
    return plugin


def _run_native_producer_wrapper(
    plugin: Path,
    source: str,
    contributor_id: str,
    hook_input: str,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["COPILOT_PLUGIN_ROOT"] = str(plugin)
    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
        assert powershell is not None
        command = [
            powershell,
            "-NoProfile",
            "-File",
            str(plugin / "scripts" / PRODUCER_POWERSHELL_WRAPPER.name),
            source,
            contributor_id,
            "scripts/emit.ps1",
        ]
    else:
        command = [
            "bash",
            str(plugin / "scripts" / PRODUCER_BASH_WRAPPER.name),
            source,
            contributor_id,
            "scripts/emit.sh",
        ]
    return subprocess.run(
        command,
        input=hook_input,
        text=True,
        capture_output=True,
        env=environment,
        check=True,
    )


def test_producer_wrapper_falls_back_without_sibling_authority(
    tmp_path: Path,
) -> None:
    policy = _plugin(
        tmp_path / "standalone",
        "copilot-extensions",
        "a-policy",
        context="STANDALONE",
    )

    result = _run_native_producer_wrapper(
        policy,
        "a-policy@copilot-extensions",
        "main",
        json.dumps({"cwd": str(tmp_path), "sessionId": "standalone"}),
    )

    assert json.loads(result.stdout) == {"additionalContext": "STANDALONE"}


def test_windows_ancestry_reader_passes_parent_pid_in_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(AGGREGATE_CONTEXT.os, "name", "nt")
    monkeypatch.setattr(AGGREGATE_CONTEXT.shutil, "which", lambda _: "pwsh")
    monkeypatch.setattr(AGGREGATE_CONTEXT.os, "getppid", lambda: 4242)
    monkeypatch.setattr(
        AGGREGATE_CONTEXT,
        "_windows_command_line_argv",
        lambda command_line: tuple(command_line.split()),
    )
    captured: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps(
                [
                    {
                        "processId": 4242,
                        "parentProcessId": 1,
                        "commandLine": "copilot --allow-all",
                    }
                ]
            ),
        )

    monkeypatch.setattr(AGGREGATE_CONTEXT.subprocess, "run", fake_run)

    assert AGGREGATE_CONTEXT._process_ancestry_argv() == [
        ("copilot", "--allow-all")
    ]
    assert captured["env"]["COPILOT_CONTEXT_INJECTION_ANCESTRY_PID"] == "4242"


def test_windows_ancestry_reader_preserves_explicit_plugin_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(AGGREGATE_CONTEXT.os, "name", "nt")
    monkeypatch.setattr(AGGREGATE_CONTEXT.shutil, "which", lambda _: "pwsh")
    monkeypatch.setattr(
        AGGREGATE_CONTEXT,
        "_windows_command_line_argv",
        lambda command_line: (
            "copilot",
            "--acp",
            "--plugin-dir",
            "C:\\tmp\\staged",
        ),
    )
    monkeypatch.setattr(
        AGGREGATE_CONTEXT.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps(
                [
                    {
                        "processId": 4242,
                        "parentProcessId": 1,
                        "commandLine": "copilot --acp --plugin-dir C:\\tmp\\staged",
                    }
                ]
            ),
        ),
    )

    ancestry = AGGREGATE_CONTEXT._process_ancestry_argv()
    assert ancestry is not None
    assert AGGREGATE_CONTEXT._plugin_dir_arguments(ancestry[0]) == (
        "C:\\tmp\\staged",
    )


def test_windows_ancestry_snapshot_stops_at_copilot_host() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "[string]$process.Name -ieq 'copilot.exe'" in source
    assert "[string]$process.Name -ieq 'copilot'" in source


def test_oversized_context_spills_to_session_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    context = "X" * (AGGREGATE_CONTEXT.MAX_INLINE_CONTEXT_BYTES + 1)

    pointer = AGGREGATE_CONTEXT._spill_context("session-1", context)

    assert pointer is not None
    target = (
        tmp_path
        / ".copilot"
        / "session-state"
        / "session-1"
        / "files"
        / "startup-context.md"
    )
    assert str(target) in pointer
    assert "Before acting, read the complete startup context" in pointer
    assert context in target.read_text(encoding="utf-8")
    assert len(pointer.encode("utf-8")) < 512


@pytest.mark.skipif(os.name != "nt", reason="Windows startup allowance")
def test_windows_contributors_receive_process_start_grace() -> None:
    assert AGGREGATE_CONTEXT.PROCESS_START_GRACE_SECONDS == 5


def _run(
    tmp_path: Path,
    plugins: list[tuple[str, Path]],
    *,
    cwd: Path | None = None,
    authority: str | None = None,
    session_id: str = "s",
    producer: str | None = None,
    adoption_case: str = "valid",
    raw_input: str | None = None,
    cache_dir: Path | None = None,
    staged: list[str | Path] | None = None,
    ancestry: list[list[str]] | None = None,
    enabled_overrides: dict[str, bool] | None = None,
    via_wrapper: bool = False,
) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    installed = home / ".copilot" / "installed-plugins" / "copilot-extensions"
    installed.mkdir(parents=True, exist_ok=True)
    for name, source in plugins:
        if name == "context-injection":
            continue
        shutil.copytree(source, installed / name, dirs_exist_ok=True)

    engine = installed / "context-injection"
    shutil.copytree(PLUGIN, engine, dirs_exist_ok=True)
    authority_declaration_path = engine / "session-context.json"
    authority_declaration = json.loads(
        authority_declaration_path.read_text(encoding="utf-8")
    )
    authority_declaration["sessionStart"]["context"] = "aggregate-authority"
    authority_declaration_path.write_text(
        json.dumps(authority_declaration), encoding="utf-8"
    )

    authority_source = "context-injection@copilot-extensions"
    settings = {
        "extraKnownMarketplaces": {
            "copilot-extensions": {
                "source": {
                    "source": "github",
                    "repo": "ThomasMichon/copilot-extensions",
                }
            },
        },
        "enabledPlugins": {
            f"{name}@copilot-extensions": True
            for name, _ in plugins
            if name != "context-injection"
        },
    }
    settings["enabledPlugins"][authority_source] = True
    settings["enabledPlugins"].update(enabled_overrides or {})
    config: dict[str, object] = {
        "schema": "copilot-extensions.context-injection",
        "version": 1,
        "authority": authority_source,
        "engine": {
            "schema": "copilot-extensions.context-injection-engine",
            "version": 4,
        },
    }
    engine_config = config["engine"]
    assert isinstance(engine_config, dict)
    if adoption_case == "incomplete":
        engine_config.pop("schema")
    elif adoption_case == "incompatible":
        engine_config["version"] = 2
    elif adoption_case == "ambiguous":
        config["authority"] = "context-injection@other-context"
    elif adoption_case == "unknown":
        config["unexpected"] = "rejected"
    elif adoption_case == "legacy-setting":
        settings["sessionContextAggregation"] = {
            "schema": "copilot-extensions.session-context-aggregation",
            "version": 1,
            "authority": authority_source,
            "engineSchema": "copilot-extensions.context-injection-engine",
            "engineVersion": 2,
        }
    elif adoption_case == "inactive":
        settings["enabledPlugins"][authority_source] = False
    elif adoption_case == "missing-authority-hook":
        (engine / "hooks.json").write_text(
            json.dumps({"version": 1, "hooks": {"sessionStart": []}}),
            encoding="utf-8",
        )
    settings_path = repo / ".github" / "copilot" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(settings), encoding="utf-8"
    )
    if adoption_case not in {"missing", "legacy-setting"}:
        config_path = repo / ".context-injection" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if adoption_case == "malformed":
            config_path.write_text("schema: [\n", encoding="utf-8")
        else:
            lines = [
                f"schema: {config['schema']}",
                f"version: {config['version']}",
                f"authority: {config['authority']}",
                "engine:",
            ]
            lines.extend(
                f"  {key}: {value}" for key, value in engine_config.items()
            )
            if "unexpected" in config:
                lines.append(f"unexpected: {config['unexpected']}")
            config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    copilot = home / ".copilot"
    (copilot / "config.json").write_text(
        json.dumps({"trustedFolders": [str(repo)]}), encoding="utf-8"
    )
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    environment["USERPROFILE"] = str(home)
    environment["COPILOT_PLUGIN_ROOT"] = str(
        installed / producer.partition("@")[0] if producer else engine
    )
    environment["COPILOT_CONTEXT_INJECTION_CACHE_DIR"] = str(
        cache_dir or tmp_path / "cache"
    )
    if ancestry is not None:
        environment["COPILOT_CONTEXT_INJECTION_TEST_ANCESTRY"] = json.dumps(
            ancestry
        )
    elif staged is not None:
        plugin_dirs = [
            str(installed / item) if isinstance(item, str) else str(item)
            for item in staged
        ]
        arguments = ["node", "copilot", "--acp", "--stdio"]
        for plugin_dir in plugin_dirs:
            arguments.extend(["--plugin-dir", plugin_dir])
        environment["COPILOT_CONTEXT_INJECTION_TEST_ANCESTRY"] = json.dumps(
            [arguments]
        )
    else:
        environment["COPILOT_CONTEXT_INJECTION_TEST_NO_STAGED_PLUGINS"] = "1"
    if authority is not None:
        environment["COPILOT_CONTEXT_INJECTION_AUTHORITY"] = authority
    command = [
        os.environ.get("PYTHON") or sys.executable,
        str(engine / "scripts" / "aggregate_context.py"),
    ]
    if producer is not None:
        if via_wrapper:
            source, contributor_id = producer.rsplit("/", 1)
            plugin_name = source.partition("@")[0]
            plugin_root = installed / plugin_name
            if os.name == "nt":
                powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
                assert powershell is not None
                command = [
                    powershell,
                    "-NoProfile",
                    "-File",
                    str(plugin_root / "scripts" / PRODUCER_POWERSHELL_WRAPPER.name),
                    source,
                    contributor_id,
                    "scripts/emit.ps1",
                ]
            else:
                command = [
                    "bash",
                    str(plugin_root / "scripts" / PRODUCER_BASH_WRAPPER.name),
                    source,
                    contributor_id,
                    "scripts/emit.sh",
                ]
        else:
            command.extend(["--producer", producer])
    return subprocess.run(
        command,
        input=raw_input
        if raw_input is not None
        else json.dumps(
            {
                "cwd": str(cwd or repo),
                "source": "new",
                "sessionId": session_id,
            }
        ),
        text=True,
        capture_output=True,
        env=environment,
        check=True,
    )


def test_aggregates_complete_active_stack_in_stable_order(tmp_path: Path) -> None:
    home = tmp_path / "sources"
    first = _plugin(home, "mkt", "a-policy", context="POLICY")
    second = _plugin(home, "mkt", "b-catalog", context="CATALOG")
    aggregator = tmp_path / "aggregator"
    shutil.copytree(PLUGIN, aggregator)

    result = _run(
        tmp_path,
        [
            ("b-catalog", second),
            ("a-policy", first),
            ("context-injection", aggregator),
        ],
    )

    payload = json.loads(result.stdout)
    context = payload["additionalContext"]
    assert context.index("a-policy@copilot-extensions/main") < context.index(
        "b-catalog@copilot-extensions/main"
    )
    assert "POLICY" in context
    assert "CATALOG" in context


def test_stands_down_when_active_hook_plugin_has_no_declaration(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    legacy = _plugin(sources, "mkt", "legacy", context="LEGACY")
    manifest = json.loads((legacy / "plugin.json").read_text(encoding="utf-8"))
    manifest.pop("sessionContext")
    (legacy / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    aggregator = tmp_path / "aggregator"
    shutil.copytree(PLUGIN, aggregator)

    result = _run(
        tmp_path,
        [("legacy", legacy), ("context-injection", aggregator)],
    )

    assert json.loads(result.stdout) == {}
    assert "no complete context declaration" in result.stderr


def test_repository_authority_does_not_depend_on_lexical_plugin_order(
    tmp_path: Path,
) -> None:
    sources = tmp_path / "sources"
    later = _plugin(sources, "mkt", "zzz-later", context="LATER")
    aggregator = tmp_path / "aggregator"
    shutil.copytree(PLUGIN, aggregator)

    result = _run(
        tmp_path,
        [("context-injection", aggregator), ("zzz-later", later)],
    )

    assert "LATER" in json.loads(result.stdout)["additionalContext"]


def test_rejects_incomplete_declaration(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    incomplete = _plugin(
        sources, "mkt", "a-incomplete", context="NO", complete=False
    )
    aggregator = tmp_path / "aggregator"
    shutil.copytree(PLUGIN, aggregator)

    result = _run(
        tmp_path,
        [("a-incomplete", incomplete), ("context-injection", aggregator)],
    )

    assert json.loads(result.stdout) == {}
    assert "incomplete or incompatible" in result.stderr


def test_checks_every_declared_hook_file(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    plugin = _plugin(sources, "mkt", "a-multi", context="MULTI")
    (plugin / "first-hooks.json").write_text(
        json.dumps({"version": 1, "hooks": {"sessionEnd": [{"bash": "true"}]}}),
        encoding="utf-8",
    )
    (plugin / "second-hooks.json").write_text(
        json.dumps({"version": 1, "hooks": {"sessionStart": [{"bash": "true"}]}}),
        encoding="utf-8",
    )
    manifest = json.loads((plugin / "plugin.json").read_text(encoding="utf-8"))
    manifest["hooks"] = ["first-hooks.json", "second-hooks.json"]
    manifest.pop("sessionContext")
    (plugin / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    aggregator = tmp_path / "aggregator"
    shutil.copytree(PLUGIN, aggregator)

    result = _run(
        tmp_path,
        [("a-multi", plugin), ("context-injection", aggregator)],
    )

    assert json.loads(result.stdout) == {}
    assert "no complete context declaration" in result.stderr


def test_rejects_declared_context_over_budget(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    plugin = _plugin(sources, "mkt", "a-large", context="LARGE")
    declaration_path = plugin / "session-context.json"
    declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
    declaration["contributors"][0]["maxBytes"] = 64 * 1024
    declaration_path.write_text(json.dumps(declaration), encoding="utf-8")
    aggregator = tmp_path / "aggregator"
    shutil.copytree(PLUGIN, aggregator)

    result = _run(
        tmp_path,
        [("a-large", plugin), ("context-injection", aggregator)],
    )

    assert json.loads(result.stdout) == {}
    assert "bytes exceed aggregate admission budget" in result.stderr


def test_untrusted_repository_settings_do_not_activate_plugins(
    tmp_path: Path,
) -> None:
    sources = tmp_path / "sources"
    repo_only = _plugin(sources, "mkt", "a-repo-only", context="REPO")
    aggregator = tmp_path / "aggregator"
    shutil.copytree(PLUGIN, aggregator)
    result = _run(
        tmp_path,
        [("context-injection", aggregator)],
    )
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    (home / ".copilot" / "config.json").write_text(
        json.dumps({"trustedFolders": [str(tmp_path)]}),
        encoding="utf-8",
    )
    settings = repo / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps(
            {
                "enabledPlugins": {
                    "a-repo-only@copilot-extensions": True,
                    "context-injection@copilot-extensions": True,
                }
            }
        ),
        encoding="utf-8",
    )
    target = (
        home
        / ".copilot"
        / "installed-plugins"
        / "copilot-extensions"
        / "a-repo-only"
    )
    shutil.copytree(repo_only, target)

    environment = os.environ.copy()
    environment["HOME"] = str(home)
    environment["COPILOT_PLUGIN_ROOT"] = str(
        home
        / ".copilot"
        / "installed-plugins"
        / "copilot-extensions"
        / "context-injection"
    )
    rerun = subprocess.run(
        [os.environ.get("PYTHON") or sys.executable, str(SCRIPT)],
        input=json.dumps({"cwd": str(repo), "source": "new", "sessionId": "s"}),
        text=True,
        capture_output=True,
        env=environment,
        check=True,
    )

    assert json.loads(result.stdout) == {}
    assert json.loads(rerun.stdout) == {}
    assert "REPO" not in rerun.stdout


def test_direct_marketplace_authority_resolves_exact_payloads(
    tmp_path: Path,
) -> None:
    first = _plugin(tmp_path / "sources-a", "mkt", "a-policy", context="POLICY")
    aggregator = tmp_path / "aggregator"
    shutil.copytree(PLUGIN, aggregator)

    result = _run(
        tmp_path,
        [("a-policy", first), ("context-injection", aggregator)],
    )

    assert "POLICY" in json.loads(result.stdout)["additionalContext"]


def test_staged_authority_and_producers_form_the_active_stack(
    tmp_path: Path,
) -> None:
    sources = tmp_path / "sources"
    first = _plugin(sources, "mkt", "a-policy", context="POLICY")
    second = _plugin(sources, "mkt", "b-catalog", context="CATALOG")

    result = _run(
        tmp_path,
        [
            ("a-policy", first),
            ("b-catalog", second),
            ("context-injection", PLUGIN),
        ],
        staged=["context-injection", "a-policy", "b-catalog"],
    )

    context = json.loads(result.stdout)["additionalContext"]
    assert "POLICY" in context
    assert "CATALOG" in context


def test_staged_inventory_ignores_enabled_but_unstaged_plugins(
    tmp_path: Path,
) -> None:
    sources = tmp_path / "sources"
    staged_policy = _plugin(sources, "mkt", "a-policy", context="STAGED")
    unstaged_policy = _plugin(sources, "mkt", "b-policy", context="UNSTAGED")

    result = _run(
        tmp_path,
        [
            ("a-policy", staged_policy),
            ("b-policy", unstaged_policy),
            ("context-injection", PLUGIN),
        ],
        staged=["context-injection", "a-policy"],
    )

    context = json.loads(result.stdout)["additionalContext"]
    assert "STAGED" in context
    assert "UNSTAGED" not in context


def test_duplicate_staged_root_is_canonicalized_and_deduplicated(
    tmp_path: Path,
) -> None:
    policy = _plugin(
        tmp_path / "sources",
        "mkt",
        "a-policy",
        context="POLICY",
    )

    result = _run(
        tmp_path,
        [("a-policy", policy), ("context-injection", PLUGIN)],
        staged=["context-injection", "a-policy", "a-policy"],
    )

    assert "POLICY" in json.loads(result.stdout)["additionalContext"]


def test_duplicate_staged_identity_restores_producer_direct_output(
    tmp_path: Path,
) -> None:
    policy = _plugin(
        tmp_path / "sources",
        "mkt",
        "a-policy",
        context="POLICY",
    )
    duplicate = tmp_path / "duplicate-policy"
    shutil.copytree(policy, duplicate)

    result = _run(
        tmp_path,
        [("a-policy", policy), ("context-injection", PLUGIN)],
        producer="a-policy@copilot-extensions/main",
        staged=["context-injection", "a-policy", duplicate],
    )

    assert json.loads(result.stdout) == {"additionalContext": "POLICY"}
    assert "duplicate staged plugin identity" in result.stderr


def test_ambiguous_staged_source_restores_producer_direct_output(
    tmp_path: Path,
) -> None:
    policy = _plugin(
        tmp_path / "sources",
        "mkt",
        "a-policy",
        context="POLICY",
    )

    result = _run(
        tmp_path,
        [("a-policy", policy), ("context-injection", PLUGIN)],
        producer="a-policy@copilot-extensions/main",
        staged=["context-injection", "a-policy"],
        enabled_overrides={"a-policy@other-marketplace": True},
    )

    assert json.loads(result.stdout) == {"additionalContext": "POLICY"}
    assert "source is missing or ambiguous" in result.stderr


@pytest.mark.parametrize(
    "invalid_arguments",
    [
        ["node", "copilot", "--acp", "--plugin-dir"],
        ["node", "copilot", "--acp", "--plugin-dir="],
        ["node", "copilot", "--acp", "--plugin-directory", "/tmp/plugin"],
    ],
)
def test_malformed_staged_arguments_restore_producer_direct_output(
    tmp_path: Path,
    invalid_arguments: list[str],
) -> None:
    policy = _plugin(
        tmp_path / "sources",
        "mkt",
        "a-policy",
        context="POLICY",
    )

    result = _run(
        tmp_path,
        [("a-policy", policy), ("context-injection", PLUGIN)],
        producer="a-policy@copilot-extensions/main",
        ancestry=[invalid_arguments],
    )

    assert json.loads(result.stdout) == {"additionalContext": "POLICY"}
    assert "staged plugin arguments are malformed" in result.stderr


def test_missing_staged_path_restores_producer_direct_output(
    tmp_path: Path,
) -> None:
    policy = _plugin(
        tmp_path / "sources",
        "mkt",
        "a-policy",
        context="POLICY",
    )
    missing = tmp_path / "missing-plugin"

    result = _run(
        tmp_path,
        [("a-policy", policy), ("context-injection", PLUGIN)],
        producer="a-policy@copilot-extensions/main",
        ancestry=[
            [
                "node",
                "copilot",
                "--acp",
                "--plugin-dir",
                str(missing),
            ]
        ],
    )

    assert json.loads(result.stdout) == {"additionalContext": "POLICY"}
    assert "staged plugin root is unavailable" in result.stderr


def test_conflicting_acp_ancestry_restores_producer_direct_output(
    tmp_path: Path,
) -> None:
    policy = _plugin(
        tmp_path / "sources",
        "mkt",
        "a-policy",
        context="POLICY",
    )
    other = _plugin(
        tmp_path / "other-sources",
        "mkt",
        "b-policy",
        context="OTHER",
    )

    result = _run(
        tmp_path,
        [("a-policy", policy), ("context-injection", PLUGIN)],
        producer="a-policy@copilot-extensions/main",
        ancestry=[
            ["node", "copilot", "--acp", "--plugin-dir", str(policy)],
            ["node", "copilot", "--acp", "--plugin-dir", str(other)],
        ],
    )

    assert json.loads(result.stdout) == {"additionalContext": "POLICY"}
    assert "conflicting staged plugin arguments" in result.stderr


def test_unstaged_authority_restores_producer_direct_output(
    tmp_path: Path,
) -> None:
    policy = _plugin(
        tmp_path / "sources",
        "mkt",
        "a-policy",
        context="POLICY",
    )

    result = _run(
        tmp_path,
        [("a-policy", policy), ("context-injection", PLUGIN)],
        producer="a-policy@copilot-extensions/main",
        staged=["a-policy"],
    )

    assert json.loads(result.stdout) == {"additionalContext": "POLICY"}
    assert "configured context authority is missing or ambiguous" in result.stderr


def test_contributors_run_from_session_cwd(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    plugin = _plugin(sources, "mkt", "a-cwd", context="placeholder")
    (plugin / "scripts" / "emit.sh").write_text(
        "#!/usr/bin/env bash\n"
        "printf '{\"additionalContext\":\"CWD:%s\"}' \"$PWD\"\n",
        encoding="utf-8",
    )
    (plugin / "scripts" / "emit.ps1").write_text(
        "[Console]::Out.Write((@{ additionalContext = "
        "('CWD:' + $PWD.Path) } | ConvertTo-Json -Compress))\n",
        encoding="utf-8",
    )
    aggregator = tmp_path / "aggregator"
    shutil.copytree(PLUGIN, aggregator)
    subdir = tmp_path / "repo" / "nested"
    subdir.mkdir(parents=True)

    result = _run(
        tmp_path,
        [("a-cwd", plugin), ("context-injection", aggregator)],
        cwd=subdir,
    )

    assert f"CWD:{subdir}" in json.loads(result.stdout)[
        "additionalContext"
    ]


def test_one_failed_contributor_rejects_partial_aggregate(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    healthy = _plugin(sources, "mkt", "a-healthy", context="HEALTHY")
    broken = _plugin(sources, "mkt", "b-broken", context="BROKEN")
    (broken / "scripts" / "emit.sh").write_text(
        "#!/usr/bin/env bash\nprintf 'not-json'\n",
        encoding="utf-8",
    )
    (broken / "scripts" / "emit.ps1").write_text(
        "[Console]::Out.Write('not-json')\n",
        encoding="utf-8",
    )
    aggregator = tmp_path / "aggregator"
    shutil.copytree(PLUGIN, aggregator)

    result = _run(
        tmp_path,
        [
            ("a-healthy", healthy),
            ("b-broken", broken),
            ("context-injection", aggregator),
        ],
    )

    assert json.loads(result.stdout) == {}
    assert "contributors failed" in result.stderr


def test_engine_contract_is_versioned() -> None:
    manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    contract = json.loads(
        (PLUGIN / manifest["sessionContextEngine"]).read_text(encoding="utf-8")
    )
    assert contract == {
        "schema": "copilot-extensions.context-injection-engine",
        "version": 4,
    }


def test_malformed_authority_override_fails_closed_with_diagnostic(
    tmp_path: Path,
) -> None:
    authority_plugin = tmp_path / "authority"
    shutil.copytree(PLUGIN, authority_plugin)

    result = _run(
        tmp_path,
        [("context-injection", authority_plugin)],
        authority="../invalid",
    )

    assert json.loads(result.stdout) == {}
    assert "override does not match repository adoption" in result.stderr


def test_authority_override_cannot_replace_canonical_plugin_name(
    tmp_path: Path,
) -> None:
    authority_plugin = tmp_path / "authority"
    shutil.copytree(PLUGIN, authority_plugin)

    result = _run(
        tmp_path,
        [("context-injection", authority_plugin)],
        authority="other-plugin@copilot-extensions",
    )

    assert json.loads(result.stdout) == {}
    assert "override does not match repository adoption" in result.stderr


def test_producer_suppresses_direct_output_only_for_proven_authority(
    tmp_path: Path,
) -> None:
    policy = _plugin(
        tmp_path / "sources",
        "mkt",
        "a-policy",
        context="POLICY",
    )
    aggregator = tmp_path / "aggregator"
    shutil.copytree(PLUGIN, aggregator)

    result = _run(
        tmp_path,
        [("a-policy", policy), ("context-injection", aggregator)],
        producer="a-policy@copilot-extensions/main",
    )
    authority_result = _run(
        tmp_path,
        [("a-policy", policy), ("context-injection", aggregator)],
    )
    repeated_producer = _run(
        tmp_path,
        [("a-policy", policy), ("context-injection", aggregator)],
        producer="a-policy@copilot-extensions/main",
    )

    assert result.stdout == authority_result.stdout
    assert repeated_producer.stdout == authority_result.stdout
    context = json.loads(authority_result.stdout)["additionalContext"]
    assert "a-policy@copilot-extensions/main" in context
    assert "POLICY" in context


def test_producer_wrapper_uses_adopted_authority(tmp_path: Path) -> None:
    policy = _plugin(
        tmp_path / "sources",
        "mkt",
        "a-policy",
        context="POLICY",
    )
    aggregator = tmp_path / "aggregator"
    shutil.copytree(PLUGIN, aggregator)

    producer = _run(
        tmp_path,
        [("a-policy", policy), ("context-injection", aggregator)],
        producer="a-policy@copilot-extensions/main",
        via_wrapper=True,
    )
    authority = _run(
        tmp_path,
        [("a-policy", policy), ("context-injection", aggregator)],
    )

    assert producer.stdout == authority.stdout
    assert "POLICY" in json.loads(producer.stdout)["additionalContext"]


def test_authority_and_producer_order_yield_one_identical_aggregate(
    tmp_path: Path,
) -> None:
    policy = _plugin(
        tmp_path / "sources",
        "mkt",
        "a-policy",
        context="POLICY",
    )
    authority = tmp_path / "authority"
    shutil.copytree(PLUGIN, authority)
    plugins = [
        ("a-policy", policy),
        ("context-injection", authority),
    ]
    aggregate_outputs: list[str] = []

    for session_id, order in (
        ("authority-first", ("authority", "producer")),
        ("producer-first", ("producer", "authority")),
    ):
        outputs: list[str] = []
        for caller in order:
            result = _run(
                tmp_path,
                plugins,
                producer=(
                    "a-policy@copilot-extensions/main"
                    if caller == "producer"
                    else None
                ),
                session_id=session_id,
            )
            outputs.append(result.stdout)
            if caller == "authority":
                aggregate_outputs.append(result.stdout)

        assert len(set(outputs)) == 1
        assert json.loads(outputs[0]) != {}

    assert aggregate_outputs[0] == aggregate_outputs[1]


@pytest.mark.parametrize(
    "adoption_case",
    [
        "missing",
        "malformed",
        "incomplete",
        "incompatible",
        "ambiguous",
        "unknown",
        "legacy-setting",
        "inactive",
        "missing-authority-hook",
    ],
)
def test_producer_restores_direct_output_without_exact_compatible_authority(
    tmp_path: Path,
    adoption_case: str,
) -> None:
    policy = _plugin(
        tmp_path / "sources",
        "mkt",
        "a-policy",
        context="POLICY",
    )
    aggregator = tmp_path / "aggregator"
    shutil.copytree(PLUGIN, aggregator)

    result = _run(
        tmp_path,
        [("a-policy", policy), ("context-injection", aggregator)],
        producer="a-policy@copilot-extensions/main",
        adoption_case=adoption_case,
    )

    assert json.loads(result.stdout) == {"additionalContext": "POLICY"}
    assert "context-contributor:" not in result.stdout


def test_second_declared_authority_restores_producer_direct_output(
    tmp_path: Path,
) -> None:
    policy = _plugin(
        tmp_path / "sources",
        "mkt",
        "a-policy",
        context="POLICY",
    )
    second_authority = _plugin(
        tmp_path / "sources",
        "mkt",
        "other-authority",
        context="UNUSED",
    )
    declaration_path = second_authority / "session-context.json"
    declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
    declaration["sessionStart"]["context"] = "aggregate-authority"
    declaration["contributors"] = []
    declaration_path.write_text(json.dumps(declaration), encoding="utf-8")
    authority = tmp_path / "authority"
    shutil.copytree(PLUGIN, authority)

    result = _run(
        tmp_path,
        [
            ("a-policy", policy),
            ("other-authority", second_authority),
            ("context-injection", authority),
        ],
        producer="a-policy@copilot-extensions/main",
    )

    assert json.loads(result.stdout) == {"additionalContext": "POLICY"}
    assert "missing or ambiguous" in result.stderr


def test_producer_fallback_supports_claude_plugin_manifest_only(
    tmp_path: Path,
) -> None:
    policy = _plugin(
        tmp_path / "sources",
        "mkt",
        "a-policy",
        context="POLICY",
    )
    claude_manifest = policy / ".claude-plugin" / "plugin.json"
    claude_manifest.parent.mkdir()
    (policy / "plugin.json").replace(claude_manifest)

    result = _run(
        tmp_path,
        [("a-policy", policy)],
        producer="a-policy@copilot-extensions/main",
        adoption_case="missing",
    )

    assert json.loads(result.stdout) == {"additionalContext": "POLICY"}
    assert "context-contributor:" not in result.stdout


def test_post_proof_unsafe_declaration_publishes_shared_empty(
    tmp_path: Path,
) -> None:
    policy = _plugin(
        tmp_path / "sources",
        "mkt",
        "a-policy",
        context="POLICY",
    )
    declaration_path = policy / "session-context.json"
    declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
    declaration["sessionStart"]["context"] = "none"
    declaration_path.write_text(json.dumps(declaration), encoding="utf-8")

    result = _run(
        tmp_path,
        [("a-policy", policy)],
        producer="a-policy@copilot-extensions/main",
        session_id="unsafe",
    )
    authority_result = _run(
        tmp_path,
        [("a-policy", policy)],
        session_id="unsafe",
    )

    assert result.stdout == authority_result.stdout == "{}"
    assert "not adoption-safe" in result.stderr


def test_producer_restores_direct_output_for_oversized_hook_input(
    tmp_path: Path,
) -> None:
    policy = _plugin(
        tmp_path / "sources",
        "mkt",
        "a-policy",
        context="POLICY",
    )
    hook_input = json.dumps(
        {
            "cwd": str(tmp_path / "repo"),
            "sessionId": "oversized",
            "padding": "x" * (64 * 1024),
        }
    )

    result = _run(
        tmp_path,
        [("a-policy", policy)],
        producer="a-policy@copilot-extensions/main",
        raw_input=hook_input,
    )

    assert json.loads(result.stdout) == {"additionalContext": "POLICY"}
    assert "exceeds the configured limit" in result.stderr


def test_direct_fallback_does_not_apply_aggregate_admission_budget(
    tmp_path: Path,
) -> None:
    policy = _plugin(
        tmp_path / "sources",
        "mkt",
        "a-policy",
        context="DIRECT",
    )
    declaration_path = policy / "session-context.json"
    declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
    declaration["contributors"][0]["maxBytes"] = 64 * 1024
    declaration_path.write_text(json.dumps(declaration), encoding="utf-8")

    result = _run(
        tmp_path,
        [("a-policy", policy)],
        producer="a-policy@copilot-extensions/main",
        adoption_case="missing",
    )

    assert json.loads(result.stdout) == {"additionalContext": "DIRECT"}


def test_invalid_contributor_id_cannot_enter_adopted_stack(
    tmp_path: Path,
) -> None:
    policy = _plugin(
        tmp_path / "sources",
        "mkt",
        "a-policy",
        context="POLICY",
    )
    declaration_path = policy / "session-context.json"
    declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
    declaration["contributors"][0]["id"] = "bad/id"
    declaration_path.write_text(json.dumps(declaration), encoding="utf-8")

    result = _run(tmp_path, [("a-policy", policy)])

    assert json.loads(result.stdout) == {}
    assert "not adoption-safe" in result.stderr


def test_rendezvous_failure_returns_stable_empty_output(
    tmp_path: Path,
) -> None:
    policy = _plugin(
        tmp_path / "sources",
        "mkt",
        "a-policy",
        context="POLICY",
    )
    blocked_cache = tmp_path / "blocked-cache"
    blocked_cache.write_text("not a directory", encoding="utf-8")

    first = _run(
        tmp_path,
        [("a-policy", policy)],
        producer="a-policy@copilot-extensions/main",
        session_id="blocked",
        cache_dir=blocked_cache,
    )
    second = _run(
        tmp_path,
        [("a-policy", policy)],
        producer="a-policy@copilot-extensions/main",
        session_id="blocked",
        cache_dir=blocked_cache,
    )

    assert first.stdout == second.stdout == "{}"
    assert "rendezvous lock is unavailable" in first.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership and mode contract")
def test_rendezvous_rejects_non_private_cache_root(tmp_path: Path) -> None:
    policy = _plugin(
        tmp_path / "sources",
        "mkt",
        "a-policy",
        context="POLICY",
    )
    shared_cache = tmp_path / "shared-cache"
    shared_cache.mkdir(mode=0o755)
    shared_cache.chmod(0o755)

    result = _run(
        tmp_path,
        [("a-policy", policy)],
        producer="a-policy@copilot-extensions/main",
        session_id="shared",
        cache_dir=shared_cache,
    )

    assert json.loads(result.stdout) == {}
    assert "rendezvous lock is unavailable" in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership and mode contract")
def test_rendezvous_cache_files_are_private(tmp_path: Path) -> None:
    policy = _plugin(
        tmp_path / "sources",
        "mkt",
        "a-policy",
        context="POLICY",
    )
    cache = tmp_path / "private-cache"

    result = _run(
        tmp_path,
        [("a-policy", policy)],
        producer="a-policy@copilot-extensions/main",
        session_id="private",
        cache_dir=cache,
    )

    assert "POLICY" in json.loads(result.stdout)["additionalContext"]
    assert cache.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in cache.iterdir())


def test_post_admission_failure_is_one_shared_empty_result(
    tmp_path: Path,
) -> None:
    sources = tmp_path / "sources"
    first_policy = _plugin(sources, "mkt", "a-policy", context="A")
    second_policy = _plugin(sources, "mkt", "b-policy", context="B")
    failing = _plugin(sources, "mkt", "failing", context="unused")
    (failing / "scripts" / "emit.sh").write_text(
        "#!/usr/bin/env bash\nprintf 'not-json'\n",
        encoding="utf-8",
    )
    (failing / "scripts" / "emit.ps1").write_text(
        "[Console]::Out.Write('not-json')\n",
        encoding="utf-8",
    )
    plugins = [
        ("a-policy", first_policy),
        ("b-policy", second_policy),
        ("failing", failing),
    ]

    first = _run(
        tmp_path,
        plugins,
        producer="a-policy@copilot-extensions/main",
        session_id="failed-pair",
    )
    second = _run(
        tmp_path,
        plugins,
        producer="b-policy@copilot-extensions/main",
        session_id="failed-pair",
    )
    authority_result = _run(
        tmp_path,
        plugins,
        session_id="failed-pair",
    )

    assert first.stdout == second.stdout == authority_result.stdout == "{}"
    assert "one or more contributors failed" in first.stderr


def test_rendezvous_identity_is_exact_session_and_canonical_cwd_pair(
    tmp_path: Path,
) -> None:
    policy = _plugin(
        tmp_path / "sources",
        "mkt",
        "a-policy",
        context="FIRST",
    )
    aggregator = tmp_path / "aggregator"
    shutil.copytree(PLUGIN, aggregator)
    first = _run(
        tmp_path,
        [("a-policy", policy), ("context-injection", aggregator)],
        session_id="session-a",
    )

    (policy / "scripts" / "emit.sh").write_text(
        "#!/usr/bin/env bash\n"
        "payload=\"$(cat)\"\n"
        "python3 -c 'import json,sys; p=json.loads(sys.argv[1]); "
        "print(json.dumps({\"additionalContext\":\"SECOND:\"+p[\"sessionId\"]"
        "+\":\"+p[\"cwd\"]},separators=(\",\",\":\")))' \"$payload\"\n",
        encoding="utf-8",
    )
    (policy / "scripts" / "emit.ps1").write_text(
        "$p = [Console]::In.ReadToEnd() | ConvertFrom-Json\n"
        "$context = 'SECOND:' + $p.sessionId + ':' + $p.cwd\n"
        "[Console]::Out.Write((@{ additionalContext = $context } | "
        "ConvertTo-Json -Compress))\n",
        encoding="utf-8",
    )

    repeated = _run(
        tmp_path,
        [("a-policy", policy), ("context-injection", aggregator)],
        session_id="session-a",
    )
    nested = tmp_path / "repo" / "nested"
    nested.mkdir()
    different_cwd = _run(
        tmp_path,
        [("a-policy", policy), ("context-injection", aggregator)],
        cwd=nested,
        session_id="session-a",
    )
    different_session = _run(
        tmp_path,
        [("a-policy", policy), ("context-injection", aggregator)],
        session_id="session-b",
    )

    assert repeated.stdout == first.stdout
    assert different_cwd.stdout != first.stdout
    assert "SECOND:session-a" in different_cwd.stdout
    assert different_session.stdout != first.stdout
    assert "SECOND:session-b" in different_session.stdout


def test_concurrent_producers_and_authority_emit_stable_bytes(
    tmp_path: Path,
) -> None:
    first_policy = _plugin(
        tmp_path / "sources",
        "mkt",
        "a-policy",
        context="SETUP",
    )
    second_policy = _plugin(
        tmp_path / "sources",
        "mkt",
        "b-policy",
        context="SETUP",
    )
    _run(
        tmp_path,
        [("a-policy", first_policy), ("b-policy", second_policy)],
        session_id="setup",
    )

    installed = (
        tmp_path
        / "home"
        / ".copilot"
        / "installed-plugins"
        / "copilot-extensions"
    )
    for name in ("a-policy", "b-policy"):
        installed_policy = installed / name
        (installed_policy / "scripts" / "emit.sh").write_text(
            "#!/usr/bin/env bash\n"
            "sleep 0.3\n"
            "python3 -c 'import json,uuid; "
            "print(json.dumps({\"additionalContext\":str(uuid.uuid4())},"
            "separators=(\",\",\":\")))'\n",
            encoding="utf-8",
        )
        (installed_policy / "scripts" / "emit.ps1").write_text(
            "Start-Sleep -Milliseconds 300\n"
            "$context = [guid]::NewGuid().ToString()\n"
            "[Console]::Out.Write((@{ additionalContext = $context } | "
            "ConvertTo-Json -Compress))\n",
            encoding="utf-8",
        )
    engine_command = [
        os.environ.get("PYTHON") or sys.executable,
        str(installed / "context-injection" / "scripts" / "aggregate_context.py"),
    ]
    producer_commands = [
        [
            *engine_command,
            "--producer",
            f"{name}@copilot-extensions/main",
        ]
        for name in ("a-policy", "b-policy")
    ]
    caller_roots = [
        installed / "a-policy",
        installed / "b-policy",
        installed / "context-injection",
    ]
    hook_input = json.dumps(
        {
            "cwd": str(tmp_path / "repo"),
            "source": "new",
            "sessionId": "concurrent",
        }
    )
    processes: list[subprocess.Popen[str]] = []
    for command, caller_root in zip(
        [*producer_commands, engine_command],
        caller_roots,
        strict=True,
    ):
        environment = os.environ.copy()
        environment["HOME"] = str(tmp_path / "home")
        environment["USERPROFILE"] = str(tmp_path / "home")
        environment["COPILOT_PLUGIN_ROOT"] = str(caller_root)
        environment["COPILOT_CONTEXT_INJECTION_CACHE_DIR"] = str(
            tmp_path / "cache"
        )
        environment["COPILOT_CONTEXT_INJECTION_TEST_NO_STAGED_PLUGINS"] = "1"
        processes.append(
            subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
            )
        )

    results = [
        process.communicate(hook_input, timeout=30)
        for process in processes
    ]
    for process, (_, stderr) in zip(processes, results, strict=True):
        assert process.returncode == 0, stderr
    outputs = [stdout for stdout, _ in results]
    assert len(set(outputs)) == 1
    assert "context-contributor:" in outputs[0]

    authority_environment = os.environ.copy()
    authority_environment["HOME"] = str(tmp_path / "home")
    authority_environment["USERPROFILE"] = str(tmp_path / "home")
    authority_environment["COPILOT_PLUGIN_ROOT"] = str(caller_roots[2])
    authority_environment["COPILOT_CONTEXT_INJECTION_CACHE_DIR"] = str(
        tmp_path / "cache"
    )
    authority_environment["COPILOT_CONTEXT_INJECTION_TEST_NO_STAGED_PLUGINS"] = "1"
    repeated_authority = subprocess.run(
        engine_command,
        input=hook_input,
        text=True,
        capture_output=True,
        env=authority_environment,
        check=True,
    )

    assert repeated_authority.stdout == outputs[2]


def test_bash_wrapper_discards_partial_output_on_aggregator_failure(
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "context-injection"
    shutil.copytree(PLUGIN, plugin)
    aggregate = plugin / "scripts" / "aggregate_context.py"
    aggregate.write_text(
        "import sys\nprint('{\"partial\":true}', end='')\nsys.exit(1)\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["COPILOT_PLUGIN_ROOT"] = _bash_path(plugin)

    result = subprocess.run(
        ["bash", _bash_path(BASH_WRAPPER)],
        input="{}",
        text=True,
        capture_output=True,
        env=environment,
        check=True,
    )

    assert json.loads(result.stdout) == {}
    assert "partial" not in result.stdout


@pytest.mark.skipif(
    not (shutil.which("pwsh") or shutil.which("powershell.exe")),
    reason="PowerShell is unavailable",
)
def test_powershell_wrapper_discards_partial_output_on_aggregator_failure(
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "context-injection"
    shutil.copytree(PLUGIN, plugin)
    aggregate = plugin / "scripts" / "aggregate_context.py"
    aggregate.write_text(
        "import sys\nprint('{\"partial\":true}', end='')\nsys.exit(1)\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["COPILOT_PLUGIN_ROOT"] = str(plugin)
    powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
    assert powershell

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-File",
            str(POWERSHELL_WRAPPER),
        ],
        input="{}",
        text=True,
        capture_output=True,
        env=environment,
        check=True,
    )

    assert json.loads(result.stdout) == {}
    assert "partial" not in result.stdout
