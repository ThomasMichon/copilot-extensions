"""One account-candidate set for authentication and CodeSpace inventory."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import CodespacesConfig, ConfigProviderReports
    from .gh_account import AccountInventory


@dataclass(frozen=True)
class CandidateRead:
    """Values read by one candidate resolution, not a freshness authority.

    Configuration and routing remain their source-owned values. Consumers must
    project them without mutation. Provider reports retain partial/error
    provenance; exceptions from the existing readers still propagate.
    """

    accounts: tuple[str | None, ...]
    routing: AccountInventory
    configuration: CodespacesConfig
    provider_reports: ConfigProviderReports
    bound_accounts: tuple[str, ...]
    provider_configs: tuple[tuple[str, Path], ...]
    active_account: str | None


def candidates(
    *, on_account: Callable[[str | None], None] | None = None,
    concurrent: bool = False,
) -> tuple[str | None, ...]:
    """Return the existing ordered accounts projection."""
    return read_candidates(on_account=on_account, concurrent=concurrent).accounts


def read_candidates(
    *, on_account: Callable[[str | None], None] | None = None,
    concurrent: bool = False,
) -> CandidateRead:
    """Resolve account sources, optionally overlapping namespace discovery.

    Notifications follow discovery order; the returned tuple retains the existing
    source precedence regardless of which metadata query finishes first.
    """
    from . import account_binding, gh_account, picker_auth
    from .config import load_merged_config, scan_config_providers

    providers = picker_auth.authorized_configs()
    bound = account_binding.bound_accounts_or_raise()
    groups: dict[int, tuple[str, ...]] = {}
    notified: set[str] = set()
    configuration = None
    provider_reports = None

    def record(rank: int, accounts: Iterable[str]) -> None:
        groups[rank] = tuple(account for account in accounts if account)
        for account in groups[rank]:
            key = account.casefold()
            if on_account is not None and key not in notified:
                notified.add(key)
                on_account(account)

    def configured_accounts(inventory: Callable[[], gh_account.AccountInventory]) -> tuple[str, ...]:
        nonlocal configuration, provider_reports
        provider_reports = (
            scan_config_providers(concurrent_roots=True)
            if concurrent else scan_config_providers()
        )
        configuration = load_merged_config(include_cwd=False, provider_reports=provider_reports)
        repos = configuration.repos
        routing = inventory()
        return tuple(
            login for repo in repos if (login := routing.account_for(repo))
        )

    def active_accounts() -> tuple[str, ...]:
        current = gh_account.active_account()
        return (current,) if current else ()

    if not concurrent:
        routing = gh_account.account_inventory()
        record(0, routing.accounts)
        record(1, bound)
        record(2, providers)
        record(3, configured_accounts(lambda: routing))
        record(4, active_accounts())
    else:
        with ThreadPoolExecutor(max_workers=3) as executor:
            mapped = executor.submit(gh_account.account_inventory)
            configured = executor.submit(configured_accounts, mapped.result)
            active = executor.submit(active_accounts)
            record(1, bound)
            record(2, providers)
            for future in as_completed((mapped, configured, active)):
                if future is mapped:
                    routing = mapped.result()
                    record(0, routing.accounts)
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
    return CandidateRead(
        tuple(selected), routing, configuration, provider_reports, bound,
        tuple(providers.items()), groups[4][0] if groups[4] else None,
    )
