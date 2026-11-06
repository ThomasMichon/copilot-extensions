"""Pure compact bodies and actual profile/CLI entrypoints; no forge effects."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import attribution_profile as profile
import comment_preparation as comments

COMMENT = "11111111-1111-4111-8111-111111111111"
WRITE = "22222222-2222-4222-8222-222222222222"
EDIT = "33333333-3333-4333-8333-333333333333"
CONTEXT: dict[str, object] = {
    "domain": "forge", "visibility": "public", "activity": "comment", "required": True,
}


def source(privacy: str = "public") -> dict[str, object]:
    return {
        "protocol": comments.SOURCE_PROTOCOL, "privacy": privacy, "effects": "none",
        "origin": None if privacy == "anonymous" else {
            "codename": "example-label", **({"worktree": "example-private-tree"} if privacy == "internal" else {}),
        },
    }


def prepare(draft: str = "Review text.", **changes: Any) -> comments.Prepared:
    arguments: dict[str, Any] = {
        "draft": draft, "profile": profile.validate({"version": 1}),
        "context": dict(CONTEXT), "source": source(), "comment_id": COMMENT, "write_id": WRITE,
    }
    arguments.update(changes)
    return comments.prepare(**arguments)


def test_actual_profile_and_one_compact_distinct_marker_are_repeatable() -> None:
    settings = profile.validate({
        "version": 1, "rules": [{"header": "[Copilot] ", "footer": "\nConfigured footer."}],
    })
    first = prepare(profile=settings)
    assert first["body"].startswith("[Copilot] Review text.")
    assert first["body"].count("Configured footer.") == 1
    assert first["body"].count(comments.PREFIX) == 1 and "agent-worktrees:source" not in first["body"]
    assert COMMENT in first["body"] and WRITE in first["body"]
    assert len(first["body"].splitlines()[-1].encode()) < 160
    assert prepare(first["body"], profile=settings, previous=first) == first
    assert prepare(first["body"], profile=settings) == first


def test_intentional_edit_keeps_comment_id_and_replaces_only_the_owned_marker() -> None:
    first = prepare()
    edited = prepare(first["body"].replace("Review text.", "Updated review."), write_id=EDIT, previous=first)
    assert edited["identity"]["comment_id"] == COMMENT and edited["identity"]["write_id"] == EDIT
    assert WRITE not in edited["body"] and edited["body"].count(comments.PREFIX) == 1
    assert edited["body"].startswith("[AI-assisted] Updated review.")
    assert prepare(edited["body"], write_id=EDIT, previous=edited) == edited
    with pytest.raises(profile.ProfileError, match="logical comment"):
        prepare("Different comment.", comment_id=EDIT, previous=first)


@pytest.mark.parametrize("change", ["body", "profile", "source"])
def test_retry_cannot_silently_change_retained_write_bytes(change: str) -> None:
    first = prepare()
    arguments: dict[str, Any] = {"previous": first}
    draft = first["body"]
    if change == "body":
        draft = draft.replace("Review text.", "Changed intent.")
    elif change == "profile":
        arguments["profile"] = profile.validate({"version": 1, "rules": [{"footer": "\nNew signature."}]})
    else:
        arguments["source"] = {**source(), "origin": {"codename": "another-label"}}
    with pytest.raises(profile.ProfileError, match="retry"):
        prepare(draft, **arguments)


def test_privacy_and_anonymous_policy_are_independent_of_visible_ai_disclosure() -> None:
    with pytest.raises(profile.ProfileError, match="privacy"):
        prepare(source=source("internal"))
    private = prepare(context={**CONTEXT, "visibility": "internal"}, source=source("internal"))
    assert "wt=example-private-tree" in private["body"]
    public = prepare(private["body"], write_id=EDIT, previous=private)
    assert "example-private-tree" not in public["body"] and "cn=example-label" in public["body"]
    with pytest.raises(profile.ProfileError, match="unavailable"):
        prepare(source=source("anonymous"))
    anonymous = prepare(source=source("anonymous"), allow_anonymous=True)
    assert "cn=" not in anonymous["body"] and "wt=" not in anonymous["body"]
    assert anonymous["body"].startswith("[AI-assisted] ")


def test_profile_owns_required_signature_and_truthful_review_claim() -> None:
    settings = profile.validate({
        "version": 1, "rules": [{"header": "[{assistant}; {review_status}] ", "required": True}],
    })
    result = prepare(profile=settings, context={**CONTEXT, "required": False})
    assert result["body"].startswith("[Copilot; not human-reviewed] ")
    assert prepare(context={**CONTEXT, "required": False})["body"].startswith("Review text.")
    assert prepare(context={**CONTEXT, "required": False})["body"].count(comments.PREFIX) == 1


def test_fenced_and_quoted_examples_survive_without_becoming_owned_metadata() -> None:
    example = "<!-- agent-comment:v1 c=example w=example cn=example -->"
    draft = f"Example:\n\n```html\n{example}\n```\n\n> {example}\n\nAuthored review."
    result = prepare(draft)
    assert result["body"].startswith("[AI-assisted] " + draft)
    assert result["body"].count(example) == 2
    assert prepare(result["body"], previous=result) == result


@pytest.mark.parametrize("draft", [
    "Review.\n\n<!-- agent-comment:v2 unknown -->",
    "Review.\n\n<!-- agent-comment:v1 c=foreign w=foreign -->",
    "Review.\n\n<!-- agent-comment:v1 bad -->\n\n<!-- agent-comment:v1 another -->",
    "Review.\n\n```text\nunfinished",
    "Review.\n\n<!-- unfinished",
    "",
])
def test_unknown_duplicate_or_unfinished_outer_metadata_refuses(draft: str) -> None:
    with pytest.raises(profile.ProfileError):
        prepare(draft)


@pytest.mark.parametrize("value", [
    "not-a-guid", "00000000-0000-0000-0000-000000000000",
    "123e4567-e89b-12d3-a456-426614174000", WRITE.upper() + " ",
])
def test_invalid_or_reused_guid_is_not_silently_reallocated(value: str) -> None:
    with pytest.raises(profile.ProfileError, match="GUID"):
        prepare(comment_id=value)
    with pytest.raises(profile.ProfileError, match="distinct"):
        prepare(comment_id=WRITE)


def test_final_utf8_signature_and_metadata_count_against_the_body_bound() -> None:
    text = "é" * 100
    complete = prepare(text)
    size = len(complete["body"].encode())
    assert prepare(text, max_body_bytes=size) == complete
    with pytest.raises(profile.ProfileError, match="bound"):
        prepare(text, max_body_bytes=size - 1)


def test_signature_and_metadata_alone_cannot_become_a_filler_comment() -> None:
    first = prepare()
    with pytest.raises(profile.ProfileError, match="empty comment"):
        prepare(first["body"].replace("Review text.", ""), write_id=EDIT, previous=first)


@pytest.mark.parametrize("change", [
    {"protocol": "agent-worktrees.comment-origin/v2"},
    {"origin": {"codename": "example-label", "session": "not-allowed"}},
    {"origin": {"codename": "bad --> injected"}},
    {"effects": "publish"},
])
def test_foreign_source_contract_cannot_become_comment_origin(change: dict[str, object]) -> None:
    with pytest.raises(profile.ProfileError):
        prepare(source={**source(), **change})


def test_corrupt_previous_record_cannot_authorize_marker_replacement() -> None:
    first = prepare()
    changed = {**first, "body": "Changed " + first["body"]}
    with pytest.raises(profile.ProfileError, match="inconsistent"):
        prepare(first["body"], write_id=EDIT, previous=changed)


def test_real_cli_uses_actual_profile_and_returns_value_free_errors(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".github").mkdir(parents=True)
    (repo / ".github" / "ai-attribution.json").write_text(json.dumps({
        "version": 1, "rules": [{"header": "[Configured AI] "}],
    }))
    env = {**os.environ, "HOME": str(tmp_path / "home"), "USERPROFILE": str(tmp_path / "home"),
           "XDG_CONFIG_HOME": str(tmp_path / "config"), "APPDATA": str(tmp_path / "config"),
           "COPILOT_CUSTOM_INSTRUCTIONS_DIRS": ""}
    request: dict[str, object] = {
        "draft": "Selected review.", "context": CONTEXT, "source": source(),
        "comment_id": COMMENT, "write_id": WRITE,
    }
    command = [sys.executable, str(SCRIPTS / "comment_preparation.py"), "--repo", str(repo)]
    result = subprocess.run(
        command, input=json.dumps(request), env=env, capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["body"].startswith("[Configured AI] Selected review.")
    assert value["identity"]["comment_id"] == COMMENT and value["effects"] == "none"
    bad = subprocess.run(
        command, input='{"draft":"not-returned","draft":"duplicate"}', env=env,
        capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert bad.returncode == 2
    assert json.loads(bad.stdout) == {"error": "comment_preparation_unavailable", "effects": "none"}
    assert "not-returned" not in bad.stdout
