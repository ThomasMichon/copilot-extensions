"""Literal Windows check/create overlap across the shipped native boundaries.

The mux protocol process is synthetic. No productive mux registry or backend
is used; these controls are not full Picker/embody or lifecycle-fence parity.
"""

import base64
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from single_instance_lease import AlreadyRunningError, SingleInstance


CSC = next((path for framework in ("Framework64", "Framework") if (
    path := Path(os.environ.get("WINDIR", "")) / "Microsoft.NET" /
    framework / "v4.0.30319" / "csc.exe"
).is_file()), None)
pytestmark = pytest.mark.skipif(
    os.name != "nt" or not shutil.which("pwsh") or CSC is None,
    reason="Native Windows PowerShell and system C# compiler",
)
ROOT = Path(__file__).resolve().parents[4]
LITERALS = ["child with spaces", 'quote "inside"', "tail\\", "", "--allow-all"]

MUX = r"""
using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
class Mux {
    static int Main(string[] args) {
        string verb = args[0];
        int index = Array.IndexOf(args, verb == "has-session" ? "-t" : "-s");
        string session = args[index + 1];
        if (verb == "has-session") {
            File.WriteAllText("queried-" + session + "-" +
                Environment.GetEnvironmentVariable("MUX_ACTOR_ID"), "");
            return File.Exists("live-" + session) ? 0 : 1;
        }
        string[] encoded = new string[args.Length];
        for (int i = 0; i < args.Length; i++)
            encoded[i] = Convert.ToBase64String(Encoding.UTF8.GetBytes(args[i]));
        File.WriteAllLines("args-" + session, encoded);
        using (new FileStream("entered-" + session, FileMode.CreateNew)) {}
        Stopwatch clock = Stopwatch.StartNew();
        foreach (string phase in new[] {"publish-", "return-"}) {
            while (!File.Exists(phase + session)) {
                if (clock.Elapsed.TotalSeconds >= 25) return 71;
                Thread.Sleep(20);
            }
            if (phase == "publish-") File.WriteAllText("live-" + session, "");
        }
        if (Array.IndexOf(args, "-P") >= 0) Console.WriteLine("%1");
        return 0;
    }
}
"""

ENGINE = r"""
import json, sys
from pathlib import Path
import os, time
home = Path(sys.argv[2])
actor = os.environ["MUX_ACTOR_ID"]
(home / ("boot-" + actor)).write_text(json.dumps({"home": str(home), "actor": actor}))
from agent_worktrees import sessions
sessions.activity.log_event = lambda *a, **k: None
sessions.build_mux_new_session_argv = lambda wt, cwd, *a, **k: [
    sys.argv[3], "new-session", "-s", sessions.mux_session_name(wt),
    "-P", "-F", "#{pane_id}",
    "child with spaces", 'quote "inside"', "tail\\", "", "--allow-all",
]
(home / ("ready-" + actor)).touch()
deadline = time.monotonic() + 60
while not (home / ("go-" + actor)).exists():
    if time.monotonic() >= deadline:
        raise SystemExit(73)
    time.sleep(0.02)
result = sessions.mux_new_session(sys.argv[1], sys.argv[2], ["unused"], mux=sys.argv[3])
print(json.dumps(result))
raise SystemExit(0 if result["ok"] else 1)
"""


def _function(source, name):
    start = source.index("function " + name + " {")
    return source[start:source.index("\n}\n", start) + 3]


def _manager_driver(path):
    source = (ROOT / "worktree-manager/bin/launch-session.ps1").read_text("utf-8")
    helpers = "\n".join(_function(source, name) for name in (
        "Invoke-AwPsmuxFromHome", "Test-AwMuxCreationSession", "Invoke-AwPsmuxCreation",
        "Enter-AwMuxCreation",
    ))
    start = source.index("                try {\n", source.index("$savedAuth = $null"))
    end = source.index("\n                    $newSessionExit", start)
    creation = source[start + len("                try {\n"):end]
    path.write_text(
        "$ErrorActionPreference='Stop'\n"
        "$LASTEXITCODE=41\n"
        "$script:AwPsmuxBin=$args[0]\n$sessName='wt-'+$args[1]\n"
        "$plan=@{work_dir=$args[2]}\n$envFlags=@('-e','FIXTURE=two words')\n"
        "$paneCmd=@('child with spaces','quote \"inside\"','tail\\','','--allow-all')\n"
        "$script:CleanupCalled=$false\n"
        "function Stop-AwOwnedPsmuxSession { $script:CleanupCalled=$true }\n"
        "$joinedDuringCreate=$false\n" + helpers + "\n"
        "$ready=Join-Path $HOME ('ready-'+$env:MUX_ACTOR_ID)\n"
        "$go=Join-Path $HOME ('go-'+$env:MUX_ACTOR_ID)\n"
        "$boot=Join-Path $plan.work_dir ('boot-'+$env:MUX_ACTOR_ID)\n"
        "[IO.File]::WriteAllText($boot,('home='+$HOME+'; ready='+$ready))\n"
        "[IO.File]::WriteAllText($ready,'')\n"
        "$deadline=[DateTime]::UtcNow.AddSeconds(60)\n"
        "while(-not [IO.File]::Exists($go)){\n"
        "if([DateTime]::UtcNow -ge $deadline){exit 73}\n"
        "Start-Sleep -Milliseconds 20\n}\n" + creation + "\n"
        "ConvertTo-Json -Compress @{created=(-not $joinedDuringCreate);"
        "code=$LASTEXITCODE;cleanup=$script:CleanupCalled}\n",
        encoding="utf-8",
    )


