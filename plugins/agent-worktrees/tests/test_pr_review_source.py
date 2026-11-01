"""Synthetic real Git consumers, without provider, checkout, build or credentials."""

import copy
import json
import os
from pathlib import Path
import subprocess

from agent_procutil import no_window_kwargs
import pytest

from agent_worktrees.pr_review_source import LocalReviewSource


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "plugins" / "agent-pull-requests" / "src"))
    from agent_pull_requests.review_workspace import build_generation

    repository = tmp_path / "source"
    repository.mkdir()
    environment = dict(
        os.environ, GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
        GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid",
    )
    for key in list(environment):
        if key.startswith("GIT_CONFIG_") or key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            del environment[key]

    def git(*args, data=b""):
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-c", "core.longpaths=true",
             "-c", "commit.gpgsign=false", *args],
            cwd=repository, env=environment, input=data, capture_output=True, timeout=15,
            check=True, **no_window_kwargs(),
        )
        return result.stdout.decode().strip()

    git("init", "-q")

    def blob(body):
        return git("hash-object", "-w", "--stdin", data=body)

    def tree(entries):
        body = b"".join(
            f"{mode} {'commit' if mode == '160000' else 'blob'} {oid}\t{name}\0".encode()
            for name, mode, oid in sorted(entries)
        )
        return git("mktree", "-z", data=body)

    old, new, empty, binary = blob(b"before\r\n"), blob(b"after\r\n"), blob(b""), blob(b"\0\xff")
    base = git("commit-tree", tree([
        ("edit.txt", "100644", old), ("gone.txt", "100644", old), ("old.txt", "100644", old),
    ]), data=b"common\n")
    head = git("commit-tree", tree([
        ("edit.txt", "100644", new), ("empty.txt", "100644", empty),
        ("new.txt", "100644", old), ("binary.dat", "100644", binary),
        ("link", "120000", blob(b"outside-owner")),
        ("module", "160000", base),
    ]), "-p", base, data=b"head\n")
    changes = [
        {"changeTrackingId": 1, "changeType": "edit", "item": {"path": "/edit.txt", "objectId": new}},
        {"changeTrackingId": 2, "changeType": "delete", "item": {"path": "/gone.txt", "objectId": old}},
        {"changeTrackingId": 3, "changeType": "rename", "originalPath": "/old.txt",
         "item": {"path": "/new.txt", "objectId": old, "originalObjectId": old}},
        *[{"changeTrackingId": index, "changeType": "add", "item": {"path": "/" + path}}
          for index, path in enumerate(("empty.txt", "binary.dat", "link", "module"), 4)],
    ]
    evidence = json.loads((root / "plugins" / "agent-pull-requests" / "docs" /
                           "review-evidence.example.json").read_text(encoding="utf-8"))
    evidence["sections"]["pull_request"]["data"]["lastMergeSourceCommit"]["commitId"] = head
    iteration = evidence["sections"]["iterations"]["data"][0]
    iteration["sourceRefCommit"]["commitId"], iteration["commonRefCommit"]["commitId"] = head, base
    evidence["sections"]["changes"]["revision"].update(head_sha=head, common_sha=base)
    evidence["sections"]["changes"]["data"] = changes
    generation = build_generation(evidence)["generation"]
    checks = []
    owner = LocalReviewSource(repository.resolve(), "synthetic-owned-workspace", generation["scope"],
                              checkpoint=lambda: checks.append("current"))
    return owner, generation, git, checks


def attach(owner, generation, **limits):
    from agent_pull_requests.source_attachment import attach_source
    return attach_source(generation, owner, budget=60, **limits)


def test_real_source_manifest_and_immutable_refs_do_not_rewrite_metadata(prepared):
    owner, generation, git, checks = prepared
    original = copy.deepcopy(generation)
    result = attach(owner, generation)
    assert result["state"] == "ready", result["source"]["errors"]
    assert result["review_authority"] is False
    assert generation == original and generation["complete"] is False
    source = result["source"]
    files = {row["path"]: row for row in source["files"]}
    assert files["edit.txt"]["before"]["object_id"] != files["edit.txt"]["after"]["object_id"]
    assert files["edit.txt"]["after"]["size"] == len(b"after\r\n")
    assert files["gone.txt"]["after"] is None
    assert files["old.txt"]["after"] is None and files["new.txt"]["before"] is None
    assert files["empty.txt"]["after"]["size"] == 0
    assert files["empty.txt"]["after"]["classification"] == "text"
    assert files["binary.dat"]["after"]["classification"] == "binary"
    assert files["link"]["after"]["classification"] == "symlink"
    assert files["module"]["after"]["classification"] == "gitlink"
    assert len(checks) >= 2 * len(files)
    assert not (owner.root / ".git" / "index").exists()
    assert list(owner.root.iterdir()) == [owner.root / ".git"]
    for retained in source["retained_refs"].values():
        assert git("rev-parse", retained["ref"]) == retained["object_id"]
    repeated = attach(owner, generation)
    assert repeated["state"] == "ready"
    assert repeated["source"]["retained_refs"] == source["retained_refs"]


