"""``agent-dispatch attention``: the operator's ordered "what needs me" queue.

    agent-dispatch attention [--json] [--source NAME ...]
    agent-dispatch attention next [--after CURSOR] [--json] [--source NAME ...]
    agent-dispatch attention source add NAME [--timeout S] -- ARGV...
    agent-dispatch attention source remove NAME
    agent-dispatch attention source list

The queue merges every source (this coordinator's tasks plus registered command
sources) in one deterministic order, worst first, and never reads a failed
source as "all clear": its ``status`` is ``degraded`` (a source failed or a
registration was rejected), ``partial`` (a source couldn't classify some
entities), ``attention`` (items) or ``clear``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from typing import Any

from . import attention_contract as ac
from . import attention_sources as srcs
from .attention_dismiss import Dismissals, source_of
from .attention_store import FirstObserved, locked


def _core():
    from . import __main__ as core

    return core


def _target_cli(args: argparse.Namespace) -> tuple[str, ...]:
    """The invocation that reaches this read's coordinator: its ``--url`` and
    ``--shared``, never a token (the operator's environment supplies that)."""
    cli = ["agent-dispatch"]
    if getattr(args, "url", None):
        cli += ["--url", args.url]
    if getattr(args, "shared", False):
        cli.append("--shared")
    return tuple(cli)


def _effective_cli(args: argparse.Namespace, client: Any) -> tuple[str, ...] | None:
    """:func:`_target_cli`, plus ``--shared`` when the default path silently failed
    over to the shared coordinator -- so an action keeps reaching the queue the
    read came from even once the local coordinator is back. ``None`` -- no
    action, since none could reach that coordinator as-is -- when the read was
    authenticated by a ``--token`` argument (an action never carries a secret)
    or went over an SSH failover (no flag pins that peer)."""
    if getattr(args, "token", None):
        return None
    if getattr(client, "_tunnel", None) is not None:
        # An SSH failover: no flag pins that peer, and after the local
        # coordinator recovers a bare action would read a different queue.
        return None
    cli = _target_cli(args)
    if len(cli) == 1:
        from .config import shared_url

        surl, base = shared_url(), getattr(client, "base_url", None)
        if surl and base and base.rstrip("/") == surl.rstrip("/"):
            cli += ("--shared",)
    return cli


def _coordinator_scope(args: argparse.Namespace, client: Any) -> str | None:
    """Which coordinator's queue this read saw, so first-observed times from one
    coordinator are never cleared by a read of another: the peer for an SSH
    failover, the URL for ``--url``, ``--shared`` or a silent shared failover.
    ``None`` for this machine's own coordinator, whose port can move across
    restarts (its times must survive those)."""
    tunnel = getattr(client, "_tunnel", None)
    if tunnel is not None:
        return f"ssh:{getattr(tunnel, '_machine', None) or 'peer'}"
    base = (getattr(client, "base_url", None) or "").rstrip("/")
    if getattr(args, "url", None) or getattr(args, "shared", False):
        return base or None
    from .config import shared_url

    surl = (shared_url() or "").rstrip("/")
    return base if surl and base == surl else None


def _dispatch_reader(args: argparse.Namespace):
    """The ``dispatch`` reader; records the effective invocation on ``args`` for
    the ``next`` hint, and tags its result with the coordinator it read."""
    args.attention_cli = _target_cli(args)

    def read(read_at: str) -> dict[str, Any]:
        client = _core()._client(args)
        args.attention_cli = _effective_cli(args, client)
        # Before the read: closing the client (as the read does) drops its SSH tunnel.
        scope = _coordinator_scope(args, client)
        result = srcs.read_dispatch(lambda: client, read_at, cli=args.attention_cli)
        return {**result, "scope": scope} if scope else result

    return read


def _disabled(read_at: str) -> dict[str, Any]:
    return {"items": [], "status": "disabled", "uncertain": 0, "read_at": read_at}


def _sibling_readers(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, float]]:
    """The ``bridge`` and ``pr`` readers, each ``disabled`` when its plugin
    isn't installed here."""
    from . import attention_siblings as sib
    from . import procutil
    from .remote_dispatch import local_machine

    bridge, worktrees = procutil.agent_bridge_launch_prefix(), procutil.agent_worktrees_launch_prefix()
    if procutil.sibling_absent("agent-bridge"):
        bridge = None
    if procutil.sibling_absent("agent-worktrees"):
        worktrees = None
    include_remote = bool(getattr(args, "include_remote", False))
    readers = {
        "bridge": (lambda read_at: sib.read_bridge(read_at, prefix=bridge, machine=local_machine(),
                                                   include_remote=include_remote)) if bridge else _disabled,
        "pr": (lambda read_at: sib.read_pr(read_at, prefix=worktrees, env=procutil.agent_worktrees_environment()))
        if worktrees else _disabled,
    }
    return readers, {"bridge": sib.BRIDGE_TIMEOUT, "pr": sib.PR_TIMEOUT}


