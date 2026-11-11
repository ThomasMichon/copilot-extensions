"""Original-owner regex parity and optional native argument-proof compatibility."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess

import pytest

from test_managed_session import SCRIPTS, environment, load, no_window_kwargs


@pytest.mark.skipif(os.name != "nt", reason="Native Windows regex and shared proof consumers")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
def test_native_shell_arguments_preserve_original_predicates_and_rollback(tmp_path, shell):
    executable = shutil.which(shell)
    if not executable:
        if os.environ.get("AGENT_WORKTREES_REQUIRE_NATIVE_MEMBERSHIP") == "1":
            pytest.fail(f"{shell} is required")
        pytest.skip(f"{shell} is unavailable")
    policy = json.loads((SCRIPTS / "hook-read-policy.json").read_text("utf-8"))
    facts = policy["nativeShellReadArguments"]
    relevance = load("hook_pre_relevance")
    originals = [
        relevance._SAFE_READ_SHELL,
        *(load(name)._WRITE_VERBS for name in (
            "statelessness_guard", "cross_repo_guard", "anchor_write_guard",
        )),
        load("pr_supersede_guard")._GH_PR_CLOSE,
        load("pr_supersede_guard")._GH_API_PULL,
    ]
    indicators = (
        "Set-Content", "Add-Content", "Out-File", "New-Item", "Remove-Item",
        "Move-Item", "Copy-Item", "Clear-Content", "Rename-Item", "Set-ItemProperty",
        "Tee-Object", ">", ">>", "tee", "sed -i", "cp", "mv", "rm", "touch",
        "mkdir", "dd", "truncate", "patch", "git add", "git -C path commit",
        "git apply", "git mv", "git rm", "git checkout", "git restore", "git status",
        "gh pr close 1", "gh pr close 1 --repo example/project",
        "gh api repos/example/project/pulls/1",
        "gh api github.com/repos/example/project/pulls/1 -f state=closed",
        "gh pr close 1suffix", "gh api /repos/example/project/pulls/1suffix",
    )
    commands = [
        head + suffix for head in relevance.SHELL_READ_HEADS
        for suffix in ("", " .", " .\\read.txt -Tail 1", " C:\\path-name\\*.txt -Name")
    ]
    commands += [
        prefix + indicator + suffix
        for indicator in indicators
        for prefix, suffix in (("", ""), ("cat ", ""), ("cat path-", ".txt"))
    ]
    commands += [
        " \tget-content .\\read.txt\t ", "cat .\n", "cat .\r\n", "cat .\v",
        "cat 'read.txt'", 'cat "read.txt"', "cat $(pwd)", "cat .; ls .",
        "cat .|ls .", "cat .&ls .", "cat .`n", "cat . # comment",
        "cat /a/tee-file", "cat /a/teeth", "cat /a/widget", "cat /a/git-file",
    ]
    expected_regex = [
        [bool(originals[0].fullmatch(command)),
         *(bool(pattern.search(command)) for pattern in originals[1:])]
        for command in commands
    ]
    cases = []

    def add(args, expected, *, key="toolArgs", declaration=None, prior=False, batch=None):
        event = {"toolName": "powershell", key: args}
        if key == "toolArgs":
            event["tool_input"] = {"command": "Set-Content ignored"}
        if batch is not None:
            event["toolCalls"] = batch
        cases.append({
            "event": event, "expected": expected,
            "policy": json.dumps(policy if declaration is None else declaration),
            "prior": prior,
        })

    for head in relevance.SHELL_READ_HEADS:
        command = " \t" + head.swapcase() + " .\\read.txt -Tail 1\t"
        assert relevance._SAFE_READ_SHELL.fullmatch(command)
        assert not any(pattern.search(command) for pattern in originals[1:])
        for carrier in ("toolArgs", "tool_input"):
            add(command, True, key=carrier)
            for index, key in enumerate(policy["nativeShellReadCommandKeys"]):
                args = {earlier: None for earlier in policy["nativeShellReadCommandKeys"][:index]}
                args[key] = command
                args.update(description="Fixture read", path="efforts/private.md")
                add(args, True, key=carrier)
    for command in commands:
        if command in relevance.SHELL_READ_HEADS:
            expected = True
        else:
            expected = (
                len(command.strip(" \t").split()) > 1
                and all(char == "\t" or 32 <= ord(char) <= 126 for char in command)
                and bool(originals[0].fullmatch(command))
                and not any(pattern.search(command) for pattern in originals[1:])
            )
        add({"command": command}, expected)
    for args in (None, False, "", {}, {"command": " "}, '{"command":"cat ."}',
                 '["cat ."]', "null", {"command": "cat \u0131"}, {"Command": "cat ."}):
        add(args, False)
    add({"command": None, "cmd": "cat .", "input": "Set-Content ignored"}, True)
    add({"command": " ", "cmd": "cat ."}, False)
    add({"command": "Set-Content ignored", "cmd": "cat ."}, False)
    safe = [{"name": "powershell", "args": {"cmd": "cat ."}},
            {"name": "view", "args": {"command": "Set-Content ignored"}},
            {"name": "pwsh", "args": "ls ."}, {"name": "powershell", "args": "Get-Location"}]
    add({"command": "Set-Content ignored"}, True, batch=safe)
    for index in range(len(safe) + 1):
        for member in ({"name": "powershell", "args": {"command": "cat git"}},
                       {"name": "powershell", "args": '{"command":"cat ."}'},
                       {"name": "unknown", "args": {"command": "cat ."}}, None):
            batch = copy.deepcopy(safe)
            batch.insert(index, member)
            add({"command": "cat ."}, False, batch=batch)
    add("cat .", True, batch=[safe[0]] * 128)
    add("cat .", False, batch=[safe[0]] * 129)
    add("cat .", False, batch=[])
    add("cat .", False, batch={"name": "powershell", "args": "cat ."})
    invalid = [None, False, [], {}, {"version": 2, **{k: v for k, v in facts.items() if k != "version"}},
               {**facts, "read": ""}, {**facts, "read": "("}, {**facts, "reject": []},
               {**facts, "reject": [*facts["reject"][:4], False]},
               {**facts, "reject": [*facts["reject"][:4], "("]}]
    legacy = copy.deepcopy(policy)
    del legacy["nativeShellReadArguments"]
    declarations = [legacy, *({**policy, "nativeShellReadArguments": bad} for bad in invalid)]
    for declaration in declarations:
        add("cat .", False, declaration=declaration)
        add("Get-Location", True, declaration=declaration)
        add("cat .", False, declaration=declaration, batch=safe)
    add("cat .", False, prior=True)
    add("Get-Location", True, prior=True)
    add("cat .", False, prior=True, batch=safe)
    add("Get-Location", True, prior=True,
        batch=[{"name": "powershell", "args": "Get-Location"}, {"name": "view", "args": {}}])

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in (*policy["guards"], *policy["nativeWriteDependencies"],
                 "hook_pre_relevance.py", "managed-session.ps1"):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    source = (SCRIPTS / "pre-tool-hook.ps1").read_text("utf-8")
    source = source[:source.index("\nfunction Read-NativePreToolText(")]
    start = source.index("            $readArguments = $null")
    end = source.index("            $shellDependencies =", start)
    prior = source[:start] + (
        "            foreach ($command in $selectedCommands) {\n"
        "                if (-not $heads.Contains($command)) { return $false }\n"
        "            }\n"
    ) + source[end:]
    previous_function = prior[prior.index("function Test-NativePreToolSources("):
                              prior.index("function Get-NativePreToolBatch(")]
    assert hashlib.sha256(previous_function.encode()).hexdigest() == (
        "58f58127ea9f13ab2ea41e5a24dbf012cb068025d093c98664f10bfc9edd7c99"
    )
    (scripts / "current.ps1").write_text(source, encoding="utf-8")
    (scripts / "prior.ps1").write_text(prior, encoding="utf-8")
    command = r"""
