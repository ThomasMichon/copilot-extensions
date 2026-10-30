"""Pick up Windows registry ``Path`` entries missing from an inherited ``PATH``.

A long-running service keeps the environment of the shell that started it, so
a tool whose installer later added its directory to the registry ``Path``
stays invisible to every process the service spawns, while any new shell finds
it. Environments built for spawned processes therefore append the registry
entries that the inherited ``PATH`` lacks. Inherited entries keep their order
and precedence, and a registry failure leaves ``PATH`` unchanged. Off Windows
this is a no-op.
"""

from __future__ import annotations

import logging
import ntpath
import sys

log = logging.getLogger("agent-bridge")

_MACHINE_ENVIRONMENT_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
_USER_ENVIRONMENT_KEY = "Environment"


def registry_path_entries() -> list[str]:
    """Return the machine, then the user, registry ``Path`` entries.

    ``REG_EXPAND_SZ`` values are expanded against the current process
    environment; ``REG_SZ`` values are used verbatim. A key or value that
    cannot be read is skipped.
    """
    if sys.platform != "win32":
        return []
    import winreg

    entries: list[str] = []
    for hive, subkey in (
        (winreg.HKEY_LOCAL_MACHINE, _MACHINE_ENVIRONMENT_KEY),
        (winreg.HKEY_CURRENT_USER, _USER_ENVIRONMENT_KEY),
    ):
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value, kind = winreg.QueryValueEx(key, "Path")
            if not isinstance(value, str):
                continue
            if kind == winreg.REG_EXPAND_SZ:
                value = winreg.ExpandEnvironmentStrings(value)
        except OSError:
            continue
        entries.extend(entry for entry in value.split(";") if entry.strip())
    return entries


def _entry_key(entry: str) -> str:
    return ntpath.normcase(entry.strip().strip('"')).rstrip("\\")


def append_registry_path(env: dict[str, str]) -> None:
    """Append the registry ``Path`` entries that ``env``'s ``PATH`` lacks.

    Entries match case-insensitively, ignoring trailing backslashes. Any
    failure leaves ``env`` unchanged.
    """
    try:
        name = next((key for key in env if key.upper() == "PATH"), "PATH")
        current = env.get(name, "")
        seen = {_entry_key(entry) for entry in current.split(";")}
        missing: list[str] = []
        for entry in registry_path_entries():
            key = _entry_key(entry)
            if key and key not in seen:
                seen.add(key)
                missing.append(entry)
    except Exception as error:
        # Best effort: the inherited PATH keeps working exactly as before.
        log.debug("Registry PATH refresh skipped: %s", type(error).__name__)
        return
    if missing:
        separator = "" if not current or current.endswith(";") else ";"
        env[name] = current + separator + ";".join(missing)
        log.info(
            "Appended %d PATH entries from the Windows registry that the "
            "inherited environment lacked", len(missing),
        )