def _readers(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, float], list[dict[str, str]], set[str]]:
    registrations, config_errors = srcs.load_registrations()
    readers: dict[str, Any] = {"dispatch": _dispatch_reader(args)}
    timeouts = {"dispatch": srcs.DEFAULT_TIMEOUT}
    sibling_readers, sibling_timeouts = _sibling_readers(args)
    readers.update(sibling_readers)
    timeouts.update(sibling_timeouts)
    for name, spec in registrations.items():
        readers[name] = lambda read_at, n=name, s=spec: srcs.read_command(n, s, read_at)
        # The command's own timeout fires first, and its runner then stops the
        # tree (a SIGTERM grace, SIGKILL, a reap): never abandon it mid-cleanup.
        timeouts[name] = spec["timeout"] + srcs.COMMAND_CLEANUP_SECONDS
    known = set(readers) | {e["name"] for e in config_errors if e["name"] != "*"}
    return readers, timeouts, config_errors, known


def _read(args: argparse.Namespace) -> dict[str, Any] | None:
    readers, timeouts, config_errors, known = _readers(args)
    selected = sorted(set(args.source)) if args.source else None
    unknown = sorted(set(selected or ()) - known)
    if unknown:
        print(f"agent-dispatch: unknown attention source(s): {', '.join(unknown)} "
              f"(known: {', '.join(sorted(known))})", file=sys.stderr)
        return None
    disabled = sorted(n for n in selected or () if readers.get(n) is _disabled)
    if disabled:
        print(f"agent-dispatch: attention source(s) not installed on this machine: {', '.join(disabled)}",
              file=sys.stderr)
        return None
    return srcs.collect(readers, timeouts=timeouts, selected=selected,
                        config_errors=config_errors, store=FirstObserved(), dismissals=Dismissals(),
                        dismiss_cli=lambda: getattr(args, "attention_cli", _target_cli(args)),
                        include_remote=bool(getattr(args, "include_remote", False)))


def _banner(envelope: dict[str, Any]) -> list[str]:
    lines = []
    if envelope["status"] == "degraded":
        failed = [f"{s['name']} ({s.get('error', 'failed')})" for s in envelope["sources"] if s["status"] == "failed"]
        rejected = [f"{e['name']} ({e['error']})" for e in envelope["config_errors"]]
        lines.append("[DEGRADED] this queue may be incomplete: " + "; ".join(failed + rejected))
    elif envelope["status"] == "partial":
        lines.append("[PARTIAL] some entities couldn't be classified: " + ", ".join(
            f"{s['name']} ({s['uncertain']})" for s in envelope["sources"] if s["status"] == "uncertain"))
    return [ac.for_terminal(line) for line in lines]


def _item_lines(item: dict[str, Any]) -> list[str]:
    lines = [f"[{item['display_state']}] {item['reason']}", f"    {item['id']}  since {item['created_at']}"]
    if item["actions"]:
        lines.append("    -> " + " ".join(item["actions"][0]["argv"]))
    return [ac.for_terminal(line) for line in lines]


def _cmd_attention(args: argparse.Namespace) -> int:
    verb = getattr(args, "attention_verb", None)
    if verb == "next":
        return _cmd_next(args)
    if verb in ("dismiss", "undismiss", "dismissed"):
        return _cmd_dismissal(args)
    envelope = _read(args)
    if envelope is None:
        return 2
    if args.json:
        return _core()._emit(envelope)
    for line in _banner(envelope):
        print(line)
    if not envelope["items"]:
        scope = f" from {', '.join(envelope['selected'])}" if envelope["selected"] else ""
        print(f"Nothing needs you{scope}." if envelope["status"] == "clear" else "No items read.")
    for item in envelope["items"]:
        print("\n".join(_item_lines(item)))
    if envelope["dismissed"]:
        print(f"({len(envelope['dismissed'])} dismissed: agent-dispatch attention dismissed)")
    return 0


