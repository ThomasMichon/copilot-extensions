"""Passive exact-store discovery and efforts-owned reads, across process boundaries."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
import sys
from threading import Event
from typing import Any

from . import agenda_transport as transport, engine_client as ec
from .companion_timeline_contract import TimelineContractError
from .companion_timeline_controller import TimelineCleanupError
from .companion_worktree_codec import OwnerReadError, generation, object_fields
from .engine_process import EngineCleanupError

_SELECT = r"""
import json,sys
from pathlib import Path
from plugin_activation import resolve_active_plugins
try:
    report=resolve_active_plugins(plugin_names=("efforts",),project_root=Path(sys.argv[1]))
    if report.authority.value != "complete" or len(report.decisions)!=1 or len(report.active)!=1:
        raise ValueError("one unambiguous enabled efforts payload is required")
    plugin=next(iter(report.active.values()))
    root=plugin.root_for_scope("project:current") or plugin.root_for_scope("global")
    if root is None:
        raise ValueError("efforts payload has no selected activation scope")
    script=root/"skills/driving-effort-agendas/scripts/agenda_history_cli.py"
    for member in (root,*[root/p for p in script.relative_to(root).parents],script):
        if member.is_symlink() or getattr(member.lstat(),"st_reparse_tag",0):
            raise ValueError("rich reader path is indirect")
    if not script.is_file():
        raise ValueError("selected efforts payload has no rich reader")
    result={"ok":True,"source":plugin.source,"script":str(script)}
except (OSError,ValueError,RuntimeError) as error:
    result={"ok":False,"code":"unsupported","message":str(error)[:1024]}
print(json.dumps(result),flush=True)
"""

_PLACEMENT = r"""
import json,sys
try:
    from agent_worktrees import worktree_data_cli as contract
    if (getattr(contract,"CAPABILITY",None)!="worktree-data-v1"
        or getattr(contract,"VERSION",None)!=1
        or "worktree-data-read" not in getattr(contract,"COMMANDS",())):
        raise ImportError("selected owner does not declare passive worktree-data-v1")
    from agent_worktrees.__main__ import main,build_parser
    arguments=["--project",sys.argv[1],"worktree-data-read","--worktree",sys.argv[2],
        "--store-id",sys.argv[3],"--contract-version","1","--json",*sys.argv[4:]]
    build_parser().parse_args(arguments[2:])
except (ImportError,SystemExit) as error:
    print(json.dumps({"ok":False,"code":"unsupported","message":str(error)[:1024]}),flush=True)
else:
    main(arguments)
"""

_READ = r"""
import json,sys
from pathlib import Path
try:
    script=Path(sys.argv[1])
    if not script.is_absolute() or script.is_symlink() or script.resolve()!=script:
        raise ValueError("selected rich reader changed its ordinary absolute path")
    sys.path.insert(0,str(script.parent))
    import agenda_history_contract as wire
    import agenda_history_cli as reader
    if (wire.VERSION!=1 or wire.CAPABILITY!="agenda-history-page-v1"
        or not {"reader-info","history-page"} <= set(reader.COMMANDS)):
        raise ImportError("selected efforts reader lacks agenda-history-page-v1")
except (ImportError,OSError,ValueError,AttributeError) as error:
    print(json.dumps({"ok":False,"code":"unsupported","message":str(error)[:1024]}),flush=True)
else:
    raise SystemExit(reader.main(sys.argv[2:]))
