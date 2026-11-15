"""Required resolver, hook, conduct and setup copies share exact-file publication."""
from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_global_binstub_publication import inject_copy_fault
from test_source_python_publication import (
    CALLBACK_TIMEOUT, admitted, native_hosts, publication_case as shared_publication_case,
)
from test_source_install_transactions import _function

publication_case = shared_publication_case
TARGETS = (
    ("resolve-runtime.sh", "scripts/resolve-runtime.sh", "bin/resolve-runtime.sh"),
    ("session-conduct.sh", "scripts/session-conduct.sh", "bin/session-conduct.sh"),
    ("default-setup.sh", "scripts/default-setup.sh", "scripts/default-setup.sh"),
    ("native-seed-process.cs", "scripts/native-seed-process.cs", "scripts/native-seed-process.cs"),
    ("account-conduct.md", "scripts/conduct/account-conduct.md", "bin/conduct/account-conduct.md"),
)


@pytest.fixture
def publication_transform(fault, shell, monkeypatch, asset):
    name, _source, _destination = asset
    def transform(content):
        module = ast.parse((content / "scripts/runtime_install_publication.py").read_text(encoding="utf-8"))
        inventories = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in module.body
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"HOOKS", "SETUP"}
        }
        for hook in inventories["HOOKS"]:
            if hook not in (name, "versioned_runtime_native.ps1") and not hook.endswith(".py"):
                source = content / ("src/agent_worktrees" if hook == "native_source_context.py" else "scripts") / hook
                source.unlink(missing_ok=True)
        for source in (content / "scripts").glob("*"):
            if source.name in inventories["SETUP"] and source.name != name:
                source.unlink()
        for source in (content / "scripts/conduct").glob("*.md"):
            if source.name != name:
                source.unlink()
    return transform


@pytest.mark.parametrize("publication_case", ["legacy"], indirect=True)
@pytest.mark.parametrize(("shell", "executable"), native_hosts())
@pytest.mark.parametrize("asset", TARGETS, ids=["resolver", "hook", "setup", "native-creation", "conduct"])
@pytest.mark.parametrize("fault", ["healthy", "initial-directory", "directory-race", "partial-write"])
@pytest.mark.timeout(90)
def test_required_callback_file_publication(publication_case, asset, shell, executable, fault, monkeypatch):
    driver, root, slot, owner, content, _identity = publication_case
    name, source_path, relative = asset
    inject_copy_fault(driver, fault, name, monkeypatch)
    scope = "resolvers" if name.startswith("resolve-") else "wrappers"
    with admitted(driver, root, owner, content, monkeypatch) as (governance, _lease):
        bindings = driver.helper_publication.declared_copies(content, root, scope, shell, False)
        helper_before = {}
        for binding in bindings:
            path = Path(binding["target"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"previous\n")
            helper_before[path] = path.read_bytes()
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"previous\n")
        if fault == "initial-directory":
            target.unlink()
            target.mkdir()
            (target / "sentinel").write_text("peer", encoding="utf-8")
        for marker in ("current-version", "last-known-good"):
            (root / marker).write_text("previous\n", encoding="utf-8")
        before = {
            path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
            for path in (root / "current-version", root / "last-known-good", root / "deploy-manifest.json")
        }
        with driver.runtime.retention_custody(root, slot.name, payload_hash="candidate") as (_slot, descriptor):
            if fault == "healthy":
                transaction = driver._publish_helpers(
                    governance, {"shell": shell, "executable": executable, "scope": scope},
                    content, slot, descriptor, timeout=CALLBACK_TIMEOUT, base_python=Path(sys._base_executable),
                )
                assert target.read_bytes() == (content / source_path).read_bytes()
                if os.name != "nt" and not name.endswith(".md"):
                    assert target.stat().st_mode & 0o100
                assert not list(root.rglob("*.publication.*"))
                transaction.keep()
            else:
                with pytest.raises((OSError, ValueError, subprocess.CalledProcessError)) as caught:
                    driver._publish_helpers(
                        governance, {"shell": shell, "executable": executable, "scope": scope},
                        content, slot, descriptor, timeout=CALLBACK_TIMEOUT, base_python=Path(sys._base_executable),
                    )
                diagnostic = str(caught.value) + str(getattr(caught.value, "stderr", ""))
                assert name in diagnostic.casefold(), diagnostic
                if fault == "partial-write":
                    assert target.read_bytes() == b"previous\n"
                    assert any(path.read_bytes() == b"partial" for path in target.parent.rglob("*") if path.is_file())
                else:
                    assert target.is_dir() and [path.name for path in target.iterdir()] == ["sentinel"]
                    assert (target / "sentinel").read_text() == "peer"
                assert {path: path.read_bytes() for path in helper_before if path != target} == {
                    path: value for path, value in helper_before.items() if path != target
                }
        assert {path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns) for path in before} == before


@pytest.mark.parametrize(("extension", "publisher", "callers"), [
    ("sh", "_publish_runtime_file", ("deploy_runtime_resolvers", "deploy_wrappers", "deploy_tool_binstub")),
    ("ps1", "Publish-RuntimeFile", ("Deploy-RuntimeResolvers", "Deploy-Wrappers", "Deploy-GlobalBinstub")),
])
def test_required_callback_callers_use_one_publisher(extension, publisher, callers):
    for name in callers:
        body = _function(name, extension)
        assert publisher in body
        assert "Copy-Item" not in body and "mv -f" not in body
