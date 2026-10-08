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
import sys
from typing import Any

from . import attention_contract as ac
from . import attention_sources as srcs
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


def _readers(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, float], list[dict[str, str]], set[str]]:
    registrations, config_errors = srcs.load_registrations()
    cli = _target_cli(args)
    readers: dict[str, Any] = {
        "dispatch": lambda read_at: srcs.read_dispatch(lambda: _core()._client(args), read_at, cli=cli)}
    timeouts = {"dispatch": srcs.DEFAULT_TIMEOUT}
    for name, spec in registrations.items():
        readers[name] = lambda read_at, n=name, s=spec: srcs.read_command(n, s, read_at)
        timeouts[name] = spec["timeout"] + 5.0  # the command's own timeout fires first
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
    return srcs.collect(readers, timeouts=timeouts, selected=selected,
                        config_errors=config_errors, store=FirstObserved())


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
    if getattr(args, "attention_verb", None) == "next":
        return _cmd_next(args)
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
    return 0


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
        print("    next: " + " ".join(_next_argv(args, envelope, result["cursor"])))
    return 0


def _next_argv(args: argparse.Namespace, envelope: dict[str, Any], cursor: str) -> list[str]:
    """The follow-up that walks the same queue: same coordinator, same sources."""
    argv = [*_target_cli(args), "attention", "next", "--after", cursor]
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


def register_attention_commands(sub: argparse._SubParsersAction) -> None:
    att = sub.add_parser("attention", help="The ordered queue of what needs the operator, across sources")
    _add_read_flags(att)
    att.set_defaults(func=_cmd_attention)
    verbs = att.add_subparsers(dest="attention_verb")
    nxt = verbs.add_parser("next", help="The next item after a cursor (oldest worst first; wraps)")
    nxt.add_argument("--after", default=None, metavar="CURSOR", help="The cursor a previous `next` printed")
    _add_read_flags(nxt)
    nxt.set_defaults(func=_cmd_attention)
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
