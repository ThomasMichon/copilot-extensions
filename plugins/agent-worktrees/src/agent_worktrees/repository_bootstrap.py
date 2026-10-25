"""Attended acquisition and the shared repository setup CLI, not project adoption."""

from __future__ import annotations

import argparse
from dataclasses import replace
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit

from agent_procutil import no_window_kwargs

from . import repos, repository_setup as protocol
from .git_ops import repository_identity_env
from .repos_registry_transaction import registry_lock


def _terminal() -> bool:
    return protocol._terminal()


def _commit(value: str) -> str:
    if not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", value):
        raise protocol.SetupError("Repository commit must be a full 40- or 64-hex commit ID.")
    return value.lower()


def _require_commit(source: protocol.Source, commit: str | None) -> None:
    if commit is not None and source.commit != commit:
        raise protocol.SetupError(
            "Repository HEAD does not match the requested commit; "
            "the existing target was preserved. Reconcile through its owner.",
        )


def _selection(url: str, target: str | None, *, ask: bool) -> tuple[str, Path]:
    canonical = protocol.https_url(url)
    name = urlsplit(canonical).path.rsplit("/", 1)[-1].removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise protocol.SetupError("Repository name cannot be used as a registry key.")
    if target is None:
        root = repos.get_srcroot()
        if not root and ask and _terminal():
            root = input("Source root (absolute path; this does not change the default): ").strip()
        if not root:
            raise protocol.SetupError("Select an absolute --target or configure a source root.")
        target = str(Path(root).expanduser() / name)
    path = Path(target).expanduser()
    if not path.is_absolute():
        raise protocol.SetupError("Repository target must be absolute.")
    # Check existing ancestors without creating a target or following an escape.
    existing = path
    while not existing.exists() and not existing.is_symlink():
        if existing.parent == existing:
            raise protocol.SetupError("Repository target has no reachable filesystem ancestor.")
        existing = existing.parent
    protocol._plain(existing)
    return name, path


def _registration(registry: repos.ReposRegistry, name: str, path: Path, url: str) -> None:
    for key, entry in registry.repos.items():
        local = entry.local_path()
        same_path = bool(local and Path(local).resolve() == path.resolve())
        if key == name:
            if entry.remote and protocol.https_url(entry.remote) != url:
                raise protocol.SetupError("Repository name is registered to a different remote.")
            if local and not same_path:
                raise protocol.SetupError("Repository name is already registered at another path.")
        if same_path:
            if key != name:
                raise protocol.SetupError("Target is registered under another repository name.")
        if key == name or same_path:
            if entry.repo_class != "reference" or entry.agent:
                raise protocol.SetupError(
                    "Target is already managed; reconcile setup through its existing owner.",
                )


def _account(registry: repos.ReposRegistry, name: str, url: str) -> str:
    selected = registry.repos.get(name)
    if selected is not None and selected.account:
        return selected.account
    accounts: set[str] = set()
    for entry in registry.repos.values():
        if entry.remote and entry.account:
            try:
                matching = protocol.https_url(entry.remote) == url
            except protocol.SetupError:
                matching = False
            if matching:
                accounts.add(entry.account)
    if len(accounts) > 1:
        raise protocol.SetupError("Repository account is ambiguous; select its registry account.")
    if accounts:
        return accounts.pop()
    if urlsplit(url).hostname != "github.com":
        return ""
    owner = urlsplit(url).path.strip("/").split("/")[0].casefold()
    index = repos.AccountLookupIndex.from_registry(registry)
    return index.account_map.get(owner) or ""


