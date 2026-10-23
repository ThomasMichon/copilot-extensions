"""Exact owned completed-candidate removal, never installation retirement."""

from contextlib import ExitStack
from dataclasses import asdict
import os
import re
import sqlite3
import stat
import uuid

from . import artifact_staging, client_activation as activation, client_admission
from . import client_construction as construction, client_frontdoor as frontdoor
from . import staging_cleanup as cleanup
from .planning import PlanError, canonical, digest, record

HISTORY = "client-runtime-removals"
OUTCOMES = "client-runtime-removed"
FIELDS = {"identity", "candidate", "removal", "input", "completion", "tree", "claims"}


def _name(name):
    if not isinstance(name, str) or not name or name in (".", "..") or any(
        character in name for character in ("/", "\\", "\0", ":")
    ):
        raise PlanError("client runtime removal has an unsafe tree member")
    return name


def _same(actual, expected):
    if expected is not None and actual != expected:
        raise PlanError("client runtime removal preserves a changed or foreign object")


def _children(actual, expected):
    if expected is not None and not actual <= expected.keys():
        raise PlanError("client runtime removal preserves new or foreign tree members")


def _ordered(names):
    return sorted(names, key=lambda name: (
        name == ".install-complete.json", name == construction.COMPLETION, name,
    ))


def _validate_tree(value, publication, depth=0):
    if not isinstance(value, dict) or depth > 32:
        raise PlanError("client runtime removal has invalid retained physical evidence")
    kind = value.get("kind")
    if kind == "file":
        if set(value) != {"kind", "state"}:
            raise PlanError("client runtime removal has invalid file evidence")
        publication.PhysicalState.parse(value["state"])
        return
    fields = {"kind", "identity", "mode", "children"} if kind == "directory" else {
        "kind", "identity", "mode", "size", "modified", "target",
    }
    if kind not in ("directory", "link") or set(value) != fields or (
        not isinstance(value["identity"], list) or len(value["identity"]) != 2
        or any(type(item) is not int or item < 0 for item in value["identity"])
        or type(value["mode"]) is not int
    ):
        raise PlanError("client runtime removal has invalid entry evidence")
    if kind == "directory":
        if not stat.S_ISDIR(value["mode"]) or not isinstance(value["children"], dict):
            raise PlanError("client runtime removal has invalid directory evidence")
        for name, child in value["children"].items():
            _name(name)
            _validate_tree(child, publication, depth + 1)
    elif (
        not stat.S_ISLNK(value["mode"]) or not isinstance(value["target"], str)
        or not value["target"] or "\0" in value["target"]
        or any(type(value[name]) is not int or value[name] < 0 for name in ("size", "modified"))
    ):
        raise PlanError("client runtime removal has invalid link evidence")


def _flatten(tree):
    nodes = []

    def visit(value, path):
        nodes.append({"path": path, "entry": {
            key: item for key, item in value.items() if key != "children"
        }})
        if value["kind"] == "directory":
            for name, child in value["children"].items():
                visit(child, [*path, name])

    visit(tree, [])
    if len(nodes) > 16384:
        raise PlanError("client runtime removal tree exceeds its entry bound")
    # Shallow bounded pages preserve the existing JSON depth/collection limits.
    return [nodes[index:index + 128] for index in range(0, len(nodes), 128)]


def _inflate(pages, publication):
    if not isinstance(pages, list) or not pages or len(pages) > 128:
        raise PlanError("client runtime removal has invalid physical evidence pages")
    root = None
    for page in pages:
        if not isinstance(page, list) or not 1 <= len(page) <= 128:
            raise PlanError("client runtime removal has invalid physical evidence page")
        for item in page:
            if not isinstance(item, dict) or set(item) != {"path", "entry"} or (
                not isinstance(item["path"], list) or len(item["path"]) > 32
                or not isinstance(item["entry"], dict) or "children" in item["entry"]
            ):
                raise PlanError("client runtime removal has invalid retained physical evidence")
            path = [_name(name) for name in item["path"]]
            entry = dict(item["entry"])
            if entry.get("kind") == "directory":
                entry["children"] = {}
            if not path:
                if root is not None:
                    raise PlanError("client runtime removal has duplicate tree roots")
                root = entry
                continue
            parent = root
            for component in path[:-1]:
                if not isinstance(parent, dict) or parent.get("kind") != "directory":
                    raise PlanError("client runtime removal has an invalid tree parent")
                parent = parent["children"].get(component)
            if not isinstance(parent, dict) or parent.get("kind") != "directory" or (
                path[-1] in parent["children"]
            ):
                raise PlanError("client runtime removal has an invalid or duplicate tree entry")
            parent["children"][path[-1]] = entry
    _validate_tree(root, publication)
    if root["kind"] != "directory":
        raise PlanError("client runtime removal requires an owned directory root")
    return root


