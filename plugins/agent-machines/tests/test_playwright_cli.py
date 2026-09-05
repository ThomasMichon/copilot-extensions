from __future__ import annotations

import json
import shutil
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_machines import __main__ as cli
from agent_machines import modules
from agent_machines import playwright_cli as playwright
from agent_machines.manifest import load_package

from ._helpers import write_package

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
LATEST = "2.0.0"
SKILL_INSTALL = [playwright.CLI_COMMAND, "install", "--skills", "agents"]


class Resolver:
    def __init__(self, **paths: str | None) -> None:
        self.paths = paths

    def __call__(self, name: str) -> str | None:
        return self.paths.get(name)


class FakeRunner:
    def __init__(self, steps) -> None:
        self.steps = list(steps)
        self.calls: list[tuple[list[str], Path]] = []

    def __call__(self, argv: list[str], cwd: Path) -> playwright.RunOutcome:
        self.calls.append((list(argv), cwd))
        expected, response = self.steps.pop(0)
        assert _logical_argv(argv) == expected
        if callable(response):
            return response()
        return response


def _npm_list(installed: bool, version: str = "1.0.0") -> playwright.RunOutcome:
    dependencies = (
        {playwright.PACKAGE_NAME: {"version": version}}
        if installed
        else {}
    )
    return playwright.RunOutcome(
        0 if installed else 1,
        json.dumps({"dependencies": dependencies}),
        "",
    )


def _prefix_outcome(prefix: Path) -> playwright.RunOutcome:
    return playwright.RunOutcome(0, f"{prefix}\n", "")


def _latest_outcome(version: str = LATEST) -> playwright.RunOutcome:
    return playwright.RunOutcome(0, json.dumps(version), "")


def _root_outcome(root: Path) -> playwright.RunOutcome:
    return playwright.RunOutcome(0, f"{root}\n", "")


def _logical_argv(argv: list[str]) -> list[str]:
    if len(argv) >= 2 and Path(argv[1]).name == "npm-cli.js":
        return ["npm", *argv[2:]]
    if len(argv) >= 2 and Path(argv[1]).name == "playwright-cli.js":
        return [playwright.CLI_COMMAND, *argv[2:]]
    return argv


def _resolver(
    home: Path,
    *,
    windows: bool = True,
    node: bool = True,
    npm: bool = True,
) -> Resolver:
    install_root = home / "node-install"
    node_path = (
        install_root / "node.exe"
        if windows
        else install_root / "bin" / "node"
    )
    npm_cli = (
        install_root / "node_modules" / "npm" / "bin" / "npm-cli.js"
        if windows
        else install_root
        / "lib"
        / "node_modules"
        / "npm"
        / "bin"
        / "npm-cli.js"
    )
    npm_path = install_root / ("npm.cmd" if windows else "bin/npm")
    if node:
        node_path.parent.mkdir(parents=True, exist_ok=True)
        node_path.write_text("node\n", encoding="utf-8")
    if npm:
        npm_cli.parent.mkdir(parents=True, exist_ok=True)
        npm_cli.write_text("npm\n", encoding="utf-8")
        npm_path.parent.mkdir(parents=True, exist_ok=True)
        npm_path.write_text("npm\n", encoding="utf-8")
    return Resolver(
        node=str(node_path) if node else None,
        npm=str(npm_path) if npm else None,
    )


def _layout(home: Path, *, windows: bool) -> tuple[Path, Path, Path, Path]:
    prefix = (
        home / "AppData" / "Roaming" / "npm"
        if windows
        else home / ".local"
    )
    root = prefix / "node_modules"
    cli_path = (
        prefix / "playwright-cli.cmd"
        if windows
        else prefix / "bin" / "playwright-cli"
    )
    bundle = root / "@playwright" / "cli" / "skills" / "playwright-cli"
    return prefix, root, cli_path, bundle


def _write_tree(root: Path, files: dict[str, bytes] | None = None) -> None:
    if root.name == "playwright-cli" and root.parent.name == "skills":
        entrypoint = root.parent.parent / "playwright-cli.js"
        entrypoint.parent.mkdir(parents=True, exist_ok=True)
        if not entrypoint.exists():
            entrypoint.write_text("playwright\n", encoding="utf-8")
    content = files or {
        "SKILL.md": b"skill\n",
        "references/usage.md": b"usage\n",
    }
    for relative, value in content.items():
        path = root / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)


def _copy_tree(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def _read_steps(
    prefix: Path,
    root: Path,
    *,
    configured_prefix: Path | None = None,
    installed: bool = True,
    version: str = LATEST,
    latest: str = LATEST,
):
    return [
        (
            playwright.PREFIX_QUERY,
            _prefix_outcome(configured_prefix or prefix),
        ),
        (playwright.latest_query(prefix), _latest_outcome(latest)),
        (playwright.package_query(prefix), _npm_list(installed, version)),
        (playwright.root_query(prefix), _root_outcome(root)),
    ]


@pytest.mark.parametrize(
    ("node", "npm", "message"),
    [
        (False, True, "required executable not found: node"),
        (True, False, "required executable not found: npm"),
    ],
)
def test_missing_prerequisite_fails_without_running_commands(
    tmp_path, node, npm, message
):
    runner = FakeRunner([])

    result = playwright.provision_playwright_cli(
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path, node=node, npm=npm),
        windows=True,
    )

    assert not result.ok
    assert result.error == message
    assert runner.calls == []


@pytest.mark.parametrize("windows", [True, False])
def test_npm_cli_entry_resolves_from_node_installation(tmp_path, windows):
    resolver = _resolver(tmp_path, windows=windows)
    node_path = playwright._resolve_executable(
        "node",
        resolver=resolver,
        base=tmp_path,
    )
    npm_path = playwright._resolve_executable(
        "npm",
        resolver=resolver,
        base=tmp_path,
    )

    assert node_path is not None
    assert npm_path is not None
    resolved = playwright._trusted_npm_cli(
        node_path,
        npm_path,
        windows=windows,
    )

    expected = (
        node_path.parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
        if windows
        else node_path.parent.parent
        / "lib"
        / "node_modules"
        / "npm"
        / "bin"
        / "npm-cli.js"
    )
    assert resolved == expected.resolve()


def test_posix_npm_symlink_resolves_to_trusted_cli(tmp_path):
    resolver = _resolver(tmp_path, windows=False)
    node_path = Path(resolver("node"))
    npm_path = Path(resolver("npm"))
    npm_cli = (
        node_path.parent.parent
        / "lib"
        / "node_modules"
        / "npm"
        / "bin"
        / "npm-cli.js"
    )
    npm_path.unlink()
    try:
        npm_path.symlink_to(npm_cli)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    resolved = playwright._trusted_npm_cli(
        node_path.resolve(),
        npm_path,
        windows=False,
    )

    assert resolved == npm_cli.resolve()


def test_posix_distribution_npm_layout_is_trusted(tmp_path):
    install_root = tmp_path / "usr"
    node = install_root / "bin" / "node"
    npm_cli = install_root / "share" / "nodejs" / "npm" / "bin" / "npm-cli.js"
    node.parent.mkdir(parents=True)
    npm_cli.parent.mkdir(parents=True)
    node.write_text("node\n", encoding="utf-8")
    npm_cli.write_text("npm\n", encoding="utf-8")

    resolved = playwright._trusted_npm_cli(
        node.resolve(),
        npm_cli,
        windows=False,
    )

    assert resolved == npm_cli.resolve()