@pytest.mark.parametrize("damage", [
    "missing_path", "blob", "folder", "common_missing", "head_missing", "invalid_original",
])
def test_bad_native_mapping_or_missing_object_cannot_relabel_source(prepared, damage):
    from agent_pull_requests.review_workspace import _digest
    owner, generation, git, checks = prepared
    if damage == "missing_path":
        generation["sections"]["changes"]["data"].pop()
    elif damage == "blob":
        generation["sections"]["changes"]["data"][0]["item"]["objectId"] = "1" * 40
    elif damage == "folder":
        generation["sections"]["changes"]["data"][0]["item"]["isFolder"] = True
    elif damage == "invalid_original":
        rows = generation["sections"]["changes"]["data"]
        rows[2]["originalPath"] = "/empty.txt"
        del rows[2]["item"]["originalObjectId"]
        rows.append({"changeType": "delete", "item": {"path": "/old.txt"}})
    else:
        generation["revision"]["common_sha" if damage == "common_missing" else "head_sha"] = "1" * 40
    generation["id"] = _digest({key: value for key, value in generation.items() if key != "id"})
    result = attach(owner, generation)
    assert result["state"] != "ready"
    assert git("for-each-ref", "--format=%(refname)", "refs/agent-pull-requests/source") == ""


@pytest.mark.parametrize("limit", [
    {"max_paths": 1}, {"max_blob_bytes": 1}, {"max_total_blob_bytes": 1}, {"max_output_bytes": 32},
])
def test_source_budget_refusals_do_not_pin_partial_ready_ranges(prepared, limit):
    owner, generation, git, _ = prepared
    result = attach(owner, generation, **limit)
    assert result["state"] == "incomplete"
    assert git("for-each-ref", "--format=%(refname)", "refs/agent-pull-requests/source") == ""


def test_existing_ref_conflict_never_force_moves_and_permission_failure_is_original(prepared):
    owner, generation, git, _ = prepared
    first = attach(owner, generation)
    retained = first["source"]["retained_refs"]["head"]
    common = generation["revision"]["common_sha"]
    git("update-ref", retained["ref"], common)
    result = attach(owner, generation)
    assert result["state"] == "incomplete"
    assert result["source"]["errors"] == ["retained_ref_conflict"]
    assert git("rev-parse", retained["ref"]) == common
    denied = PermissionError("controlled existing owner refusal")

    def checkpoint():
        raise denied

    owner.checkpoint = checkpoint
    with pytest.raises(PermissionError) as error:
        attach(owner, generation)
    assert error.value is denied


def test_incomplete_changes_and_wrong_selected_scope_stop_before_git(prepared):
    from agent_pull_requests.review_workspace import _digest
    owner, generation, _, checks = prepared
    generation["sections"]["changes"]["complete"] = False
    generation["id"] = _digest({key: value for key, value in generation.items() if key != "id"})
    assert attach(owner, generation)["state"] == "unavailable"
    assert checks == []
    generation["sections"]["changes"]["complete"] = True
    generation["id"] = _digest({key: value for key, value in generation.items() if key != "id"})
    owner.scope["principal_id"] = "other-selected-principal"
    with pytest.raises(ValueError, match="scope"):
        attach(owner, generation)
    assert checks == []


def test_new_source_range_preserves_the_prior_immutable_refs(prepared):
    from agent_pull_requests.review_workspace import _digest
    owner, generation, git, _ = prepared
    before = attach(owner, generation)
    previous_head = generation["revision"]["head_sha"]
    new_head = git("commit-tree", git("rev-parse", previous_head + "^{tree}"),
                   "-p", previous_head, data=b"next source iteration\n")
    generation["revision"].update(head_sha=new_head, iteration_head_sha=new_head, iteration=4)
    generation["sections"]["pull_request"]["data"]["lastMergeSourceCommit"]["commitId"] = new_head
    generation["sections"]["iterations"]["data"][0].update(id=4, sourceRefCommit={"commitId": new_head})
    generation["sections"]["changes"]["revision"].update(head_sha=new_head, iteration=4)
    generation["id"] = _digest({key: value for key, value in generation.items() if key != "id"})
    after = attach(owner, generation)
    assert after["state"] == "ready"
    assert before["source"]["retained_refs"] != after["source"]["retained_refs"]
    for retained in before["source"]["retained_refs"].values():
        assert git("rev-parse", retained["ref"]) == retained["object_id"]