def _posix_node(parent, name, publication, expected=None, *, depth=0):
    if depth > 32:
        raise PlanError("client runtime removal tree exceeds its depth bound")
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if info.st_uid != os.geteuid():
        raise PlanError("client runtime removal requires physically owned entries")
    if stat.S_ISLNK(info.st_mode):
        actual = {
            "kind": "link", "identity": [info.st_dev, info.st_ino], "mode": info.st_mode,
            "size": info.st_size, "modified": info.st_mtime_ns,
            "target": os.readlink(name, dir_fd=parent),
        }
        _same(actual, expected)
        return actual
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise PlanError("client runtime removal refuses nonordinary entries")
    descriptor = os.open(
        name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        | (os.O_DIRECTORY if stat.S_ISDIR(info.st_mode) else 0), dir_fd=parent,
    )
    try:
        opened = os.fstat(descriptor)
        _same((opened.st_dev, opened.st_ino, opened.st_mode),
              (info.st_dev, info.st_ino, info.st_mode))
        if stat.S_ISREG(opened.st_mode):
            actual = {"kind": "file", "state": asdict(publication.state_fd(descriptor))}
            _same(actual, expected)
        else:
            actual = {"kind": "directory", "identity": [opened.st_dev, opened.st_ino],
                      "mode": opened.st_mode}
            if expected is not None:
                _same(actual, {key: value for key, value in expected.items() if key != "children"})
            names = {_name(value) for value in os.listdir(descriptor)}
            children = expected["children"] if expected is not None else None
            _children(names, children)
            actual["children"] = {
                child: _posix_node(
                    descriptor, child, publication,
                    children[child] if children is not None else None,
                    depth=depth + 1,
                ) for child in _ordered(names)
            }
        return actual
    finally:
        os.close(descriptor)


def _claim_key(path):
    return digest(path)


def _present(parent, name):
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _posix_claimed_node(parent, name, publication, expected, claims, path, visited, *, remove=False):
    key = _claim_key(path)
    claimed = _present(claims, key)
    present = _present(parent, name)
    if claimed and present:
        raise PlanError("client runtime removal preserves a replacement beside its claimed object")
    if not (claimed or present):
        return
    if remove and not claimed:
        # The single cleanup owner captures the name into its private receipt
        # namespace first. A replacement won by this rename is validated there,
        # never deleted on the strength of an older descriptor.
        os.rename(name, key, src_dir_fd=parent, dst_dir_fd=claims)
        os.fsync(parent)
        os.fsync(claims)
        claimed = True
    if claimed:
        visited.add(key)
        parent, name = claims, key
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if info.st_uid != os.geteuid():
        raise PlanError("client runtime removal preserves an entry no longer owned by this principal")
    if expected["kind"] != "directory":
        _posix_node(parent, name, publication, expected)
        if remove:
            os.unlink(name, dir_fd=claims)
            os.fsync(claims)
        return
    if not stat.S_ISDIR(info.st_mode):
        raise PlanError("client runtime removal preserves a changed claimed directory")
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        opened = os.fstat(descriptor)
        _same({"kind": "directory", "identity": [opened.st_dev, opened.st_ino],
               "mode": opened.st_mode},
              {field: value for field, value in expected.items() if field != "children"})
        _children(set(os.listdir(descriptor)), expected["children"])
        for child in _ordered(expected["children"]):
            _posix_claimed_node(
                descriptor, child, publication, expected["children"][child], claims,
                [*path, child], visited, remove=remove,
            )
        if remove:
            os.fsync(descriptor)
            os.rmdir(name, dir_fd=claims)
            os.fsync(claims)
    finally:
        os.close(descriptor)