def test_unrelated_npm_javascript_is_not_trusted(tmp_path):
    node = tmp_path / "node-install" / "node.exe"
    npm = tmp_path / "unrelated" / "npm-cli.js"
    node.parent.mkdir(parents=True)
    npm.parent.mkdir(parents=True)
    node.write_text("node\n", encoding="utf-8")
    npm.write_text("npm\n", encoding="utf-8")
    runner = FakeRunner([])

    result = playwright.provision_playwright_cli(
        home=tmp_path,
        runner=runner,
        resolver=Resolver(node=str(node), npm=str(npm)),
        windows=True,
    )

    assert not result.ok
    assert result.error == (
        "trusted npm CLI entry point not found for detected Node installation"
    )
    assert runner.calls == []


def test_symlinked_npm_package_root_cannot_escape_node_installation(tmp_path):
    install_root = tmp_path / "node-install"
    node = install_root / "node.exe"
    npm = install_root / "npm.cmd"
    package_link = install_root / "node_modules" / "npm"
    outside_package = tmp_path / "outside-npm"
    cli = outside_package / "bin" / "npm-cli.js"
    node.parent.mkdir(parents=True)
    package_link.parent.mkdir(parents=True)
    cli.parent.mkdir(parents=True)
    node.write_text("node\n", encoding="utf-8")
    npm.write_text("npm\n", encoding="utf-8")
    cli.write_text("npm\n", encoding="utf-8")
    try:
        package_link.symlink_to(outside_package, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    result = playwright.provision_playwright_cli(
        home=tmp_path,
        runner=FakeRunner([]),
        resolver=Resolver(node=str(node), npm=str(npm)),
        windows=True,
    )

    assert not result.ok
    assert result.error == (
        "trusted npm CLI entry point not found for detected Node installation"
    )


def test_configured_prefix_inside_home_is_accepted(tmp_path):
    prefix = tmp_path / "custom" / "npm"
    root = prefix / "node_modules"
    cli_path = prefix / "playwright-cli.cmd"
    bundle = root / "@playwright" / "cli" / "skills" / "playwright-cli"
    target = tmp_path / ".agents" / "skills" / "playwright-cli"
    cli_path.parent.mkdir(parents=True)
    cli_path.write_text("@echo off\n", encoding="utf-8")
    _write_tree(bundle)
    _copy_tree(bundle, target)
    runner = FakeRunner(_read_steps(prefix, root))

    result = playwright.provision_playwright_cli(
        apply=True,
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path),
        windows=True,
    )

    assert result.ok
    assert result.npm_configured_prefix == prefix.resolve()
    assert result.npm_prefix == prefix.resolve()
    assert result.npm_prefix_source == "configured"


@pytest.mark.parametrize("windows", [True, False])
def test_system_prefix_outside_home_uses_platform_user_fallback(tmp_path, windows):
    prefix, root, cli_path, bundle = _layout(tmp_path, windows=windows)
    target = tmp_path / ".agents" / "skills" / "playwright-cli"
    system_prefix = tmp_path.parent / "system-npm"
    cli_path.parent.mkdir(parents=True, exist_ok=True)
    cli_path.write_text("launcher\n", encoding="utf-8")
    _write_tree(bundle)
    _copy_tree(bundle, target)
    runner = FakeRunner(
        _read_steps(
            prefix,
            root,
            configured_prefix=system_prefix,
        )
    )

    result = playwright.provision_playwright_cli(
        apply=True,
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path, windows=windows),
        windows=windows,
    )

    assert result.ok
    assert result.npm_configured_prefix == system_prefix.resolve()
    assert result.npm_prefix == prefix.resolve()
    assert result.npm_prefix_source == "user-fallback"


def test_resolved_fallback_prefix_escape_is_rejected(tmp_path):
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    home.mkdir()
    outside.mkdir()
    fallback = home / ".local"
    try:
        fallback.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    runner = FakeRunner(
        [
            (
                playwright.PREFIX_QUERY,
                _prefix_outcome(tmp_path / "system-prefix"),
            )
        ]
    )

    result = playwright.provision_playwright_cli(
        home=home,
        runner=runner,
        resolver=_resolver(home, windows=False),
        windows=False,
    )

    assert not result.ok
    assert "selected npm prefix contains a symlink or reparse point" in result.error
    assert len(runner.calls) == 1


@pytest.mark.parametrize(
    "unsafe_relative",
    [
        ".agents",
        ".agents/skills",
        ".agents/skills/playwright-cli",
        ".playwright",
        ".playwright/cli.config.json",
    ],
)
def test_user_home_managed_paths_reject_symlink_entries(
    tmp_path,
    unsafe_relative,
):
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    home.mkdir()
    outside.mkdir()
    target = home / unsafe_relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix:
        outside_target = outside / "target"
        outside_target.write_text("outside\n", encoding="utf-8")
        target_is_directory = False
    else:
        outside_target = outside
        target_is_directory = True
    try:
        target.symlink_to(outside_target, target_is_directory=target_is_directory)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    result = playwright.provision_playwright_cli(
        home=home,
        runner=FakeRunner([]),
        resolver=_resolver(home),
        windows=True,
    )

    assert not result.ok
    assert "contains a symlink or reparse point" in result.error
    assert str(target) in result.error


def test_user_home_managed_paths_reject_windows_reparse_attribute_mock(
    tmp_path,
    monkeypatch,
):
    agents = tmp_path / ".agents"
    agents.mkdir()
    monkeypatch.setattr(
        playwright,
        "_stat_is_reparse",
        lambda _info: True,
    )

    result = playwright.provision_playwright_cli(
        home=tmp_path,
        runner=FakeRunner([]),
        resolver=_resolver(tmp_path),
        windows=True,
    )

    assert not result.ok
    assert result.error == (
        f"Agent Skills root contains a symlink or reparse point: {agents}"
    )


def test_windows_file_attribute_marks_reparse_point():
    info = SimpleNamespace(
        st_mode=stat.S_IFDIR,
        st_file_attributes=playwright._FILE_ATTRIBUTE_REPARSE_POINT,
    )

    assert playwright._stat_is_reparse(info)


def test_future_managed_target_validates_from_nearest_existing_ancestor(tmp_path):
    target = tmp_path / ".agents" / "skills" / "playwright-cli"

    validated = playwright._validate_path_chain(
        target,
        allowed_root=tmp_path,
        label="registered skill",
    )

    assert validated == target


def test_all_post_discovery_npm_commands_use_explicit_chosen_prefix(tmp_path):
    prefix, root, _, _ = _layout(tmp_path, windows=True)
    runner = FakeRunner(
        _read_steps(prefix, root, installed=False)
    )

    result = playwright.provision_playwright_cli(
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path),
        windows=True,
    )

    assert result.ok
    assert _logical_argv(runner.calls[0][0]) == ["npm", "prefix", "-g"]
    for argv, cwd in runner.calls[1:]:
        assert argv[:2] == [result.node_path, result.npm_cli_path]
        assert argv[-2:] == ["--prefix", str(prefix.resolve())]
        assert cwd == tmp_path.resolve()


def test_windows_prefix_metacharacters_remain_literal_node_arguments(tmp_path):
    home = tmp_path / "user & (qa)^"
    home.mkdir()
    prefix, root, _, _ = _layout(home, windows=True)
    runner = FakeRunner(_read_steps(prefix, root, installed=False))

    result = playwright.provision_playwright_cli(
        home=home,
        runner=runner,
        resolver=_resolver(home),
        windows=True,
    )

    assert result.ok
    for argv, _cwd in runner.calls:
        assert argv[:2] == [result.node_path, result.npm_cli_path]
        assert all("cmd.exe" not in item.casefold() for item in argv)
    assert runner.calls[-1][0][-1] == str(prefix.resolve())