$ErrorActionPreference = 'Stop'
. './scripts/managed-session.ps1'
$inputData = ConvertFrom-ManagedWorktreesJson ([Console]::In.ReadToEnd())
[Threading.Thread]::CurrentThread.CurrentCulture = [Globalization.CultureInfo]::GetCultureInfo('tr-TR')
$options = [Text.RegularExpressions.RegexOptions]::IgnoreCase -bor [Text.RegularExpressions.RegexOptions]::CultureInvariant
$predicates = @([regex]::new('\A(?:' + $inputData['read'] + ')\z', $options))
foreach ($pattern in $inputData['reject']) { $predicates += [regex]::new($pattern, $options) }
$rows = [Collections.Generic.List[object]]::new()
foreach ($text in $inputData['commands']) {
    $row = @()
    foreach ($predicate in $predicates) { $row += $predicate.IsMatch($text) }
    $rows.Add($row)
}
$proofs = @()
. './scripts/current.ps1'
$usingPrior = $false
$currentPolicy = $null
foreach ($case in $inputData['cases']) {
    if ($currentPolicy -cne $case['policy']) {
        [IO.File]::WriteAllText(($PWD.Path + '\scripts\hook-read-policy.json'), $case['policy'])
        $currentPolicy = $case['policy']
    }
    if ($usingPrior -ne $case['prior']) {
        if ($case['prior']) { . './scripts/prior.ps1' } else { . './scripts/current.ps1' }
        $usingPrior = $case['prior']
    }
    $proofs += Test-NativePreToolRead -Payload $case['event'] -PreparedMembership $true
}
[Console]::Out.Write((ConvertTo-PreparedWorktreesJson @{ regex = $rows.ToArray(); proofs = $proofs } -Depth 6))
"""
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", command],
        input=json.dumps({"read": facts["read"], "reject": facts["reject"],
                          "commands": commands, "cases": cases}).encode(),
        capture_output=True, timeout=10, cwd=tmp_path, env=environment(tmp_path, tmp_path),
        **no_window_kwargs(),
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    actual = json.loads(result.stdout)
    assert actual["regex"] == expected_regex
    assert len(actual["proofs"]) == len(cases)
    for case, proved in zip(cases, actual["proofs"], strict=True):
        assert proved is case["expected"], case["event"]
