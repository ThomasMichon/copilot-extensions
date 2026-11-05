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
from managed_project_identity import _is_link_or_reparse, _validate_receipt, project_identity
from registry_root import resolve_registry_context, resolve_registry_root

_LIMIT = 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


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
    if not value or value in {"null", "Null", "NULL", "~"}:
        return None
    if value == "{}":
        return {}
    if value in {"true", "false"}:
        return value == "true"
    if value[0] in "[{&*!|>":
        raise ValueError("unsupported registry scalar")
    return value


def _entries(
    path: Path, section: str, *, fields: set[str] | None = None,
    required: bool = True, text: str | None = None,
) -> dict:
    """Read YAML with its normal parser, or the owner's generated map shape."""
    text = _read(path) if text is None else text
    fields = fields if fields is not None else (
        set() if section == "projects" else {"windows", "wsl", "linux", "remote"}
    )
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
            if key.startswith(("'", '"')):
                key = _scalar(key)
            if indent == 0:
                active, current = key == section, None
                if active:
                    if not sep or "\t" in raw[:indent + 1]:
                        raise ValueError("unsupported registry layout")
                    if section in document:
                        raise ValueError("duplicate registry section")
                    document[section] = {}
                    if value.strip() not in {"", "{}"}:
                        if not required and value.strip() in {"null", "~"}:
                            document[section] = None
                            active = False
                            continue
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
                and key in fields
            ):
                if not sep or "\t" in raw[:indent + 1]:
                    raise ValueError("unsupported registry layout")
                if key in current:
                    raise ValueError("duplicate project field")
                current[key] = _scalar(value)
        # Nested optional fields are not ownership inputs.
    entries = document.get(section) if isinstance(document, dict) else None
    if entries is None and not required:
        return {}
    if not isinstance(entries, dict):
        raise ValueError("registry is not a name map")
    return entries


def _stamp(path: str | Path, *, listing: bool = False) -> list[str] | None:
    try:
        info = Path(path).stat()
        if Path(path).is_dir():
            if listing:
                return ["listing", canonical(path), str(info.st_mtime_ns)]
            return ["directory", canonical(path)]
        return ["file", canonical(path), str(info.st_size), str(info.st_mtime_ns)]
    except OSError:
        return None


def _current(inputs: dict) -> bool:
    return all(
        _stamp(path, listing=bool(stamp and stamp[0] == "listing")) == stamp
        for path, stamp in inputs.items()
    )


def _config_values(path: Path, name: str, inputs: dict) -> dict:
    inputs[str(path)] = _stamp(path)
    if inputs[str(path)] is None:
        return {}
    text = _read(path)
    entries = _entries(path, "repos", fields={"anchor"}, required=False, text=text)
    row = entries.get(name)
    values = {"anchor": row["anchor"]} if isinstance(row, dict) and "anchor" in row else {}
    if text.lstrip().startswith("{"):
        document = json.loads(text)
        if "platform" in document:
            values["platform"] = document["platform"]
        if "repos" in document and document["repos"] is None:
            values["anchor"] = None
    else:
        for line in text.splitlines():
            if not line or line[0].isspace() or line.startswith("#"):
                continue
            key, sep, raw = line.partition(":")
            if not sep:
                continue
            key = _scalar(key)
            if key == "platform":
                values["platform"] = _scalar(raw)
            if key == "repos" and not entries:
                if _scalar(raw) is None:
                    values["anchor"] = None
    return values


def _anchor_candidates(
    name: str, repo: dict, registry: Path,
    selected: dict | None, platform: str, inputs: dict,
) -> list[str]:
    if selected is None:
        root = registry.parent / f".{name}"
    else:
        remote = repo.get("remote")
        if not isinstance(remote, str) or not remote:
            root = None
        else:
            identity = project_identity(name, remote, selected)
            root = identity.state_root
            inputs[str(root)] = _stamp(root)
            receipt = identity.repository_root / "identity.json"
            inputs[str(receipt)] = _stamp(receipt)
            if inputs[str(receipt)] is not None:
                _validate_receipt(identity)
            elif root.exists():
                raise ValueError("project state has no identity receipt")
    machine = {}
    conditional = []
    global_values = _config_values(registry / "config.yaml", name, inputs)
    if root is not None:
        inputs[str(root)] = _stamp(root)
        if selected is not None and _is_link_or_reparse(root):
            raise ValueError("project state is indirected")
        machine = _config_values(root / "config.yaml", name, inputs)
        fragments = root / "config.d"
        inputs[str(fragments)] = _stamp(fragments, listing=True)
        if fragments.is_dir():
            entries = sorted(fragments.iterdir())
            if len(entries) > 128:
                raise ValueError("project ownership fragments exceed their bound")
            for entry in entries:
                if entry.suffix.lower() in {".yaml", ".yml"}:
                    conditional.append(_config_values(entry, name, inputs))
                elif entry.suffix.lower() == ".json":
                    inputs[str(entry)] = _stamp(entry)
                    pointer = json.loads(_read(entry))
                    if not isinstance(pointer, dict) or pointer.get("schema_version") != 1:
                        continue
                    owner, target = pointer.get("plugin_root"), pointer.get("target")
                    if not isinstance(owner, str) or not isinstance(target, str):
                        continue
                    owner, target = Path(owner).expanduser(), Path(target).expanduser()
                    if not owner.is_absolute() or not target.is_absolute():
                        continue
                    inputs[str(owner)] = _stamp(owner)
                    inputs[str(target)] = _stamp(target)
                    if canonical(target).startswith(canonical(owner) + os.sep):
                        conditional.append(_config_values(target, name, inputs))
    default_platform = machine.get("platform") or global_values.get("platform") or platform
    platforms = {default_platform}
    if "platform" not in machine:
        platforms.update(row["platform"] for row in conditional if row.get("platform"))
    candidates = []
    for selected_platform in platforms:
        if selected_platform not in {"windows", "wsl", "linux"}:
            raise ValueError("project platform is unavailable")
        registered = repo.get(selected_platform)
        candidates.append(machine.get("anchor") or registered)
    if "anchor" not in machine:
        candidates.extend(row["anchor"] for row in conditional if row.get("anchor"))
    return candidates


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
        platform = detect_platform()
        scope = {"session": session, "workspace": workspace, "registry": canonical(registry),
                 "context": context, "platform": platform}
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
        for name, project in projects.items():
            if not isinstance(name, str) or not _PROJECT.fullmatch(name) or not isinstance(project, dict):
                continue
            result["inputs"] = workspace_inputs.copy()
            repo = repos.get(name)
            repo = repo if isinstance(repo, dict) else {}
            try:
                anchors = _anchor_candidates(name, repo, registry, selected, platform, result["inputs"])
            except (OSError, ValueError, TypeError):
                ownership_inputs.update(result["inputs"])
                continue
            owners = []
            for anchor in anchors:
                if not isinstance(anchor, str) or not os.path.isabs(os.path.expanduser(anchor)):
                    owners.append(None)
                    continue
                anchor = os.path.expanduser(anchor)
                candidate = repository_identity(anchor, observe=observe)
                owners.append(
                    candidate if candidate is not None and canonical(anchor) == candidate["root"] else None
                )
            owner_inputs = result["inputs"].copy()
            ownership_inputs.update(owner_inputs)
            result["inputs"] = ownership_inputs
            if not owners or any(
                owner is None or owner["common_dir"] != identity["common_dir"] for owner in owners
            ):
                continue
            owner = owners[0]
            inputs = owner_inputs
            # Only the adopted anchor or an actual Git-linked worktree belongs.
            if any(identity["root"] != candidate["root"] for candidate in owners):
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