def _cmd_dismissal(args: argparse.Namespace) -> int:
    """``dismiss ID [--until TIME | --forever]``, ``undismiss ID``, ``dismissed``.
    A dismissal until the item changes reads the item's own source first, so it
    records the condition the operator actually saw."""
    store = Dismissals()
    if args.attention_verb == "dismissed":
        entries, error = store.entries()
        if error:
            print(f"agent-dispatch: {error}", file=sys.stderr)
        if args.json:
            return _core()._emit({"dismissed": entries})
        for item_id, entry in sorted(entries.items()):
            until = f" until {entry['until']}" if entry["mode"] == "until" else ""
            print(ac.for_terminal(f"{item_id}  [{entry['mode']}{until}]  since {entry['at']}"))
        if not entries:
            print("Nothing is dismissed.")
        return 0
    if args.attention_verb == "undismiss":
        return _core()._emit({"undismissed": store.undismiss(args.id), "id": args.id})
    now = srcs.now_iso()
    if args.forever:
        return _core()._emit({"dismissed": args.id, **store.dismiss(args.id, "forever", now)})
    if args.until is not None:
        try:
            until = ac.canonical_time(args.until)
        except ac.ContractError as exc:
            print(f"agent-dispatch: --until: {exc}", file=sys.stderr)
            return 2
        if until <= now:
            print(f"agent-dispatch: --until {until} is not in the future", file=sys.stderr)
            return 2
        return _core()._emit({"dismissed": args.id, **store.dismiss(args.id, "until", now, until=until)})
    read_args = argparse.Namespace(**{**vars(args), "source": [source_of(args.id)]})
    envelope = _read(read_args)
    if envelope is None:
        return 2
    item = next((i for i in envelope["items"] + envelope["dismissed"] if i["id"] == args.id), None)
    if item is None:
        print(f"agent-dispatch: {args.id} is not in the attention queue now "
              "(--forever or --until dismiss it anyway)", file=sys.stderr)
        return 2
    return _core()._emit({"dismissed": args.id, **store.dismiss(args.id, "changed", now, item=item)})


def _cmd_next(args: argparse.Namespace) -> int:
    envelope = _read(args)
    if envelope is None:
        return 2
    try:
        item = ac.next_item(envelope["items"], args.after)
    except ac.ContractError as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 2
    result = {k: v for k, v in envelope.items() if k != "items"}
    result.update(item=item, cursor=ac.encode_cursor(item) if item else None)
    if args.json:
        return _core()._emit(result)
    for line in _banner(envelope):
        print(line)
    if item is None:
        print("Nothing needs you." if envelope["status"] == "clear" else "No items read.")
    else:
        print("\n".join(_item_lines(item)))
        hint = _next_argv(args, envelope, result["cursor"])
        if hint:
            print("    next: " + " ".join(hint))
    return 0


def _next_argv(args: argparse.Namespace, envelope: dict[str, Any], cursor: str) -> list[str] | None:
    """The follow-up that walks the same queue: same coordinator, same sources.
    ``None`` when no invocation reaches this read's coordinator as-is (a
    ``--token`` argument or an SSH failover): a hint that silently walked
    another queue would be worse than none."""
    cli = getattr(args, "attention_cli", _target_cli(args))
    if cli is None:
        return None
    argv = [*cli, "attention", "next", "--after", cursor]
    for name in envelope["selected"] or ():
        argv += ["--source", name]
    return argv


