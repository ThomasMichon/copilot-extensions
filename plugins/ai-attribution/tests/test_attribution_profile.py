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


def directory_link(link, target):
    if os.name == "nt":
        subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
                       capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        link.symlink_to(target, target_is_directory=True)


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


@pytest.mark.parametrize("variable", ["AI\u202e", "AI\u200b", "AI\u0085"])
def test_variables_reject_display_controls(variable):
    with pytest.raises(profile.ProfileError, match="variable"):
        parsed({"header": "{assistant}"}, assistant=variable)


@pytest.mark.parametrize("slot,template,variable", [
    ("header", "{assistant}", "human-reviewed"),
    ("footer", "{assistant}ed", "human-review"),
    ("header_variants", "{assistant}", "human-reviewed"),
    ("footer_variants", "{assistant}ed", "human-review"),
])
def test_interpolated_review_claims_rejected_by_both_consumers(slot, template, variable):
    data = parsed({slot: [template] if slot.endswith("_variants") else template},
                  assistant=variable)
    with pytest.raises(profile.ProfileError, match="review claims"):
        profile.resolve(data, {**CONTEXT, "human_reviewed": False})
    with pytest.raises(profile.ProfileError, match="review claims"):
        profile.guidance(data)


@pytest.mark.parametrize("slot", ["header", "footer", "header_variants", "footer_variants"])
@pytest.mark.parametrize("template,variable", [
    ("!{assistant}[AI-assisted]\n\n", ""),
    ("&{assistant};", "nbsp"),
    (r"\{assistant}[AI-assisted]", "!"),
])
def test_assembled_markup_rejected_in_both_consumers(slot, template, variable):
    data = parsed({slot: [template] if slot.endswith("_variants") else template},
                  assistant=variable)
    with pytest.raises(profile.ProfileError, match="markup"):
        profile.resolve(data, CONTEXT)
    with pytest.raises(profile.ProfileError, match="markup"):
        profile.guidance(data)


def test_assembled_validation_preserves_escaped_literal_identity_and_review_status():
    data = parsed({"header": "[{assistant} for {operator}; {review_status}] "},
                  assistant="![literal]", operator="<Example> & *Name*")
    signature = profile.resolve(data, CONTEXT)
    assert r"\!\[literal\]" in signature["header"]
    assert "&lt;Example&gt; &amp;" in signature["header"]
    assert "not human-reviewed" in signature["header"]
    assert profile.guidance(data)


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


def test_current_marker_wins_over_overlapping_historical_variant():
    signature = profile.resolve(parsed({
        "header": "[AI-assisted] ",
        "header_variants": ["[AI-assisted] Drafted with Copilot.\n\n"],
    }), CONTEXT)
    draft = "Drafted with Copilot.\n\nDetails"
    once = profile.decorate(draft, signature)
    assert once == "[AI-assisted] " + draft
    assert profile.decorate(once, signature) == once
    signature = profile.resolve(parsed({
        "header": "", "footer": " [AI-assisted]",
        "footer_variants": ["\n\nDrafted with Copilot. [AI-assisted]"],
    }), CONTEXT)
    draft = "Details\n\nDrafted with Copilot."
    once = profile.decorate(draft, signature)
    assert once == draft + " [AI-assisted]"
    assert profile.decorate(once, signature) == once
    signature = profile.resolve(parsed({
        "footer": "", "footer_variants": [" [old AI]"],
    }), CONTEXT)
    draft = "Details [old AI] [old AI]"
    once = profile.decorate(draft, signature)
    assert once == "[AI-assisted] " + draft
    assert profile.decorate(once, signature) == once


@pytest.mark.parametrize("rule,draft", [
    ({"header": "[AI-assisted] ", "footer": "\nAI footer",
      "footer_variants": ["[AI-assisted] Drafted with Copilot."]},
     "Drafted with Copilot."),
    ({"header": "[AI] ", "footer": "\nAI footer",
      "footer_variants": ["[AI] original"]}, "[AI] original"),
    ({"header": "[AI] ", "footer": "\nAI footer",
      "header_variants": ["original"], "footer_variants": ["original"]}, "original"),
])
def test_cross_slot_overlap_preserves_draft_and_idempotence(rule, draft):
    signature = profile.resolve(parsed(rule), CONTEXT)
    once = profile.decorate(draft, signature)
    assert draft in once
    assert once.startswith(signature["header"])
    assert once.endswith(signature["footer"])
    assert profile.decorate(once, signature) == once