def test_package_absent_dry_run_plans_install_and_skill_registration(tmp_path):
    prefix, root, _, _ = _layout(tmp_path, windows=True)
    runner = FakeRunner(_read_steps(prefix, root, installed=False))

    result = playwright.provision_playwright_cli(
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path),
        windows=True,
    )

    assert result.ok
    assert result.mode == "dry-run"
    assert result.package_installed is False
    assert result.package_latest_version == LATEST
    assert result.changes_needed
    assert not result.changed
    assert [(item.action, item.status, item.argv) for item in result.actions] == [
        (
            "install-package",
            "planned",
            playwright.package_install(prefix.resolve()),
        ),
        (
            "register-skill",
            "planned",
            SKILL_INSTALL,
        ),
    ]


def test_absent_prefix_enoent_is_package_absence_not_query_failure(tmp_path):
    prefix, root, _, _ = _layout(tmp_path, windows=True)
    query_argv = playwright.package_query(prefix.resolve())
    runner = FakeRunner(
        [
            (playwright.PREFIX_QUERY, _prefix_outcome(prefix)),
            (playwright.latest_query(prefix.resolve()), _latest_outcome()),
            (
                query_argv,
                playwright.RunOutcome(
                    2,
                    json.dumps(
                        {
                            "error": {
                                "code": "ENOENT",
                                "summary": "prefix does not exist",
                            }
                        }
                    ),
                    "prefix does not exist",
                ),
            ),
            (playwright.root_query(prefix.resolve()), _root_outcome(root)),
        ]
    )

    result = playwright.provision_playwright_cli(
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path),
        windows=True,
    )

    assert result.ok
    assert result.package_installed is False
    assert [item.argv for item in result.actions] == [
        playwright.package_install(prefix.resolve()),
        SKILL_INSTALL,
    ]


def test_stale_package_dry_run_plans_update_and_registration(tmp_path):
    prefix, root, _, _ = _layout(tmp_path, windows=True)
    runner = FakeRunner(_read_steps(prefix, root, version="1.0.0"))

    result = playwright.provision_playwright_cli(
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path),
        windows=True,
    )

    assert result.ok
    assert result.package_version == "1.0.0"
    assert result.package_latest_version == LATEST
    assert [item.argv for item in result.actions] == [
        playwright.package_install(prefix.resolve()),
        SKILL_INSTALL,
    ]


@pytest.mark.parametrize(
    ("installed", "installed_version"),
    [(False, "1.0.0"), (True, "1.0.0")],
)
def test_missing_or_stale_package_apply_updates_and_registers(
    tmp_path, installed, installed_version
):
    prefix, root, cli_path, bundle = _layout(tmp_path, windows=True)
    target = tmp_path / ".agents" / "skills" / "playwright-cli"
    config = tmp_path / ".playwright" / "cli.config.json"

    def install() -> playwright.RunOutcome:
        cli_path.parent.mkdir(parents=True, exist_ok=True)
        cli_path.write_text("@echo off\n", encoding="utf-8")
        _write_tree(bundle)
        return playwright.RunOutcome(0, "installed", "")

    def register() -> playwright.RunOutcome:
        _copy_tree(bundle, target)
        config.parent.mkdir(parents=True)
        config.write_text("{}", encoding="utf-8")
        return playwright.RunOutcome(0, "registered", "")

    runner = FakeRunner(
        [
            *_read_steps(
                prefix,
                root,
                installed=installed,
                version=installed_version,
            ),
            (playwright.package_install(prefix.resolve()), install),
            (
                playwright.package_query(prefix.resolve()),
                _npm_list(True, LATEST),
            ),
            (SKILL_INSTALL, register),
        ]
    )

    result = playwright.provision_playwright_cli(
        apply=True,
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path),
        windows=True,
    )

    assert result.ok
    assert result.package_version == LATEST
    assert result.cli_path == str(cli_path.resolve())
    assert result.skill_registered
    assert result.skill_file_count == 2
    assert result.config_present
    assert result.changed
    assert runner.calls[-1][0] == [
        result.node_path,
        result.cli_entrypoint,
        "install",
        "--skills",
        "agents",
    ]
    assert [(item.action, item.status) for item in result.actions] == [
        ("install-package", "succeeded"),
        ("register-skill", "succeeded"),
    ]


def test_latest_query_failure_is_structured(tmp_path):
    prefix, _, _, _ = _layout(tmp_path, windows=True)
    latest_argv = playwright.latest_query(prefix.resolve())
    runner = FakeRunner(
        [
            (playwright.PREFIX_QUERY, _prefix_outcome(prefix)),
            (
                latest_argv,
                playwright.RunOutcome(7, "", "registry unavailable"),
            ),
        ]
    )

    result = playwright.provision_playwright_cli(
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path),
        windows=True,
    )

    assert not result.ok
    assert result.error == f"`{' '.join(latest_argv)}` exited with 7"
    assert result.commands[-1].stderr_tail == "registry unavailable"


def test_post_install_version_mismatch_fails(tmp_path):
    prefix, root, cli_path, bundle = _layout(tmp_path, windows=True)

    def install() -> playwright.RunOutcome:
        cli_path.parent.mkdir(parents=True, exist_ok=True)
        cli_path.write_text("@echo off\n", encoding="utf-8")
        _write_tree(bundle)
        return playwright.RunOutcome(0, "installed", "")

    runner = FakeRunner(
        [
            *_read_steps(prefix, root, installed=False),
            (playwright.package_install(prefix.resolve()), install),
            (
                playwright.package_query(prefix.resolve()),
                _npm_list(True, "1.9.9"),
            ),
        ]
    )

    result = playwright.provision_playwright_cli(
        apply=True,
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path),
        windows=True,
    )

    assert not result.ok
    assert "does not match registry latest" in result.error
    assert result.actions[-1].status == "failed"


@pytest.mark.parametrize("windows", [True, False])
def test_prefix_local_cli_resolution_on_windows_and_posix(tmp_path, windows):
    prefix, root, cli_path, bundle = _layout(tmp_path, windows=windows)
    target = tmp_path / ".agents" / "skills" / "playwright-cli"
    cli_path.parent.mkdir(parents=True, exist_ok=True)
    cli_path.write_text("launcher\n", encoding="utf-8")
    _write_tree(bundle)
    _copy_tree(bundle, target)
    runner = FakeRunner(_read_steps(prefix, root))

    result = playwright.provision_playwright_cli(
        apply=True,
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path, windows=windows),
        windows=windows,
    )

    assert result.ok
    assert result.cli_path == str(cli_path.resolve())


def test_prefix_cli_symlink_outside_home_is_report_only_and_not_executed(
    tmp_path,
):
    prefix, root, cli_path, bundle = _layout(tmp_path, windows=False)
    outside = tmp_path.parent / "outside-playwright-cli"
    outside.write_text("outside\n", encoding="utf-8")
    cli_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        cli_path.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    _write_tree(bundle)
    target = tmp_path / ".agents" / "skills" / "playwright-cli"

    def register() -> playwright.RunOutcome:
        _copy_tree(bundle, target)
        return playwright.RunOutcome(0, "registered", "")

    runner = FakeRunner(
        [
            *_read_steps(prefix, root),
            (SKILL_INSTALL, register),
        ]
    )

    result = playwright.provision_playwright_cli(
        apply=True,
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path, windows=False),
        windows=False,
    )

    assert result.ok
    assert result.cli_path is None
    assert result.cli_entrypoint == str(
        (root / "@playwright" / "cli" / "playwright-cli.js").resolve()
    )
    registration_argv = runner.calls[-1][0]
    assert registration_argv[:2] == [
        result.node_path,
        result.cli_entrypoint,
    ]
    assert str(outside.resolve()) not in registration_argv


def test_missing_prefix_cli_is_report_only_when_js_entry_is_trusted(tmp_path):
    prefix, root, _, bundle = _layout(tmp_path, windows=True)
    target = tmp_path / ".agents" / "skills" / "playwright-cli"
    _write_tree(bundle)
    _copy_tree(bundle, target)
    runner = FakeRunner(_read_steps(prefix, root))

    result = playwright.provision_playwright_cli(
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path),
        windows=True,
    )

    assert result.ok
    assert result.cli_path is None


