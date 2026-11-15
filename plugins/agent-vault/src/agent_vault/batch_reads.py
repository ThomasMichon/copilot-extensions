"""Ordered multi-read CLI using the ordinary credential read path."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

from .credential_reads import read_credential


def _invalid_constant(_value):
    raise ValueError("non-finite JSON number")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _requests(stream):
    requests = json.load(
        stream, object_pairs_hook=_unique_object, parse_constant=_invalid_constant,
    )
    if not isinstance(requests, list):
        raise ValueError("expected an array of entry/field objects")
    normalized = []
    for request in requests:
        if not isinstance(request, dict) or set(request) - {"entry", "field"}:
            raise ValueError("expected entry/field objects")
        entry = request.get("entry")
        field = request.get("field", "password")
        if not isinstance(entry, str) or not entry or not isinstance(field, str) or not field:
            raise ValueError("entry and field must be nonempty strings")
        normalized.append((entry, field))
    return normalized


def cmd_batch_get(args):
    from .cache_dpapi import CacheProtectionError
    from .kek import KekError

    try:
        requests = _requests(sys.stdin)
    except (ValueError, OSError, UnicodeError) as exc:
        print(f"Error: invalid batch input ({exc})", file=sys.stderr)
        return 1
    results = []
    for entry, field in requests:
        try:
            result = read_credential(SimpleNamespace(
                entry=entry, field=field, refresh=args.refresh, prompt=False, strict_cache=True,
            ))
        except (CacheProtectionError, KekError, ValueError):
            results.append({
                "entry": entry, "field": field, "value": None, "ok": False, "source": "cache",
                "error": "cache_protection_failed; protected cache requires repair before retrying",
            })
            continue
        except OSError:
            results.append({
                "entry": entry, "field": field, "value": None, "ok": False, "source": "cache",
                "error": "cache_read_failed; credential cache could not be read",
            })
            continue
        warning = getattr(result, "warning", None)
        if warning:
            print(f"Warning: {warning}", file=sys.stderr)
        ok = result.code == 0 and isinstance(result.value, str)
        record = {
            "entry": entry, "field": field, "value": result.value if ok else None,
            "ok": ok, "source": result.source,
        }
        if not ok:
            record["error"] = (
                result.error if isinstance(result.error, str)
                else "credential read returned an invalid value"
            )
        results.append(record)
    print(json.dumps({"results": results}, ensure_ascii=True))
    successes = sum(record["ok"] for record in results)
    return 0 if successes == len(results) else 2 if successes else 1


def add_commands(sub, get_handler):
    p = sub.add_parser("get", help="Read a credential")
    p.add_argument("entry", help="Entry path")
    p.add_argument("field", nargs="?", default="password",
                   help="Field to read (default: password)")
    p.add_argument("-p", "--prompt", action="store_true",
                   help="Prompt for the master password if locked "
                        "(default: fail fast and ask you to run 'agent-vault unlock')")
    p.add_argument("--refresh", action="store_true",
                   help="Bypass the persistent cache and fetch from the live vault")
    p.add_argument("--max-cache-age", type=int, default=None, dest="max_cache_age",
                   help="Use the persistent cache only when it is at most this many seconds old")
    p.add_argument("--cache-only", action="store_true", dest="cache_only",
                   help="Read from the persistent cache only; never contact the service")
    p.set_defaults(func=get_handler)

    p = sub.add_parser("batch-get", help="Read ordered entry/field requests from JSON stdin")
    p.add_argument("--refresh", action="store_true",
                   help="Bypass the persistent cache and reconcile live values")
    p.set_defaults(func=cmd_batch_get)