def _posix_claimed_tree(slot, publication, expected, claims_path, identity, *, remove=False):
    artifact_staging._directory(claims_path)
    with ExitStack() as pinned:
        parent = os.open(slot.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        pinned.callback(os.close, parent)
        claims = os.open(claims_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        pinned.callback(os.close, claims)
        opened = os.fstat(claims)
        _same([opened.st_dev, opened.st_ino], identity)
        if opened.st_uid != os.geteuid() or stat.S_IMODE(opened.st_mode) & 0o077:
            raise PlanError("client runtime removal claims are not private to the owner")
        present = set(os.listdir(claims))
        visited = set()
        _posix_claimed_node(
            parent, slot.name, publication, expected, claims, [], visited, remove=remove,
        )
        if (not remove and present != visited) or (remove and os.listdir(claims)):
            raise PlanError("client runtime removal preserves unattributed or orphaned claimed objects")


def _windows_node(path, publication, expected=None, *, remove=False, depth=0):
    if depth > 32:
        raise PlanError("client runtime removal tree exceeds its depth bound")
    with cleanup._windows_entry(path, delete=remove) as (kernel, handle, descriptor, info):
        if stat.S_ISDIR(info.st_mode):
            client_admission._windows_private(path)
            actual = {"kind": "directory", "identity": list(cleanup._windows_identity(kernel, handle)),
                      "mode": info.st_mode}
            if expected is not None:
                _same(actual, {key: value for key, value in expected.items() if key != "children"})
            names = {_name(entry.name) for entry in path.iterdir()}
            children = expected["children"] if expected is not None else None
            _children(names, children)
            actual["children"] = {
                name: _windows_node(
                    path / name, publication, children[name] if children is not None else None,
                    remove=remove, depth=depth + 1,
                ) for name in _ordered(names)
            }
        else:
            actual = {"kind": "file", "state": asdict(publication.state_fd(descriptor))}
            _same(actual, expected)
        if remove:
            if actual["kind"] == "file":
                _same(asdict(publication.state_fd(descriptor)), actual["state"])
            cleanup._windows_delete(kernel, handle)
        return actual


def _tree(slot, publication, expected=None, *, remove=False):
    artifact_staging._directory(slot.parent)
    with ExitStack() as pinned:
        if os.name == "nt":
            for path in reversed(slot.parents):
                pinned.enter_context(cleanup._windows_handle(path))
            return _windows_node(slot, publication, expected, remove=remove)
        parent = os.open(slot.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        pinned.callback(os.close, parent)
        if remove:
            raise PlanError("POSIX client deletion requires its private claimed-object namespace")
        return _posix_node(parent, slot.name, publication, expected)


def _quiesced(namespace):
    if any(os.path.lexists(namespace / name) for name in (
        "current", *activation.SELECTORS, activation.ACTIVE, activation.PENDING, activation.REMOVING,
        frontdoor.REGISTERED, frontdoor.PENDING, frontdoor.REMOVING,
    )):
        raise PlanError("client runtime removal requires settled command and selector withdrawal first")


def _directory(path):
    if not os.path.lexists(path):
        path.mkdir(mode=0o700)
        client_admission._sync_directory(path.parent)
    artifact_staging._directory(path)
    return path


def _write(path, value):
    construction._write(path, value)


def _binding(namespace, identity, candidate, value):
    record(value, "fleet-manager.client-construction-input",
           {"identity", "selection", "selection_digest"})
    selection = client_admission._selection(value["selection"], identity)
    if value["identity"] != identity or (
        candidate != f"client-{selection['sequence']}-{selection['release_digest']}"
        or value["selection_digest"] != digest(selection)
    ):
        raise PlanError("client runtime removal has a foreign construction binding")
    construction._completed_intent(namespace, candidate, value)


def _intent(namespace, identity, candidate, publication):
    path = namespace / construction.REMOVING
    if not os.path.lexists(path):
        return None
    value = construction._read(path)
    record(value, "fleet-manager.client-runtime-removal-intent", FIELDS)
    if (
        value["identity"] != identity or value["candidate"] != candidate
        or not isinstance(value["removal"], str)
        or re.fullmatch("[0-9a-f]{32}", value["removal"]) is None
    ):
        raise PlanError("client runtime removal requires its exact installation and candidate")
    directory = namespace / HISTORY / value["removal"]
    artifact_staging._directory(directory)
    _same(construction._read(directory / "intent.json"), value)
    binding = value["input"]
    _binding(namespace, identity, candidate, binding)
    record(value["completion"], "fleet-manager.client-constructed", {"input", "runtime_sha256"})
    if value["completion"]["input"] != binding or (
        not isinstance(value["completion"]["runtime_sha256"], str)
        or re.fullmatch("[0-9a-f]{64}", value["completion"]["runtime_sha256"]) is None
    ):
        raise PlanError("client runtime removal has invalid retained completion evidence")
    _inflate(value["tree"], publication)
    if os.name == "nt":
        _same(value["claims"], None)
    else:
        claims = directory / "claims"
        _same(list(artifact_staging._directory(claims)), value["claims"])
    return value


def _outcome(value, identity, candidate):
    record(value, "fleet-manager.client-runtime-removed",
           {"identity", "candidate", "removal", "intent_sha256"})
    if (
        value["identity"] != identity or value["candidate"] != candidate
        or not isinstance(value["removal"], str)
        or re.fullmatch("[0-9a-f]{32}", value["removal"]) is None
        or not isinstance(value["intent_sha256"], str)
        or re.fullmatch("[0-9a-f]{64}", value["intent_sha256"]) is None
    ):
        raise PlanError("absent client candidate has no owned removal outcome")
    return value


def remove_client_runtime(root, identity, *, candidate):
    """Delete exactly one completed client only after owned withdrawal has settled."""
    identity = client_admission._identity(identity)
    if not isinstance(candidate, str) or re.fullmatch(
        r"client-[1-9][0-9]*-[0-9a-f]{64}", candidate,
    ) is None:
        raise PlanError("client runtime removal requires an exact constructed candidate")
    removing = False
    reused = False
    try:
        with (
            client_admission._owned(root) as (path, database_root, unchanged),
            client_admission._connection(path, database_root=database_root, readonly=False) as connection,
            artifact_staging._primitive() as primitive,
        ):
            connection.execute("BEGIN IMMEDIATE")
            client_admission._read_state(connection, identity)
            namespace = path / construction.NAMESPACE
            artifact_staging._directory(namespace)
            construction._namespace(path, identity)
            publication = primitive._runtime_component("versioned_runtime_publication")

            def remove(slot):
                nonlocal removing, reused
                _quiesced(namespace)
                unchanged()
                value = _intent(namespace, identity, candidate, publication)
                if value is None and not os.path.lexists(slot):
                    artifact_staging._directory(namespace / OUTCOMES)
                    outcome = _outcome(
                        construction._read(namespace / OUTCOMES / (candidate + ".json")),
                        identity, candidate,
                    )
                    history = namespace / HISTORY / outcome["removal"]
                    artifact_staging._directory(history)
                    saved = construction._read(history / "intent.json")
                    _same(construction._read(history / "outcome.json"), outcome)
                    record(saved, "fleet-manager.client-runtime-removal-intent", FIELDS)
                    _binding(namespace, identity, candidate, saved["input"])
                    if digest(saved) != outcome["intent_sha256"] or (
                        saved["identity"], saved["candidate"], saved["removal"]
                    ) != (identity, candidate, outcome["removal"]):
                        raise PlanError("client runtime removal outcome differs from its intent")
                    reused = True
                    return
                if value is None:
                    binding = construction._read(namespace / "client-inputs" / (candidate + ".json"))
                    _binding(namespace, identity, candidate, binding)
                    completed = construction._completion(primitive, namespace, candidate, binding)
                    snapshot = _tree(slot, publication)
                    _same(construction._completion(primitive, namespace, candidate, binding), completed)
                    _same(_tree(slot, publication), snapshot)
                    value = {
                        "schema": "fleet-manager.client-runtime-removal-intent", "version": 1,
                        "identity": identity, "candidate": candidate, "removal": uuid.uuid4().hex,
                        "input": binding, "completion": completed, "tree": _flatten(snapshot),
                        "claims": None,
                    }
                    canonical(value)
                    history = _directory(namespace / HISTORY) / value["removal"]
                    history.mkdir(mode=0o700)
                    client_admission._sync_directory(history.parent)
                    if os.name != "nt":
                        claims = _directory(history / "claims")
                        value["claims"] = list(artifact_staging._directory(claims))
                    _write(history / "intent.json", value)
                    _write(namespace / construction.REMOVING, value)
                removing = True
                _same(_intent(namespace, identity, candidate, publication), value)
                _quiesced(namespace)
                unchanged()
                expected = _inflate(value["tree"], publication)
                if os.name != "nt":
                    claims = namespace / HISTORY / value["removal"] / "claims"
                    _posix_claimed_tree(slot, publication, expected, claims, value["claims"])
                    _posix_claimed_tree(
                        slot, publication, expected, claims, value["claims"], remove=True,
                    )
                elif os.path.lexists(slot):
                    # Whole remaining-tree preflight precedes any further effects.
                    _tree(slot, publication, expected)
                    _tree(slot, publication, expected, remove=True)
                if os.path.lexists(slot):
                    raise PlanError("client runtime removal did not settle")
                outcome = {
                    "schema": "fleet-manager.client-runtime-removed", "version": 1,
                    "identity": identity, "candidate": candidate, "removal": value["removal"],
                    "intent_sha256": digest(value),
                }
                history = namespace / HISTORY / value["removal"]
                _write(history / "outcome.json", outcome)
                outcomes = _directory(namespace / OUTCOMES)
                _write(outcomes / (candidate + ".json"), outcome)
                unchanged()
                _same(_intent(namespace, identity, candidate, publication), value)
                _quiesced(namespace)
                (namespace / construction.REMOVING).unlink()
                client_admission._sync_directory(namespace)

            primitive.remove_owned_candidate(namespace, candidate, remove=remove)
            unchanged()
            return {
                "schema": "fleet-manager.client-runtime-removal", "version": 1,
                "identity": identity, "candidate": candidate, "runtime_removed": True,
                "active": False, "frontdoor_registered": False, "installation_retired": False,
                "path_modified": False, "live_readiness_verified": False,
                "reused": reused, "mutations_performed": not reused,
            }
    except PlanError:
        raise
    except (OSError, sqlite3.Error, RuntimeError, ValueError, KeyError, TypeError):
        message = ("client runtime removal outcome is unknown; retry the exact owned candidate"
                   if removing else "client runtime removal could not establish owned deletion")
        raise PlanError(message) from None
