"""Regression tests for the report-only marketplace-isolation inventory."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check-marketplace-isolation.py"
REPO = SCRIPT.parent.parent

_spec = importlib.util.spec_from_file_location("check_marketplace_isolation", SCRIPT)
cmi = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules[_spec.name] = cmi
_spec.loader.exec_module(cmi)


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _categories(path: Path, root: Path) -> list[str]:
    return [finding.category for finding in cmi._scan_file(path, root)]


def test_inventory_categories(tmp_path: Path) -> None:
    root = tmp_path
    assert _categories(
        _write(
            root,
            "plugins/example/scripts/install.sh",
            'root="$HOME/.agent-example"\n',
        ),
        root,
    ) == ["unqualified-runtime-root"]
    assert _categories(
        _write(
            root,
            "plugins/example/scripts/binstub.sh",
            'dst="$HOME/.local/bin/agent-example"\n',
        ),
        root,
    ) == ["global-plugin-binstub"]
    assert _categories(
        _write(
            root,
            "plugins/example/src/example/peer.py",
            'subprocess.run(["agent-peer", "status"], check=True)\n',
        ),
        root,
    ) == ["path-sibling-launch"]
    assert _categories(
        _write(
            root,
            "plugins/example/scripts/service.ps1",
            '$TaskName = "Agent Example"\n',
        ),
        root,
    ) == ["fixed-service-identity"]
    assert _categories(
        _write(
            root,
            "plugins/example/skills/example/SKILL.md",
            "Run `agent-example status` before continuing.\n",
        ),
        root,
    ) == ["bare-agent-command"]
    assert _categories(
        _write(
            root,
            "plugins/example/skills/example/SKILL.md",
            "Run `agent-example verify\n<name>` before continuing.\n",
        ),
        root,
    ) == ["bare-agent-command"]
    assert _categories(
        _write(
            root,
            "plugins/example/skills/example/SKILL.md",
            "```\nagent-example verify\n<name>\n```\n",
        ),
        root,
    ) == ["bare-agent-command"]
    assert _categories(
        _write(
            root,
            "plugins/example/skills/example/SKILL.md",
            "Run `agent-example verify\n<name>` before continuing. "
            "<!-- marketplace-isolation: allow legacy-management -->\n",
        ),
        root,
    ) == []

    _write(
        root,
        "plugins/example/payload-invocation.json",
        '{"commands":[{"command":"session-sync"}]}\n',
    )
    assert _categories(
        _write(
            root,
            "plugins/example/skills/example/SKILL.md",
            "Run `session-sync run --prune` now.\n",
        ),
        root,
    ) == ["bare-agent-command"]
    assert _categories(
        _write(
            root,
            "plugins/example/skills/example/SKILL.md",
            "session-sync run --prune\n",
        ),
        root,
    ) == ["bare-agent-command"]
    assert _categories(
        _write(
            root,
            "plugins/example/skills/example/SKILL.md",
            "Session-sync is machine-local.\n",
        ),
        root,
    ) == []
    assert _categories(
        _write(
            root,
            "plugins/example/skills/example/SKILL.md",
            "- session-sync run --prune\n",
        ),
        root,
    ) == ["bare-agent-command"]
    assert _categories(
        _write(
            root,
            "plugins/example/src/example/runtime.py",
            'subprocess.run(["session-sync", "run"], check=True)\n',
        ),
        root,
    ) == ["path-sibling-launch"]
    assert _categories(
        _write(
            root,
            "plugins/example/src/example/runtime.py",
            'cmd = ["session-sync", "run"]\nsubprocess.run(cmd, check=True)\n',
        ),
        root,
    ) == ["path-sibling-launch"]
    assert _categories(
        _write(
            root,
            "plugins/example/src/example/runtime.py",
            "subprocess.run(\n"
            '    ["session-sync", "run"],\n'
            "    check=True,\n"
            ")\n",
        ),
        root,
    ) == ["path-sibling-launch"]
    assert _categories(
        _write(
            root,
            "plugins/example/src/example/runtime.js",
            "function launch(cmd) { return spawn(cmd, []); }\n"
            'launch("session-sync");\n',
        ),
        root,
    ) == ["path-sibling-launch"]
    assert _categories(
        _write(
            root,
            "plugins/example/skills/example/SKILL.md",
            "`agent-bridge\n"
            "live-sessions progress --handle example`\n",
        ),
        root,
    ) == ["bare-agent-command"]
    assert _categories(
        _write(
            root,
            "plugins/example/src/example/runtime.js",
            "function launch(cmd) { return spawn(cmd, []); }\n"
            "launch(\n"
            '  "session-sync"\n'
            ");\n",
        ),
        root,
    ) == ["path-sibling-launch"]


def test_payload_catalog_adopter_capabilities_avoid_bare_global_commands() -> None:
    patterns = cmi._command_patterns(REPO)
    for plugin in (
        "agent-bridge",
        "agent-codespaces",
        "agent-containers",
        "agent-dispatch",
        "agent-logger",
        "agent-machines",
        "agent-mcp",
        "agent-ssh",
        "agent-vault",
        "agent-worktrees",
    ):
        findings = [
            finding
            for surface in ("skills", "agents")
            if (REPO / "plugins" / plugin / surface).is_dir()
            for capability in (REPO / "plugins" / plugin / surface).rglob("*.md")
            for finding in cmi._scan_file(capability, REPO, patterns)
            if finding.category == "bare-agent-command"
        ]
        assert findings == [], plugin


def test_payload_manifest_command_is_a_declaration_not_a_path_launch(
    tmp_path: Path,
) -> None:
    manifest = _write(
        tmp_path,
        "plugins/example/payload-invocation.json",
        '{"command": "agent-example", "runtimeRoot": ".agent-example"}\n',
    )
    assert _categories(manifest, tmp_path) == ["unqualified-runtime-root"]


def test_common_python_powershell_and_javascript_forms(tmp_path: Path) -> None:
    root = tmp_path
    python = _write(
        root,
        "plugins/example/src/example/runtime.py",
        'root = Path.home() / ".agent-example"\n'
        '_SERVICE = "agent-example"\n',
    )
    assert _categories(python, root) == [
        "unqualified-runtime-root",
        "fixed-service-identity",
    ]

    powershell = _write(
        root,
        "plugins/example/scripts/install.ps1",
        "$InstallDir = Join-Path $env:USERPROFILE '.agent-example'\n",
    )
    assert _categories(powershell, root) == ["unqualified-runtime-root"]

    javascript = _write(
        root,
        "plugins/example/extensions/example/index.mjs",
        'execSync("agent-peer health");\n',
    )
    assert _categories(javascript, root) == ["path-sibling-launch"]


def test_prefixed_lifecycle_names_and_javascript_wrappers(tmp_path: Path) -> None:
    root = tmp_path
    lifecycle = _write(
        root,
        "plugins/example/scripts/tasks.ps1",
        "$OwnerTaskName = 'agent-example-owner'\n"
        "$SupervisorTaskName = 'agent-example-supervisor'\n",
    )
    assert _categories(lifecycle, root) == [
        "fixed-service-identity",
        "fixed-service-identity",
    ]

    javascript = _write(
        root,
        "plugins/example/extensions/example/index.mjs",
        "export function runCli(bin, args) {\n"
        "  return execFileSync(bin, args);\n"
        "}\n"
        'runCli("agent-peer", ["status"]);\n',
    )
    assert _categories(javascript, root) == ["path-sibling-launch"]


def test_nested_transport_and_multiline_launch_are_scanned(tmp_path: Path) -> None:
    root = tmp_path
    transport = _write(
        root,
        "plugins/example/transports/ssh/scripts/launcher.ps1",
        '$mutexName = "Global\\Launcher_$Alias"\n',
    )
    python = _write(
        root,
        "plugins/example/core/board_cli.py",
        'command = ["agent-peer", "status"]\n'
        "subprocess.run(command, check=True)\n",
    )
    files_scanned, findings = cmi.scan(root)
    assert files_scanned == 2
    assert [(finding.path, finding.category) for finding in findings] == [
        (
            "plugins/example/core/board_cli.py",
            "path-sibling-launch",
        ),
        (
            "plugins/example/transports/ssh/scripts/launcher.ps1",
            "fixed-service-identity",
        ),
    ]
    assert _categories(transport, root) == ["fixed-service-identity"]
    assert _categories(python, root) == ["path-sibling-launch"]


def test_fixed_identity_ignores_mapping_prose_and_cell_qualified_value(
    tmp_path: Path,
) -> None:
    root = tmp_path
    path = _write(
        root,
        "plugins/example/src/example/help.py",
        "help_text = \"task: 'worktree'\"\n"
        '_SERVICE = f"agent-example-{installation_id}"\n',
    )
    assert _categories(path, root) == []


def test_comments_and_allow_markers_are_not_flagged(tmp_path: Path) -> None:
    root = tmp_path
    shell = _write(
        root,
        "plugins/example/scripts/install.sh",
        '# root="$HOME/.agent-example"\n'
        'root="$HOME/.agent-example"  # marketplace-isolation: allow legacy migration\n',
    )
    assert _categories(shell, root) == []

    powershell = _write(
        root,
        "plugins/example/scripts/install.ps1",
        "<#\n$TaskName = \"Agent Example\"\n#>\n",
    )
    assert _categories(powershell, root) == []


def test_inline_comments_are_not_flagged(tmp_path: Path) -> None:
    root = tmp_path
    shell = _write(
        root,
        "plugins/example/scripts/install.sh",
        'root="$HOME/cell"  # old root was "$HOME/.agent-example"\n',
    )
    powershell = _write(
        root,
        "plugins/example/scripts/install.ps1",
        "$root = Join-Path $HOME 'cell'  # old: ~/.agent-example\n",
    )
    yaml = _write(
        root,
        "plugins/example/hooks.yaml",
        "root: cell  # old: ~/.agent-example\n",
    )
    javascript = _write(
        root,
        "plugins/example/extensions/example/index.mjs",
        'const root = "cell"; // execSync("agent-peer status");\n'
        'const other = "cell"; /* ~/.agent-example */\n'
        "const label = 'it\\'s // data'; execSync(\"agent-peer status\");\n",
    )
    assert _categories(shell, root) == []
    assert _categories(powershell, root) == []
    assert _categories(yaml, root) == []
    assert _categories(javascript, root) == ["path-sibling-launch"]


def test_python_docstrings_and_inline_comments_are_not_flagged(tmp_path: Path) -> None:
    root = tmp_path
    path = _write(
        root,
        "plugins/example/src/example/runtime.py",
        '"""Legacy runtime root: ``~/.agent-example``."""\n'
        "def root():\n"
        '    """Return ``~/.agent-example`` for old installs."""\n'
        '    return Path.home() / "cell"  # ~/.agent-example is legacy\n',
    )
    assert _categories(path, root) == []


def test_allow_marker_requires_reason(tmp_path: Path) -> None:
    root = tmp_path
    path = _write(
        root,
        "plugins/example/scripts/install.sh",
        'root="$HOME/.agent-example"  # marketplace-isolation: allow\n',
    )
    assert _categories(path, root) == [
        "unqualified-runtime-root",
        "invalid-allow",
    ]


def test_scan_scope_excludes_tests_and_docs(tmp_path: Path) -> None:
    root = tmp_path
    _write(
        root,
        "plugins/example/tests/test_legacy.py",
        'root = "~/.agent-example"\n',
    )
    _write(
        root,
        "plugins/example/docs/legacy.md",
        "Run `agent-example status`.\n",
    )
    _write(
        root,
        "plugins/example/.venv/lib/runtime.py",
        'root = "~/.agent-example"\n',
    )
    _write(
        root,
        "plugins/example/libs/example/build/generated.py",
        'subprocess.run(["agent-example", "status"])\n',
    )
    _write(
        root,
        "plugins/example/hooks.json",
        '{"hooks": {"sessionStart": [{"command": "agent-example status"}]}}\n',
    )
    files_scanned, findings = cmi.scan(root)
    assert files_scanned == 1
    assert [finding.category for finding in findings] == ["path-sibling-launch"]


def test_main_report_and_strict_exit_codes(tmp_path: Path, capsys: object) -> None:
    _write(
        tmp_path,
        "plugins/example/scripts/install.sh",
        'root="$HOME/.agent-example"\n',
    )
    assert cmi.main(["--root", str(tmp_path)]) == 0
    assert cmi.main(["--root", str(tmp_path), "--strict"]) == 1
