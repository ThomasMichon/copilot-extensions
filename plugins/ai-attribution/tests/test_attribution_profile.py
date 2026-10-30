"""Profile selection, truthful interpolation, pure decoration and real entrypoints."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SPEC = importlib.util.spec_from_file_location("attribution_profile", SCRIPTS / "attribution_profile.py")
profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile)
CONTEXT = {"domain": "forge", "visibility": "public", "activity": "comment"}


def parsed(*rules, **variables):
    return profile.validate({"version": 1, "variables": variables, "rules": list(rules)})


def test_sparse_slots_specificity_and_explicit_empty():
    data = parsed(
        {"header": "AI: ", "footer": "\nAI footer"},
        {"when": {"domain": "forge"}, "header": ""},
        {"when": {"domain": "forge", "activity": "comment"}, "footer": "\nCopilot"},
    )
    signature = profile.resolve(data, CONTEXT)
    assert signature["header"] == ""
    assert signature["footer"] == "\nCopilot"
    assert profile.decorate("body", signature) == "body\nCopilot"
    data["rules"][-1]["footer"] = ""
    assert profile.resolve(data, CONTEXT)["header"] == "[AI-assisted] "


def test_conflicting_incomparable_selectors_fail_not_key_order():
    rules = [
        {"when": {"domain": "forge"}, "header": "AI one"},
        {"when": {"visibility": "public"}, "header": "AI two"},
    ]
    for ordered in (rules, rules[::-1]):
        with pytest.raises(profile.ProfileError, match="conflict"):
            profile.resolve(parsed(*ordered), CONTEXT)
    rules[1]["footer"] = rules[1].pop("header")
    assert profile.resolve(parsed(*rules), CONTEXT)["footer"] == "AI two"


def test_later_layer_wins_equal_specificity_and_only_tightens():
    data = parsed({"header": "AI one", "required": True})
    data["rules"].append({"header": "AI two", "_layer": 1})
    assert profile.resolve(data, {**CONTEXT, "required": False})["required"] is True
    assert profile.resolve(data, CONTEXT)["header"] == "AI two"
    with pytest.raises(profile.ProfileError):
        parsed({"required": False})


def test_interpolation_is_text_and_review_facts_are_not_assumed():
    data = parsed({"header": "**[{assistant} for {operator}; {review_status}]** "},
                  operator="<a>*name*", assistant="Copilot")
    actual = profile.resolve(data, CONTEXT)
    assert "not human-reviewed" in actual["header"]
    assert "<a>" not in actual["header"]
    assert r"\*name\*" in actual["header"]
    assert "; human-reviewed" in profile.resolve(
        data, {**CONTEXT, "human_reviewed": True}
    )["header"]
    with pytest.raises(profile.ProfileError):
        parsed({"header": "Copilot; human reviewed this"})


@pytest.mark.parametrize("rule", [
    {"header": "{operator.__class__}"}, {"header": "{operator!r}"},
    {"header": "{operator:20}"}, {"header": "{"}, {"header": "x" * 513},
    {"header": "AI\x00"}, {"when": {"unknown": "x"}},
    {"when": {"domain": "a`b"}}, {"footer_variants": [""]},
])
def test_rejects_malformed_data(rule):
    with pytest.raises(profile.ProfileError):
        parsed(rule)


def test_dedup_only_owned_boundary_variants_not_quotes_or_other_authors():
    signature = profile.resolve(parsed({
        "header": "**[Copilot for {operator}]** ",
        "footer": "\n[AI-assisted]",
        "header_variants": ["[Copilot for {operator}] "],
    }, operator="Alice"), CONTEXT)
    draft = '[Copilot for Alice] body quoting "[Copilot for Alice] "'
    result = profile.decorate(draft, signature)
    assert result == '**[Copilot for Alice]** body quoting "[Copilot for Alice] "\n[AI-assisted]'
    assert profile.decorate(result, signature) == result
    other = "[Copilot for Bob] body"
    assert other in profile.decorate(other, signature)
    signature["required"] = False
    assert profile.decorate(draft, signature) == draft


def test_load_authority_order_invalid_layer_and_target_contained_dirs(tmp_path):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    shared = tmp_path / "shared"
    paths = [repo / ".github" / "ai-attribution.json",
             shared / "ai-attribution.json", home / ".copilot" / "ai-attribution.json"]
    for path, text in zip(paths, ("AI target", "AI shared", "AI personal")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(parsed({"header": text})), encoding="utf-8")
    env = {"COPILOT_CUSTOM_INSTRUCTIONS_DIRS": str(shared) + os.pathsep + str(repo)}
    data, errors = profile.load_profile(home, repo, env)
    assert not errors
    assert len(data["rules"]) == 3
    assert profile.resolve(data, CONTEXT)["header"] == "AI personal"
    paths[-1].write_text('{"version": 1, "rules": [{"required": false}]}', encoding="utf-8")
    data, errors = profile.load_profile(home, repo, env)
    assert errors == ["profile layer 3 rejected"]
    assert profile.resolve(data, CONTEXT)["header"] == "AI shared"


def test_guidance_and_resolver_consume_same_effective_profile(tmp_path):
    config = tmp_path / ".copilot" / "ai-attribution.json"
    config.parent.mkdir()
    config.write_text(json.dumps(parsed(
        {"when": {"domain": "chat"}, "header": "**[AI for {operator}]** "},
        operator="Example",
    )), encoding="utf-8")
    data, errors = profile.load_profile(tmp_path, None, {})
    assert not errors
    assert "**[AI for {operator}]** " in profile.guidance(data)
    assert profile.resolve(data, {**CONTEXT, "domain": "chat"})["header"] == "**[AI for Example]** "


@pytest.mark.parametrize("raw", [
    b'{"version":1,"version":1}', b" " * (65536 + 1), b'{"version":true}',
], ids=["duplicate-key", "oversized-file", "boolean-version"])
def test_invalid_profile_file_is_rejected_as_a_layer(tmp_path, raw):
    config = tmp_path / ".copilot" / "ai-attribution.json"
    config.parent.mkdir()
    config.write_bytes(raw)
    data, errors = profile.load_profile(tmp_path, None, {})
    assert errors == ["profile layer 1 rejected"]
    assert not data["_inputs"]


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_actual_cli_is_pure_and_invalid_config_is_visible(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    config = home / ".copilot" / "ai-attribution.json"
    config.parent.mkdir()
    config.write_text(json.dumps(parsed({"header": "[AI] "})), encoding="utf-8")
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home),
           "XDG_CONFIG_HOME": str(home / "config"), "COPILOT_CUSTOM_INSTRUCTIONS_DIRS": ""}
    command = [sys.executable, str(SCRIPTS / "attribution_profile.py"), "decorate"]
    result = subprocess.run(command, input=json.dumps({"context": CONTEXT, "draft": "draft"}),
                            text=True, capture_output=True, env=env, check=True)
    assert json.loads(result.stdout)["draft"] == "[AI] draft"
    config.write_text("invalid", encoding="utf-8")
    result = subprocess.run(command, input="{}", text=True, capture_output=True, env=env)
    assert result.returncode == 2
    assert "rejected" in json.loads(result.stdout)["error"]


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_native_hook_reads_profile_changes_with_warm_base_cache(tmp_path):
    shell = (shutil.which("pwsh") or shutil.which("powershell.exe")) if os.name == "nt" else shutil.which("bash")
    if not shell:
        pytest.skip("native shell unavailable")
    home, repo = tmp_path / "home", tmp_path / "repo"
    home.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    config = home / ".copilot" / "ai-attribution.json"
    config.parent.mkdir()
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home),
           "XDG_CONFIG_HOME": str(home / "config"), "COPILOT_CUSTOM_INSTRUCTIONS_DIRS": "",
           "COPILOT_PLUGIN_ROOT": str(SCRIPTS.parent),
           "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]}
    env.pop("AI_ATTRIBUTION_FORCE_REFRESH", None)
    script = SCRIPTS / ("write-session-guidance.ps1" if os.name == "nt" else "write-session-guidance.sh")
    command = [shell, "-NoProfile", "-File", str(script)] if os.name == "nt" else [shell, str(script)]
    for index, template in enumerate((None, "[AI first] ", "[AI updated] ", None)):
        if template is None:
            config.unlink(missing_ok=True)
        else:
            config.write_text(json.dumps(parsed({"header": template})), encoding="utf-8")
        session = f"fresh-{index}"
        result = subprocess.run(command, input=json.dumps({"sessionId": session, "cwd": str(repo)}),
                                text=True, capture_output=True, env=env, check=True)
        assert json.loads(result.stdout) == {}
        target = home / ".copilot" / "session-state" / session / "instructions" / "ai-attribution" / "session-guidance.instructions.md"
        text = target.read_text(encoding="utf-8")
        assert "[owner: ai-attribution@" in text
        if template:
            assert template in text
            if os.name == "nt":
                warm_env = dict(env)
                warm_env["PATH"] = os.pathsep.join((
                    str(Path(shutil.which("git")).parent), str(Path(shell).parent),
                    str(Path(os.environ["SystemRoot"]) / "System32"),
                ))
                warm_session = session + "-no-python"
                warm = subprocess.run(
                    command, input=json.dumps({"sessionId": warm_session, "cwd": str(repo)}),
                    text=True, capture_output=True, env=warm_env, check=True,
                )
                assert json.loads(warm.stdout) == {}
                warm_target = target.parents[3] / warm_session / "instructions" / "ai-attribution" / target.name
                assert warm_target.read_text(encoding="utf-8") == text
        else:
            assert "Signature profile" not in text
        assert len(text.encode("utf-8")) <= 4096
    shared = [tmp_path / "shared-a", tmp_path / "shared-b"]
    for directory in shared:
        directory.mkdir()
        (directory / "ai-attribution.json").write_text(
            json.dumps(parsed({"header": f"[AI {directory.name}] "})), encoding="utf-8"
        )
    for index, directories in enumerate((shared, shared[::-1])):
        env["COPILOT_CUSTOM_INSTRUCTIONS_DIRS"] = os.pathsep.join(map(str, directories))
        session = f"order-{index}"
        result = subprocess.run(
            command, input=json.dumps({"sessionId": session, "cwd": str(repo)}),
            text=True, capture_output=True, env=env, check=True,
        )
        assert json.loads(result.stdout) == {}
        target = home / ".copilot" / "session-state" / session / "instructions" / "ai-attribution" / "session-guidance.instructions.md"
        assert target.read_text(encoding="utf-8").index(directories[0].name) < target.read_text(
            encoding="utf-8"
        ).index(directories[1].name)