def _wait(check, message, actors=(), *, timeout=15, diagnostic=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for actor in actors:
            if actor.poll() is not None:
                stdout, stderr = actor.communicate(timeout=5)
                pytest.fail(message + "\nActor exited: " + stdout + stderr)
        if check():
            return
        time.sleep(0.02)
    pytest.fail(message + ("\n" + str(diagnostic()) if diagnostic else ""))


@pytest.mark.parametrize("first,second", [("manager", "engine"), ("engine", "manager")])
def test_same_target_joins_at_discovery_while_other_target_creates_in_parallel(
    tmp_path, first, second,
):
    home = tmp_path / "home with spaces"
    home.mkdir()
    mux_source = tmp_path / "mux.cs"
    mux_source.write_text(MUX, encoding="utf-8")
    binary = tmp_path / "psmux.exe"
    compiled = subprocess.run(
        [str(CSC), "/nologo", "/target:exe", "/out:" + str(binary), str(mux_source)],
        capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    driver = tmp_path / "manager.ps1"
    _manager_driver(driver)
    engine = tmp_path / "engine.py"
    engine.write_text(ENGINE, encoding="utf-8")
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    actors = []
    identities = {}

    def start(kind, wt):
        identity = kind + "-" + str(len(actors))
        argv = (
            [shutil.which("pwsh"), "-NoLogo", "-NoProfile", "-File", str(driver),
            str(binary), wt, str(home)]
            if kind == "manager" else [sys.executable, str(engine), wt, str(home), str(binary)]
        )
        child = subprocess.Popen(
            argv, env={**env, "MUX_ACTOR_ID": identity}, cwd=home,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        actors.append(child)
        identities[child] = identity
        return child

    creator = start(first, "shared")
    try:
        contender = start(second, "shared")
        parallel = start(second, "other")
        _wait(
            lambda: all((home / ("ready-" + identities[actor])).exists() for actor in actors),
            "Actors did not finish preparation before native creation", actors, timeout=45,
            diagnostic=lambda: {
                "expected": list(identities.values()),
                "ready": sorted(path.name for path in home.glob("ready-*")),
                "boot": {path.name: path.read_text("utf-8-sig") for path in home.glob("boot-*")},
            },
        )
        (home / ("go-" + identities[creator])).touch()
        _wait(lambda: (home / "entered-wt-shared").exists(),
              "Creator never entered native creation", actors)
        (home / ("go-" + identities[contender])).touch()
        _wait(
            lambda: (home / ("queried-wt-shared-" + identities[contender])).exists(),
            "Contender never queried the real protocol process", actors,
        )
        assert contender.poll() is None
        (home / ("go-" + identities[parallel])).touch()
        _wait(lambda: (home / "entered-wt-other").exists(),
              "Different target was serialized", actors)
        for wt in ("shared", "other"):
            observed = [
                base64.b64decode(line).decode("utf-8")
                for line in (home / ("args-wt-" + wt)).read_text("utf-8-sig").splitlines()
            ]
            assert observed[-len(LITERALS):] == LITERALS
        assert creator.poll() is None and parallel.poll() is None
        (home / "publish-wt-shared").touch()
        stdout, stderr = contender.communicate(timeout=10)
        assert contender.returncode == 0, stdout + stderr
        joined = json.loads(stdout)
        assert joined["created"] is False
        if "code" in joined:
            assert joined["code"] == 0
        key = hashlib.sha256(b"wt-shared").hexdigest()
        lease = SingleInstance(home / ".psmux" / "launch-locks", lock_name=key + ".lock")

        def released():
            try:
                lease.acquire()
            except AlreadyRunningError:
                return False
            lease.release()
            return True

        _wait(released, "Creator retained the kernel lease after discoverability", [creator])
        assert creator.poll() is None, "Join waited for the creator's return instead of discovery"
        assert (home / "entered-wt-shared").exists()
    finally:
        failing = sys.exc_info()[0] is not None
        errors = []
        for wt in ("shared", "other"):
            (home / ("publish-wt-" + wt)).touch()
            (home / ("return-wt-" + wt)).touch()
        for actor in actors:
            (home / ("go-" + identities[actor])).touch()
        for child in actors:
            try:
                stdout, stderr = child.communicate(timeout=10)
                if child.returncode != 0:
                    errors.append(stdout + stderr)
                elif not failing and child is not contender:
                    report = json.loads(stdout)
                    if "code" in report and report["code"] != 0:
                        errors.append("Native creation retained a stale exit code: " + stdout)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate(timeout=5)
        if not failing:
            assert not errors, "\n".join(errors)
    with lease:
        pass
