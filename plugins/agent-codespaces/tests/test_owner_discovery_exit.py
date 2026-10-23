"""Bounded credential-owner lookup must not retain executor exit hooks."""

from __future__ import annotations

import subprocess
import sys

from agent_procutil import no_window_flags


def test_bounded_owner_lookup_does_not_delay_interpreter_exit():
    code = """
from threading import Event
from types import SimpleNamespace
from agent_codespaces import account_binding, config, gh_account, lifecycle, picker_auth

account_binding.bound_accounts_or_raise = lambda: ()
gh_account.account_inventory = lambda: gh_account.AccountInventory(("example",))
gh_account.active_account = lambda **_: "example"
picker_auth.authorized_configs = lambda: {}
config.load_merged_config = lambda **_: SimpleNamespace(repos={})
lifecycle.validate_context = lambda: None

started = Event()
def listing(account):
    started.set()
    Event().wait(20)
    return []
lifecycle._list_codespaces_under = listing

assert gh_account._discover_owner("missing", 0.1) is None
assert started.is_set()
print("unknown owner; bounded caller complete", flush=True)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=5,
        creationflags=no_window_flags(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "unknown owner; bounded caller complete"