"""


def _point(argv: list[str], cancelled: Event, cwd: str) -> dict[str, Any]:
    stream = transport.command_frames(argv, cancelled=cancelled, cwd=cwd)
    try:
        value = next(stream, None)
        if value is None:
            raise InterruptedError("rich agenda read stopped")
        if value.get("ok") is False and set(value) == {"ok", "code", "message"}:
            raise OwnerReadError(str(value["code"]), str(value["message"]))
        return value
    except EngineCleanupError as error:
        raise TimelineCleanupError(str(error)) from error
    except transport.AgendaUnavailable as error:
        raise OwnerReadError(error.code or "unavailable", str(error)) from error
    finally:
        try:
            stream.close()
        except EngineCleanupError as error:
            raise TimelineCleanupError(str(error)) from error


class AgendaCommands:
    def __init__(self, project: str, worktree: str, store_id: str, *, cwd: str) -> None:
        self.project, self.worktree, self.store_id, self.cwd = project, worktree, generation(store_id), cwd
        self.base: list[str] | None = None
        self.script: str | None = None
        self.placement: dict[str, Any] | None = None

    def discover(self, cancelled: Event) -> None:
        if self.base is None:
            base = ec.engine_base_command()
            if not base or not Path(base[0]).is_absolute():
                raise OwnerReadError("unsupported", "no absolute selected placement owner")
            transport.validate_agenda_entry(base)
            if base[-2:] not in (["-m", "agent_worktrees"], ["-m", "agent_worktrees.__main__"]):
                raise OwnerReadError("unsupported", "selected placement owner has no passive module probe")
            self.base = list(base)
        expected = self.placement
        fences = [] if expected is None else [
            "--creation-nonce", expected["creation_nonce"],
            "--placement-generation", str(expected["placement_generation"]),
        ]
        reply = _point(
            [*self.base[:-2], "-I", "-B", "-c", _PLACEMENT,
             self.project, self.worktree, self.store_id, *fences], cancelled, self.cwd,
        )
        if reply.get("ok") is False:
            error = object_fields(reply.get("error"), {"code", "message"})
            raise OwnerReadError(str(error["code"]), str(error["message"]))
        object_fields(reply, {"schema", "version", "ok", "project", "worktree_id", "availability", "data"})
        if (
            reply["schema"] != "agent-worktrees.worktree-data"
            or type(reply["version"]) is not int or reply["version"] != 1
            or reply["ok"] is not True or reply["project"] != self.project
            or reply["worktree_id"] != self.worktree
        ):
            raise TimelineContractError("placement owner changed the selected source")
        if reply["availability"] != "registered":
            raise OwnerReadError("not-registered", "selected agenda has no admitted managed placement")
        inventory = object_fields(reply["data"], {
            "schema", "version", "scope", "worktree_id", "creation_nonce",
            "placements", "canonical_record", "live_authority",
        })
        if (
            inventory["schema"] != "copilot-extensions.worktree-data"
            or type(inventory["version"]) is not int or inventory["version"] != 1
            or inventory["worktree_id"] != self.worktree or inventory["live_authority"] is not False
            or not isinstance(inventory["placements"], list) or len(inventory["placements"]) != 1
        ):
            raise TimelineContractError("placement inventory is not an exact single-store read")
        placement = object_fields(inventory["placements"][0], {
            "scope", "worktree_id", "creation_nonce", "domain", "lane", "store_id",
            "placement_generation", "state_home", "state_home_allocated",
            "store_content_validated", "live_authority",
        })
        scope = object_fields(placement["scope"], {
            "project", "repository", "repository_id", "marketplace_id",
            "project_root_device", "project_root_inode",
        })
        if (
            scope["project"] != self.project or scope["repository"] != self.project
            or placement["scope"] != inventory["scope"]
            or placement["creation_nonce"] != inventory["creation_nonce"]
            or placement["worktree_id"] != self.worktree or placement["store_id"] != self.store_id
            or placement["domain"] != "agendas" or placement["state_home_allocated"] is not True
            or placement["store_content_validated"] is not False or placement["live_authority"] is not False
            or type(placement["placement_generation"]) is not int or placement["placement_generation"] < 1
            or any(not isinstance(placement[key], str) or re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}", placement[key]) is None
                   for key in ("creation_nonce", "lane"))
            or not isinstance(placement["state_home"], str) or "\x00" in placement["state_home"]
            or not Path(placement["state_home"]).is_absolute()
        ):
            raise TimelineContractError("invalid exact agenda placement qualification")
        if expected is not None and placement != expected:
            raise OwnerReadError("source-replaced", "admitted agenda placement changed; Refresh cannot erase its fence")
        if self.script is None:
            selected = _point([sys.executable, "-I", "-B", "-c", _SELECT, self.cwd], cancelled, self.cwd)
            object_fields(selected, {"ok", "source", "script"})
            if (
                selected["ok"] is not True or not isinstance(selected["source"], str)
                or not selected["source"].startswith("efforts@")
                or not isinstance(selected["script"], str) or not Path(selected["script"]).is_absolute()
            ):
                raise TimelineContractError("invalid attributed efforts reader")
            self.script = selected["script"]
        self.placement = deepcopy(placement)

    def read(self, cancelled: Event, *, history: bool = False, before: str | None = None) -> dict[str, Any]:
        if self.placement is None or self.script is None:
            raise OwnerReadError("unsupported", "rich agenda discovery has not been admitted")
        return _point([
            sys.executable, "-I", "-B", "-c", _READ, self.script,
            "history-page" if history else "reader-info",
            "--state-home", self.placement["state_home"], "--worktree", self.worktree,
            "--store-id", self.store_id, "--contract-version", "1", "--json",
            *(["--limit", "64"] if history else []),
            *(["--before-cursor", before] if before is not None else []),
        ], cancelled, self.cwd)
