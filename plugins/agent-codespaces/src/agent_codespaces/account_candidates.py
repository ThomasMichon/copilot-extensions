"""One account-candidate set for authentication and CodeSpace inventory."""
from __future__ import annotations


def candidates() -> tuple[str | None, ...]:
    from . import account_binding, gh_account, picker_auth
    from .config import load_merged_config

    providers = picker_auth.authorized_configs()
    accounts = [
        *gh_account.mapped_accounts(), *account_binding.bound_accounts_or_raise(),
        *providers,
    ]
    for repo in load_merged_config(include_cwd=False).repos:
        if login := gh_account.account_for_repo(repo):
            accounts.append(login)
    selected: list[str | None] = []
    seen = set()
    for account in accounts:
        if account and account.casefold() not in seen:
            seen.add(account.casefold())
            selected.append(account)
    active = gh_account.active_account()
    if active:
        if active.casefold() not in seen:
            selected.append(active)
    elif not selected or not providers:
        selected.append(None)
    return tuple(selected)