def _credential_args(url: str, account: str, *, cwd: Path) -> list[str]:
    if not account:
        return []
    if urlsplit(url).hostname != "github.com":
        raise protocol.SetupError(
            "Explicit account filtering is not qualified for this Git host; "
            "ambient authentication was not attempted.",
        )
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", account):
        raise protocol.SetupError("Configured repository account is invalid.")
    # An arbitrary helper may ignore username and return ambient credentials.
    # Qualify the supported account-filtering owner instead of copying its token.
    result = subprocess.run(
        ["git", "-C", str(cwd), "config", "--get-urlmatch", "credential", url],
        capture_output=True, text=True, timeout=30,
        env=repository_identity_env(), **no_window_kwargs(),
    )
    if result.returncode not in (0, 1):
        raise protocol.SetupError("Could not inspect the repository credential owner.")
    helpers: list[str] = []
    settings: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition(" ")
        settings[key.casefold()] = value.strip()
        if key.casefold() == "credential.helper":
            if not value:
                helpers.clear()
            else:
                helpers.append(value.strip())
    provider = (
        os.environ.get("GCM_PROVIDER") or settings.get("credential.provider") or "auto"
    ).strip().casefold()
    authority = os.environ.get("GCM_AUTHORITY") or settings.get("credential.authority")
    if (
        len(helpers) != 1 or helpers[0] not in ("manager", "manager-core")
        or provider not in ("auto", "github") or authority
    ):
        raise protocol.SetupError(
            "An explicit repository account requires a qualified GCM account-filtering owner; "
            "ambient authentication was not attempted.",
        )
    return [
        "-c", "credential.helper=",
        "-c", f"credential.helper={helpers[0]}",
        "-c", f"credential.{url}.username={account}",
        "-c", f"credential.{url}.provider=github",
        "-c", "credential.gitHubAccountFiltering=true",
        "-c", "credential.useHttpPath=true",
        "-c", "http.extraHeader=",
        "-c", f"http.{url}.extraHeader=",
    ]


def _acquisition_environment() -> dict[str, str]:
    return protocol._environment(attended=_terminal())


def _clone(
    url: str, path: Path, credential_args: list[str], *, commit: str | None = None,
) -> None:
    environment = _acquisition_environment()
    resolved = subprocess.run(
        ["git", "ls-remote", "--get-url", "--", url], cwd=path.parent,
        capture_output=True, text=True, timeout=30, env=environment, **no_window_kwargs(),
    )
    if resolved.returncode or protocol.https_url(resolved.stdout.strip()) != protocol.https_url(url):
        raise protocol.SetupError("Git URL rewrites select another remote; acquisition was refused.")
    completed = subprocess.run(
        ["git", "--no-replace-objects", *credential_args, "-c", "core.hooksPath=" + os.devnull,
         "clone", *(["--no-checkout"] if commit else []), "--", url, str(path)],
        cwd=path.parent, env=environment, **protocol._stdio_kwargs(),
    )
    if completed.returncode:
        raise protocol.SetupError(
            f"Git acquisition exited with status {completed.returncode}; "
            "any partial target was preserved. Reconcile before retrying.",
        )
    if commit is not None:
        protocol._plain(path)
        protocol._plain(path / ".git")
        try:
            resolved_commit = protocol._git(path, "rev-parse", "--verify", f"{commit}^{{commit}}")
        except protocol.SetupError:
            raise protocol.SetupError(
                "Requested commit is unavailable in the acquired source; "
                "the partial target was preserved without checkout or registration. "
                "No additional fetch was attempted.",
            ) from None
        if resolved_commit != commit:
            raise protocol.SetupError("Requested object is not the exact selected commit.")
        completed = subprocess.run(
            ["git", "--no-replace-objects", *credential_args, "-c", "core.hooksPath=" + os.devnull,
             "-C", str(path), "checkout", "--detach", commit, "--"],
            env=environment, **protocol._stdio_kwargs(),
        )
        if completed.returncode:
            raise protocol.SetupError(
                f"Git commit selection exited with status {completed.returncode}; "
                "the partial target was preserved without registration.",
            )