def test_playwright_entry_symlink_outside_prefix_is_rejected(tmp_path):
    prefix, root, cli_path, bundle = _layout(tmp_path, windows=False)
    cli_path.parent.mkdir(parents=True, exist_ok=True)
    cli_path.write_text("launcher\n", encoding="utf-8")
    entrypoint = root / "@playwright" / "cli" / "playwright-cli.js"
    outside = tmp_path.parent / "outside-playwright-entry.js"
    outside.write_text("outside\n", encoding="utf-8")
    entrypoint.parent.mkdir(parents=True, exist_ok=True)
    try:
        entrypoint.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    _write_tree(bundle)
    runner = FakeRunner(_read_steps(prefix, root))

    result = playwright.provision_playwright_cli(
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path, windows=False),
        windows=False,
    )

    assert not result.ok
    assert "trusted Playwright CLI JavaScript entry point" in result.error


@pytest.mark.parametrize("corrupt", [False, True])
def test_bundled_skill_missing_or_corrupt_fails(tmp_path, corrupt):
    prefix, root, cli_path, bundle = _layout(tmp_path, windows=True)
    cli_path.parent.mkdir(parents=True, exist_ok=True)
    cli_path.write_text("@echo off\n", encoding="utf-8")
    if corrupt:
        _write_tree(bundle, {"SKILL.md": b""})
    else:
        entrypoint = root / "@playwright" / "cli" / "playwright-cli.js"
        entrypoint.parent.mkdir(parents=True, exist_ok=True)
        entrypoint.write_text("playwright\n", encoding="utf-8")
    runner = FakeRunner(_read_steps(prefix, root))

    result = playwright.provision_playwright_cli(
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path),
        windows=True,
    )

    assert not result.ok
    if corrupt:
        assert "contains empty file: SKILL.md" in result.error
    else:
        assert "bundled Playwright CLI skill directory is absent" in result.error


@pytest.mark.parametrize("unsafe_tree", ["bundle", "registered"])
@pytest.mark.parametrize("entry_kind", ["file", "directory"])
def test_skill_tree_rejects_symlinked_entries(tmp_path, unsafe_tree, entry_kind):
    prefix, root, cli_path, bundle = _layout(tmp_path, windows=False)
    target = tmp_path / ".agents" / "skills" / "playwright-cli"
    cli_path.parent.mkdir(parents=True, exist_ok=True)
    cli_path.write_text("launcher\n", encoding="utf-8")
    _write_tree(bundle)
    _copy_tree(bundle, target)
    tree = bundle if unsafe_tree == "bundle" else target
    if entry_kind == "file":
        outside = tmp_path / "outside.md"
        outside.write_text("outside\n", encoding="utf-8")
        unsafe = tree / "references" / "usage.md"
        unsafe.unlink()
        target_is_directory = False
    else:
        outside = tmp_path / "outside-dir"
        outside.mkdir()
        unsafe = tree / "references" / "linked"
        target_is_directory = True
    try:
        unsafe.symlink_to(outside, target_is_directory=target_is_directory)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    runner = FakeRunner(_read_steps(prefix, root))

    result = playwright.provision_playwright_cli(
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path, windows=False),
        windows=False,
    )

    if unsafe_tree == "bundle":
        assert not result.ok
        assert "bundled Playwright CLI skill contains a symlink" in result.error
    else:
        assert not result.ok
        assert "registered Playwright CLI skill contains a symlink" in result.error


def test_skill_tree_rejects_root_outside_allowed_package(tmp_path):
    outside = tmp_path / "outside"
    _write_tree(outside)
    package_root = tmp_path / "prefix" / "node_modules" / "@playwright" / "cli"
    package_root.mkdir(parents=True)

    tree = playwright._skill_tree(
        outside,
        label="bundled Playwright CLI skill",
        allowed_root=package_root,
    )

    assert tree.error is not None
    assert "escapes allowed root" in tree.error


@pytest.mark.parametrize(
    ("target_files", "expected_detail"),
    [
        (None, "directory is absent"),
        (
            {"SKILL.md": b"old\n", "references/usage.md": b"usage\n"},
            "different files: SKILL.md",
        ),
        (
            {
                "SKILL.md": b"skill\n",
                "references/usage.md": b"usage\n",
                "extra.md": b"extra\n",
            },
            "extra files: extra.md",
        ),
        (
            {"SKILL.md": b"", "references/usage.md": b"usage\n"},
            "contains empty file: SKILL.md",
        ),
    ],
)
def test_target_skill_missing_stale_extra_or_corrupt_plans_registration(
    tmp_path, target_files, expected_detail
):
    prefix, root, cli_path, bundle = _layout(tmp_path, windows=True)
    target = tmp_path / ".agents" / "skills" / "playwright-cli"
    cli_path.parent.mkdir(parents=True, exist_ok=True)
    cli_path.write_text("@echo off\n", encoding="utf-8")
    _write_tree(bundle)
    if target_files is not None:
        _write_tree(target, target_files)
    runner = FakeRunner(_read_steps(prefix, root))

    result = playwright.provision_playwright_cli(
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path),
        windows=True,
    )

    assert result.ok
    assert not result.skill_registered
    assert [(item.action, item.status) for item in result.actions] == [
        ("register-skill", "planned")
    ]
    bundle_tree = playwright._skill_tree(
        bundle,
        label="bundled Playwright CLI skill",
        allowed_root=root / "@playwright" / "cli",
    )
    target_tree = playwright._skill_tree(
        target,
        label="registered Playwright CLI skill",
        allowed_root=tmp_path,
    )
    assert expected_detail in playwright._tree_difference(bundle_tree, target_tree)


def test_exact_target_tree_is_healthy_noop_with_read_only_queries(tmp_path):
    prefix, root, cli_path, bundle = _layout(tmp_path, windows=True)
    target = tmp_path / ".agents" / "skills" / "playwright-cli"
    cli_path.parent.mkdir(parents=True, exist_ok=True)
    cli_path.write_text("@echo off\n", encoding="utf-8")
    _write_tree(bundle)
    _copy_tree(bundle, target)
    runner = FakeRunner(_read_steps(prefix, root))

    result = playwright.provision_playwright_cli(
        apply=True,
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path),
        windows=True,
    )

    assert result.ok
    assert result.skill_registered
    assert result.skill_file_count == 2
    assert not result.changes_needed
    assert not result.changed
    assert result.actions == []
    assert [_logical_argv(call[0]) for call in runner.calls] == [
        playwright.PREFIX_QUERY,
        playwright.latest_query(prefix.resolve()),
        playwright.package_query(prefix.resolve()),
        playwright.root_query(prefix.resolve()),
    ]


def test_package_update_forces_skill_refresh_even_when_old_trees_match(tmp_path):
    prefix, root, cli_path, bundle = _layout(tmp_path, windows=True)
    target = tmp_path / ".agents" / "skills" / "playwright-cli"
    cli_path.parent.mkdir(parents=True, exist_ok=True)
    cli_path.write_text("@echo off\n", encoding="utf-8")
    _write_tree(bundle, {"SKILL.md": b"old\n"})
    _copy_tree(bundle, target)

    def install() -> playwright.RunOutcome:
        _write_tree(
            bundle,
            {
                "SKILL.md": b"new\n",
                "references/usage.md": b"new reference\n",
            },
        )
        return playwright.RunOutcome(0, "updated", "")

    def register() -> playwright.RunOutcome:
        _copy_tree(bundle, target)
        return playwright.RunOutcome(0, "registered", "")

    runner = FakeRunner(
        [
            *_read_steps(prefix, root, version="1.0.0"),
            (playwright.package_install(prefix.resolve()), install),
            (
                playwright.package_query(prefix.resolve()),
                _npm_list(True, LATEST),
            ),
            (SKILL_INSTALL, register),
        ]
    )

    result = playwright.provision_playwright_cli(
        apply=True,
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path),
        windows=True,
    )

    assert result.ok
    assert [item.action for item in result.actions] == [
        "install-package",
        "register-skill",
    ]
    assert (target / "references" / "usage.md").is_file()