@pytest.mark.parametrize("header", [
    "<!-- AI-assisted -->", "<span hidden>AI-assisted</span>",
    "[](#AI-assisted)", "[hidden]: https://example.com/AI-assisted",
    "&nbsp;", "\u200bAI-assisted", "```AI-assisted\n```",
    "![AI-assisted] ", "![AI-assisted][]", "![AI-assisted][logo]",
])
def test_hidden_or_active_markup_is_not_a_disclosure(header):
    with pytest.raises(profile.ProfileError, match="markup"):
        parsed({"header": header})
    signature = profile.resolve(parsed({"header": "***", "footer": ""}), CONTEXT)
    assert profile.decorate("body", signature) == "[AI-assisted] body"


def test_split_slot_link_fragments_cannot_hide_disclosure():
    with pytest.raises(profile.ProfileError, match="markup"):
        parsed({"header": "[", "footer": "](https://example.com/AI-assisted)"})
    for template in ("[", "]", "[AI", "AI]"):
        with pytest.raises(profile.ProfileError, match="markup"):
            parsed({"header": template})


def test_symlinked_instruction_directory_is_rejected_before_resolution(tmp_path):
    repo, target, link = tmp_path / "repo", tmp_path / "target", tmp_path / "link"
    repo.mkdir()
    target.mkdir()
    (target / "ai-attribution.json").write_text(json.dumps(parsed({"header": "AI marker"})),
                                              encoding="utf-8")
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    data, errors = profile.load_profile(tmp_path, repo, {
        "COPILOT_CUSTOM_INSTRUCTIONS_DIRS": str(link),
    })
    assert errors == ["profile layer 2 rejected"]
    assert not data["_inputs"]


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


def test_personal_profile_keeps_its_last_high_priority_occurrence(tmp_path):
    shared = tmp_path / "shared"
    personal = tmp_path / ".copilot"
    for directory, header in ((personal, "AI personal"), (shared, "AI shared")):
        directory.mkdir()
        (directory / "ai-attribution.json").write_text(
            json.dumps(parsed({"header": header})), encoding="utf-8"
        )
    data, errors = profile.load_profile(tmp_path, None, {
        "COPILOT_CUSTOM_INSTRUCTIONS_DIRS": os.pathsep.join(map(str, (personal, shared))),
    })
    assert not errors
    assert len(data["_inputs"]) == 2
    assert profile.resolve(data, CONTEXT)["header"] == "AI personal"


@pytest.mark.parametrize("directories", ["x" * 65537, ",".join(["x"] * 129)],
                         ids=["character-limit", "entry-limit"])
def test_discovery_overflow_is_bounded_before_probing(monkeypatch, tmp_path, directories):
    monkeypatch.setattr(profile, "_safe_file", lambda path: pytest.fail("must not probe"))
    data, errors = profile.load_profile(tmp_path, None, {
        "COPILOT_CUSTOM_INSTRUCTIONS_DIRS": directories,
    })
    assert errors == ["profile discovery rejected"]
    assert not data["_probes"]


def test_discovery_failure_becomes_profile_diagnostic(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "expanduser", lambda path: (_ for _ in ()).throw(
        RuntimeError("unavailable user")
    ))
    data, errors = profile.load_profile(tmp_path, None, {
        "COPILOT_CUSTOM_INSTRUCTIONS_DIRS": "~missing-user/instructions",
    })
    assert errors == ["profile discovery rejected"]
    assert not data["_probes"]


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
    b'{"version":1,"rules":' + b"[" * 10000 + b"]" * 10000 + b"}",
], ids=["duplicate-key", "oversized-file", "boolean-version", "deep-json"])
def test_invalid_profile_file_is_rejected_as_a_layer(tmp_path, raw):
    config = tmp_path / ".copilot" / "ai-attribution.json"
    config.parent.mkdir()
    config.write_bytes(raw)
    data, errors = profile.load_profile(tmp_path, None, {})
    assert errors == ["profile layer 1 rejected"]
    assert not data["_inputs"]