def _cmd_source(args: argparse.Namespace) -> int:
    path = srcs.registry_path()
    if args.source_verb == "list":
        valid, errors = srcs.load_registrations()
        return _core()._emit({"builtin": list(srcs.BUILTIN_SOURCES), "registered": valid,
                              "config_errors": errors, "file": str(path)})
    spec = None
    if args.source_verb == "add":
        tokens = list(args.argv or [])
        # REMAINDER takes everything after NAME, so options placed before `--`
        # (the documented form) are parsed here; the command is what follows `--`.
        head, argv = (tokens[:tokens.index("--")], tokens[tokens.index("--") + 1:]) if "--" in tokens else ([], tokens)
        options = argparse.ArgumentParser(prog="attention source add", add_help=False)
        options.add_argument("--timeout", type=float, default=args.timeout)
        try:
            parsed = options.parse_args(head)
        except SystemExit:
            return 2
        spec = {"argv": argv, "timeout": parsed.timeout}
        if argv and not os.path.isabs(argv[0]):  # pin the command now, not at each read
            resolved = shutil.which(argv[0])
            if resolved:
                spec["argv"] = [os.path.abspath(resolved), *argv[1:]]
        error = srcs.registration_error(args.name, spec)
        if error:
            print(f"agent-dispatch: cannot register {args.name!r}: {error}", file=sys.stderr)
            return 2
    with locked(path):  # one read-modify-write, so concurrent edits never lose each other
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            raw = doc.get("sources", {}) if isinstance(doc, dict) else None
            if not isinstance(raw, dict):
                raise ValueError("its sources is not an object")
        except FileNotFoundError:
            raw = {}
        except (OSError, ValueError) as exc:
            print(f"agent-dispatch: {path} is unreadable ({exc}); fix or remove it first", file=sys.stderr)
            return 1
        if spec is not None:
            raw[args.name] = spec
            srcs.save_registrations(raw)
            return _core()._emit({"registered": args.name, **spec})
        removed = raw.pop(args.name, None) is not None
        if removed:
            srcs.save_registrations(raw)
        return _core()._emit({"removed": removed, "name": args.name})


def _add_read_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true", help="Print the versioned JSON envelope")
    p.add_argument("--source", action="append", default=[], metavar="NAME",
                   help="Read only this source (repeatable); an unknown name is a usage error")
    p.add_argument("--include-remote", action="store_true",
                   help="Also read the transcript presence of bridge sessions on remote targets (an SSH read each)")


def register_attention_commands(sub: argparse._SubParsersAction) -> None:
    att = sub.add_parser("attention", help="The ordered queue of what needs the operator, across sources")
    _add_read_flags(att)
    att.set_defaults(func=_cmd_attention)
    verbs = att.add_subparsers(dest="attention_verb")
    nxt = verbs.add_parser("next", help="The next item after a cursor (oldest worst first; wraps)")
    nxt.add_argument("--after", default=None, metavar="CURSOR", help="The cursor a previous `next` printed")
    _add_read_flags(nxt)
    nxt.set_defaults(func=_cmd_attention)
    dis = verbs.add_parser("dismiss", help="Hide an item until it changes (or --until a time, or --forever)")
    dis.add_argument("id", help="The item id (`<source>:<entity>:<entity_ref>`)")
    mode = dis.add_mutually_exclusive_group()
    mode.add_argument("--until", default=None, metavar="TIME", help="Hide it until this ISO-8601 time, even unchanged")
    mode.add_argument("--forever", action="store_true", help="Hide it until it is undismissed")
    dis.add_argument("--include-remote", action="store_true", help=argparse.SUPPRESS)
    undis = verbs.add_parser("undismiss", help="Show a dismissed item again")
    undis.add_argument("id")
    listed = verbs.add_parser("dismissed", help="List this machine's dismissals")
    listed.add_argument("--json", action="store_true", help="Print them as JSON")
    for p in (dis, undis, listed):
        p.set_defaults(func=_cmd_attention)
    source = verbs.add_parser("source", help="Register, remove or list command sources on this machine")
    sverbs = source.add_subparsers(dest="source_verb", required=True)
    add = sverbs.add_parser("add", help="Register a command source: source add NAME [--timeout S] -- ARGV...")
    add.add_argument("name")
    add.add_argument("--timeout", type=float, default=srcs.DEFAULT_TIMEOUT)
    add.add_argument("argv", nargs=argparse.REMAINDER, help="[--timeout S] -- the command and its arguments")
    rm = sverbs.add_parser("remove", help="Remove a registered command source")
    rm.add_argument("name")
    sverbs.add_parser("list", help="List built-in and registered sources, and rejected registrations")
    for p in (add, rm, *sverbs.choices.values()):
        p.set_defaults(func=_cmd_source)
