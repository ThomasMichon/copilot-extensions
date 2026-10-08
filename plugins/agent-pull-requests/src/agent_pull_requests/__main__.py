"""CLI entry point for agent-pull-requests."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from typing import Any

from agent_procutil import no_window_kwargs

from . import __version__

_STATUS_QUERY = (
    "query($owner: String!, $name: String!, $number: Int!) { "
    "repository(owner: $owner, name: $name) { "
    "pullRequest(number: $number) { "
    "number title url state isDraft mergeable reviewDecision "
    "} } }"
)

_WATCH_QUERY = (
    "query($owner: String!, $name: String!, $number: Int!) { "
    "repository(owner: $owner, name: $name) { "
    "pullRequest(number: $number) { "
    "state merged mergeable reviewDecision headRefOid updatedAt "
    "commits(last: 1) { nodes { commit { statusCheckRollup { state } } } } "
    "} } }"
)

_PR_URL_NUMBER_RE = re.compile(r"/pull/(\d+)\s*$")

_WAIT_TERMINAL_STATES = frozenset({"MERGED", "CLOSED"})
_WATCH_HANDLER_DRAIN_TIMEOUT_S = 5.0
_WATCH_RESTART_STOP_TIMEOUT_S = 20.0


def _parse_repo_slug(value: str) -> tuple[str, str]:
    owner, sep, name = value.partition("/")
    if not sep or not owner or not name:
        raise argparse.ArgumentTypeError("repo must be 'owner/name'")
    return owner, name


def _validate_repo_slug(value: str) -> str:
    _parse_repo_slug(value)
    return value


def _agent_worktrees_command() -> str:
    command = shutil.which("agent-worktrees")
    if command:
        return command
    raise RuntimeError(
        "agent-worktrees command not found on PATH; this initial scaffold "
        "depends on 'agent-worktrees repos gh <repo> -- ...' for GitHub auth"
    )


def _run_agent_worktrees_gh_raw(repo: str, gh_args: list[str]) -> subprocess.CompletedProcess[str]:
    command = [
        _agent_worktrees_command(),
        "repos",
        "gh",
        repo,
        "--",
        *gh_args,
    ]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **no_window_kwargs(),
    )


def _run_agent_worktrees_gh(repo: str, gh_args: list[str]) -> dict[str, Any]:
    proc = _run_agent_worktrees_gh_raw(repo, gh_args)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or "unknown gh failure"
        raise RuntimeError(detail)
    return _parse_json_tail(proc.stdout)


def _strip_leading_diagnostic(stdout: str) -> str:
    """Drop a leading 'ambient auth' warning line 'repos gh' may emit on stdout.

    Mirrors the tolerance already applied to JSON output in
    ``_parse_json_tail``, but for plain-text gh output (e.g. the PR URL
    printed by ``gh pr create``, or ``gh pr merge``'s status lines).
    """
    lines = stdout.strip("\n").splitlines()
    while lines and "using ambient auth" in lines[0]:
        lines = lines[1:]
    return "\n".join(lines).strip()


def _parse_json_tail(stdout: str) -> dict[str, Any]:
    """Parse the JSON payload from gh output that may be preceded by warnings.

    'agent-worktrees repos gh' can write a diagnostic (e.g. an ambient-auth
    token-fallback warning) to stdout ahead of the actual JSON response. Fall
    back to locating the last top-level JSON object/array in the output
    rather than assuming the whole stream is JSON.
    """
    stripped = stdout.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    for marker in ("{", "["):
        idx = stripped.rfind(marker)
        while idx != -1:
            candidate = stripped[idx:]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                idx = stripped.rfind(marker, 0, idx)
    raise RuntimeError(
        "agent-worktrees repos gh returned non-JSON output: " + stripped[:200]
    )


def _watch_github_snapshot(repo: str, number: int):
    """Fetch one :class:`~agent_pull_requests.watch_contract.PRSnapshot` for
    ``repo``#``number`` -- the richer query the watch daemon's poll loop
    needs (merged flag, CI rollup), separate from ``status``'s own stable
    human-facing query/output contract."""
    from .watch_contract import PRSnapshot

    owner, name = _parse_repo_slug(repo)
    payload = _run_agent_worktrees_gh(
        repo,
        [
            "api",
            "graphql",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={number}",
            "-f",
            f"query={_WATCH_QUERY}",
        ],
    )
    repository = payload.get("data", {}).get("repository")
    pull_request = repository.get("pullRequest") if isinstance(repository, dict) else None
    if not isinstance(pull_request, dict):
        raise RuntimeError(f"GitHub did not return pull request #{number} for {repo}")
    commits = pull_request.get("commits") or {}
    nodes = commits.get("nodes") or []
    checks_state = ""
    if nodes and isinstance(nodes[0], dict):
        commit = nodes[0].get("commit") or {}
        rollup = commit.get("statusCheckRollup") or {}
        checks_state = str(rollup.get("state") or "").lower()
    return PRSnapshot(
        pr_state=str(pull_request.get("state") or "open").lower(),
        merged=bool(pull_request.get("merged", False)),
        head_sha=str(pull_request.get("headRefOid") or ""),
        mergeable=str(pull_request.get("mergeable") or ""),
        review_decision=str(pull_request.get("reviewDecision") or ""),
        checks_state=checks_state,
        updated_at=str(pull_request.get("updatedAt") or ""),
    )


def _github_status(repo: str, number: int) -> dict[str, Any]:
    owner, name = _parse_repo_slug(repo)
    payload = _run_agent_worktrees_gh(
        repo,
        [
            "api",
            "graphql",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={number}",
            "-f",
            f"query={_STATUS_QUERY}",
        ],
    )
    repository = payload.get("data", {}).get("repository")
    pull_request = repository.get("pullRequest") if isinstance(repository, dict) else None
    if not isinstance(pull_request, dict):
        raise RuntimeError(f"GitHub did not return pull request #{number} for {repo}")
    return {
        "repo": repo,
        "number": int(pull_request.get("number", number)),
        "title": str(pull_request.get("title") or ""),
        "url": str(pull_request.get("url") or ""),
        "state": str(pull_request.get("state") or ""),
        "isDraft": bool(pull_request.get("isDraft", False)),
        "mergeable": str(pull_request.get("mergeable") or ""),
        "reviewDecision": str(pull_request.get("reviewDecision") or ""),
        "transport": "agent-worktrees repos gh",
    }


def _render_status(status: dict[str, Any]) -> str:
    review = status["reviewDecision"] or "(none)"
    mergeable = status["mergeable"] or "(unknown)"
    draft = "yes" if status["isDraft"] else "no"
    return "\n".join(
        [
            f"repo:            {status['repo']}",
            f"number:          #{status['number']}",
            f"state:           {status['state']}",
            f"mergeable:       {mergeable}",
            f"review decision: {review}",
            f"draft:           {draft}",
            f"title:           {status['title']}",
            f"url:             {status['url']}",
        ]
    )


def _cmd_status(args: argparse.Namespace) -> int:
    try:
        status = _github_status(args.repo, args.number)
    except RuntimeError as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "repo": args.repo, "number": args.number}))
        else:
            print(f"agent-pull-requests: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
    else:
        print(_render_status(status))
    return 0


def _run_agent_worktrees_raw(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a plain ``agent-worktrees <argv>`` call (NOT the ``repos gh``
    proxy -- this is agent-worktrees' own claim/identity surface, local to
    THIS worktree, with no cross-account GitHub auth to route)."""
    return subprocess.run(
        [_agent_worktrees_command(), *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **no_window_kwargs(),
    )


def _resolve_claimant_worktree_id() -> str | None:
    """The worktree CWD traces back to, or ``None`` if it doesn't trace to
    any tracked worktree at all -- same claimant contract
    ``agent_worktrees.pr_cli.require_claimant_worktree`` enforces for
    ``create-pr``/``pr-watch``/``pr-merge`` (owning project is always the
    CWD; this plugin can't import that check directly since it lives in a
    separate package, so it shells the equivalent query instead)."""
    try:
        proc = _run_agent_worktrees_raw(["get", "worktree-id"])
    except (RuntimeError, OSError):
        return None
    if proc.returncode != 0:
        return None
    worktree_id = proc.stdout.strip()
    return worktree_id or None


_CLAIMANT_REFUSAL = (
    "agent-pull-requests: this directory isn't a tracked agent-worktrees "
    "worktree, so there's no claimant to own this PR. Run this FROM the "
    "worktree responsible for the work (your own project's worktree -- `cd` "
    "there, or launch/resume it first) and address the target repo as an "
    "explicit argument -- this works for a repo you have no local checkout "
    "of at all. Do not fall back to a bare `gh pr create` -- that skips the "
    "claim this command journals automatically."
)


def _journal_pr_claim(worktree_id: str, url: str, *, note: str) -> str | None:
    """Best-effort: journal a ``pr``-kind claim for ``url`` onto
    ``worktree_id``'s own ledger. Returns an error string on failure (never
    raises) -- a failed journal must never un-create the PR that already
    exists; the caller surfaces it as a warning instead."""
    try:
        proc = _run_agent_worktrees_raw(
            ["claims", "add", "pr", url, "--worktree", worktree_id, "--note", note, "--json"]
        )
    except (RuntimeError, OSError) as e:
        return str(e)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or "unknown claims-add failure"
        return detail
    return None


def _github_create(
    repo: str, head: str, base: str | None, title: str, body: str, draft: bool
) -> dict[str, Any]:
    gh_args = ["pr", "create", "--repo", repo, "--head", head, "--title", title, "--body", body]
    if base:
        gh_args += ["--base", base]
    if draft:
        gh_args.append("--draft")
    proc = _run_agent_worktrees_gh_raw(repo, gh_args)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or "unknown gh failure"
        raise RuntimeError(detail)
    url = _strip_leading_diagnostic(proc.stdout)
    match = _PR_URL_NUMBER_RE.search(url)
    if not match:
        raise RuntimeError(f"could not parse a PR number from gh output: {url!r}")
    return {
        "repo": repo,
        "number": int(match.group(1)),
        "title": title,
        "url": url,
        "head": head,
        "base": base,
        "isDraft": draft,
    }


def _cmd_create(args: argparse.Namespace) -> int:
    worktree_id = _resolve_claimant_worktree_id()
    if not worktree_id:
        if args.json:
            print(json.dumps({"error": _CLAIMANT_REFUSAL, "repo": args.repo}))
        else:
            print(_CLAIMANT_REFUSAL, file=sys.stderr)
        return 2
    try:
        result = _github_create(args.repo, args.head, args.base, args.title, args.body, args.draft)
    except RuntimeError as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "repo": args.repo}))
        else:
            print(f"agent-pull-requests: {exc}", file=sys.stderr)
        return 1
    claim_error = _journal_pr_claim(
        worktree_id, result["url"], note=f"PR #{result['number']} ({result['repo']})",
    )
    result["claimed_by"] = worktree_id if claim_error is None else None
    if claim_error:
        result["claim_warning"] = (
            f"PR opened, but claiming it onto worktree {worktree_id!r} failed: "
            f"{claim_error}. Run `agent-worktrees claims add pr {result['url']} "
            f"--worktree {worktree_id}` manually."
        )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"created:  {result['url']}")
        print(f"repo:     {result['repo']}")
        print(f"number:   #{result['number']}")
        print(f"head/base: {result['head']} -> {result['base'] or '(default)'}")
        if result.get("claimed_by"):
            print(f"claimed:  {result['claimed_by']}")
        elif result.get("claim_warning"):
            print(f"agent-pull-requests: {result['claim_warning']}", file=sys.stderr)
    return 0