def test_nonregular_profile_is_rejected_before_open(monkeypatch, tmp_path):
    config = tmp_path / ".copilot" / "ai-attribution.json"
    config.parent.mkdir()
    config.write_text('{"version":1}', encoding="utf-8")
    is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda path: False if path == config else is_file(path))
    monkeypatch.setattr(profile.os, "open", lambda *args: pytest.fail("must not open"))
    data, errors = profile.load_profile(tmp_path, None, {})
    assert errors == ["profile layer 1 rejected"]
    assert not data["_inputs"]


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFOs unavailable")
def test_fifo_profile_does_not_block_loading(tmp_path):
    config = tmp_path / ".copilot" / "ai-attribution.json"
    config.parent.mkdir()
    os.mkfifo(config)
    _, errors = profile.load_profile(tmp_path, None, {})
    assert errors == ["profile layer 1 rejected"]


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
    config.unlink()
    result = subprocess.run(command, input="[" * 10000 + "]" * 10000,
                            text=True, capture_output=True, env=env)
    assert result.returncode == 2
    assert "error" in json.loads(result.stdout)
    env["COPILOT_CUSTOM_INSTRUCTIONS_DIRS"] = "~missing-attribution-user/instructions"
    result = subprocess.run(command, input="{}", text=True, capture_output=True, env=env)
    assert result.returncode == 2
    assert "discovery rejected" in json.loads(result.stdout)["error"]


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.parametrize("home_suffix", ["", os.sep], ids=["plain-home", "trailing-separator"])
def test_native_hook_reads_profile_changes_with_warm_base_cache(tmp_path, home_suffix):
    shell = (shutil.which("pwsh") or shutil.which("powershell.exe")) if os.name == "nt" else shutil.which("bash")
    if not shell:
        pytest.skip("native shell unavailable")
    home, repo = tmp_path / "home", tmp_path / "repo"
    home.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    config = home / ".copilot" / "ai-attribution.json"
    config.parent.mkdir()
    env = {**os.environ, "HOME": str(home) + home_suffix, "USERPROFILE": str(home) + home_suffix,
           "XDG_CONFIG_HOME": str(home / "config"), "COPILOT_CUSTOM_INSTRUCTIONS_DIRS": "",
           "COPILOT_PLUGIN_ROOT": str(SCRIPTS.parent),
           "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]}
    env.pop("AI_ATTRIBUTION_FORCE_REFRESH", None)
    script = SCRIPTS / ("write-session-guidance.ps1" if os.name == "nt" else "write-session-guidance.sh")
    command = [shell, "-NoProfile", "-File", str(script)] if os.name == "nt" else [shell, str(script)]
    warm_env = dict(env)
    if os.name == "nt":
        warm_env["PATH"] = os.pathsep.join((
            str(Path(shutil.which("git")).parent), str(Path(shell).parent),
            str(Path(os.environ["SystemRoot"]) / "System32"),
        ))
    elif shutil.which("jq") and (shutil.which("sha256sum") or shutil.which("shasum")):
        tools = tmp_path / "native-tools"
        tools.mkdir()
        for name in ("git", "jq", "sha256sum", "shasum", "cat", "cut", "date",
                     "awk", "dirname", "wc", "mkdir", "mv", "rm"):
            executable = shutil.which(name)
            if executable:
                (tools / name).symlink_to(executable)
        warm_env["PATH"] = str(tools)
    else:
        warm_env = None
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
            if warm_env is not None:
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
    contained = repo / "contained"
    contained.mkdir()
    (contained / "ai-attribution.json").write_text(
        json.dumps(parsed({"header": "AI excluded"})), encoding="utf-8"
    )
    env["COPILOT_CUSTOM_INSTRUCTIONS_DIRS"] = os.pathsep.join(map(str, (
        shared[0], shared[0], contained, shared[1],
    )))
    session = "roster-normalized"
    subprocess.run(command, input=json.dumps({"sessionId": session, "cwd": str(repo)}),
                   text=True, capture_output=True, env=env, check=True)
    target = home / ".copilot" / "session-state" / session / "instructions" / "ai-attribution" / "session-guidance.instructions.md"
    expected = target.read_text(encoding="utf-8")
    assert "AI excluded" not in expected
    assert expected.count("[AI shared-a]") == 1
    if warm_env is not None:
        warm_env["COPILOT_CUSTOM_INSTRUCTIONS_DIRS"] = env["COPILOT_CUSTOM_INSTRUCTIONS_DIRS"]
        session += "-no-python"
        subprocess.run(command, input=json.dumps({"sessionId": session, "cwd": str(repo)}),
                       text=True, capture_output=True, env=warm_env, check=True)
        target = target.parents[3] / session / "instructions" / "ai-attribution" / target.name
        assert target.read_text(encoding="utf-8") == expected
    env["COPILOT_CUSTOM_INSTRUCTIONS_DIRS"] = os.pathsep.join(
        str(tmp_path / f"missing-{i}") for i in range(129)
    )
    for cache in (home / ".copilot" / "ai-attribution-cache").glob("*.json"):
        cache.unlink()
    for index in range(2):
        session = f"discovery-error-{index}"
        subprocess.run(command, input=json.dumps({"sessionId": session, "cwd": str(repo)}),
                       text=True, capture_output=True, env=env, check=True)
        target = home / ".copilot" / "session-state" / session / "instructions" / "ai-attribution" / target.name
        text = target.read_text(encoding="utf-8")
        assert text.startswith("Disclose AI for ALL contributions.\n\n")
        assert "profile discovery rejected" in text
        assert "[owner: ai-attribution@" in text
    missing_parent = tmp_path / "missing-parent"
    missing_parent.mkdir()
    env["COPILOT_CUSTOM_INSTRUCTIONS_DIRS"] = str(missing_parent)
    subprocess.run(command, input=json.dumps({"sessionId": "before-link", "cwd": str(repo)}),
                   text=True, capture_output=True, env=env, check=True)
    missing_parent.rmdir()
    empty_target = tmp_path / "empty-target"
    empty_target.mkdir()
    directory_link(missing_parent, empty_target)
    session = "after-link"
    subprocess.run(command, input=json.dumps({"sessionId": session, "cwd": str(repo)}),
                   text=True, capture_output=True, env=env, check=True)
    target = home / ".copilot" / "session-state" / session / "instructions" / "ai-attribution" / target.name
    text = target.read_text(encoding="utf-8")
    assert text.startswith("Disclose AI for ALL contributions.\n\n")
    assert "profile layer" in text and "rejected" in text
    external_link = tmp_path / "external-link"
    directory_link(external_link, contained)
    env["COPILOT_CUSTOM_INSTRUCTIONS_DIRS"] = str(external_link)
    session = "linked-into-target"
    subprocess.run(command, input=json.dumps({"sessionId": session, "cwd": str(repo)}),
                   text=True, capture_output=True, env=env, check=True)
    target = target.parents[3] / session / "instructions" / "ai-attribution" / target.name
    assert target.read_text(encoding="utf-8").startswith("Disclose AI for ALL contributions.")
    if os.name == "nt":
        external_link.rmdir()
    else:
        external_link.unlink()
    external_link.mkdir()
    (external_link / "ai-attribution.json").write_text(
        json.dumps(parsed({"header": "AI external", "required": True})), encoding="utf-8"
    )
    session = "replaced-with-external-defaults"
    subprocess.run(command, input=json.dumps({"sessionId": session, "cwd": str(repo)}),
                   text=True, capture_output=True, env=env, check=True)
    target = target.parents[3] / session / "instructions" / "ai-attribution" / target.name
    text = target.read_text(encoding="utf-8")
    assert "AI external" in text and '"required": true' in text
    excluded = repo / "excluded-safe"
    excluded.mkdir()
    env["COPILOT_CUSTOM_INSTRUCTIONS_DIRS"] = str(excluded)
    subprocess.run(command, input=json.dumps({"sessionId": "excluded-before", "cwd": str(repo)}),
                   text=True, capture_output=True, env=env, check=True)
    excluded.rmdir()
    directory_link(excluded, external_link)
    session = "excluded-after"
    subprocess.run(command, input=json.dumps({"sessionId": session, "cwd": str(repo)}),
                   text=True, capture_output=True, env=env, check=True)
    target = target.parents[3] / session / "instructions" / "ai-attribution" / target.name
    assert target.read_text(encoding="utf-8").startswith("Disclose AI for ALL contributions.")
