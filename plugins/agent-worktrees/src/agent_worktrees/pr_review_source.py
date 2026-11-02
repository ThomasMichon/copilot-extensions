"""Exact local Git source inspection under the existing workspace/resource owner."""

from __future__ import annotations

import copy
import base64
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import time

from agent_procutil.owned_process import OutputLimitExceeded, OwnedCleanupError, run_caller_owned

from .git_repository_environment import _NO_HOOKS_PATH, repository_identity_env
from .git_tree_diff import _path_batches, parse as parse_tree_diff

_OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


class SourceIncomplete(ValueError):
    """Local source cannot establish the requested complete range."""


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _path(value):
    if (
        not isinstance(value, str) or not value.startswith("/") or "\0" in value
        or any(part in ("", ".", "..") for part in value[1:].split("/"))
    ):
        raise SourceIncomplete("invalid_native_path")
    return value[1:]


def _owned_git(root, checkpoint, deadline, environment, maximum, args,
               *, limit=None, input_text=None, codes=(0,)):
    checkpoint()
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SourceIncomplete("deadline_exhausted")
    try:
        output = run_caller_owned(
            ["git", "--no-lazy-fetch", "--no-replace-objects", "-c", "core.fsmonitor=false",
             "-c", "core.longpaths=true", "-c", "protocol.allow=never",
             "-c", f"core.hooksPath={_NO_HOOKS_PATH}", *args],
            cwd=root, env=environment, timeout=remaining,
            max_stdout_bytes=min(maximum, maximum if limit is None else limit),
            max_stderr_bytes=8192, input_text=input_text, binary=True,
        )
    except (OutputLimitExceeded, subprocess.TimeoutExpired):
        raise SourceIncomplete("git_io_budget_exhausted") from None
    checkpoint()
    if time.monotonic() >= deadline:
        raise SourceIncomplete("deadline_exhausted")
    if output.returncode not in codes:
        raise SourceIncomplete("git_operation_failed")
    return output