def acquire(
    name: str, path: Path, url: str, *, commit: str | None = None,
) -> protocol.Source:
    """Preserve unknown outcomes and register only a qualified matching checkout."""
    commit = _commit(commit) if commit is not None else None
    canonical = protocol.https_url(url)
    registry = repos.read_registry(strict=True)
    _registration(registry, name, path, canonical)
    if path.exists() or path.is_symlink():
        source = protocol.qualify(path, canonical)
    else:
        parent = path.parent
        if not parent.is_dir():
            raise protocol.SetupError("Repository target parent must already exist.")
        protocol._plain(parent)
        enclosing = subprocess.run(
            ["git", "-C", str(parent), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=30,
            env=repository_identity_env(), **no_window_kwargs(),
        )
        if enclosing.returncode == 0:
            raise protocol.SetupError("Repository target cannot be nested inside another checkout.")
        if enclosing.returncode != 128:
            raise protocol.SetupError("Repository target ownership could not be qualified.")
        account = _account(registry, name, canonical)
        credential_args = _credential_args(url, account, cwd=parent)
        _clone(url, path, credential_args, commit=commit)
        source = protocol.qualify(path, canonical)
    _require_commit(source, commit)
    with registry_lock(repos._repos_yaml_path()):
        current = repos.read_registry(strict=True)
        _registration(current, name, path, canonical)
        if _account(current, name, canonical) != _account(registry, name, canonical):
            raise protocol.SetupError("Repository account changed during acquisition; reconcile first.")
        if protocol.qualify(path, canonical) != source:
            raise protocol.SetupError("Repository revision changed during acquisition; reconcile first.")
        entry = current.repos.get(name)
        if entry is not None and entry.local_path() and entry.remote:
            return source
        if entry is None:
            entry = repos.RepoEntry(name=name, remote=canonical)
        current.repos[name] = replace(
            entry, remote=canonical, paths={**entry.paths, repos._current_platform(): str(path)},
        )
        repos.write_registry(current)
    return source


def command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="repos setup", allow_abbrev=False)
    parser.add_argument("repository")
    parser.add_argument("--target")
    parser.add_argument("--commit", type=_commit)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--trust-repository-setup", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code)
    try:
        canonical = protocol.https_url(args.repository)
        name, path = _selection(canonical, args.target, ask=args.apply)
        _registration(repos.read_registry(strict=True), name, path, canonical)
        print(f"Repository source: {canonical}\nSelected checkout: {path}", flush=True)
        if args.commit:
            print(f"Requested commit: {args.commit}", flush=True)
        if not args.apply:
            if path.exists():
                selected = protocol.inspect(path, canonical)
                _require_commit(selected.source, args.commit)
                print(f"Commit: {selected.source.commit}\nSetup entry: {selected.script}")
            else:
                print("Setup declaration: uninspected until acquisition.")
            print("Plan only: no acquisition, registration or repository code execution.")
            return 0
        source = acquire(name, path, args.repository, commit=args.commit)
        print(f"Acquisition/registration: ready\nCommit: {source.commit}", flush=True)
        selected = protocol.inspect(source.path, source.url)
        if selected.source != source:
            raise protocol.SetupError("Repository revision changed before setup qualification.")
        print(f"Setup entry: {selected.script}", flush=True)
        trusted = args.trust_repository_setup
        if not trusted and _terminal():
            trusted = input("Run setup from this exact repository revision? [y/N] ").strip().lower() == "y"
        result = protocol.run(selected, apply=True, trusted=trusted)
        print(f"Repository setup: {result.status}: {result.message}", flush=True)
        return 0 if result.status == "ready" else 1
    except (protocol.SetupError, OSError, subprocess.TimeoutExpired, repos.RepositoryResolutionError) as error:
        print(f"Repository setup refused/failed: {error}", file=sys.stderr, flush=True)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("Repository setup interrupted; completed stages were preserved. Check through its owner.",
              file=sys.stderr, flush=True)
        return 130
