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
SCHEMA = "copilot-extensions.session-context-contributors"

SPEC = importlib.util.spec_from_file_location("aggregate_context", SCRIPT)
assert SPEC and SPEC.loader
AGGREGATE_CONTEXT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AGGREGATE_CONTEXT
SPEC.loader.exec_module(AGGREGATE_CONTEXT)


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
    declaration = {
        "schema": SCHEMA,
        "version": 1,
        "complete": complete,
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


def test_windows_staging_probe_passes_parent_pid_in_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(AGGREGATE_CONTEXT.os, "name", "nt")
    monkeypatch.setattr(AGGREGATE_CONTEXT.shutil, "which", lambda _: "pwsh")
    monkeypatch.setattr(AGGREGATE_CONTEXT.os, "getppid", lambda: 4242)
    captured: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(argv, 0, stdout="copilot --allow-all\n")

    monkeypatch.setattr(AGGREGATE_CONTEXT.subprocess, "run", fake_run)

    assert AGGREGATE_CONTEXT._has_unlisted_staged_plugins() is False
    assert "-ProcessId" not in captured["argv"]
    assert captured["env"]["COPILOT_CONTEXT_PARENT_PID"] == "4242"


def test_windows_staging_probe_detects_explicit_plugin_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(AGGREGATE_CONTEXT.os, "name", "nt")
    monkeypatch.setattr(AGGREGATE_CONTEXT.shutil, "which", lambda _: "pwsh")
    monkeypatch.setattr(
        AGGREGATE_CONTEXT.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv,
            0,
            stdout="copilot --plugin-dir /tmp/staged\n",
        ),
    )

    assert AGGREGATE_CONTEXT._has_unlisted_staged_plugins() is True


@pytest.mark.skipif(os.name != "nt", reason="Windows startup allowance")
def test_windows_contributors_receive_process_start_grace() -> None:
    assert AGGREGATE_CONTEXT.PROCESS_START_GRACE_SECONDS == 2