class LocalReviewSource:
    """Embedding owner selects a writable owned repository, scope and checkpoint.

    Construction does not resolve repository authority or adopt a reference
    checkout. The caller's existing workspace/resource pipeline owns that step.
    Prepared operations never fetch. Optional preparation uses only a separately
    bound existing Git owner; checkout/build/resource selection are not performed.
    """

    def __init__(self, root: Path, workspace_id: str, scope: dict, *, checkpoint,
                 acquisition=None, remote=None):
        root = Path(root)
        if not root.is_absolute() or root.resolve() != root or not root.is_dir():
            raise ValueError("source requires an existing canonical owner-selected repository")
        if (
            not isinstance(workspace_id, str) or not 1 <= len(workspace_id) <= 256
            or any(ord(char) < 32 for char in workspace_id)
            or not isinstance(scope, dict) or not callable(checkpoint)
        ):
            raise ValueError("source requires an owner workspace identity, scope and checkpoint")
        self.root, self.workspace_id = root, workspace_id
        self.scope, self.checkpoint = copy.deepcopy(scope), checkpoint
        if (acquisition is None) != (remote is None) or (
            acquisition is not None and (not callable(acquisition) or not isinstance(remote, str))
        ):
            raise ValueError("acquisition requires an existing owner callable and selected remote")
        self._acquisition, self._remote = acquisition, remote

    def acquisition_contract(self):
        if self._acquisition is None:
            return None
        return {
            "schema": "agent-pull-requests.source-acquisition", "version": 1,
            "range": "exact_head_common", "depth": 1, "storage": "selected_dedicated_shallow",
            "resource_owner": "existing_workspace_owner",
        }

    def prepare_and_retain(self, request, *, budget=60, **limits):
        self.checkpoint()
        if self._acquisition is None:
            raise SourceIncomplete("source_acquisition_unavailable")
        if (
            not isinstance(request, dict) or set(request) != {"scope", "revision", "changes"}
            or request.get("scope") != self.scope
            or not isinstance(request.get("revision"), dict) or not isinstance(request.get("changes"), list)
            or type(budget) not in (int, float) or not math.isfinite(budget) or budget <= 0
            or set(limits) - {"max_paths", "max_blob_bytes", "max_total_blob_bytes", "max_output_bytes"}
            or any(type(value) is not int or value <= 0 for value in limits.values())
        ):
            raise ValueError("invalid source preparation selection")
        request = copy.deepcopy(request)
        revision = request["revision"]
        head, common = revision.get("head_sha"), revision.get("common_sha")
        if (
            any(not isinstance(oid, str) or _OID.fullmatch(oid) is None for oid in (head, common))
            or len(head) != len(common) or type(revision.get("iteration")) is not int or revision["iteration"] <= 0
        ):
            raise ValueError("source requires exact reviewed range")
        deadline = time.monotonic() + budget
        from .git_review_acquisition import validate_selection
        validate_selection(self.root, head, common, checkpoint=self.checkpoint, deadline=deadline)
        self._acquisition(self.root, self._remote, head, common,
                          budget=deadline - time.monotonic(), checkpoint=self.checkpoint)
        self.checkpoint()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SourceIncomplete("deadline_exhausted")
        return self.inspect_and_retain(copy.deepcopy(request), budget=remaining, **limits)

    @staticmethod
    def contract():
        return {
            "schema": "agent-pull-requests.local-source-contract", "version": 1,
            "backend_access": False, "fetch": False, "checkout": False,
            "retention": "immutable_git_refs", "resource_owner": "existing_workspace_owner",
        }

    @staticmethod
    def read_contract():
        return {
            "schema": "agent-pull-requests.local-source-read-contract", "version": 1,
            "fetch": False, "checkout": False, "working_tree": False,
            "range": "verified_retained_refs", "effects": "none",
        }

    def read_retained(self, manifest, path, side, *, budget, max_bytes=1024 * 1024):
        self.checkpoint()
        if (
            not isinstance(manifest, dict) or manifest.get("complete") is not True
            or manifest.get("schema") != "agent-pull-requests.local-source"
            or type(manifest.get("version")) is not int or manifest["version"] != 1
            or manifest.get("errors") != [] or not isinstance(manifest.get("files"), list)
            or not isinstance(manifest.get("revision"), dict)
            or manifest.get("scope") != self.scope or manifest.get("workspace_id") != self.workspace_id
            or manifest.get("id") != _digest({k: v for k, v in manifest.items() if k != "id"})
            or side not in ("before", "after")
            or type(budget) not in (int, float) or not math.isfinite(budget) or budget <= 0
            or type(max_bytes) is not int or max_bytes <= 0
        ):
            raise SourceIncomplete("invalid_source_read_selection")
        selected_path = _path(path)
        matches = [row for row in manifest.get("files", []) if isinstance(row, dict) and row.get("path") == selected_path]
        if len(matches) != 1:
            raise SourceIncomplete("source_path_not_selected")
        revision = manifest.get("revision") or {}
        head, common, iteration = (revision.get(k) for k in ("head_sha", "common_sha", "iteration"))
        if (
            not isinstance(head, str) or _OID.fullmatch(head) is None
            or not isinstance(common, str) or _OID.fullmatch(common) is None
            or len(head) != len(common) or type(iteration) is not int or iteration <= 0
        ):
            raise SourceIncomplete("invalid_retained_range")
        prefix = "refs/agent-pull-requests/source/" + _digest({
            "scope": self.scope, "workspace_id": self.workspace_id,
            "head_sha": head, "common_sha": common, "iteration": iteration,
        })
        references = {role: {"ref": prefix + "/" + role, "object_id": oid}
                      for role, oid in (("head", head), ("common", common))}
        if manifest.get("retained_refs") != references:
            raise SourceIncomplete("retained_range_mismatch")
        environment = repository_identity_env()
        environment.update(GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0")
        deadline = time.monotonic() + budget

        def git(*args, **kwargs):
            return _owned_git(self.root, self.checkpoint, deadline, environment,
                              max_bytes + 512, args, **kwargs)

        def finish(value):
            self.checkpoint()
            if time.monotonic() >= deadline:
                raise SourceIncomplete("deadline_exhausted")
            return value

        for retained in references.values():
            symbolic = git("symbolic-ref", "--quiet", retained["ref"], limit=256, codes=(0, 1, 128))
            if symbolic.returncode == 0:
                raise SourceIncomplete("symbolic_retained_ref")
            observed = git("show-ref", "--verify", "--hash", retained["ref"], limit=128)
            if observed.stdout.decode().strip() != retained["object_id"]:
                raise SourceIncomplete("retained_ref_conflict")
        commit = head if side == "after" else common
        raw = git("ls-tree", "--full-tree", "-z", commit, "--", ":(literal)" + selected_path).stdout
        metadata = matches[0].get(side)
        result = {
            "schema": "agent-pull-requests.local-source-read", "version": 1,
            "source_id": manifest["id"], "path": path, "side": side,
            "content": None, "encoding": None, "effects": "none",
        }
        if metadata is None and not raw:
            return finish(dict(result, available=False, classification="absent"))
        match = re.fullmatch(rb"([0-7]{6}) (blob|tree|commit) ([0-9a-f]+)\t(.+)\0", raw, re.DOTALL)
        if match is None or match[4].decode("utf-8") != selected_path:
            raise SourceIncomplete("source_tree_entry_mismatch")
        mode, kind, oid = (part.decode() for part in match.group(1, 2, 3))
        if metadata is None and mode == "040000" and kind == "tree":
            return finish(dict(result, available=False, classification="container"))
        if (
            not isinstance(metadata, dict)
            or metadata.get("classification") not in ("text", "binary", "symlink", "gitlink")
            or (mode, kind, oid) != (
            metadata.get("mode"), metadata.get("type"), metadata.get("object_id"),
            )
        ):
            raise SourceIncomplete("source_blob_mapping_mismatch")
        result.update(available=True, object_id=oid, mode=mode, classification=metadata["classification"])
        if mode == "160000" and kind == "commit" and metadata["classification"] == "gitlink":
            return finish(result)
        if mode not in ("100644", "100755", "120000") or kind != "blob":
            raise SourceIncomplete("unsupported_source_blob")
        framed = git("cat-file", "--batch", input_text=oid + "\n").stdout
        header, sep, remainder = framed.partition(b"\n")
        parsed = re.fullmatch(rb"([0-9a-f]+) blob ([0-9]+)", header)
        if not sep or parsed is None or parsed[1].decode() != oid:
            raise SourceIncomplete("invalid_source_blob_frame")
        size = int(parsed[2])
        if size > max_bytes or size != metadata.get("size") or len(remainder) != size + 1 or remainder[-1:] != b"\n":
            raise SourceIncomplete("source_blob_size_mismatch")
        body = remainder[:-1]
        algorithm = hashlib.sha1 if len(oid) == 40 else hashlib.sha256
        if algorithm(b"blob " + str(size).encode() + b"\0" + body).hexdigest() != oid:
            raise SourceIncomplete("source_blob_identity_mismatch")
        try:
            text = body.decode("utf-8")
            classification = "binary" if b"\0" in body else "text"
        except UnicodeError:
            text, classification = None, "binary"
        if mode == "120000":
            classification = "symlink"
        if metadata["classification"] != classification:
            raise SourceIncomplete("source_blob_classification_mismatch")
        result["size"] = size
        if mode != "120000" and metadata["classification"] == "text":
            if b"\0" in body:
                raise SourceIncomplete("source_blob_classification_mismatch")
            result.update(encoding="utf-8", content=text)
        else:
            result.update(encoding="base64", content=base64.b64encode(body).decode("ascii"))
        return finish(result)

    def inspect_and_retain(
        self, request: dict, *, budget: float, max_paths: int = 20000,
        max_blob_bytes: int = 1024 * 1024, max_total_blob_bytes: int = 16 * 1024 * 1024,
        max_output_bytes: int = 8 * 1024 * 1024,
    ) -> dict:
        if (
            type(budget) not in (int, float) or not math.isfinite(budget) or budget <= 0
            or any(type(value) is not int or value <= 0 for value in (
                max_paths, max_blob_bytes, max_total_blob_bytes, max_output_bytes,
            ))
            or not isinstance(request, dict) or set(request) != {"scope", "revision", "changes"}
            or request["scope"] != self.scope or not isinstance(request["revision"], dict)
            or not isinstance(request["changes"], list)
        ):
            raise ValueError("source requires the exact selected scope and finite Git/IO limits")
        request = copy.deepcopy(request)
        revision = request["revision"]
        head, common, iteration = (revision.get(key) for key in ("head_sha", "common_sha", "iteration"))
        if (
            not isinstance(head, str) or _OID.fullmatch(head) is None
            or not isinstance(common, str) or _OID.fullmatch(common) is None
            or len(head) != len(common) or type(iteration) is not int or iteration <= 0
        ):
            raise ValueError("source requires exact head/common object IDs and a positive iteration")
        start, deadline = time.monotonic(), time.monotonic() + budget
        result = {
            "schema": "agent-pull-requests.local-source", "version": 1,
            "scope": copy.deepcopy(self.scope), "revision": copy.deepcopy(revision),
            "workspace_id": self.workspace_id, "complete": False, "errors": [],
            "files": [], "native_changes": request["changes"], "retained_refs": {},
            "effects": "none",
        }
        environment = repository_identity_env()
        environment.update(GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0")

        def git(*args, limit=max_output_bytes, input_text=None, codes=(0,)):
            return _owned_git(self.root, self.checkpoint, deadline, environment,
                              max_output_bytes, args, limit=limit,
                              input_text=input_text, codes=codes)

        blobs = {}
        trees = {}

        def entry(commit, path, expected):
            if not any(char != "0" for char in expected):
                retained = trees[commit].get(path)
                if retained is not None and retained[:2] != ("040000", "tree"):
                    raise SourceIncomplete("tree_entry_mismatch")
                return None
            retained = trees[commit].get(path)
            if retained is None or retained[2] != expected:
                raise SourceIncomplete("tree_entry_mismatch")
            mode, kind, _ = retained
            value = {"object_id": expected, "mode": mode, "type": kind, "size": None}
            if mode == "160000" and kind == "commit":
                value["classification"] = "gitlink"
                return value
            if mode not in ("100644", "100755", "120000") or kind != "blob":
                raise SourceIncomplete("unsupported_tree_entry")
            value["size"], value["classification"] = blobs[expected]
            if mode == "120000":
                value["classification"] = "symlink"
            return value

        try:
            for commit in (common, head):
                resolved = git("rev-parse", "--verify", commit + "^{commit}", limit=128).stdout.decode().strip()
                if resolved != commit:
                    raise SourceIncomplete("commit_identity_mismatch")
            diff = git(
                "diff", "--raw", "--no-abbrev", "--no-renames", "--no-ext-diff",
                "--no-textconv", "--ignore-submodules=none", "--no-relative", "-z", common, head, "--",
            ).stdout.decode("utf-8")
            changes = parse_tree_diff(diff)
            if len(changes) > max_paths:
                raise SourceIncomplete("path_budget_exhausted")
            native_paths = set()
            for change in request["changes"]:
                if not isinstance(change, dict) or not isinstance(change.get("item"), dict):
                    raise SourceIncomplete("invalid_native_change")
                item = change["item"]
                if (
                    item.get("isFolder") is not None and item["isFolder"] is not False
                    or item.get("gitObjectType") not in (None, "blob", "commit")
                ):
                    raise SourceIncomplete("unsupported_folder_mapping")
                path = _path(item.get("path"))
                native_paths.add(path)
                if change.get("originalPath") is not None:
                    original_path = _path(change["originalPath"])
                    original_entry = changes.get(original_path)
                    if original_entry is None or not any(char != "0" for char in original_entry[0]):
                        raise SourceIncomplete("original_path_has_no_common_object")
                    native_paths.add(original_path)
                old, new = changes.get(path, (None, None))
                if old is None:
                    raise SourceIncomplete("native_path_missing")
                for field, selected in (
                    ("objectId", new if any(char != "0" for char in new) else old),
                    ("originalObjectId", changes.get(_path(change["originalPath"]), (old, None))[0]
                     if change.get("originalPath") is not None else old),
                ):
                    if field in item and item[field] is not None and item[field] != selected:
                        raise SourceIncomplete("native_blob_mismatch")
            if native_paths != set(changes):
                raise SourceIncomplete("native_path_coverage_mismatch")
            for commit in (common, head):
                entries = trees.setdefault(commit, {})
                arguments = ("ls-tree", "--full-tree", "-z", commit, "--")
                for batch in _path_batches(arguments, changes):
                    output = git(*arguments, *(":(literal)" + path for path in batch)).stdout
                    if output and not output.endswith(b"\0"):
                        raise SourceIncomplete("unterminated_tree_entries")
                    for row in output.split(b"\0")[:-1]:
                        match = re.fullmatch(rb"([0-7]{6}) (blob|tree|commit) ([0-9a-f]+)\t(.+)", row, re.DOTALL)
                        if match is None:
                            raise SourceIncomplete("invalid_tree_entry")
                        path = match[4].decode("utf-8")
                        if path not in batch or path in entries:
                            raise SourceIncomplete("unexpected_tree_entry")
                        entries[path] = tuple(part.decode() for part in match.group(1, 2, 3))
            identifiers = sorted({
                oid for tree in trees.values() for _, kind, oid in tree.values() if kind == "blob"
            })
            if identifiers:
                metadata = git("cat-file", "--batch-check", input_text="\n".join(identifiers) + "\n").stdout
                rows = metadata.splitlines()
                if len(rows) != len(identifiers):
                    raise SourceIncomplete("invalid_blob_metadata")
                sizes = {}
                for identifier, row in zip(identifiers, rows):
                    match = re.fullmatch(rb"([0-9a-f]+) blob ([0-9]+)", row)
                    if match is None or match[1].decode() != identifier:
                        raise SourceIncomplete("invalid_blob_metadata")
                    sizes[identifier] = int(match[2])
                if any(size > max_blob_bytes for size in sizes.values()) or sum(sizes.values()) > max_total_blob_bytes:
                    raise SourceIncomplete("blob_budget_exhausted")
                batches, batch, size = [], [], 0
                for identifier in identifiers:
                    cost = sizes[identifier] + len(identifier) + 32
                    if cost > max_output_bytes:
                        raise SourceIncomplete("blob_output_budget_exhausted")
                    if batch and size + cost > max_output_bytes:
                        batches.append(batch)
                        batch, size = [], 0
                    batch.append(identifier)
                    size += cost
                if batch:
                    batches.append(batch)
                for batch in batches:
                    output = git("cat-file", "--batch", input_text="\n".join(batch) + "\n").stdout
                    offset = 0
                    for identifier in batch:
                        end = output.find(b"\n", offset)
                        size = sizes[identifier]
                        if end < 0 or output[offset:end] != f"{identifier} blob {size}".encode():
                            raise SourceIncomplete("invalid_blob_frame")
                        offset = end + 1
                        body = output[offset:offset + size]
                        offset += size
                        if len(body) != size or output[offset:offset + 1] != b"\n":
                            raise SourceIncomplete("invalid_blob_frame")
                        offset += 1
                        algorithm = hashlib.sha1 if len(identifier) == 40 else hashlib.sha256
                        if algorithm(b"blob " + str(size).encode() + b"\0" + body).hexdigest() != identifier:
                            raise SourceIncomplete("blob_identity_mismatch")
                        try:
                            body.decode("utf-8")
                            classification = "binary" if b"\0" in body else "text"
                        except UnicodeError:
                            classification = "binary"
                        blobs[identifier] = (size, classification)
                    if offset != len(output):
                        raise SourceIncomplete("unexpected_blob_frame")
            for path, (before, after) in sorted(changes.items()):
                result["files"].append({
                    "path": path, "before": entry(common, path, before), "after": entry(head, path, after),
                })
            reference = "refs/agent-pull-requests/source/" + _digest({
                "scope": self.scope, "workspace_id": self.workspace_id,
                "head_sha": head, "common_sha": common, "iteration": iteration,
            })
            commands = ["start"]
            for role, commit in (("head", head), ("common", common)):
                ref = reference + "/" + role
                symbolic = git("symbolic-ref", "--quiet", ref, limit=256, codes=(0, 1, 128))
                if symbolic.returncode == 0:
                    raise SourceIncomplete("symbolic_retained_ref")
                observed = git("show-ref", "--verify", "--hash", ref, limit=128, codes=(0, 1, 128))
                if observed.returncode == 0:
                    if observed.stdout.decode().strip() != commit:
                        raise SourceIncomplete("retained_ref_conflict")
                    commands.append(f"verify {ref} {commit}")
                else:
                    commands.append(f"create {ref} {commit}")
                result["retained_refs"][role] = {"ref": ref, "object_id": commit}
            result["effects"] = "local_refs_possible"
            git("update-ref", "--no-deref", "--stdin", input_text="\n".join([*commands, "prepare", "commit", ""]))
            for retained in result["retained_refs"].values():
                symbolic = git("symbolic-ref", "--quiet", retained["ref"], limit=256, codes=(0, 1, 128))
                if symbolic.returncode == 0:
                    raise SourceIncomplete("symbolic_retained_ref")
                observed = git("show-ref", "--verify", "--hash", retained["ref"], limit=128)
                if observed.stdout.decode().strip() != retained["object_id"]:
                    raise SourceIncomplete("retained_ref_readback_failed")
            result["complete"], result["effects"] = True, "local_refs_only"
        except OwnedCleanupError:
            raise
        except PermissionError:
            raise
        except SourceIncomplete as error:
            result["errors"] = [str(error)]
        except (OutputLimitExceeded, subprocess.TimeoutExpired):
            result["errors"] = ["git_io_budget_exhausted"]
        except (OSError, UnicodeError, ValueError):
            result["errors"] = ["source_unavailable"]
        result["elapsed_seconds"] = time.monotonic() - start
        result["id"] = _digest(result)
        return result
