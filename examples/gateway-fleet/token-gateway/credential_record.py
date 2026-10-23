"""Keep a credential and its lifetime in one encrypted cache field."""

import json
import math


def valid_credential(token, expires_epoch):
    return (
        isinstance(token, str) and 0 < len(token) <= 16384
        and all(32 < ord(char) < 127 for char in token)
        and type(expires_epoch) in (int, float)
        and 0 < expires_epoch < 1e12 and math.isfinite(expires_epoch)
    )


def generation_for_expiry(expires_epoch):
    return int(expires_epoch * 1_000_000)


def store_credential(cache, entry, token, expires_epoch):
    if not valid_credential(token, expires_epoch):
        raise ValueError("Invalid credential record")
    record = json.dumps({"token": token, "expires_epoch": expires_epoch}, allow_nan=False)
    if not cache.put(entry, "credential", record, generation=generation_for_expiry(expires_epoch)):
        return False
    # put() may accept a stale no-op; never acknowledge it as current custody.
    return cache.get(entry, "credential") == record


def load_credential(cache, entry):
    raw = cache.get(entry, "credential")
    if raw is None:
        return None
    try:
        record = json.loads(raw)
    except (TypeError, ValueError, RecursionError):
        return None
    if (
        not isinstance(record, dict)
        or set(record) != {"token", "expires_epoch"}
        or not valid_credential(record["token"], record["expires_epoch"])
    ):
        return None
    return record
