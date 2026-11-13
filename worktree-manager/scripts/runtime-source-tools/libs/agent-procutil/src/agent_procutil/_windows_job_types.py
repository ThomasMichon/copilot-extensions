"""Native Job accounting layout, loaded only by a native operation."""

from __future__ import annotations

import threading

_type_lock = threading.Lock()


def __getattr__(name: str):
    if name != "_JobBasicAccountingInformation":
        raise AttributeError(name)
    with _type_lock:
        if name not in globals():
            from . import _JobBasicAccountingInformation

            globals()[name] = _JobBasicAccountingInformation
        return globals()[name]
