"""Required resolver, hook, conduct and setup copies share exact-file publication."""
from __future__ import annotations

import os
from pathlib import Path
import re
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
    ("account-conduct.md", "scripts/conduct/account-conduct.md", "bin/conduct/account-conduct.md"),
)


@pytest.fixture
def publication_transform(fault, shell, monkeypatch, asset):
    name, _source, _destination = asset
    def transform(content):
        text = (content / "scripts/install.sh").read_text(encoding="utf-8")
        hooks = re.search(r"    for script in (session-conduct.*?); do", text)
        assert hooks
        for hook in hooks[1].split():
            if hook not in (name, "registry_root.py"):
                source = content / ("src/agent_worktrees" if hook == "native_source_context.py" else "scripts") / hook
                source.unlink(missing_ok=True)
        for source in (content / "scripts").glob("*"):
            if source.name in {
                "default-setup.ps1", "default-setup.sh", "launch-command.ps1", "launch-command.sh",
                "native-seed-boundary.ps1",
                "reconcile-machine-settings.ps1", "reconcile-machine-settings.sh",
            } and source.name != name:
                source.unlink()
        for source in (content / "scripts/conduct").glob("*.md"):
            if source.name != name:
                source.unlink()
    return transform


@pytest.mark.parametrize("publication_case", ["legacy"], indirect=True)
@pytest.mark.parametrize(("shell", "executable"), native_hosts())
@pytest.mark.parametrize("asset", TARGETS, ids=["resolver", "hook", "setup", "conduct"])
@pytest.mark.parametrize("fault", ["healthy", "initial-directory", "directory-race", "partial-write"])
@pytest.mark.timeout(90)
def test_required_callback_file_publication(publication_case, asset, shell, executable, fault, monkeypatch):
    driver, root, slot, owner, content, _identity = publication_case
    name, source_path, relative = asset
    inject_copy_fault(driver, fault, name, monkeypatch)
    scope = "resolvers" if name.startswith("resolve-") else "wrappers"
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
    with admitted(driver, root, owner, content, monkeypatch) as (governance, _lease):
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
