"""Passive project membership before automatic Worktrees behavior."""

from __future__ import annotations

import hashlib
import base64
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from guarded_anchor_map import canonical, repository_identity
from execution_platform import detect_platform
from registry_root import resolve_registry_context, resolve_registry_root

_LIMIT = 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _read(path: Path) -> str:
    with path.open("rb") as stream:
        raw = stream.read(_LIMIT + 1)
    if len(raw) > _LIMIT:
        raise ValueError("membership input exceeds its bound")
    return raw.decode("utf-8-sig")


def _scalar(value: str):
    value = value.strip()
    if value.startswith('"'):
        return json.loads(value)
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    value = value.split(" #", 1)[0].strip()
    if not value or value == "{}":
        return {}
    if value in {"true", "false"}:
        return value == "true"
    if value[0] in "[{&*!|>":
        raise ValueError("unsupported registry scalar")
    return value


def _entries(path: Path, section: str) -> dict:
    """Read YAML with its normal parser, or the owner's generated map shape."""
    text = _read(path)
    try:
        import yaml
    except ImportError:
        yaml = None
    if yaml is not None:
        class Loader(yaml.SafeLoader):
            pass

        def mapping(loader, node):
            result = {}
            for key, value in node.value:
                key = loader.construct_object(key)
                if key in result:
                    raise ValueError("duplicate membership key")
                result[key] = loader.construct_object(value)
            return result

        Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
        document = yaml.load(text, Loader=Loader)
    elif text.lstrip().startswith("{"):
        document = json.loads(text)
    else:
        # Both registry owners publish two-space name maps and scalar fields.
        document, current, active = {}, None, False
        for raw in text.splitlines():
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            key, sep, value = raw.strip().partition(":")
            if indent == 0:
                active, current = key == section, None
                if active:
                    if not sep or "\t" in raw[:indent + 1]:
                        raise ValueError("unsupported registry layout")
                    if section in document:
                        raise ValueError("duplicate registry section")
                    document[section] = {}
                    if value.strip() not in {"", "{}"}:
                        raise ValueError("unsupported registry map")
            elif active and indent == 2:
                if not sep or "\t" in raw[:indent + 1]:
                    raise ValueError("unsupported registry layout")
                name = _scalar(key)
                if not isinstance(name, str) or name in document[section]:
                    raise ValueError("invalid or duplicate project name")
                if value.strip() not in {"", "{}"}:
                    raise ValueError("unsupported project entry")
                current = document[section][name] = {}
            elif (
                active and indent == 4 and current is not None
                and key in ({"anchor"} if section == "projects" else {"windows", "wsl", "linux"})
            ):
                if not sep or "\t" in raw[:indent + 1]:
                    raise ValueError("unsupported registry layout")
                if key in current:
                    raise ValueError("duplicate project field")
                current[key] = _scalar(value)
        # Nested optional fields are not ownership inputs.
    entries = document.get(section) if isinstance(document, dict) else None
    if not isinstance(entries, dict):
        raise ValueError("registry is not a name map")
    return entries


def _stamp(path: str | Path) -> list[str] | None:
    try:
        info = Path(path).stat()
        if Path(path).is_dir():
            return ["directory", canonical(path)]
        return ["file", canonical(path), str(info.st_size), str(info.st_mtime_ns)]
    except OSError:
        return None


def _current(inputs: dict) -> bool:
    return all(_stamp(path) == stamp for path, stamp in inputs.items())