@pytest.mark.parametrize("damage", ["errors", "retained_range", "version", "scope_mutation"])
def test_actual_owner_result_corruption_is_not_a_ready_attachment(prepared, damage):
    from agent_pull_requests.source_attachment import attach_source
    owner, generation, _, _ = prepared

    class CorruptResult:
        contract = staticmethod(owner.contract)

        def inspect_and_retain(self, request, **limits):
            result = owner.inspect_and_retain(request, **limits)
            assert result["complete"]
            if damage == "errors":
                result["errors"] = ["contradictory-readiness"]
            elif damage == "version":
                result["version"] = True
            elif damage == "scope_mutation":
                request["scope"]["principal_id"] = "different-native-principal"
                result["scope"] = request["scope"]
            else:
                result["retained_refs"]["head"]["object_id"] = "1" * 40
            return result

    with pytest.raises(ValueError, match="incompatible"):
        attach_source(generation, CorruptResult(), budget=60)


def test_internal_ref_publication_never_executes_repository_hooks(prepared):
    owner, generation, git, _ = prepared
    hook = owner.root / ".git" / "hooks" / "reference-transaction"
    hook.write_text("#!/bin/sh\nprintf unexpected > source-hook-ran\n", encoding="utf-8")
    hook.chmod(0o755)
    git("-c", "core.hooksPath=" + str(hook.parent), "update-ref",
        "refs/fixture/hook-control", generation["revision"]["head_sha"])
    sentinel = owner.root / "source-hook-ran"
    assert sentinel.read_text() == "unexpected"
    sentinel.unlink()
    assert attach(owner, generation)["state"] == "ready"
    assert not sentinel.exists()


def test_wide_source_diff_batches_physical_git_operations(prepared):
    from agent_pull_requests.review_workspace import _digest
    owner, generation, git, checks = prepared
    head = generation["revision"]["head_sha"]
    blob = git("rev-parse", head + ":edit.txt")
    entries = git("ls-tree", "-z", head).encode()
    entries += b"".join(f"100644 blob {blob}\twide-{index:03}.txt\0".encode() for index in range(100))
    tree = git("mktree", "-z", data=entries)
    new_head = git("commit-tree", tree, "-p", head, data=b"wide source fixture\n")
    generation["revision"].update(head_sha=new_head, iteration_head_sha=new_head, iteration=4)
    generation["sections"]["pull_request"]["data"]["lastMergeSourceCommit"]["commitId"] = new_head
    generation["sections"]["iterations"]["data"][0].update(id=4, sourceRefCommit={"commitId": new_head})
    generation["sections"]["changes"]["revision"].update(head_sha=new_head, iteration=4)
    generation["sections"]["changes"]["data"].extend(
        {"changeType": "add", "item": {"path": f"/wide-{index:03}.txt"}} for index in range(100)
    )
    generation["id"] = _digest({key: value for key, value in generation.items() if key != "id"})
    result = attach(owner, generation)
    assert result["state"] == "ready", result["source"]["errors"]
    assert len(result["source"]["files"]) == 108
    assert len(checks) <= 40  # Two checkpoints per physical command, not per changed path.


@pytest.mark.parametrize("target_exists", [True, False])
def test_symbolic_source_refs_never_become_ready_or_redirect_publication(prepared, target_exists):
    owner, generation, git, _ = prepared
    first = attach(owner, generation)
    retained = first["source"]["retained_refs"]["head"]["ref"]
    outside = "refs/fixture/external-source"
    if target_exists:
        git("update-ref", outside, generation["revision"]["head_sha"])
    git("symbolic-ref", retained, outside)
    result = attach(owner, generation)
    assert result["state"] == "incomplete"
    assert result["source"]["errors"] == ["symbolic_retained_ref"]
    assert git("symbolic-ref", retained) == outside
    refs = git("for-each-ref", "--format=%(refname) %(objectname)", "refs/fixture")
    assert refs == (f"{outside} {generation['revision']['head_sha']}" if target_exists else "")


