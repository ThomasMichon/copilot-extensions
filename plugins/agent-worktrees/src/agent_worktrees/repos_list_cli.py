"""Repository inventory presentation and explicit strict-read authority."""

from __future__ import annotations

from . import output, repos


def command(argv: list[str]) -> int:
    """List registry entries; strict failures never become a healthy empty list."""
    class_filter = None
    for flag in ("--class", "--type"):
        if flag in argv:
            index = argv.index(flag)
            if index + 1 < len(argv):
                class_filter = argv[index + 1]
    strict = "--strict" in argv
    try:
        entries = repos.list_repos(class_filter=class_filter, strict=strict)
    except repos.RepositoryResolutionError as exc:
        message = f"Repository inventory could not be read: {exc}"
        if "--json" in argv:
            return output._json_error(message, exit_code=2)
        output.err(message)
        return 2
    if "--json" in argv:
        payload = {
            "platform": repos._current_platform(),
            "repos": [
                {
                    "name": entry.name,
                    "class": entry.repo_class,
                    "remote": entry.remote,
                    "default_branch": entry.default_branch,
                    "tags": entry.tags,
                    "contributing": entry.contributing,
                    "account": entry.account,
                    "resolved_account": repos.resolve_account(entry),
                    "agent": entry.agent,
                    "visibility": entry.visibility,
                    "paths": entry.paths,
                }
                for entry in entries
            ],
        }
        if strict:
            payload["inventory_authority"] = "complete"
        output._json_output(payload)
    elif not entries:
        print("No repos registered.")
        print("Add one with: repos add <name> <path> --class <class>")
        print("Or import the legacy registry with: repos migrate")
    else:
        platform = repos._current_platform()
        output.header("Repos Registry")
        for entry in entries:
            tag = f"[{entry.repo_class}]" if entry.agent else f"[{entry.repo_class} no-agent]"
            if entry.visibility:
                tag = f"{tag[:-1]} visibility={entry.visibility}]"
            local = entry.local_path(platform) or "(no local path)"
            print(f"  {entry.name:<25} {tag:<20} {local}")
            if entry.remote:
                print(f"  {'':25} {'':20} {entry.remote}")
            account = repos.resolve_account(entry)
            if account:
                source = "explicit" if entry.account else "derived"
                print(f"  {'':25} {'':20} account: {account} ({source})")
    return 0