def inspect(payload: dict, *, home: Path | None = None) -> dict:
    """Return membership facts, never a tool permission or lifecycle result."""
    result = {"admitted": False, "inputs": {}}
    try:
        if not isinstance(payload, dict):
            return result
        session = payload.get("sessionId", os.environ.get("COPILOT_AGENT_SESSION_ID"))
        cwd = payload.get("cwd", payload.get("workingDirectory", os.environ.get("COPILOT_PROJECT_DIR")))
        if (
            not isinstance(session, str) or not _ID.fullmatch(session)
            or not isinstance(cwd, str) or not os.path.isabs(cwd) or not Path(cwd).is_dir()
        ):
            return result
        workspace = canonical(cwd)
        context = os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "")
        selected = resolve_registry_context() if context else None
        registry = Path(selected["pluginRoot"]) if selected is not None else resolve_registry_root(
            legacy_root=home / ".agent-worktrees" if home is not None else None,
        )
        if not registry.is_absolute():
            return result
        sources = [registry / "projects.yaml", registry / "repos.yaml"]
        inputs = {str(path): _stamp(path) for path in sources}
        if context:
            inputs[context] = _stamp(context)
            for name in ("namespaceReceipt", "installReceipt", "payloadRoot", "cellRoot", "pluginRoot"):
                value = selected[name]
                inputs[value] = _stamp(value)
            manifest = Path(selected["payloadRoot"]) / "plugin.json"
            inputs[str(manifest)] = _stamp(manifest)
        source_inputs = inputs.copy()
        result.update(workspace=workspace, inputs=inputs.copy())
        scope = {"session": session, "workspace": workspace, "registry": canonical(registry), "context": context}
        key = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
        cache = registry / "cache" / "managed-sessions" / f"{key}.json"
        def observe(path: Path) -> None:
            result["inputs"][str(path)] = _stamp(path)

        identity = repository_identity(cwd, observe=observe)
        try:
            cached = json.loads(_read(cache))
            if (
                cached.get("version") == 1 and cached.get("scope") == scope
                and cached.get("identity") == identity and identity is not None
                and cached.get("sources") == source_inputs
                and isinstance(cached.get("inputs"), dict)
                and all(cached["inputs"].get(path) == stamp for path, stamp in source_inputs.items())
                and _current(cached["inputs"])
            ):
                return {"admitted": True, "workspace": workspace, "inputs": cached["inputs"]}
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            pass
        if identity is None:
            return result
        if inputs[str(sources[0])] is None:
            return result
        projects = _entries(sources[0], "projects")
        repos = _entries(sources[1], "repos") if inputs[str(sources[1])] is not None else {}
        workspace_inputs = result["inputs"].copy()
        ownership_inputs = workspace_inputs.copy()
        platform = detect_platform()
        platforms = ("wsl", "linux") if platform == "wsl" else (platform,)
        for name, project in projects.items():
            if not isinstance(project, dict):
                continue
            repo = repos.get(name)
            anchor = next(
                (repo[key] for key in platforms if isinstance(repo, dict) and isinstance(repo.get(key), str) and repo[key]),
                project.get("anchor"),
            )
            if not isinstance(anchor, str):
                continue
            anchor = os.path.expanduser(anchor)
            if not os.path.isabs(anchor):
                continue
            result["inputs"] = workspace_inputs.copy()
            owner = repository_identity(anchor, observe=observe)
            owner_inputs = result["inputs"].copy()
            ownership_inputs.update(owner_inputs)
            result["inputs"] = ownership_inputs
            if owner is None or canonical(anchor) != owner["root"] or owner["common_dir"] != identity["common_dir"]:
                continue
            inputs = owner_inputs
            # Only the adopted anchor or an actual Git-linked worktree belongs.
            if identity["root"] != owner["root"]:
                git_dir = Path(identity["git_dir"])
                if git_dir.parent != Path(owner["common_dir"]) / "worktrees":
                    continue
                backlink = git_dir / "gitdir"
                inputs[str(backlink)] = _stamp(backlink)
                ownership_inputs.update(inputs)
                target = Path(_read(backlink).strip())
                if not target.is_absolute():
                    target = git_dir / target
                if canonical(target) != canonical(Path(identity["root"]) / ".git"):
                    continue
            if not _current(inputs):
                result["inputs"] = ownership_inputs
                return result
            record = {
                "version": 1, "scope": scope, "identity": identity,
                "sources": source_inputs,
                "inputs": inputs,
            }
            # Cache publication is optional; admission itself is read-only.
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    json.dump(record, stream)
                try:
                    os.replace(temporary, cache)
                finally:
                    temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return {"admitted": True, "workspace": workspace, "inputs": inputs}
        result["inputs"] = ownership_inputs
        return result
    except Exception:
        return result


def admitted(payload: dict, *, home: Path | None = None) -> bool:
    """Whether an automatic event has positively established project ownership."""
    return inspect(payload, home=home)["admitted"]


if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["--check"], ["--base64-input"]):
        raise SystemExit(2)
    owned = sys.argv[1:] == ["--base64-input"]
    try:
        if owned:
            raw = base64.b64decode(sys.stdin.buffer.readline(_LIMIT * 2).rstrip(b"\r\n"), validate=True)
            from hook_policy_process import _watch_caller

            threading.Thread(target=_watch_caller, daemon=True).start()
        else:
            raw = sys.stdin.buffer.read(_LIMIT + 1)
        if len(raw) > _LIMIT:
            raise ValueError("membership query exceeds its bound")
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        result = {"admitted": False, "inputs": {}}
    else:
        result = inspect(payload)
    if sys.argv[1:] == ["--check"]:
        raise SystemExit(0 if result["admitted"] else 1)
    print(json.dumps(result, separators=(",", ":")))
    if owned:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)
