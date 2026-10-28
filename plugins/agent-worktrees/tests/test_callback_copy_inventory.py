"""Callback requests cannot expand the Python owner's declared copy scope."""
from __future__ import annotations

from pathlib import Path

import pytest

from test_source_python_publication import (
    native_hosts,
    publication_case as shared_publication_case,
    publication_transform as default_publication_transform,
)
from test_source_install_transactions import _function, _ps, _run, _sh

publication_case = shared_publication_case
publication_transform = default_publication_transform


@pytest.mark.parametrize("publication_case", ["legacy"], indirect=True)
@pytest.mark.parametrize(("shell", "executable"), native_hosts())
def test_native_wrapper_inventory_matches_declared_scope(publication_case, shell, executable):
    driver, root, _slot, _owner, content, _identity = publication_case
    copies = root / "wrapper-copies"
    if shell == "ps1":
        script = f"""
$ErrorActionPreference = 'Stop'
$PluginDir = {_ps(content)}
$ScriptDir = Join-Path $PluginDir 'scripts'
$InstallDir = {_ps(root)}
$BinDir = Join-Path $InstallDir 'bin'
$Action = 'publish-runtime-helpers'
$script:RuntimePublicationCopies = {_ps(copies)}
function Ensure-InstallDir {{ param($Path) }}
function Assert-SourceRuntime {{ }}
function Write-ServiceOk {{ param($Message) }}
{_function('Publish-RuntimeFile', shell)}
{_function('Deploy-RuntimeResolvers', shell)}
{_function('Deploy-Wrappers', shell)}
if (-not (Deploy-Wrappers)) {{ throw 'Wrapper inventory failed' }}
"""
        argv = [executable, "-NoProfile", "-NonInteractive", "-File"]
    else:
        script = f"""
set -eu
PLUGIN_DIR={_sh(content)}
SCRIPT_DIR="$PLUGIN_DIR/scripts"
INSTALL_DIR={_sh(root)}
BIN_DIR="$INSTALL_DIR/bin"
ACTION=publish-runtime-helpers
RUNTIME_PUBLICATION_COPIES={_sh(copies)}
_assert_source_runtime() {{ :; }}
ok() {{ :; }}
{_function('_publish_runtime_file', shell)}
{_function('deploy_runtime_resolvers', shell)}
{_function('deploy_wrappers', shell)}
deploy_wrappers
"""
        argv = [executable]
    entry = root / f"wrapper-inventory.{shell}"
    entry.write_text(script, encoding="utf-8")
    result = _run([*argv, str(entry)], root)
    assert result.returncode == 0, result.stdout + result.stderr
    helper = driver.helper_publication
    bindings = helper.declared_copies(content, root, "wrappers", shell, True)
    parsed = helper.parse_copies({"copies": str(copies), "bindings": bindings})
    expected = content / "src" / "agent_worktrees" / "native_source_context.py"
    assert any(item.source == expected for item in parsed)
    assert any(item.source == content / "scripts" / "native-seed-reply.sh" for item in parsed)
    assert any(item.source == content / "scripts" / "native-seed-boundary.sh" for item in parsed)
    assert not any(item.source.name.startswith(("pane-wrapper.", "pane-launch.")) for item in parsed)
    assert all(not Path(item["target"]).exists() for item in bindings)


@pytest.mark.parametrize("publication_case", ["legacy"], indirect=True)
@pytest.mark.parametrize("fault", ["target", "source", "duplicate", "alias", "relative", "policy", "incomplete"])
def test_invalid_copy_inventory_refuses_before_target_writes(publication_case, fault):
    driver, root, _slot, _owner, content, _identity = publication_case
    helper = driver.helper_publication
    bindings = helper.declared_copies(content, root, "resolvers", "sh", False)
    rows = [[item["source"], item["target"], str(int(item["executable"])),
             str(int(item["normalized_noop"]))] for item in bindings]
    if fault == "target":
        rows[0][1] = str(root.parent / "unapproved")
    elif fault == "source":
        rows[0][0] = str(root.parent / "unapproved-source")
    elif fault == "duplicate":
        rows.append(rows[0])
    elif fault == "alias":
        rows[0][1] = str(Path(rows[0][1]).parent / ".." / "bin" / Path(rows[0][1]).name)
    elif fault == "relative":
        rows[0][1] = "relative"
    elif fault == "policy":
        rows[0][2] = "unexpected"
    copies = root / "copy-inventory"
    copies.write_bytes(("\0".join(field for row in rows for field in row) + "\0").encode("utf-8"))
    copies.chmod(0o600)
    if fault == "incomplete":
        copies.write_bytes(copies.read_bytes()[:-1])
    with pytest.raises(ValueError, match="Callback copy"):
        helper.parse_copies({"copies": str(copies), "bindings": bindings})
    assert all(not Path(item["target"]).exists() for item in bindings)


@pytest.mark.parametrize("publication_case", ["legacy"], indirect=True)
def test_copy_inventory_mutation_refuses_before_first_target_write(publication_case):
    driver, root, _slot, _owner, content, _identity = publication_case
    helper = driver.helper_publication
    bindings = helper.declared_copies(content, root, "resolvers", "sh", False)
    copies = root / "copy-inventory"
    copies.write_bytes(("\0".join(
        field for item in bindings for field in
        (item["source"], item["target"], str(int(item["executable"])), str(int(item["normalized_noop"])))
    ) + "\0").encode("utf-8"))
    copies.chmod(0o600)
    directory = root / "publication"
    directory.mkdir()

    def mutate() -> None:
        copies.write_bytes(copies.read_bytes() + b"changed")

    with pytest.raises(ValueError, match="inventory changed"):
        helper.publish({"root": str(root), "copies": str(copies), "bindings": bindings},
                       directory, {}, validate_inputs=mutate)
    assert all(not Path(item["target"]).exists() for item in bindings)
