"""Native source preparation must not acquire optional Fleet imports."""

import importlib.abc
import sys

from agent_pull_requests.workspace_operations import NativeWorkspaceClient
from agent_pull_requests.workspace_tools import WorkspaceTools


def test_native_and_unsupported_preparation_do_not_import_optional_fleet(monkeypatch):
    class NoFleet(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "fleet_manager" or fullname.startswith("fleet_manager."):
                raise ModuleNotFoundError("optional Fleet deliberately unavailable")
    monkeypatch.delitem(sys.modules, "agent_pull_requests.workspace_client", raising=False)
    for name in tuple(sys.modules):
        if name == "fleet_manager" or name.startswith("fleet_manager."):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "meta_path", [NoFleet(), *sys.meta_path])
    native = NativeWorkspaceClient(None, checkpoint=lambda: None, expires_at=lambda: 0)
    assert WorkspaceTools(native)._preparation_mode() == "worker"
    class Unknown:
        def exchange(self, *args):
            raise AssertionError("discovery performed IO")
        materialize = exchange
    assert WorkspaceTools(Unknown())._preparation_mode() is None