def _run(
    tmp_path: Path,
    plugins: list[tuple[str, Path]],
    *,
    cwd: Path | None = None,
    marketplace: str = "copilot-extensions",
    authority: str | None = None,
    include_official_engine: bool = False,
) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    settings = {
        "extraKnownMarketplaces": {
            marketplace: {
                "source": {
                    "source": "github",
                    "repo": "ThomasMichon/copilot-extensions",
                }
            }
        },
        "enabledPlugins": {
            f"{name}@{marketplace}": True for name, _ in plugins
        },
    }
    if include_official_engine:
        settings["extraKnownMarketplaces"]["copilot-extensions"] = {
            "source": {
                "source": "github",
                "repo": "ThomasMichon/copilot-extensions",
            }
        }
        settings["enabledPlugins"][
            "zz-context-injection@copilot-extensions"
        ] = True
    (home / ".copilot").mkdir(parents=True, exist_ok=True)
    (home / ".copilot" / "settings.json").write_text(
        json.dumps(settings), encoding="utf-8"
    )
    installed = home / ".copilot" / "installed-plugins" / marketplace
    installed.mkdir(parents=True, exist_ok=True)
    for name, source in plugins:
        target = installed / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
    if include_official_engine:
        official = (
            home
            / ".copilot"
            / "installed-plugins"
            / "copilot-extensions"
            / "zz-context-injection"
        )
        shutil.copytree(PLUGIN, official)
    aggregator = installed / "zz-context-injection"
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    environment["COPILOT_PLUGIN_ROOT"] = str(aggregator)
    if authority is not None:
        environment["COPILOT_CONTEXT_INJECTION_AUTHORITY"] = authority
    return subprocess.run(
        [os.environ.get("PYTHON") or sys.executable, str(SCRIPT)],
        input=json.dumps(
            {
                "cwd": str(cwd or repo),
                "source": "new",
                "sessionId": "s",
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
            ("zz-context-injection", aggregator),
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
        [("legacy", legacy), ("zz-context-injection", aggregator)],
    )

    assert json.loads(result.stdout) == {}
    assert "no complete context declaration" in result.stderr


def test_stands_down_when_an_active_plugin_sorts_after_it(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    later = _plugin(sources, "mkt", "zzz-later", context="LATER")
    aggregator = tmp_path / "aggregator"
    shutil.copytree(PLUGIN, aggregator)

    result = _run(
        tmp_path,
        [("zz-context-injection", aggregator), ("zzz-later", later)],
    )

    assert json.loads(result.stdout) == {}
    assert "not the final active plugin" in result.stderr


def test_rejects_incomplete_declaration(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    incomplete = _plugin(
        sources, "mkt", "a-incomplete", context="NO", complete=False
    )
    aggregator = tmp_path / "aggregator"
    shutil.copytree(PLUGIN, aggregator)

    result = _run(
        tmp_path,
        [("a-incomplete", incomplete), ("zz-context-injection", aggregator)],
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
        [("a-multi", plugin), ("zz-context-injection", aggregator)],
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
        [("a-large", plugin), ("zz-context-injection", aggregator)],
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
        [("zz-context-injection", aggregator)],
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
                    "zz-context-injection@copilot-extensions": True,
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
        / "zz-context-injection"
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


def test_trusted_directory_marketplace_resolves_exact_payloads(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    marketplace = tmp_path / "marketplace"
    (repo / ".git").mkdir(parents=True)
    first = _plugin(tmp_path / "sources-a", "mkt", "a-policy", context="POLICY")
    aggregator_source = tmp_path / "aggregator"
    shutil.copytree(PLUGIN, aggregator_source)
    shutil.copytree(first, marketplace / "a-policy")
    shutil.copytree(aggregator_source, marketplace / "zz-context-injection")
    manifest = {
        "name": "copilot-extensions",
        "owner": {"name": "Test"},
        "metadata": {"version": "1.0.0"},
        "plugins": [
            {
                "name": "a-policy",
                "version": "1.0.0",
                "description": "test",
                "source": "a-policy",
            },
            {
                "name": "zz-context-injection",
                "version": "0.1.0-dev1",
                "description": "test",
                "source": "zz-context-injection",
            },
        ],
    }
    manifest_path = marketplace / ".github" / "plugin" / "marketplace.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    settings_path = repo / ".github" / "copilot" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "extraKnownMarketplaces": {
                    "copilot-extensions": {
                        "source": {
                            "source": "directory",
                            "path": str(marketplace),
                        }
                    }
                },
                "enabledPlugins": {
                    "a-policy@copilot-extensions": True,
                    "zz-context-injection@copilot-extensions": True,
                },
            }
        ),
        encoding="utf-8",
    )
    copilot = home / ".copilot"
    copilot.mkdir(parents=True)
    (copilot / "config.json").write_text(
        json.dumps({"trustedFolders": [str(repo)]}),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    environment["COPILOT_PLUGIN_ROOT"] = str(
        marketplace / "zz-context-injection"
    )

    result = subprocess.run(
        [os.environ.get("PYTHON") or sys.executable, str(SCRIPT)],
        input=json.dumps({"cwd": str(repo), "source": "new", "sessionId": "s"}),
        text=True,
        capture_output=True,
        env=environment,
        check=True,
    )

    assert "POLICY" in json.loads(result.stdout)["additionalContext"]


def test_contributors_run_from_session_cwd(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    plugin = _plugin(sources, "mkt", "a-cwd", context="placeholder")
    (plugin / "scripts" / "emit.sh").write_text(
        "#!/usr/bin/env bash\n"
        "printf '{\"additionalContext\":\"CWD:%s\"}' \"$PWD\"\n",
        encoding="utf-8",
    )
    aggregator = tmp_path / "aggregator"
    shutil.copytree(PLUGIN, aggregator)
    subdir = tmp_path / "repo" / "nested"
    subdir.mkdir(parents=True)

    result = _run(
        tmp_path,
        [("a-cwd", plugin), ("zz-context-injection", aggregator)],
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
    aggregator = tmp_path / "aggregator"
    shutil.copytree(PLUGIN, aggregator)

    result = _run(
        tmp_path,
        [
            ("a-healthy", healthy),
            ("b-broken", broken),
            ("zz-context-injection", aggregator),
        ],
    )

    assert json.loads(result.stdout) == {}
    assert "contributors failed" in result.stderr


def test_source_qualified_tail_adapter_can_own_final_slot(
    tmp_path: Path,
) -> None:
    sources = tmp_path / "sources"
    policy = _plugin(sources, "mkt", "a-policy", context="POLICY")
    adapter = tmp_path / "adapter"
    shutil.copytree(PLUGIN, adapter)

    result = _run(
        tmp_path,
        [("a-policy", policy), ("zz-context-injection", adapter)],
        marketplace="aperture",
        authority="zz-context-injection@aperture",
        include_official_engine=True,
    )

    assert "POLICY" in json.loads(result.stdout)["additionalContext"]


def test_engine_contract_is_versioned() -> None:
    manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    contract = json.loads(
        (PLUGIN / manifest["sessionContextEngine"]).read_text(encoding="utf-8")
    )
    assert contract == {
        "schema": "copilot-extensions.context-injection-engine",
        "version": 1,
    }


def test_malformed_authority_override_fails_closed_with_diagnostic(
    tmp_path: Path,
) -> None:
    adapter = tmp_path / "adapter"
    shutil.copytree(PLUGIN, adapter)

    result = _run(
        tmp_path,
        [("zz-context-injection", adapter)],
        authority="../invalid",
    )

    assert json.loads(result.stdout) == {}
    assert "authority identity is invalid" in result.stderr


def test_authority_override_cannot_replace_canonical_plugin_name(
    tmp_path: Path,
) -> None:
    adapter = tmp_path / "adapter"
    shutil.copytree(PLUGIN, adapter)

    result = _run(
        tmp_path,
        [("zz-context-injection", adapter)],
        authority="other-plugin@aperture",
    )

    assert json.loads(result.stdout) == {}
    assert "authority identity is invalid" in result.stderr


def test_bash_wrapper_discards_partial_output_on_aggregator_failure(
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "zz-context-injection"
    shutil.copytree(PLUGIN, plugin)
    aggregate = plugin / "scripts" / "aggregate_context.py"
    aggregate.write_text(
        "import sys\nprint('{\"partial\":true}', end='')\nsys.exit(1)\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["COPILOT_PLUGIN_ROOT"] = str(plugin)

    result = subprocess.run(
        ["bash", str(BASH_WRAPPER)],
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
    plugin = tmp_path / "zz-context-injection"
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