_MERGE_METHOD_FLAGS = {"merge": "--merge", "squash": "--squash", "rebase": "--rebase"}


def _github_merge(
    repo: str, number: int, method: str, auto: bool, delete_branch: bool
) -> dict[str, Any]:
    gh_args = ["pr", "merge", str(number), "--repo", repo, _MERGE_METHOD_FLAGS[method]]
    if auto:
        gh_args.append("--auto")
    if delete_branch:
        gh_args.append("--delete-branch")
    proc = _run_agent_worktrees_gh_raw(repo, gh_args)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or "unknown gh failure"
        raise RuntimeError(detail)
    return {
        "repo": repo,
        "number": number,
        "method": method,
        "auto": auto,
        "message": _strip_leading_diagnostic(proc.stdout) or _strip_leading_diagnostic(proc.stderr),
    }


def _cmd_merge(args: argparse.Namespace) -> int:
    try:
        result = _github_merge(args.repo, args.number, args.method, args.auto, args.delete_branch)
    except RuntimeError as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "repo": args.repo, "number": args.number}))
        else:
            print(f"agent-pull-requests: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(result["message"])
    return 0


def _cmd_wait(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + args.timeout
    status: dict[str, Any] | None = None
    timed_out = False
    while True:
        try:
            status = _github_status(args.repo, args.number)
        except RuntimeError as exc:
            if args.json:
                print(json.dumps({"error": str(exc), "repo": args.repo, "number": args.number}))
            else:
                print(f"agent-pull-requests: {exc}", file=sys.stderr)
            return 1
        if status["state"] in _WAIT_TERMINAL_STATES:
            break
        if time.monotonic() >= deadline:
            timed_out = True
            break
        time.sleep(args.interval)
    assert status is not None
    if args.json:
        print(json.dumps({**status, "timedOut": timed_out}, indent=2, sort_keys=True))
    else:
        print(_render_status(status))
        print(f"timed out:       {'yes' if timed_out else 'no'}")
    if timed_out:
        return 3
    return 0 if status["state"] == "MERGED" else 1


# ---------------------------------------------------------------------------
# watch -- shared-daemon subscription for a long-poll waiter (#5530-follow-up)
# ---------------------------------------------------------------------------


def _watch_dial() -> tuple[str, int, str] | None:
    from .watch_daemon import endpoint_from_rendezvous, read_lock_data

    return endpoint_from_rendezvous(read_lock_data())


def _watch_boot() -> None:
    """Best-effort: spawn a detached ``agent-pull-requests serve`` if no
    daemon currently answers rendezvous. Windowless on Windows; races with
    another caller doing the same thing are harmless -- only one process
    wins the rendezvous-file write/bind, callers just re-dial."""
    from agent_procutil import detached_kwargs

    subprocess.Popen(
        [sys.executable, "-m", "agent_pull_requests", "serve"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **detached_kwargs(),
    )


def _watch_request(
    kind: str, payload: dict, *, boot_wait_s: float = 6.0, boot: bool = True
) -> dict:
    from work_coalescing_singleton import call_with_fallback

    def _fallback() -> dict:
        return {"error": "no watch daemon reachable and no inline fallback for this kind"}

    return call_with_fallback(
        dial=_watch_dial,
        boot=_watch_boot if boot else None,
        boot_wait_s=boot_wait_s,
        kind=kind,
        key=f"{payload.get('repo', '')}#{payload.get('number', '')}",
        payload=payload,
        request_deadline_s=5.0,
        fallback=_fallback,
    )


def _cmd_watch_subscribe(args: argparse.Namespace) -> int:
    from .watch_contract import DEFAULT_UNTIL

    until = tuple(args.until) if args.until else DEFAULT_UNTIL
    notify: dict[str, Any] = {}
    if args.notify_argv:
        notify["argv"] = args.notify_argv
    result = _watch_request(
        "register",
        {
            "repo": args.repo,
            "number": args.number,
            "subscriber_id": args.subscriber_id,
            "until": list(until),
            "notify": notify,
            "timeout": args.timeout,
        },
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(result)
    return 0 if result.get("registered") else 1


def _cmd_watch_unsubscribe(args: argparse.Namespace) -> int:
    result = _watch_request(
        "unregister",
        {"repo": args.repo, "number": args.number, "subscriber_id": args.subscriber_id},
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(result)
    return 0 if result.get("unregistered") else 1


def _cmd_watch_status(args: argparse.Namespace) -> int:
    result = _watch_request("status", {"repo": "", "number": 0}, boot_wait_s=0.0, boot=False)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        subscribers = result.get("subscribers") or {}
        if not subscribers:
            print("no watch daemon reachable, or no active subscriptions")
        for key, ids in subscribers.items():
            print(f"{key}: {', '.join(ids)}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    """Run the resident PR-watch daemon in the foreground.

    A long-lived host (Scheduled Task / systemd-user unit) is deferred to a
    follow-up per ``docs/patterns/service-lifecycle-supervision.md`` -- this
    first slice is manually invocable and auto-booted on demand by
    ``_watch_boot`` (``call_with_fallback``'s own boot path), matching
    ``agent-worktrees``' resident status-monitor's own bootstrap shape
    before its own supervised-task wiring landed. The single-instance lease
    (not just "last rendezvous-file write wins") is what actually makes two
    racing ``_watch_boot`` callers safe: the loser raises
    ``AlreadyRunningError`` and exits 0 immediately rather than silently
    running a second, orphaned daemon nobody will ever discover.

    On startup the daemon reattaches every subscription a prior generation
    persisted (``WatchDaemon``'s own ``_reattach_from_disk``) -- a
    ``serve stop`` followed by ``serve restart`` (the update path) never
    silently drops a caller that's suspended waiting on this daemon to wake
    it.
    """
    from single_instance_lease import AlreadyRunningError, SingleInstance
    from work_coalescing_singleton import CoalescingServer

    from .watch_daemon import WatchDaemon, rendezvous_fields, state_dir, write_lock_data

    lease = SingleInstance(state_dir(), service="agent-pull-requests-watch")
    try:
        lease.acquire()
    except AlreadyRunningError as exc:
        print(f"agent-pull-requests: watch daemon already running (pid {exc.holder_pid})")
        return 0

    daemon = WatchDaemon(fetch=_watch_github_snapshot, poll_interval=args.poll_interval)
    server = CoalescingServer(daemon.compute, on_idle=None)
    server.start()
    write_lock_data(rendezvous_fields(server))
    print(f"agent-pull-requests: watch daemon listening ({server.rendezvous()['endpoint']})")
    try:
        daemon.wait_for_shutdown()
    except KeyboardInterrupt:
        pass
    finally:
        server.close_admission(reason="shutdown")
        server.close()
        deadline = time.monotonic() + _WATCH_HANDLER_DRAIN_TIMEOUT_S
        while server.active_handler_count():
            if time.monotonic() >= deadline:
                raise TimeoutError("PR watch request handlers did not drain")
            time.sleep(0.02)
        daemon.close()
        lease.release()
    return 0


def _cmd_serve_stop(args: argparse.Namespace) -> int:
    """Request a graceful shutdown of a running daemon over the control
    plane (not a process kill): it finishes its current poll tick, closes
    its listener, and releases its single-instance lease, exiting cleanly.
    Every subscription is already durable (persisted on every mutation), so
    nothing is lost -- 'serve' (or the next auto-boot) reattaches all of
    them."""
    result = _watch_request("shutdown", {"repo": "", "number": 0}, boot_wait_s=0.0, boot=False)
    ok = bool(result.get("shutting_down"))
    if args.json:
        print(json.dumps({"stopped": ok, **result}, indent=2, sort_keys=True))
    elif ok:
        print("agent-pull-requests: watch daemon is shutting down")
    else:
        print("agent-pull-requests: no watch daemon reachable")
    return 0 if ok else 1


def _cmd_serve_status(args: argparse.Namespace) -> int:
    """Daemon health (pid, subscriber/key counts) -- distinct from
    ``watch status``'s per-subscriber listing."""
    result = _watch_request("health", {"repo": "", "number": 0}, boot_wait_s=0.0, boot=False)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif "pid" in result:
        print(
            f"running (pid {result['pid']}) · "
            f"{result.get('subscriber_count', 0)} subscriber(s) across "
            f"{result.get('active_keys', 0)} PR(s)"
        )
    else:
        print("not running")
    return 0 if "pid" in result else 1


def _cmd_serve_restart(args: argparse.Namespace) -> int:
    """Stop (if running) then start a fresh daemon on *this* invocation's
    interpreter -- the update/reattach path: every durable subscription
    survives (see ``_cmd_serve``'s own docstring), so 'restart to pick up
    an update' never orphans a caller that's suspended waiting on a PR."""
    from single_instance_lease import AlreadyRunningError, SingleInstance

    from .watch_daemon import state_dir

    lease = SingleInstance(state_dir(), service="agent-pull-requests-watch")
    try:
        lease.acquire()
    except AlreadyRunningError:
        was_running = True
    else:
        lease.release()
        was_running = False
    if was_running:
        stop_result = _watch_request(
            "shutdown", {"repo": "", "number": 0}, boot_wait_s=0.0, boot=False
        )
        if not stop_result.get("shutting_down"):
            print("agent-pull-requests: could not reach the running daemon to stop it")
            return 1
        # A persistent rendezvous file is not proof of lease availability.
        deadline = time.monotonic() + _WATCH_RESTART_STOP_TIMEOUT_S
        while time.monotonic() < deadline:
            try:
                lease.acquire()
            except AlreadyRunningError:
                time.sleep(0.2)
            else:
                lease.release()
                break
        else:
            message = "watch daemon did not release its lease; successor not started"
            print(json.dumps({"error": message}) if args.json else f"agent-pull-requests: {message}")
            return 1
    _watch_boot()
    # Confirm a live response, not merely the predecessor's leftover metadata.
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        health = _watch_request("health", {"repo": "", "number": 0}, boot_wait_s=0.0, boot=False)
        if "pid" in health:
            if args.json:
                print(json.dumps({"restarted": True, "was_running": was_running}))
            else:
                print("agent-pull-requests: watch daemon restarted")
            return 0
        time.sleep(0.2)
    print("agent-pull-requests: restart requested, but the new daemon has not answered yet")
    return 1


def _cmd_version(_args: argparse.Namespace) -> int:
    print(f"agent-pull-requests {__version__}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-pull-requests",
        description="Cross-repository pull-request commands for owner/repo targets.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="read one pull request by owner/repo + number")
    status.add_argument(
        "--repo",
        required=True,
        type=_validate_repo_slug,
        help="target GitHub repo as owner/name",
    )
    status.add_argument("--number", required=True, type=int, help="pull request number")
    status.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    status.set_defaults(handler=_cmd_status)

    create = subparsers.add_parser("create", help="open a pull request on owner/repo")
    create.add_argument("--repo", required=True, type=_validate_repo_slug, help="owner/name")
    create.add_argument("--head", required=True, help="head branch (must already be pushed)")
    create.add_argument("--base", default=None, help="base branch (defaults to the repo default)")
    create.add_argument("--title", required=True, help="pull request title")
    create.add_argument("--body", default="", help="pull request body (default: empty)")
    create.add_argument("--draft", action="store_true", help="open as a draft pull request")
    create.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    create.set_defaults(handler=_cmd_create)

    merge = subparsers.add_parser("merge", help="merge an existing pull request")
    merge.add_argument("--repo", required=True, type=_validate_repo_slug, help="owner/name")
    merge.add_argument("--number", required=True, type=int, help="pull request number")
    method_group = merge.add_mutually_exclusive_group()
    method_group.add_argument(
        "--squash", dest="method", action="store_const", const="squash",
        help="squash-merge (default)",
    )
    method_group.add_argument(
        "--merge", dest="method", action="store_const", const="merge", help="ordinary merge commit"
    )
    method_group.add_argument(
        "--rebase", dest="method", action="store_const", const="rebase", help="rebase-merge"
    )
    merge.set_defaults(method="squash")
    merge.add_argument(
        "--auto", action="store_true", help="enable auto-merge instead of merging immediately"
    )
    merge.add_argument(
        "--delete-branch", action="store_true", help="delete the head branch after merging"
    )
    merge.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    merge.set_defaults(handler=_cmd_merge)

    wait = subparsers.add_parser("wait", help="poll a pull request until it merges or closes")
    wait.add_argument("--repo", required=True, type=_validate_repo_slug, help="owner/name")
    wait.add_argument("--number", required=True, type=int, help="pull request number")
    wait.add_argument("--interval", type=float, default=15.0, help="poll interval in seconds")
    wait.add_argument("--timeout", type=float, default=600.0, help="give up after this many seconds")
    wait.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    wait.set_defaults(handler=_cmd_wait)

    watch = subparsers.add_parser(
        "watch", help="non-blocking subscription against the shared watch daemon"
    )
    watch_sub = watch.add_subparsers(dest="watch_command", required=True)

    watch_subscribe = watch_sub.add_parser(
        "subscribe",
        help="register interest in a PR; returns immediately (boots the daemon if needed)",
    )
    watch_subscribe.add_argument("--repo", required=True, type=_validate_repo_slug)
    watch_subscribe.add_argument("--number", required=True, type=int)
    watch_subscribe.add_argument(
        "--subscriber-id", required=True, help="caller-chosen unique id for this registration"
    )
    watch_subscribe.add_argument(
        "--until",
        action="append",
        choices=["merged", "closed", "review_changed", "mergeable_changed", "checks_changed"],
        help="transition(s) to wake on (default: merged, closed); repeatable",
    )
    watch_subscribe.add_argument(
        "--timeout", type=float, default=None, help="also fire (timed_out) after this many seconds"
    )
    watch_subscribe.add_argument(
        "--notify-argv",
        nargs="+",
        metavar="ARGV",
        help="command to run (no agent-pull-requests-specific meaning; the "
        "fired event is passed as JSON on its stdin) when this fires -- "
        "e.g. a caller-supplied 'agent-dispatch resume <task-id>'",
    )
    watch_subscribe.add_argument("--json", action="store_true")
    watch_subscribe.set_defaults(handler=_cmd_watch_subscribe)

    watch_unsubscribe = watch_sub.add_parser(
        "unsubscribe", help="cancel a registration before it fires"
    )
    watch_unsubscribe.add_argument("--repo", required=True, type=_validate_repo_slug)
    watch_unsubscribe.add_argument("--number", required=True, type=int)
    watch_unsubscribe.add_argument("--subscriber-id", required=True)
    watch_unsubscribe.add_argument("--json", action="store_true")
    watch_unsubscribe.set_defaults(handler=_cmd_watch_unsubscribe)

    watch_status = watch_sub.add_parser(
        "status", help="list active subscriptions, if a daemon answers"
    )
    watch_status.add_argument("--json", action="store_true")
    watch_status.set_defaults(handler=_cmd_watch_status)

    watch_health = watch_sub.add_parser(
        "health", help="daemon health (pid, subscriber/key counts) -- never auto-boots"
    )
    watch_health.add_argument("--json", action="store_true")
    watch_health.set_defaults(handler=_cmd_serve_status)

    serve = subparsers.add_parser(
        "serve", help="run the resident PR-watch daemon in the foreground"
    )
    serve.add_argument(
        "--poll-interval", type=float, default=30.0, help="seconds between polls per watched PR"
    )
    serve.set_defaults(handler=_cmd_serve)

    stop = subparsers.add_parser(
        "stop", help="gracefully stop a running watch daemon -- never auto-boots"
    )
    stop.add_argument("--json", action="store_true")
    stop.set_defaults(handler=_cmd_serve_stop)

    restart = subparsers.add_parser(
        "restart",
        help="stop (if running) then start a fresh watch daemon -- the update/reattach path",
    )
    restart.add_argument("--json", action="store_true")
    restart.set_defaults(handler=_cmd_serve_restart)

    version = subparsers.add_parser("version", help="print the agent-pull-requests version and exit")
    version.set_defaults(handler=_cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