def test_registration_postcondition_compares_complete_tree(tmp_path):
    prefix, root, cli_path, bundle = _layout(tmp_path, windows=True)
    target = tmp_path / ".agents" / "skills" / "playwright-cli"
    cli_path.parent.mkdir(parents=True, exist_ok=True)
    cli_path.write_text("@echo off\n", encoding="utf-8")
    _write_tree(bundle)

    def incomplete_registration() -> playwright.RunOutcome:
        _write_tree(target, {"SKILL.md": b"skill\n"})
        return playwright.RunOutcome(0, "registered", "")

    runner = FakeRunner(
        [
            *_read_steps(prefix, root),
            (
                SKILL_INSTALL,
                incomplete_registration,
            ),
        ]
    )

    result = playwright.provision_playwright_cli(
        apply=True,
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path),
        windows=True,
    )

    assert not result.ok
    assert "missing files: references/usage.md" in result.error
    assert result.actions[-1].status == "failed"


def test_registration_rejects_workspace_config_symlink_created_by_command(tmp_path):
    prefix, root, cli_path, bundle = _layout(tmp_path, windows=False)
    target = tmp_path / ".agents" / "skills" / "playwright-cli"
    config = tmp_path / ".playwright" / "cli.config.json"
    outside = tmp_path / "outside-config.json"
    cli_path.parent.mkdir(parents=True, exist_ok=True)
    cli_path.write_text("launcher\n", encoding="utf-8")
    outside.write_text("{}", encoding="utf-8")
    _write_tree(bundle)

    def unsafe_registration() -> playwright.RunOutcome:
        _copy_tree(bundle, target)
        config.parent.mkdir(parents=True)
        try:
            config.symlink_to(outside)
        except OSError as exc:
            pytest.skip(f"symlinks unavailable: {exc}")
        return playwright.RunOutcome(0, "registered", "")

    runner = FakeRunner(
        [
            *_read_steps(prefix, root),
            (SKILL_INSTALL, unsafe_registration),
        ]
    )

    result = playwright.provision_playwright_cli(
        apply=True,
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path, windows=False),
        windows=False,
    )

    assert not result.ok
    assert "Playwright workspace config contains a symlink" in result.error
    assert result.actions[-1].status == "failed"


def test_skill_registration_nonzero_preserves_evidence(tmp_path):
    prefix, root, cli_path, bundle = _layout(tmp_path, windows=True)
    cli_path.parent.mkdir(parents=True, exist_ok=True)
    cli_path.write_text("@echo off\n", encoding="utf-8")
    _write_tree(bundle)
    register_argv = SKILL_INSTALL
    runner = FakeRunner(
        [
            *_read_steps(prefix, root),
            (
                register_argv,
                playwright.RunOutcome(9, "workspace output", "permission denied"),
            ),
        ]
    )

    result = playwright.provision_playwright_cli(
        apply=True,
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path),
        windows=True,
    )

    assert not result.ok
    assert result.error == f"`{' '.join(register_argv)}` exited with 9"
    assert result.commands[-1].stdout_tail == "workspace output"
    assert result.commands[-1].stderr_tail == "permission denied"


def test_timeout_returns_structured_failure_with_bounded_evidence(tmp_path):
    runner = FakeRunner(
        [
            (
                playwright.PREFIX_QUERY,
                lambda: (_ for _ in ()).throw(
                    subprocess.TimeoutExpired(
                        playwright.PREFIX_QUERY,
                        30,
                        output="partial output",
                        stderr="partial error",
                    )
                ),
            )
        ]
    )

    result = playwright.provision_playwright_cli(
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path),
    )

    assert not result.ok
    assert result.commands[0].returncode == 124
    assert result.commands[0].stdout_tail == "partial output"
    assert "partial error" in result.commands[0].stderr_tail
    assert "command timed out after 30 seconds" in result.commands[0].stderr_tail
    assert result.to_dict()["ok"] is False


def test_command_launch_oserror_returns_structured_failure(tmp_path):
    runner = FakeRunner(
        [
            (
                playwright.PREFIX_QUERY,
                lambda: (_ for _ in ()).throw(
                    PermissionError("execution denied")
                ),
            )
        ]
    )

    result = playwright.provision_playwright_cli(
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path),
    )

    assert not result.ok
    assert result.error == "`npm prefix -g` exited with 126"
    assert result.commands[0].returncode == 126
    assert result.commands[0].stderr_tail == "execution denied"


@pytest.mark.parametrize(
    ("outcome", "message_fragment"),
    [
        (
            playwright.RunOutcome(1, "not-json", "npm warning"),
            "returned invalid JSON",
        ),
        (
            playwright.RunOutcome(
                2,
                json.dumps({"error": {"summary": "configuration failed"}}),
                "npm error",
            ),
            "failed with exit 2: configuration failed",
        ),
    ],
)
def test_package_detection_failures_are_literal(tmp_path, outcome, message_fragment):
    prefix, _, _, _ = _layout(tmp_path, windows=True)
    query_argv = playwright.package_query(prefix.resolve())
    runner = FakeRunner(
        [
            (playwright.PREFIX_QUERY, _prefix_outcome(prefix)),
            (playwright.latest_query(prefix.resolve()), _latest_outcome()),
            (query_argv, outcome),
        ]
    )

    result = playwright.provision_playwright_cli(
        home=tmp_path,
        runner=runner,
        resolver=_resolver(tmp_path),
        windows=True,
    )

    assert not result.ok
    assert message_fragment in result.error
    assert result.commands[-1].stderr_tail == outcome.stderr


@pytest.mark.parametrize(
    ("argv", "apply"),
    [
        (["provision-playwright-cli"], False),
        (["provision-playwright-cli", "--dry-run"], False),
        (["provision-playwright-cli", "--apply"], True),
    ],
)
def test_cli_modes_and_success_exit_code(monkeypatch, capsys, tmp_path, argv, apply):
    calls = []

    def provision(**kwargs):
        calls.append(kwargs)
        return playwright.ProvisionResult(
            mode="apply" if kwargs["apply"] else "dry-run",
            home=tmp_path,
            node_path="/node",
            npm_path="/npm",
            npm_cli_path="/node_modules/npm/bin/npm-cli.js",
            skill_path=tmp_path / "skill",
            config_path=tmp_path / "cli.config.json",
            package_installed=True,
            package_version=LATEST,
            package_latest_version=LATEST,
            cli_path="/playwright-cli",
            skill_registered=True,
        )

    monkeypatch.setattr(cli._playwright_cli, "provision_playwright_cli", provision)

    assert cli.main(argv) == 0
    assert calls == [{"apply": apply}]
    assert "up-to-date" in capsys.readouterr().out


