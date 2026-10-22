"""One account-candidate set for authentication and CodeSpace inventory."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed


def candidates(
    *, on_account: Callable[[str | None], None] | None = None,
) -> tuple[str | None, ...]:
    """Resolve independent sources together, optionally starting account reads early.

    Notifications follow discovery order; the returned tuple retains the existing
    source precedence regardless of which metadata query finishes first.
    """
    from . import account_binding, gh_account, picker_auth
    from .config import load_merged_config

    providers = picker_auth.authorized_configs()
    bound = account_binding.bound_accounts_or_raise()
    groups: dict[int, tuple[str, ...]] = {}
    notified: set[str] = set()

    def record(rank: int, accounts: Iterable[str]) -> None:
        groups[rank] = tuple(account for account in accounts if account)
        for account in groups[rank]:
            key = account.casefold()
            if on_account is not None and key not in notified:
                notified.add(key)
                on_account(account)

    def configured_accounts(inventory: Future[gh_account.AccountInventory]) -> tuple[str, ...]:
        repos = load_merged_config(include_cwd=False).repos
        routing = inventory.result()
        return tuple(
            login for repo in repos if (login := routing.account_for(repo))
        )

    def active_accounts() -> tuple[str, ...]:
        current = gh_account.active_account()
        return (current,) if current else ()

    with ThreadPoolExecutor(max_workers=3) as executor:
        mapped = executor.submit(gh_account.account_inventory)
        configured = executor.submit(configured_accounts, mapped)
        active = executor.submit(active_accounts)
        record(1, bound)
        record(2, providers)
        for future in as_completed((mapped, configured, active)):
            if future is mapped:
                record(0, mapped.result().accounts)
            elif future is configured:
                record(3, configured.result())
            else:
                record(4, active.result())

    selected: list[str | None] = []
    seen = set()
    for rank in sorted(groups):
        for account in groups[rank]:
            if account.casefold() in seen:
                continue
            seen.add(account.casefold())
            selected.append(account)
    if not groups[4] and (not selected or not providers):
        selected.append(None)
        if on_account is not None:
            on_account(None)
    return tuple(selected)