@pytest.mark.parametrize("reverse", [False, True])
def test_file_directory_transitions_preserve_absent_file_sides(prepared, reverse):
    from agent_pull_requests.review_workspace import _digest
    owner, generation, git, _ = prepared
    original_head = generation["revision"]["head_sha"]
    blob = git("rev-parse", original_head + ":edit.txt")
    leaf = git("mktree", "-z", data=f"100644 blob {blob}\tchild.txt\0".encode())
    file_tree = git("mktree", "-z", data=f"100644 blob {blob}\tnode\0".encode())
    directory_tree = git("mktree", "-z", data=f"040000 tree {leaf}\tnode\0".encode())
    before, after = (directory_tree, file_tree) if reverse else (file_tree, directory_tree)
    common = git("commit-tree", before, data=b"transition common\n")
    head = git("commit-tree", after, "-p", common, data=b"transition head\n")
    generation["revision"].update(head_sha=head, iteration_head_sha=head, common_sha=common, iteration=4)
    generation["sections"]["pull_request"]["data"]["lastMergeSourceCommit"]["commitId"] = head
    generation["sections"]["iterations"]["data"][0].update(
        id=4, sourceRefCommit={"commitId": head}, commonRefCommit={"commitId": common},
    )
    changes = generation["sections"]["changes"]
    changes["revision"].update(head_sha=head, common_sha=common, iteration=4)
    changes["data"] = [{"changeType": "edit", "item": {"path": path}}
                       for path in ("/node", "/node/child.txt")]
    generation["id"] = _digest({key: value for key, value in generation.items() if key != "id"})
    result = attach(owner, generation)
    assert result["state"] == "ready", result["source"]["errors"]
    files = {row["path"]: row for row in result["source"]["files"]}
    assert files["node"]["before" if reverse else "after"] is None
    assert files["node/child.txt"]["after" if reverse else "before"] is None
    container_side = "before" if reverse else "after"
    read = owner.read_retained(result["source"], "/node", container_side, budget=60)
    assert read["classification"] == "container" and read["available"] is False


@pytest.mark.parametrize("path,classification", [
    ("/edit.txt", "text"), ("/empty.txt", "text"), ("/binary.dat", "binary"),
    ("/link", "symlink"), ("/module", "gitlink"), ("/gone.txt", "absent"),
])
def test_retained_reader_preserves_special_object_and_absent_representation(prepared, path, classification):
    owner, generation, _, _ = prepared
    source = attach(owner, generation)["source"]
    read = owner.read_retained(source, path, "after", budget=60)
    assert read["classification"] == classification and read["effects"] == "none"
    if path == "/empty.txt":
        assert read["content"] == "" and read["size"] == 0
    if classification in ("gitlink", "absent"):
        assert read["content"] is None
    if classification in ("symlink", "binary"):
        assert read["encoding"] == "base64"


@pytest.mark.parametrize("damage,reason", [
    ("header", "invalid_source_blob_frame"), ("frame-size", "source_blob_size_mismatch"),
    ("body", "source_blob_identity_mismatch"), ("metadata-size", "source_blob_size_mismatch"),
    ("classification", "source_blob_classification_mismatch"),
])
def test_retained_reader_rejects_malformed_blob_frames_and_manifest_metadata(prepared, monkeypatch, damage, reason):
    from agent_worktrees.pr_review_source import SourceIncomplete, _digest
    from agent_worktrees import pr_review_source
    owner, generation, _, _ = prepared
    source = attach(owner, generation)["source"]
    source = copy.deepcopy(source)
    if damage in ("metadata-size", "classification"):
        entry = next(row for row in source["files"] if row["path"] == "edit.txt")["after"]
        if damage == "metadata-size":
            entry["size"] += 1
        else:
            entry["classification"] = "binary"
        source["id"] = _digest({key: value for key, value in source.items() if key != "id"})
    else:
        original = pr_review_source.run_caller_owned

        def altered(argv, **kwargs):
            result = original(argv, **kwargs)
            if "cat-file" in argv:
                header, _, body = result.stdout.partition(b"\n")
                if damage == "header":
                    result.stdout = b"malformed blob header\n" + body
                elif damage == "frame-size":
                    result.stdout = header + b"\n" + body[:-1]
                else:
                    result.stdout = header + b"\n" + bytes([body[0] ^ 1]) + body[1:]
            return result

        monkeypatch.setattr(pr_review_source, "run_caller_owned", altered)
    with pytest.raises(SourceIncomplete, match=reason):
        owner.read_retained(source, "/edit.txt", "after", budget=60)