def test_cli_json_failure_is_stable_and_nonzero(monkeypatch, capsys, tmp_path):
    result = playwright.ProvisionResult(
        mode="dry-run",
        home=tmp_path,
        node_path=None,
        npm_path="/npm",
        skill_path=tmp_path / "skill",
        config_path=tmp_path / "cli.config.json",
        error="required executable not found: node",
    )
    monkeypatch.setattr(
        cli._playwright_cli,
        "provision_playwright_cli",
        lambda **_kwargs: result,
    )

    rc = cli.main(["provision-playwright-cli", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 2
    assert payload["schema_version"] == 1
    assert payload["operation"] == "provision-playwright-cli"
    assert payload["ok"] is False
    assert payload["error"] == "required executable not found: node"


def test_cli_json_success_serializes_prefix_versions_actions_and_commands(
    monkeypatch, capsys, tmp_path
):
    prefix = tmp_path / ".local"
    result = playwright.ProvisionResult(
        mode="dry-run",
        home=tmp_path,
        node_path="/node",
        npm_path="/npm",
        npm_cli_path="/node_modules/npm/bin/npm-cli.js",
        skill_path=tmp_path / "skill",
        config_path=tmp_path / "cli.config.json",
        npm_configured_prefix=prefix,
        npm_prefix=prefix,
        npm_prefix_source="configured",
        npm_root=prefix / "node_modules",
        package_installed=False,
        package_latest_version=LATEST,
        changes_needed=True,
        actions=[
            playwright.ProvisionAction(
                "install-package",
                "planned",
                playwright.package_install(prefix),
            )
        ],
        commands=[
            playwright.CommandEvidence(
                playwright.PREFIX_QUERY,
                str(tmp_path),
                0,
                str(prefix),
                "",
            )
        ],
    )
    monkeypatch.setattr(
        cli._playwright_cli,
        "provision_playwright_cli",
        lambda **_kwargs: result,
    )

    rc = cli.main(["provision-playwright-cli", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["ok"] is True
    assert payload["npm"]["prefix"] == str(prefix)
    assert payload["prerequisites"]["npm"]["cli_path"].endswith("npm-cli.js")
    assert payload["package"]["latest_version"] == LATEST
    assert payload["actions"][0]["status"] == "planned"
    assert payload["commands"][0]["argv"] == playwright.PREFIX_QUERY


def test_cli_rejects_both_dry_run_and_apply():
    with pytest.raises(SystemExit):
        cli.main(["provision-playwright-cli", "--dry-run", "--apply"])


class FakeStdin:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events if events is not None else []

    def write(self, value: str) -> None:
        self.events.append(f"gate-write:{value}")

    def flush(self) -> None:
        self.events.append("gate-flush")

    def close(self) -> None:
        self.events.append("gate-close")


class FakeProcess:
    def __init__(
        self,
        communications,
        *,
        pid: int = 4312,
        events: list[str] | None = None,
    ) -> None:
        self.pid = pid
        self.returncode = None
        self._communications = list(communications)
        self.communicate_calls = 0
        self.killed = False
        self.events = events if events is not None else []
        self.stdin = FakeStdin(self.events)

    def communicate(self, timeout=None):
        self.events.append(f"communicate:{timeout}")
        self.communicate_calls += 1
        response = self._communications.pop(0)
        if isinstance(response, BaseException):
            raise response
        self.returncode = 0
        return response

    def kill(self) -> None:
        self.killed = True
        self.events.append("root-kill")


class FakeJob:
    def __init__(
        self,
        *,
        events: list[str] | None = None,
        waits: list[bool] | None = None,
    ) -> None:
        self.events = events if events is not None else []
        self.waits = list(waits or [True])

    def assign(self, pid: int) -> None:
        self.events.append(f"job-assign:{pid}")

    def terminate(self, exit_code: int) -> None:
        self.events.append(f"job-terminate:{exit_code}")

    def wait_empty(self, timeout: float) -> bool:
        self.events.append(f"job-wait:{timeout}")
        return self.waits.pop(0)

    def close(self) -> None:
        self.events.append("job-close")


def test_windows_job_launcher_is_valid_python():
    compile(playwright._WINDOWS_JOB_LAUNCHER, "<windows-job-launcher>", "exec")


@pytest.mark.parametrize("windows", [True, False])
def test_subprocess_runner_executes_literal_node_argv_without_shell(
    tmp_path, windows
):
    node = tmp_path / ("node.exe" if windows else "node")
    node.write_text("node\n", encoding="utf-8")
    prefix = tmp_path / "prefix & (literal)^"
    argv = [str(node), "npm-cli.js", "install", "--prefix", str(prefix)]
    captured = {}
    process = FakeProcess([("ok", "")])

    def popen(command, **kwargs):
        captured["argv"] = command
        captured["kwargs"] = kwargs
        return process

    job = FakeJob() if windows else None
    runner = playwright.SubprocessRunner(
        windows=windows,
        popen_factory=popen,
        job_factory=lambda: job,
    )
    outcome = runner(argv, tmp_path)

    assert outcome.returncode == 0
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    if windows:
        encoded = captured["kwargs"]["env"][playwright._WINDOWS_JOB_ARGV]
        assert json.loads(playwright.base64.urlsafe_b64decode(encoded)) == argv
        assert captured["argv"][:4] == [
            playwright.sys.executable,
            "-I",
            "-S",
            "-c",
        ]
        assert captured["kwargs"]["stdin"] is subprocess.PIPE
        assert "creationflags" in captured["kwargs"]
        assert "start_new_session" not in captured["kwargs"]
        assert job.events == [
            f"job-assign:{process.pid}",
            f"job-wait:{playwright.CLEANUP_TIMEOUT}",
            "job-close",
        ]
    else:
        assert captured["argv"] == argv
        assert "cmd.exe" not in " ".join(captured["argv"]).casefold()
        assert captured["argv"][-1] == str(prefix)
        assert captured["kwargs"]["start_new_session"] is True


def test_windows_job_is_assigned_before_launcher_release_and_closed_last(tmp_path):
    node = tmp_path / "node.exe"
    node.write_text("node\n", encoding="utf-8")
    events = []
    process = FakeProcess([("ok", "")], pid=7731, events=events)
    job = FakeJob(events=events)

    runner = playwright.SubprocessRunner(
        windows=True,
        popen_factory=lambda *_args, **_kwargs: process,
        job_factory=lambda: job,
    )
    outcome = runner([str(node), "npm-cli.js", "prefix", "-g"], tmp_path)

    assert outcome.returncode == 0
    assert events == [
        "job-assign:7731",
        "gate-write:1",
        "gate-flush",
        "gate-close",
        f"communicate:{float(playwright.DEFAULT_TIMEOUT)}",
        f"job-wait:{playwright.CLEANUP_TIMEOUT}",
        "job-close",
    ]


def test_windows_job_setup_failure_still_closes_job_after_io_error(tmp_path):
    node = tmp_path / "node.exe"
    node.write_text("node\n", encoding="utf-8")
    events = []
    process = FakeProcess(
        [OSError("cleanup pipe failed"), ("", "")],
        pid=7731,
        events=events,
    )

    class FailingAssignJob(FakeJob):
        def assign(self, pid: int) -> None:
            self.events.append(f"job-assign:{pid}")
            raise OSError("assignment failed")

    job = FailingAssignJob(events=events)
    runner = playwright.SubprocessRunner(
        windows=True,
        cleanup_timeout=5,
        popen_factory=lambda *_args, **_kwargs: process,
        job_factory=lambda: job,
    )
    runner._default_tree_cleanup = lambda _proc, _force: ""

    outcome = runner([str(node), "npm-cli.js", "prefix", "-g"], tmp_path)

    assert outcome.returncode == 126
    assert "cannot establish Windows Job Object containment" in outcome.stderr
    assert "process cleanup wait failed: cleanup pipe failed" in outcome.stderr
    assert events.index("job-terminate:126") < events.index("communicate:5")
    assert events[-2:] == ["job-wait:5", "job-close"]


def test_windows_root_exit_with_descendant_is_contained_before_close(tmp_path):
    node = tmp_path / "node.exe"
    node.write_text("node\n", encoding="utf-8")
    events = []
    process = FakeProcess([("ok", "")], pid=7731, events=events)
    job = FakeJob(events=events, waits=[False, True])

    runner = playwright.SubprocessRunner(
        windows=True,
        popen_factory=lambda *_args, **_kwargs: process,
        job_factory=lambda: job,
    )
    outcome = runner([str(node), "npm-cli.js", "prefix", "-g"], tmp_path)

    assert outcome.returncode == 0
    assert events[-4:] == [
        f"job-wait:{playwright.CLEANUP_TIMEOUT}",
        "job-terminate:0",
        f"job-wait:{playwright.CLEANUP_TIMEOUT}",
        "job-close",
    ]
    assert events.index("job-terminate:0") > events.index(
        f"communicate:{float(playwright.DEFAULT_TIMEOUT)}"
    )


def test_windows_unverified_job_cleanup_is_not_command_success(tmp_path):
    node = tmp_path / "node.exe"
    node.write_text("node\n", encoding="utf-8")
    job = FakeJob(waits=[False, False])
    process = FakeProcess([("ok", "")], pid=7731)
    runner = playwright.SubprocessRunner(
        windows=True,
        popen_factory=lambda *_args, **_kwargs: process,
        job_factory=lambda: job,
    )

    outcome = runner([str(node), "npm-cli.js", "prefix", "-g"], tmp_path)

    assert outcome.returncode == 126
    assert "retained descendants" in outcome.stderr
    assert job.events[-1] == "job-close"


def test_windows_timeout_terminates_job_before_returning_124(tmp_path):
    node = tmp_path / "node.exe"
    node.write_text("node\n", encoding="utf-8")
    partial_stdout = ("x" * playwright.OUTPUT_LIMIT) + "partial output"
    partial_stderr = ("y" * playwright.OUTPUT_LIMIT) + "partial error"
    events = []
    process = FakeProcess(
        [
            subprocess.TimeoutExpired(
                ["node"],
                30,
                output=partial_stdout,
                stderr=partial_stderr,
            ),
            (partial_stdout, partial_stderr),
        ],
        pid=7731,
        events=events,
    )
    job = FakeJob(events=events)

    runner = playwright.SubprocessRunner(
        windows=True,
        timeout=30,
        cleanup_timeout=5,
        popen_factory=lambda *_args, **_kwargs: process,
        job_factory=lambda: job,
    )
    outcome = runner([str(node), "npm-cli.js", "prefix", "-g"], tmp_path)

    assert events.index("job-terminate:124") < events.index("communicate:5")
    assert events[-2:] == ["job-wait:5", "job-close"]
    assert process.communicate_calls == 2
    assert outcome.returncode == 124
    assert len(outcome.stdout) == playwright.OUTPUT_LIMIT
    assert outcome.stdout.endswith("partial output")
    assert len(outcome.stderr) == playwright.OUTPUT_LIMIT
    assert "partial error" in outcome.stderr
    assert "command timed out after 30 seconds" in outcome.stderr


def test_windows_timeout_attempts_bounded_fallback_before_124(tmp_path):
    node = tmp_path / "node.exe"
    node.write_text("node\n", encoding="utf-8")
    events = []
    process = FakeProcess(
        [
            subprocess.TimeoutExpired(["node"], 30),
            subprocess.TimeoutExpired(["node"], 5),
            subprocess.TimeoutExpired(["node"], 5),
            subprocess.TimeoutExpired(["node"], 5),
        ],
        pid=7731,
        events=events,
    )
    job = FakeJob(events=events)
    runner = playwright.SubprocessRunner(
        windows=True,
        timeout=30,
        cleanup_timeout=5,
        popen_factory=lambda *_args, **_kwargs: process,
        job_factory=lambda: job,
    )

    def fallback(proc, force):
        events.append(f"taskkill:{proc.pid}:{force}")
        return "bounded fallback failed"

    runner._default_tree_cleanup = fallback
    outcome = runner([str(node), "npm-cli.js", "prefix", "-g"], tmp_path)

    assert outcome.returncode == 124
    assert events.index("job-terminate:124") < events.index("taskkill:7731:True")
    assert events.index("taskkill:7731:True") < events.index("root-kill")
    assert events.index("taskkill:7731:True") < events.index("job-close")
    assert "bounded fallback failed" in outcome.stderr


def test_windows_timeout_cleanup_io_error_still_closes_job(tmp_path):
    node = tmp_path / "node.exe"
    node.write_text("node\n", encoding="utf-8")
    events = []
    process = FakeProcess(
        [
            subprocess.TimeoutExpired(["node"], 30),
            OSError("cleanup pipe failed"),
            ("", ""),
        ],
        pid=7731,
        events=events,
    )
    job = FakeJob(events=events)
    runner = playwright.SubprocessRunner(
        windows=True,
        timeout=30,
        cleanup_timeout=5,
        popen_factory=lambda *_args, **_kwargs: process,
        job_factory=lambda: job,
    )

    outcome = runner([str(node), "npm-cli.js", "prefix", "-g"], tmp_path)

    assert outcome.returncode == 124
    assert "process cleanup wait failed: cleanup pipe failed" in outcome.stderr
    assert events[-2:] == ["job-wait:5", "job-close"]


def test_posix_timeout_cleans_process_group_before_returning_124(tmp_path):
    node = tmp_path / "node"
    node.write_text("node\n", encoding="utf-8")
    process = FakeProcess(
        [
            subprocess.TimeoutExpired(["node"], 30),
            subprocess.TimeoutExpired(["node"], 5),
            ("", ""),
        ],
        pid=7731,
    )
    cleanup_calls = []

    runner = playwright.SubprocessRunner(
        windows=False,
        timeout=30,
        cleanup_timeout=5,
        popen_factory=lambda *_args, **_kwargs: process,
        tree_cleanup=lambda proc, force: (
            cleanup_calls.append((proc.pid, force)) or ""
        ),
    )
    outcome = runner([str(node), "npm-cli.js", "prefix", "-g"], tmp_path)

    assert cleanup_calls == [(7731, False), (7731, True)]
    assert outcome.returncode == 124


@pytest.mark.parametrize("windows", [True, False])
def test_unexpected_io_error_cleans_process_tree(tmp_path, windows):
    node = tmp_path / ("node.exe" if windows else "node")
    node.write_text("node\n", encoding="utf-8")
    events = []
    process = FakeProcess(
        [OSError("pipe failed"), ("", "")],
        pid=7731,
        events=events,
    )
    job = FakeJob(events=events) if windows else None
    cleanup_calls = []
    runner = playwright.SubprocessRunner(
        windows=windows,
        cleanup_timeout=5,
        popen_factory=lambda *_args, **_kwargs: process,
        job_factory=lambda: job,
        tree_cleanup=lambda proc, force: (
            cleanup_calls.append((proc.pid, force)) or ""
        ),
        group_alive=(lambda _pid: False),
    )

    outcome = runner([str(node), "npm-cli.js", "prefix", "-g"], tmp_path)

    assert outcome.returncode == 126
    assert "command I/O failed: pipe failed" in outcome.stderr
    if windows:
        assert "job-terminate:126" in events
        assert events[-2:] == ["job-wait:5", "job-close"]
        assert cleanup_calls == []
    else:
        assert cleanup_calls == [(7731, False)]


def test_posix_normal_exit_cleans_and_verifies_process_group(tmp_path):
    node = tmp_path / "node"
    node.write_text("node\n", encoding="utf-8")
    process = FakeProcess([("ok", "")], pid=7731)
    cleanup_calls = []
    alive = iter([True, True, False])
    runner = playwright.SubprocessRunner(
        windows=False,
        cleanup_timeout=5,
        popen_factory=lambda *_args, **_kwargs: process,
        tree_cleanup=lambda proc, force: (
            cleanup_calls.append((proc.pid, force)) or ""
        ),
        group_alive=lambda _pid: next(alive),
        clock=lambda: 0,
        sleeper=lambda _seconds: None,
    )

    outcome = runner([str(node), "npm-cli.js", "prefix", "-g"], tmp_path)

    assert outcome.returncode == 0
    assert cleanup_calls == [(7731, False)]


def test_posix_io_error_escalates_and_verifies_process_group(tmp_path):
    node = tmp_path / "node"
    node.write_text("node\n", encoding="utf-8")
    process = FakeProcess(
        [OSError("pipe failed"), ("", "")],
        pid=7731,
    )
    cleanup_calls = []
    alive_values = iter([True, False])
    clock_values = iter([0, 1, 1])
    runner = playwright.SubprocessRunner(
        windows=False,
        cleanup_timeout=0.001,
        popen_factory=lambda *_args, **_kwargs: process,
        tree_cleanup=lambda proc, force: (
            cleanup_calls.append((proc.pid, force)) or ""
        ),
        group_alive=lambda _pid: next(alive_values),
        clock=lambda: next(clock_values),
        sleeper=lambda _seconds: None,
    )

    outcome = runner([str(node), "npm-cli.js", "prefix", "-g"], tmp_path)

    assert outcome.returncode == 126
    assert cleanup_calls == [(7731, False), (7731, True)]


def test_io_error_force_cleanup_performs_final_root_reap(tmp_path):
    node = tmp_path / "node"
    node.write_text("node\n", encoding="utf-8")
    process = FakeProcess(
        [
            OSError("pipe failed"),
            subprocess.TimeoutExpired(["node"], 5),
            ("", ""),
        ],
        pid=7731,
    )
    cleanup_calls = []
    runner = playwright.SubprocessRunner(
        windows=False,
        cleanup_timeout=5,
        popen_factory=lambda *_args, **_kwargs: process,
        tree_cleanup=lambda proc, force: (
            cleanup_calls.append((proc.pid, force)) or ""
        ),
        group_alive=lambda _pid: False,
    )

    outcome = runner([str(node), "npm-cli.js", "prefix", "-g"], tmp_path)

    assert outcome.returncode == 126
    assert process.communicate_calls == 3
    assert cleanup_calls == [(7731, False), (7731, True)]


def test_windows_taskkill_fallback_is_exact_pid_and_bounded(monkeypatch):
    captured = {}

    def run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(playwright.subprocess, "run", run)
    runner = playwright.SubprocessRunner(
        windows=True,
        resolver=Resolver(taskkill="taskkill"),
    )
    process = FakeProcess([], pid=9124)

    assert runner._default_tree_cleanup(process, False) == ""
    assert captured["argv"] == ["taskkill", "/PID", "9124", "/T", "/F"]
    assert captured["kwargs"]["check"] is False
    assert captured["kwargs"]["timeout"] == playwright.CLEANUP_TIMEOUT


def test_provision_timeout_hierarchy_preserves_outer_module_cleanup_margin():
    assert playwright.DEFAULT_TIMEOUT + playwright.CLEANUP_MARGIN < modules.DEFAULT_TIMEOUT
    assert (
        playwright.PROVISION_TIMEOUT + playwright.CLEANUP_TIMEOUT
        < modules.DEFAULT_TIMEOUT
    )


def test_shared_provision_deadline_limits_each_command_timeout(tmp_path):
    node = tmp_path / "node"
    node.write_text("node\n", encoding="utf-8")
    process = FakeProcess([("ok", "")])
    runner = playwright.SubprocessRunner(
        windows=False,
        timeout=playwright.DEFAULT_TIMEOUT,
        deadline=1000,
        clock=lambda: 500,
        popen_factory=lambda *_args, **_kwargs: process,
    )

    outcome = runner([str(node), "npm-cli.js", "prefix", "-g"], tmp_path)

    assert outcome.returncode == 0
    assert process.events[0] == "communicate:440.0"


def test_exhausted_provision_deadline_does_not_launch_process(tmp_path):
    node = tmp_path / "node"
    node.write_text("node\n", encoding="utf-8")
    launched = []
    runner = playwright.SubprocessRunner(
        windows=False,
        deadline=100,
        clock=lambda: 100,
        popen_factory=lambda *_args, **_kwargs: launched.append(True),
    )

    outcome = runner([str(node), "npm-cli.js", "prefix", "-g"], tmp_path)

    assert outcome.returncode == 124
    assert "deadline exhausted" in outcome.stderr
    assert launched == []


def test_posix_tree_cleanup_signals_process_group(monkeypatch):
    signals = []
    monkeypatch.setattr(playwright.signal, "SIGTERM", 15, raising=False)
    monkeypatch.setattr(playwright.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(
        playwright.os,
        "killpg",
        lambda group, sig: signals.append((group, sig)),
        raising=False,
    )
    runner = playwright.SubprocessRunner(windows=False)
    process = FakeProcess([], pid=1824)

    assert runner._default_tree_cleanup(process, False) == ""
    assert runner._default_tree_cleanup(process, True) == ""
    assert signals == [
        (1824, 15),
        (1824, 9),
    ]


def test_skill_tree_respects_deadline(tmp_path):
    skill = tmp_path / "skill"
    _write_tree(skill)

    tree = playwright._skill_tree(
        skill,
        label="skill",
        allowed_root=tmp_path,
        deadline=10,
        clock=lambda: 10,
    )

    assert tree.error == "skill scan exceeded provision deadline"
    assert tree.unsafe is True


def test_skill_tree_rejects_total_byte_budget(tmp_path, monkeypatch):
    skill = tmp_path / "skill"
    _write_tree(skill, {"SKILL.md": b"1234"})
    monkeypatch.setattr(playwright, "MAX_SKILL_BYTES", 3)

    tree = playwright._skill_tree(
        skill,
        label="skill",
        allowed_root=tmp_path,
    )

    assert tree.unsafe is True
    assert tree.error == "skill exceeds 3 total bytes"


def _invocation_package(tmp_path: Path):
    data = {
        "schema_version": 4,
        "package": "example/playwright",
        "gate": ["*"],
        "manage": {},
        "modules": [
            {
                "name": "playwright-cli",
                "invocation": {
                    "plugin": "agent-machines@copilot-extensions",
                    "command": "agent-machines",
                    "platforms": ["windows", "linux", "wsl"],
                    "arguments": ["provision-playwright-cli"],
                    "dry_run_arguments": ["--dry-run"],
                    "apply_arguments": ["--apply"],
                },
            }
        ],
    }
    return load_package(
        write_package(tmp_path / "example", "playwright.yaml", data),
        source_repo="example",
    )


@pytest.mark.parametrize("platform", ["windows", "linux", "wsl"])
@pytest.mark.parametrize(
    ("dry_run", "mode_argument"),
    [(True, "--dry-run"), (False, "--apply")],
)
def test_requirement_invocation_uses_existing_payload_command(
    tmp_path, monkeypatch, platform, dry_run, mode_argument
):
    package = _invocation_package(tmp_path)
    monkeypatch.setattr(
        modules,
        "_active_plugins",
        lambda: {
            "agent-machines@copilot-extensions": SimpleNamespace(root=PLUGIN_ROOT)
        },
    )
    monkeypatch.setattr(modules.shutil, "which", lambda _name: "pwsh")
    captured = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(modules.subprocess, "run", run)

    result = modules.run_module(
        package,
        package.modules[0],
        platform,
        dry_run=dry_run,
    )

    assert result.ok
    assert result.command[-2:] == ["provision-playwright-cli", mode_argument]
    assert captured["command"] == result.command
    assert captured["kwargs"]["cwd"] == str(package.repo_root())
    assert captured["kwargs"]["timeout"] == modules.DEFAULT_TIMEOUT
