"""Pure contracts for the explicitly opt-in measurement launcher prototype."""
import builtins
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools.coverage_guided_selection import measurement_launch as launch


@pytest.mark.parametrize("guard_active", [False, True])
def test_import_profile_reporting_preserves_active_child_thread_guard(tmp_path, monkeypatch, guard_active):
    import io
    from types import SimpleNamespace

    calls = []
    runtime = SimpleNamespace(
        _started=True, stop_import_profile=lambda: calls.append(("stop-diagnostic", None)),
    )
    monkeypatch.setattr(launch, "sys", SimpleNamespace(
        _cgs_import_phases=[], modules={"_cgs_child_stream": runtime} if guard_active else {},
        setprofile=lambda function: calls.append(("setprofile", function)),
    ))
    for key, value in (
        ("CGS_PRELUDE_PHASE_PROFILE", "1"), ("_CGS_PRELUDE_NS", "100"),
        ("_CGS_PRELUDE_PHASES", "timer-start:100:10;"),
        ("_CGS_PRELUDE_SCHED_START", "10 20 1"),
        ("_CGS_PRELUDE_CGROUP_START", "usage_usec 10\n"),
    ):
        monkeypatch.setenv(key, value)
    original_open = builtins.open
    def opened(path, *args, **kwargs):
        if path == "/proc/self/schedstat":
            return io.StringIO("20 30 2")
        if path == "/sys/fs/cgroup/cpu.stat":
            return io.StringIO("usage_usec 20\n")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(launch, "open", opened, raising=False)
    launch.report_prelude(tmp_path, start_ns=100, end_ns=300, end_cpu_ns=30, duration_us=0)
    assert calls == [(("stop-diagnostic" if guard_active else "setprofile"), None)]
    report = json.loads(next(tmp_path.glob("prelude-phases-*.json")).read_text())
    assert report["diagnostic_only"] and report["imports"] == []
    assert report["timer_start_ns"] == 100 and report["timer_end_ns"] == 300


@pytest.mark.parametrize("value", [
    None, True, False, 0, 1, -1, 2**100, 0.0, -0.0, 5e-324, 1.7976931348623157e308,
    "\0\b\f\n\r\t\"\\/\U0001f680\ud800\udfff", [],
    {"nonce":"a"*64,"context":"unicode \U0001f680","pid":123,"inventory":{"1":{"lines":2}}},
    {"nested":[None,True,1,False,0,{"escapes":"\u2028\u2029","float":1e30}]},
    {1:"int",2:"two",10:"ten"}, {True:"bool",False:"false",None:"null"},
])
@pytest.mark.parametrize("options", [
    {}, {"sort_keys":True,"separators":(",",":"),"ensure_ascii":True,"allow_nan":False},
    {"sort_keys":True,"ensure_ascii":False}, {"separators":(" | "," = ")},
])
def test_child_json_encoding_is_byte_exact_to_stdlib(value,options):
    import io
    codec=launch.ChildJSON()
    try:
        expected=json.dumps(value,**options)
    except (ValueError,TypeError):
        with pytest.raises((ValueError,TypeError)):
            codec.dumps(value,**options)
        with pytest.raises((ValueError,TypeError)):
            codec.dump(value,io.StringIO(),**options)
        return
    assert codec.dumps(value,**options)==expected
    stream=io.StringIO()
    codec.dump(value,stream,**options)
    assert stream.getvalue()==expected


@pytest.mark.parametrize("text", [
    ' \t\r\n{"a":[true,1,false,0,null],"b":"\\ud83d\\ude80\\ud800\\u0000"} \t\n',
    '{"duplicate":false,"duplicate":1,"duplicate":true}', '123456789012345678901234567890',
    '[-0.0,5e-324,1.7976931348623157e308,1e400,-1e400,NaN,Infinity,-Infinity]',
    '[]', 'null', 'true', '1', '"unicode \U0001f680"', '['*128+'0'+']'*128,
])
def test_child_json_decoding_preserves_types_duplicates_numbers_and_unicode(text):
    codec=launch.ChildJSON()
    # Canonical re-encoding distinguishes bool/int, negative zero and nonfinite values.
    assert json.dumps(codec.loads(text),ensure_ascii=True)==json.dumps(json.loads(text),ensure_ascii=True)


@pytest.mark.parametrize("encoding", [
    "utf-8","utf-8-sig","utf-16","utf-16-be","utf-16-le","utf-32","utf-32-be","utf-32-le",
])
@pytest.mark.parametrize("text", ['"unicode \U0001f680"','1','[]'])
def test_child_json_bytes_encoding_detection_matches_stdlib(encoding,text):
    codec=launch.ChildJSON()
    for value in (text.encode(encoding),bytearray(text.encode(encoding))):
        assert codec.loads(value)==json.loads(value)


@pytest.mark.parametrize("value", [
    "", " ", "\ufeff{}", "{", "[1,]", '{"a":}', '{"a":1,}', '"raw\0control"', '"\\x01"',
    "true trailing", "null null", "01", "1.", "+1", "nan", '"unterminated',
    b"\xff", b"\x00", b"\xef\xbb\xbf", 1, True, None,
])
def test_child_json_invalid_trailing_depth_integer_and_type_refusals(value):
    codec=launch.ChildJSON()
    try:
        json.loads(value)
    except (ValueError,TypeError,RecursionError):
        with pytest.raises((ValueError,TypeError,RecursionError)):
            codec.loads(value)
    else:
        pytest.fail("refusal vector unexpectedly accepted by stdlib")


@pytest.mark.parametrize("size",[128,2000,20000])
@pytest.mark.parametrize("kind",["array-depth","integer-digits"])
def test_child_json_inherits_native_depth_and_integer_limits(size,kind):
    codec=launch.ChildJSON()
    raw="["*size+"0"+"]"*size if kind=="array-depth" else "9"*size
    def outcome(loader):
        try:
            value=loader(raw)
        except (ValueError,TypeError,RecursionError) as exc:
            return ("refused",type(exc).__name__)
        depth=0
        while isinstance(value,list):
            assert len(value)==1
            value=value[0]
            depth+=1
        return ("accepted",depth,value)
    assert outcome(codec.loads)==outcome(json.loads)


@pytest.mark.parametrize("value",[float("nan"),float("inf"),-float("inf"),object(),{object():1}])
def test_child_json_strict_encoder_refuses_nonfinite_and_unsupported_types(value):
    codec=launch.ChildJSON()
    with pytest.raises((ValueError,TypeError)):
        codec.dumps(value,allow_nan=False)


def test_child_json_circular_failure_does_not_poison_next_encoding():
    codec=launch.ChildJSON()
    cycle=[]
    cycle.append(cycle)
    with pytest.raises(ValueError):
        codec.dumps(cycle)
    assert codec.dumps({"ok":[1,True]})==json.dumps({"ok":[1,True]})


@pytest.mark.parametrize("failure",["missing","scanner-signature","encoder-signature","wrong-output"])
def test_child_json_unsupported_capability_never_falls_back(failure):
    import _json
    from types import SimpleNamespace
    backend=SimpleNamespace(**{name:getattr(_json,name) for name in (
        "make_scanner","make_encoder","encode_basestring","encode_basestring_ascii")})
    if failure=="missing":
        del backend.make_scanner
    elif failure=="scanner-signature":
        backend.make_scanner=lambda:None
    elif failure=="encoder-signature":
        backend.make_encoder=lambda:None
    else:
        backend.make_encoder=lambda *args:lambda *values:["{}"]
    with pytest.raises(RuntimeError,match="unsupported CPython _json"):
        launch.ChildJSON(backend)


def test_child_json_refuses_non_cpython_implementation(monkeypatch):
    from types import SimpleNamespace
    with monkeypatch.context() as patch:
        patch.setattr(sys,"implementation",SimpleNamespace(name="unsupported"))
        with pytest.raises(RuntimeError,match="requires supported CPython"):
            launch.ChildJSON()


@pytest.mark.parametrize("value",[float("nan"),float("inf"),-float("inf")])
def test_child_json_nonfinite_signed_data_is_refused_before_file_creation(tmp_path,monkeypatch,value):
    codec=launch.ChildJSON()
    monkeypatch.setattr(launch,"json",codec)
    path=tmp_path/"must-not-exist.json"
    with pytest.raises(ValueError):
        launch.signed_write(path,{"untrusted":value},b"offline-test-key")
    assert not path.exists()
    path.write_text(json.dumps({"payload":{"untrusted":value},"signature":"unearned"}))
    with pytest.raises(ValueError):
        launch.signed_read(path,b"offline-test-key")


@pytest.mark.parametrize("writer_backend,reader_backend",[(False,True),(True,False),(True,True)])
def test_child_json_signed_records_are_cross_backend_byte_identical(
    tmp_path,monkeypatch,writer_backend,reader_backend,
):
    import secrets
    codec=launch.ChildJSON()
    key=secrets.token_bytes(32)
    payload={"context":"\ud800 \U0001f680","inventory":{1:{"lines":2}},"values":[True,1,None,-0.0]}
    reference=tmp_path/"reference.json"
    launch.signed_write(reference,payload,key)
    actual=tmp_path/"actual.json"
    monkeypatch.setattr(launch,"json",codec if writer_backend else json)
    launch.signed_write(actual,payload,key)
    assert actual.read_bytes()==reference.read_bytes()
    monkeypatch.setattr(launch,"json",codec if reader_backend else json)
    assert launch.signed_read(actual,key)==json.loads(json.dumps(payload))
    changed=json.loads(actual.read_text())
    changed["payload"]["values"][0]=1
    actual.write_text(json.dumps(changed))
    with pytest.raises(ValueError,match="signature"):
        launch.signed_read(actual,key)
    oversized=tmp_path/"oversized.json"
    oversized.write_bytes(b" "*(1_048_576+1))
    with pytest.raises(ValueError,match="unsafe measurement record"):
        launch.signed_read(oversized,key)


@pytest.mark.parametrize("arguments,mode,prefix,program,tail", [
    (["python", "-I", "-S", "-B", "-c", "pass", "arg"], "c", ["python","-I","-S","-B"], "pass", ["arg"]),
    (["python", "-ISBcpass", "arg"], "c", ["python","-ISB"], "pass", ["arg"]),
    (["python", "-I", "-m", "package", "--help"], "m", ["python","-I"], "package", ["--help"]),
    (["python", "-I", "script.py", "--version"], "script", ["python","-I"], "script.py", ["--version"]),
])
def test_original_program_boundaries(arguments, mode, prefix, program, tail):
    assert launch.invocation(arguments) == (mode, prefix, program, tail)
    physical = launch.rewritten(arguments, "owned/request")
    assert physical[:len(prefix)] == prefix
    assert physical[len(prefix)] == "-c"
    assert physical[-len(tail):] == tail


def test_phase_diagnostics_are_explicit_and_preserve_native_flags(monkeypatch):
    arguments=["python","-I","-S","-B","-c","pass","application-argument"]
    monkeypatch.delenv("CGS_PRELUDE_PHASE_PROFILE",raising=False)
    monkeypatch.delenv("CGS_PRELUDE_IMPORT_PROFILE",raising=False)
    ordinary=launch.rewritten(arguments,"owned/request")
    assert "_CGS_PRELUDE_PHASES" not in ordinary[5]
    for imports in ("0","1"):
        monkeypatch.setenv("CGS_PRELUDE_PHASE_PROFILE","1")
        monkeypatch.setenv("CGS_PRELUDE_IMPORT_PROFILE",imports)
        diagnostic=launch.rewritten(arguments,"owned/request")
        assert diagnostic[:5]==ordinary[:5]==arguments[:5]
        assert diagnostic[-1]==arguments[-1]
        assert "_CGS_PRELUDE_PHASES" in diagnostic[5]
        compile(diagnostic[5],"<measurement-diagnostic>","exec")


def test_verified_loader_compiles_exact_checked_bytes_without_rereading(tmp_path,monkeypatch):
    import hashlib
    filename=tmp_path/"changed-after-read.py"
    source=b"observed=(__name__,__file__,__package__,__spec__,__cached__,__loader__,__doc__)\n"
    filename.write_bytes(b"raise AssertionError('unchecked reread')\n")
    original_compile=builtins.compile
    calls=[]
    def checked_compile(value,*args,**kwargs):
        calls.append((value,kwargs))
        return original_compile(value,*args,**kwargs)
    monkeypatch.setattr(builtins,"compile",checked_compile)
    module=launch.verified_namespace(source,str(filename),"_owned_helper",hashlib.sha256(source).hexdigest())
    assert module["observed"]==("_owned_helper",str(filename),"",None,None,None,None)
    assert len(calls)==1 and calls[0][0] is source and calls[0][1]["dont_inherit"] is True


def test_verified_loader_refuses_changed_bytes_before_execution(tmp_path):
    import hashlib
    marker=tmp_path/"must-not-exist"
    original=b"pass\n"
    changed=f"open({str(marker)!r},'w').close()\n".encode()
    with pytest.raises(ValueError,match="helper source changed"):
        launch.verified_namespace(changed,"owned.py","_owned_helper",hashlib.sha256(original).hexdigest())
    assert not marker.exists()


def test_verified_loader_does_not_inherit_bootstrap_future_flags():
    import hashlib
    source=b"def f(value: UndefinedHelperType):pass\n"
    with pytest.raises(NameError,match="UndefinedHelperType"):
        launch.verified_namespace(source,"owned.py","_owned_helper",hashlib.sha256(source).hexdigest())


def test_c_program_globals_and_argv_are_not_polluted(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["-c", "original"])
    monkeypatch.setattr(sys, "orig_argv", ["host"])
    namespace = {"__name__": "__main__", "__package__": None, "__spec__": None,
                 "__builtins__": builtins, "__doc__": None}
    original = set(namespace)
    code = "import sys; observed=(sys.argv[:],sys.orig_argv[:],set(globals()))"
    arguments = ["native-python", "-I", "-S", "-B", "-c", code, "value"]
    calls = []
    launch.execute({"argv": arguments}, namespace, lambda value: calls.append(value))
    argv, original_argv, keys = namespace["observed"]
    assert argv == ["-c", "value"] and original_argv == arguments
    assert keys == original | {"sys"}
    assert len(calls) == 1


def test_system_exit_is_not_converted(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["-c"])
    monkeypatch.setattr(sys, "orig_argv", ["host"])
    with pytest.raises(SystemExit) as exc:
        launch.execute({"argv":["python","-I","-S","-c","raise SystemExit(17)"]}, {}, lambda value: None)
    assert exc.value.code == 17


def test_bootstrap_future_flags_do_not_change_program_annotations(monkeypatch):
    monkeypatch.setattr(sys,"argv",["-c"])
    monkeypatch.setattr(sys,"orig_argv",["host"])
    with pytest.raises(NameError):
        launch.execute(
            {"argv":["python","-I","-S","-c",
                     "def f(value: UndefinedApplicationType): pass\nf.__annotations__"]},
            {},lambda value:None,
        )


def test_generated_offline_signature_is_required(tmp_path):
    import secrets
    key = secrets.token_bytes(32)
    path = tmp_path / "request.json"
    launch.signed_write(path, {"identity":"offline-measurement"}, key)
    assert launch.signed_read(path,key)["identity"] == "offline-measurement"
    record = json.loads(path.read_text())
    record["payload"]["identity"] = "tampered"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="signature"):
        launch.signed_read(path,key)


def test_signed_inventory_ids_have_one_stable_json_representation(tmp_path):
    import secrets
    key=secrets.token_bytes(32)
    path=tmp_path/"inventory.json"
    value={"inventory":{1:{"line":1},2:{"line":2},10:{"line":10}}}
    launch.signed_write(path,value,key)
    assert launch.signed_read(path,key)=={
        "inventory":{"1":{"line":1},"2":{"line":2},"10":{"line":10}},
    }


def test_issued_missing_request_cannot_disappear_as_empty_success(tmp_path):
    activation=tmp_path/"activation.py"
    activation.write_text("def activate(payload):pass\n")
    registry=launch.Registry(tmp_path/"tickets",sys.executable,activation,"offline::missing")
    registry.requests.append(registry.root/"issued-but-missing")
    with pytest.raises(ValueError,match="issued request ledger"):
        registry.verify()


def test_extra_unissued_directory_is_refused_without_spawning(tmp_path):
    activation=tmp_path/"activation.py"
    activation.write_text("def activate(payload):pass\n")
    registry=launch.Registry(tmp_path/"tickets",sys.executable,activation,"offline::extra")
    (registry.root/("f"*64)).mkdir()
    with pytest.raises(ValueError,match="extra issued request"):
        registry.verify()


@pytest.mark.parametrize("flag",["-x","-ISBx"])
def test_skip_first_line_is_explicitly_unsupported(flag):
    with pytest.raises(ValueError,match="skip-first-line"):
        launch.invocation(["python",flag,"program.py"])


def test_config_enabled_uv_refuses_unobservable_backend_without_changing_flags(tmp_path):
    artifact=tmp_path/"package.whl"
    artifact.write_bytes(b"fixture")
    python=os.path.normcase(os.path.abspath(sys.executable))
    argv=["uv","pip","install","--python",sys.executable,"--target",str(tmp_path/"sdk"),
          "--offline","--no-index","--no-deps",str(artifact)]
    with pytest.raises(ValueError,match="configuration-enabled UV observation"):
        launch.uv_wheel_contract(argv,{},python)
    explicit=[argv[0],"--no-config",*argv[1:]]
    launch.uv_wheel_contract(explicit,{},python)
    assert "--no-config" not in argv


@pytest.mark.parametrize("extra", [["--requirement","extra.txt"],["--compile-bytecode"],["source.tar.gz"]])
def test_uv_additional_unobservable_build_operations_are_refused(tmp_path,extra):
    artifact=tmp_path/"package.whl"
    artifact.write_bytes(b"fixture")
    python=os.path.normcase(os.path.abspath(sys.executable))
    argv=["uv","pip","install","--python",sys.executable,"--target",str(tmp_path/"sdk"),
          "--offline","--no-index","--no-deps",*extra,str(artifact)]
    with pytest.raises(ValueError):
        launch.uv_wheel_contract(argv,{},python)


@pytest.mark.skipif(
    os.environ.get("CGS_ISOLATED_MEASUREMENT") != "1",
    reason="real process proof requires the verified isolated container lane",
)
class TestMeasurementContainer:
    def test_child_never_loads_or_compiles_parent_helpers(self,tmp_path):
        original=Path(__import__("fleet_manager").__file__).parent.parent
        activation=self._activation(tmp_path)
        activation.write_text(activation.read_text().replace(
            " for name in",
            " parent=payload['measurement']['_cgs_child_stream_parent']\n"
            " def refuse(event,args):\n"
            "  if event=='compile' and args[1]==parent:raise AssertionError('parent compiled in child')\n"
            "  if event=='exec' and args[0].co_filename==parent:raise AssertionError('parent executed in child')\n"
            " sys.addaudithook(refuse)\n for name in",1))
        registry=launch.Registry(
            tmp_path/"tickets",sys.executable,activation,"offline::runtime-only",
            measurement_plan=lambda root,payload:self._plan(root,payload,original),
        )
        registry.install()
        try:
            child=subprocess.run([sys.executable,"-I","-S","-B","-c",
                "import sys;assert not any('child_stream_parent' in name for name in sys.modules)"],
                capture_output=True,text=True)
        finally:
            registry.close()
        assert child.returncode==0,child.stderr
        assert len(registry.verify())==1

    def test_owned_loaders_keep_namespaces_and_do_not_import_runpy(self,tmp_path):
        activation=tmp_path/"activation.py"
        activation.write_text(
            "assert __name__=='_cgs_measurement_activation'\n"
            f"assert __file__=={str(activation)!r}\n"
            "assert __package__=='' and __spec__ is None and __cached__ is None\n"
            "assert __loader__ is None and __doc__ is None\n"
            "def activate(payload):pass\n"
        )
        registry=launch.Registry(tmp_path/"tickets",sys.executable,activation,"offline::loader")
        program=(
            "import sys;assert not ({'helper','source','verified_namespace'} & set(globals()));"
            "assert not ({'runpy','pkgutil','json','re','enum'} & set(sys.modules));"
            "assert sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode"
        )
        registry.install()
        try:
            child=subprocess.run([sys.executable,"-I","-S","-B","-c",program],
                                 capture_output=True,text=True)
        finally:
            registry.close()
        assert child.returncode==0,child.stderr
        assert len(registry.verify())==1

    def _activation(self, tmp_path):
        path = tmp_path / "activation.py"
        path.write_text(
            "def activate(payload,profile=None):\n"
            " import os,sys,importlib.util,hashlib\n"
            " if profile:profile['mark']('activation-enter')\n"
            " plan=payload.get('measurement')\n"
            " if plan is None:return\n"
            " for name in ('_cgs_child_audit','_cgs_child_stream'):\n"
            "  assert hashlib.sha256(open(plan[name],'rb').read()).hexdigest()==plan['child_stream_helpers'][plan[name]]\n"
            "  if profile:profile['mark'](name+'-source-checked')\n"
            "  spec=importlib.util.spec_from_file_location(name,plan[name])\n"
            "  module=importlib.util.module_from_spec(spec);sys.modules[name]=module\n"
            "  spec.loader.exec_module(module)\n"
            "  if profile:profile['mark'](name+'-loaded')\n"
            " os.environ['_CGS_CHILD_MANIFEST']=plan['manifest']\n"
            " os.environ['_CGS_CHILD_AUDITOR']=str(payload['auditor_pid'])\n"
            " if profile:\n"
            "  sys.modules['_cgs_child_stream']._phase_marker=profile['mark']\n"
            "  sys.modules['_cgs_child_stream']._phase_reporter=profile['finish']\n"
            "  profile['mark']('stream-start-call')\n"
            " sys.modules['_cgs_child_stream'].start(plan['child_stream_control'])\n"
        )
        return path

    def _plan(self, root, payload, copied):
        import hashlib
        from tools.coverage_guided_selection import standalone as s
        from tools.coverage_guided_selection import child_stream_parent as child_stream
        mapping, hashes, inventory = s.verified_source_mapping(
            s.REPO, copied / "fleet_manager", copied, with_inventory=True)
        _, options = s.coverage_files(root, mapping, inventory=inventory)
        token = hashlib.sha256(payload["context"].encode()).hexdigest()
        manifest = Path(options["child_stream_control"]) / "manifests" / (token + ".manifest")
        child_stream.write_manifest(
            manifest, nonce=options["child_stream_nonce"], context=token,
            directory=Path(options["child_stream_control"]) / "hits",
            inventory=options["child_stream_inventory"],
        )
        return dict(options, token=token, manifest=str(manifest),
                    source_binding="byte-equal-declared-source-root", canonical_sha256=hashes)

    def test_copied_sdk_real_lines_under_unchanged_isolation(self, tmp_path):
        import shutil
        import signal
        from tools.coverage_guided_selection.selection import select_tests
        assert Path("/.dockerenv").is_file()
        original = Path(__import__("fleet_manager").__file__).parent
        copied = tmp_path / "copied-sdk"
        shutil.copytree(original,copied / "fleet_manager")
        activation = self._activation(tmp_path)
        registry = launch.Registry(
            tmp_path / "tickets",sys.executable,activation,"offline::copied-sdk",
            measurement_plan=lambda root,payload:self._plan(root,payload,copied),
        )
        program = (
            "import sys;sys.path.insert(0,sys.argv[1]);"
            "from pathlib import Path;import fleet_manager;"
            "assert Path(fleet_manager.__file__).parent==Path(sys.argv[1])/'fleet_manager';"
            "from fleet_manager.planning import canonical;"
            "assert canonical({'source':'copied-sdk'})==b'{\"source\":\"copied-sdk\"}';"
            "print('SDK-OK')"
        )
        registry.install()
        try:
            result=subprocess.run([sys.executable,"-I","-S","-B","-c",program,str(copied)],
                                  capture_output=True,text=True,check=True)
            assert result.stdout=="SDK-OK\n" and not result.stderr
            common=program.rsplit("print('SDK-OK')",1)[0]
            crashed=subprocess.run([sys.executable,"-I","-S","-B","-c",
                common+"import os;os._exit(23)",str(copied)],capture_output=True,text=True)
            assert crashed.returncode==23
            forced=subprocess.Popen([sys.executable,"-I","-S","-B","-c",
                common+"import os;print('READY',flush=True);sys.stdin.read()",str(copied)],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            assert forced.stdout.readline()=="READY\n"
            forced.send_signal(signal.SIGTERM)
            forced.communicate(timeout=5)
            assert forced.returncode!=0
            thread_program=program.split("assert canonical(",1)[0]+(
                "import threading;"
                "thread=threading.Thread(target=lambda:canonical({'thread-only':True}));"
                "thread.start();thread.join();print('THREAD-ONLY')"
            )
            threaded=subprocess.run([sys.executable,"-I","-S","-B","-c",thread_program,str(copied)],
                                    capture_output=True,text=True)
            assert threaded.returncode==0 and threaded.stdout=="THREAD-ONLY\n",threaded.stderr
        finally:
            registry.close()
        records=registry.verify()
        assert len(records)==4
        record=records[0]
        assert all(row["lines"]["audit_complete"] for row in records)
        assert record["lines"]["audit_complete"] and record["lines"]["audited_producers"]==1
        key="fleet-manager/src/fleet_manager/planning.py"
        line=next(int(value) for value in record["lines"]["coverage"][key])
        selected=select_tests({"coverage":record["lines"]["coverage"]},{key:[line]})
        assert selected.selected_tests==("offline::copied-sdk",)
        assert len(record["request"]["measurement"]["canonical_sha256"])==17
        thread_record=next(row for row in records if "THREAD-ONLY" in row["request"]["argv"][5])
        import inspect
        from fleet_manager.planning import canonical as canonical_function
        body,first=inspect.getsourcelines(canonical_function)
        returns={first+index for index,text in enumerate(body) if "return " in text}
        assert returns and returns.issubset({
            int(value) for value in thread_record["lines"]["coverage"][key]
        })
        print("COPIED_SDK",json.dumps({
            "source_equivalent_modules":17,"actual_line_records":sum(map(len,record["lines"]["coverage"].values())),
            "prelude_seconds":record["lines"]["prelude_seconds"],
            "spawn_authority":record["audit"]["origin"],"selected":selected.selected_tests,
            "normal_abrupt_forced_thread_measured":True,
        }))

    def test_generated_offline_signature_is_verified_by_copied_sdk(self,tmp_path,*,interpreter=None):
        import base64
        import secrets
        import shutil
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        python=sys.executable if interpreter is None else os.fspath(interpreter)
        from tools.coverage_guided_selection import standalone as s
        sys.path.insert(0,str(s.REPO/"fleet-manager"/"tests"))
        try:
            from test_release_auth import artifact_fixture
            fixture=artifact_fixture(tmp_path)
        finally:
            sys.path.pop(0)
        from fleet_manager.planning import canonical
        from fleet_manager.release_auth import SIGNING_DOMAIN
        private=Ed25519PrivateKey.generate()
        authority="offline-"+secrets.token_hex(8)
        fixture["policy"]["authority"]=authority
        fixture["proof"]["payload"]["authority"]=authority
        fixture["policy"]["keys"][0]["public_key"]=base64.b64encode(
            private.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)).decode()
        fixture["proof"]["signature"]=base64.b64encode(
            private.sign(SIGNING_DOMAIN+canonical(fixture["proof"]["payload"]))).decode()
        inputs=tmp_path/"signed-inputs.json"
        inputs.write_text(json.dumps(fixture))
        source=Path(__import__("fleet_manager").__file__).parent
        copied=tmp_path/"sdk"
        shutil.copytree(source,copied/"fleet_manager")
        from importlib import metadata
        distribution=metadata.distribution("fleet-manager")
        record=next(item for item in distribution.files if str(item).endswith(".dist-info/RECORD"))
        dist_info=Path(distribution.locate_file(record)).parent
        shutil.copytree(dist_info,copied/dist_info.name)
        parent=tmp_path/"staging"
        parent.mkdir()
        crypto=Path(__import__("cryptography").__file__).parent.parent
        program=r'''
import base64,json,pathlib,socket,sys
target,crypto,inputs,parent,authority,mode=sys.argv[1:]
sys.path[:0]=[target,crypto]
import fleet_manager
assert pathlib.Path(fleet_manager.__file__).parent==pathlib.Path(target)/'fleet_manager'
from fleet_manager.artifact_staging import stage_authenticated_artifacts
from fleet_manager.planning import canonical,PlanError
value=json.loads(pathlib.Path(inputs).read_text())
if mode=='tampered':
    signature=bytearray(base64.b64decode(value['proof']['signature']));signature[0]^=1
    value['proof']['signature']=base64.b64encode(signature).decode()
def no_network(*args,**kwargs):raise AssertionError('network is forbidden')
socket.socket=socket.create_connection=socket.getaddrinfo=no_network
try:
    with stage_authenticated_artifacts(
        canonical(value['manifest']),value['proof'],value['policy'],value['sources'],
        authority=authority,installation='test-installation',role='client',
        staging_parent=pathlib.Path(parent),clock=lambda:150) as stage:
        result=stage.reverify(value['policy'],clock=lambda:151)
        assert result['authenticity_verified'] and result['artifact_integrity_verified']
        assert result['retention_held'] and not result['activation_authorized']
        assert mode=='valid'
except PlanError:
    assert mode=='tampered'
    raise SystemExit(19)
print('AUTH-VERIFIED')
'''
        if interpreter is not None:
            program="import os,sys;assert os.path.abspath(sys.executable)=="+repr(os.path.abspath(python))+"\n"+program
            program="import os;assert os.path.samefile('/proc/self/exe',"+repr(python)+")\n"+program
        registry=launch.Registry(
            tmp_path/"tickets",python,self._activation(tmp_path),"offline::signed-sdk",
            measurement_plan=lambda root,payload:self._plan(root,payload,copied),
        )
        registry.install()
        try:
            for mode,expected in (("valid",0),("tampered",19)):
                result=subprocess.run([python,"-I","-S","-B","-c",program,
                    str(copied),str(crypto),str(inputs),str(parent),authority,mode],
                    capture_output=True,text=True)
                assert result.returncode==expected,result.stderr
                if mode=="valid":assert result.stdout=="AUTH-VERIFIED\n"
        finally:
            registry.close()
        records=registry.verify()
        assert len(records)==2 and all(row["lines"]["audit_complete"] for row in records)
        assert all("fleet-manager/src/fleet_manager/release_auth.py" in row["lines"]["coverage"]
                   for row in records)
        print("OFFLINE_AUTH",json.dumps({"generated_ed25519":True,"valid_accepted":True,
              "tampered_rejected":True,"measured_producers":len(records),"live_auth":False}))

    def test_uv_native_probe_entry_is_truthful_without_identity_forgery(self,tmp_path):
        import shutil
        assert Path("/.dockerenv").is_file()
        uv=shutil.which("uv")
        assert uv
        from tools.coverage_guided_selection import standalone as s
        output=tmp_path / "wheel"
        output.mkdir()
        original=Path(__import__("fleet_manager").__file__).parent.parent
        registry=launch.Registry(
            tmp_path / "tickets",sys.executable,self._activation(tmp_path),"offline::uv-install",
            measurement_plan=lambda root,payload:self._plan(root,payload,original),uv=uv,
        )
        registry.install()
        try:
            built=subprocess.run([sys.executable,"-I","-c",
                "from setuptools.build_meta import build_wheel;build_wheel("+repr(str(output))+")"],
                cwd=s.REPO / "fleet-manager",capture_output=True,text=True)
            assert built.returncode==0,built.stderr
            wheel=next(output.glob("*.whl"))
            result=subprocess.run([uv,"--no-config","pip","install","--python",sys.executable,
                "--target",str(tmp_path / "sdk"),"--no-deps","--offline","--no-index",str(wheel)],
                capture_output=True,text=True)
            assert result.returncode==0,result.stderr
        finally:
            registry.close()
        records=registry.verify()
        uv_records=[row for row in records if row["request"]["kind"]=="uv-wheel-install"]
        uv_native=[row for row in records if row["request"].get("parent_uv")]
        assert len(uv_records)==1 and uv_native
        assert all(row["audit"]["origin"]=="UV/measurement-entry-before-exec" for row in uv_native)
        assert all(row["lines"]["audit_complete"] for row in records if "lines" in row)
        print("UV_ENTRY_PROOF",json.dumps({
            "returncode":result.returncode,"registered_uv_entries":len(uv_native),
            "uv_sha256":uv_records[0]["request"]["uv_sha256"],
            "origins":[row["audit"]["origin"] for row in records],
            "interpreter_identity_forged":False,
        }))
        missing=registry.root/uv_native[0]["request"]["nonce"]/"birth.json"
        missing.unlink()
        with pytest.raises((OSError,ValueError)):
            registry.verify()
        shutil.rmtree(missing.parent)
        with pytest.raises(ValueError,match="missing or extra issued"):
            registry.verify()

    def test_issued_request_lifecycle_missing_extra_live_and_replay(self,tmp_path):
        import shutil
        assert Path("/.dockerenv").is_file()
        activation=tmp_path/"activation.py"
        activation.write_text("def activate(payload):pass\n")
        registry=launch.Registry(tmp_path/"tickets",sys.executable,activation,"offline::lifecycle")
        registry.install()
        workers=[]
        try:
            for _ in range(2):
                workers.append(subprocess.Popen(
                    [sys.executable,"-I","-S","-B","-c",
                     "import sys;print('READY',flush=True);sys.stdin.read()"],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True))
            assert all(worker.poll() is None for worker in workers)
            with pytest.raises(ValueError,match="still running"):
                registry.verify()
            for worker in workers:
                assert worker.stdout.readline()=="READY\n"
                worker.communicate("release",timeout=5)
                assert worker.returncode==0
        finally:
            for worker in workers:
                if worker.poll() is None:
                    worker.kill()
                    worker.communicate(timeout=5)
            registry.close()
        assert len(registry.verify())==2
        first,second=registry.requests
        backup=tmp_path/"backup"
        shutil.copytree(first,backup)
        shutil.rmtree(first)
        with pytest.raises(ValueError,match="missing or extra issued"):
            registry.verify()
        shutil.copytree(backup,first)
        removed=registry.requests.pop()
        with pytest.raises(ValueError,match="issued request ledger"):
            registry.verify()
        registry.requests.append(removed)
        extra=registry.root/("e"*64)
        shutil.copytree(first,extra)
        with pytest.raises(ValueError,match="missing or extra issued"):
            registry.verify()
        shutil.rmtree(extra)
        birth=(second/"birth.json").read_bytes()
        (second/"birth.json").write_bytes((first/"birth.json").read_bytes())
        with pytest.raises(ValueError,match="binding mismatch"):
            registry.verify()
        (second/"birth.json").write_bytes(birth)
        process=registry.processes.pop(first)
        with pytest.raises(ValueError,match="Popen lifecycle"):
            registry.verify()
        registry.processes[first]=process
        assert len(registry.verify())==2

    def test_skip_first_line_native_behavior_is_preserved_but_not_admitted(self,tmp_path):
        assert Path("/.dockerenv").is_file()
        script=tmp_path/"skip.py"
        script.write_text("this is not valid Python !!!\nimport sys;print(sys._getframe().f_lineno)\n")
        arguments=[sys.executable,"-I","-S","-B","-x",str(script)]
        expected=subprocess.run(arguments,capture_output=True,text=True,check=True)
        assert expected.stdout=="2\n"
        activation=tmp_path/"activation.py"
        activation.write_text("def activate(payload):pass\n")
        registry=launch.Registry(tmp_path/"tickets",sys.executable,activation,"offline::skip-first")
        registry.install()
        try:
            actual=subprocess.run(arguments,capture_output=True,text=True,check=True)
        finally:
            registry.close()
        assert (actual.stdout,actual.stderr,actual.args)==(expected.stdout,expected.stderr,arguments)
        with pytest.raises(ValueError,match="skip-first-line"):
            registry.verify()

    def test_uv_implicit_config_is_not_silently_disabled(self,tmp_path):
        import shutil
        from tools.coverage_guided_selection import standalone as s
        assert Path("/.dockerenv").is_file()
        uv=shutil.which("uv")
        output=tmp_path/"wheel"
        output.mkdir()
        built=subprocess.run([sys.executable,"-I","-c",
            "from setuptools.build_meta import build_wheel;build_wheel("+repr(str(output))+")"],
            cwd=s.REPO/"fleet-manager",capture_output=True,text=True)
        assert built.returncode==0,built.stderr
        artifact=next(output.glob("*.whl"))
        (tmp_path/"uv.toml").write_text("[pip]\ncompile-bytecode = true\n")
        arguments=[uv,"pip","install","--python",sys.executable,"--target",str(tmp_path/"sdk"),
                   "--offline","--no-index","--no-deps",str(artifact)]
        activation=tmp_path/"activation.py"
        activation.write_text("def activate(payload):pass\n")
        registry=launch.Registry(tmp_path/"tickets",sys.executable,activation,"offline::uv-config",uv=uv)
        registry.install()
        try:
            result=subprocess.run(arguments,cwd=tmp_path,capture_output=True,text=True)
        finally:
            registry.close()
        assert result.returncode==0,result.stderr
        assert result.args==arguments and "--no-config" not in result.args
        with pytest.raises(ValueError,match="configuration-enabled UV observation"):
            registry.verify()

    def test_native_flags_globals_argv_exit_and_nonblocking_scheduling(self, tmp_path):
        assert Path("/.dockerenv").is_file()
        activation = tmp_path / "activation.py"
        activation.write_text("def activate(payload): pass\n")
        registry = launch.Registry(tmp_path / "tickets", sys.executable, activation, "offline::semantics")
        program = (
            "import sys,json;print(json.dumps({'argv':sys.argv,'orig':sys.orig_argv,"
            "'globals':sorted(globals()),'flags':[sys.flags.isolated,sys.flags.no_site,"
            "sys.flags.dont_write_bytecode],'name':__name__,'package':__package__,"
            "'spec':str(__spec__),'loader':str(__loader__),'file':globals().get('__file__')}))"
        )
        ordinary = {}
        for flags in (("-I","-S","-B"),("-ISB",)):
            argv = [sys.executable,*flags,"-c",program,"value","--help"]
            ordinary[flags] = subprocess.run(argv,capture_output=True,text=True,check=True)
        registry.install()
        try:
            for flags, expected in ordinary.items():
                argv = [sys.executable,*flags,"-c",program,"value","--help"]
                actual = subprocess.run(argv,capture_output=True,text=True,check=True)
                assert actual.stdout == expected.stdout
                assert actual.stderr == expected.stderr
                assert actual.args == argv
            exited = subprocess.run(
                [sys.executable,"-I","-S","-B","-c","print('before-exit');raise SystemExit(17)"],
                capture_output=True,text=True,
            )
            assert exited.returncode == 17 and exited.stdout == "before-exit\n" and not exited.stderr
            workers = [
                subprocess.Popen(
                    [sys.executable,"-I","-S","-B","-c",
                     "import sys;print('READY',flush=True);sys.stdin.read();print('DONE')"],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                ) for _ in range(2)
            ]
            assert all(worker.poll() is None for worker in workers)
            for worker in workers:
                assert worker.stdout.readline() == "READY\n"
            for worker in workers:
                output,error = worker.communicate("release",timeout=5)
                assert worker.returncode == 0 and output == "DONE\n" and not error
        finally:
            registry.close()
        records = registry.verify()
        assert len(records) == 5
        assert all(row["audit"]["origin"] == "CPython/subprocess.Popen" for row in records)

    def test_regular_script_and_module_entry_keep_native_behavior(self,tmp_path):
        assert Path("/.dockerenv").is_file()
        script=tmp_path/"program.py"
        script.write_text(
            "import sys,json\n"
            "print(json.dumps({'argv':sys.argv,'orig':sys.orig_argv,'globals':sorted(globals()),"
            "'file':__file__,'cached':__cached__,'name':__name__,'package':__package__,"
            "'spec':str(__spec__),'loader_type':type(__loader__).__name__,"
            "'flags':[sys.flags.isolated,sys.flags.no_site,sys.flags.dont_write_bytecode]}))\n"
        )
        data=tmp_path/"input.json"
        data.write_text('{"offline":true}')
        arguments=[
            [sys.executable,"-I","-S","-B",str(script),"arg"],
            [sys.executable,"-I","-S","-B","-m","json.tool",str(data)],
        ]
        expected=[subprocess.run(argv,capture_output=True,text=True) for argv in arguments]
        activation=tmp_path/"activation.py"
        activation.write_text("def activate(payload):pass\n")
        registry=launch.Registry(tmp_path/"tickets",sys.executable,activation,"offline::entrypoints")
        registry.install()
        try:
            actual=[subprocess.run(argv,capture_output=True,text=True) for argv in arguments]
        finally:
            registry.close()
        for first,second in zip(expected,actual):
            assert (first.returncode,first.stdout,first.stderr)==(second.returncode,second.stdout,second.stderr)
        assert len(registry.verify())==2

    def test_real_recovery_pair_keeps_original_deadlines_and_concurrency(self,tmp_path,*,observer_lifecycle=None):
        import time
        from tools.coverage_guided_selection.measurement_diagnostics import Recording
        from tools.coverage_guided_selection import standalone as s
        assert Path("/.dockerenv").is_file()
        node="tests/test_two_node_recovery.py"
        original=Path(__import__("fleet_manager").__file__).parent.parent
        context=["unset"]
        registry=launch.Registry(
            tmp_path/"tickets",sys.executable,self._activation(tmp_path),lambda:context[0],
            measurement_plan=lambda root,payload:self._plan(root,payload,original),
        )
        observer_lifecycle=observer_lifecycle or getattr(self,"owned_observer_lifecycle",None)
        recording=Recording(registry,lifecycle=observer_lifecycle)
        workload_results={}
        class Context:
            @pytest.hookimpl(wrapper=True,tryfirst=True)
            def pytest_runtest_protocol(self,item,nextitem):
                context[0]=item.nodeid
                return (yield)
        def ordinary_run():
            started=time.monotonic()
            workload_results["ordinary"]=pytest.main(["-q",str(s.REPO/"fleet-manager"/node),
                "--basetemp",str(tmp_path/"ordinary"),"-p","no:cacheprovider"],
                plugins=[recording.plugin("ordinary")])
            return time.monotonic()-started
        def measured_run():
            if recording.lifecycle:recording.lifecycle.install_launch_recorder(lambda:context[0])
            else:registry.install()
            started=time.monotonic()
            with recording.failure_boundary(started):
                try:
                    if recording.lifecycle:recording.lifecycle.begin(started)
                    result=pytest.main(["-q",str(s.REPO/"fleet-manager"/node),
                        "--basetemp",str(tmp_path/"measured"),"-p","no:cacheprovider"],
                        plugins=[Context(),recording.plugin("measured")])
                    if recording.lifecycle:recording.lifecycle.pytest_result=result
                    workload_results["measured"]=result
                finally:
                    try:
                        if recording.lifecycle:recording.lifecycle.complete()
                    finally:
                        if recording.lifecycle:recording.lifecycle.restore_launch_recorder()
                        else:registry.close()
            elapsed=time.monotonic()-started
            if recording.lifecycle:recording.lifecycle.caller_end=started+elapsed
            return elapsed
        with recording:
            order=os.environ.get("CGS_MEASUREMENT_ORDER","ordinary-first")
            if order=="measured-first":
                measured=measured_run()
                ordinary=ordinary_run()
            else:
                ordinary=ordinary_run()
                measured=measured_run()
            if recording.lifecycle:
                native=recording.lifecycle.evidence()
                producer_count=native["producer_count"]
                preludes=native["preludes"]
            else:
                records=registry.verify()
                producer_count=len(records)
                recording.verified=records
                assert all(row["lines"]["audit_complete"] for row in records)
                preludes=[value for row in records for value in row["lines"]["prelude_seconds"]]
            ratio=measured/ordinary
            evidence={"ordinary_s":ordinary,"measured_s":measured,
                "ratio":ratio,"native_producers":producer_count,"order":order,
                "child_prelude_seconds":preludes,"ratio_gate":ratio<=1.5,
                "prelude_gate":len(preludes)==producer_count==12 and all(value<=.2 for value in preludes),
                "workload_results":workload_results,
                "source_admitted":native["admitted"] if recording.lifecycle else True,
                "original_5s_15s_assertions":True,"fixture_changes":False}
            if recording.lifecycle:recording.lifecycle.pair_evidence=evidence
            print("REAL_RECOVERY",json.dumps(evidence))
            assert workload_results=={"ordinary":0,"measured":0}
            assert evidence["source_admitted"] and producer_count==12 and len(preludes)==12
            assert ratio<=1.5, "measurement/ordinary performance gate exceeded"
            assert all(value<=.2 for value in preludes), "child profiling prelude target exceeded"
    @pytest.mark.skipif(os.environ.get("CGS_PROFILE_MEASUREMENT")!="1",
                        reason="explicit diagnostic profile, excluded from paired timings")
    def test_profile_actual_instrumentation_path(self,tmp_path,monkeypatch):
        import cProfile
        import importlib.util
        import pstats
        from tools.coverage_guided_selection import standalone as s
        assert Path("/.dockerenv").is_file()
        spec=importlib.util.spec_from_file_location(
            "_profile_recovery",s.REPO/"fleet-manager"/"tests"/"test_two_node_recovery.py")
        worker=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        source=Path(__import__("fleet_manager").__file__).parent.parent
        registry=launch.Registry(
            tmp_path/"tickets",sys.executable,self._activation(tmp_path),"offline::profile-fast",
            measurement_plan=lambda root,payload:self._plan(root,payload,source),
        )
        child_stats=tmp_path/"child.prof"
        original=launch.rewritten
        def profiled(argv,request):
            physical=original(argv,request)
            body=physical[physical.index("-c")+1]
            physical[physical.index("-c")+1]=(
                "__import__('cProfile').runctx("+repr(body)+",globals(),globals(),"
                +repr(str(child_stats))+")"
            )
            return physical
        monkeypatch.setattr(launch,"rewritten",profiled)
        parent=cProfile.Profile()
        registry.install()
        try:
            result=parent.runcall(subprocess.run,
                [sys.executable,"-I","-c",worker.WORKER,str(tmp_path),"fast","normal"],
                capture_output=True,text=True)
            assert result.returncode==0,result.stderr
        finally:
            registry.close()
        assert len(registry.verify())==1
        parent_file=tmp_path/"parent.prof"
        parent.dump_stats(str(parent_file))
        for label,path in (("parent",parent_file),("child",child_stats)):
            stats=pstats.Stats(str(path)).stats
            rows=[]
            for (file,line,name),(primitive,calls,total,cumulative,_) in stats.items():
                if ("coverage_guided_selection" in file or "_cgs_child" in file
                    or file.endswith("test_measurement_launch.py")):
                    rows.append({"file":Path(file).name,"line":line,"function":name,
                                 "calls":calls,"self_s":total,"cumulative_s":cumulative})
            print("INSTRUMENTATION_PROFILE",json.dumps({
                "side":label,"rows":sorted(rows,key=lambda row:row["cumulative_s"],reverse=True)[:25]}))

    @pytest.mark.skipif(os.environ.get("CGS_PRELUDE_PHASE_PROFILE")!="1",
                        reason="explicit 12-producer residual wall-phase diagnostic")
    def test_profile_residual_prelude_wall_time_for_all_real_producers(self,tmp_path):
        from collections import defaultdict
        from tools.coverage_guided_selection import standalone as s
        assert Path("/.dockerenv").is_file()
        original=Path(__import__("fleet_manager").__file__).parent.parent
        context=["unset"]
        registry=launch.Registry(
            tmp_path/"tickets",sys.executable,self._activation(tmp_path),lambda:context[0],
            measurement_plan=lambda root,payload:self._plan(root,payload,original),
        )
        class Context:
            @pytest.hookimpl(wrapper=True,tryfirst=True)
            def pytest_runtest_protocol(self,item,nextitem):
                context[0]=item.nodeid
                return (yield)
        registry.install()
        try:
            result=pytest.main(["-q",str(s.REPO/"fleet-manager/tests/test_two_node_recovery.py"),
                "--basetemp",str(tmp_path/"profiled"),"-p","no:cacheprovider"],plugins=[Context()])
        finally:
            registry.close()
        assert result==0
        records=registry.verify()
        assert len(records)==12
        wall_totals,cpu_totals=defaultdict(float),defaultdict(float)
        for row in records:
            control=Path(row["request"]["measurement"]["child_stream_control"])
            paths=list(control.glob("prelude-phases-*.json"))
            assert len(paths)==1
            report=json.loads(paths[0].read_text())
            events=report["events"]
            assert report["timer_start_ns"]==events[0]["wall_ns"]
            assert report["timer_end_ns"]==events[-1]["wall_ns"]
            assert report["recorded_duration_us"]==(
                report["timer_end_ns"]-report["timer_start_ns"])//1000
            assert row["lines"]["prelude_seconds"]==[report["recorded_duration_us"]/1_000_000]
            intervals=[]
            for before,after in zip(events,events[1:]):
                wall=(after["wall_ns"]-before["wall_ns"])/1_000_000
                cpu=(after["cpu_ns"]-before["cpu_ns"])/1_000_000
                assert wall>=0 and cpu>=0
                intervals.append({"phase":after["phase"],"wall_ms":wall,"cpu_ms":cpu,
                                  "wall_minus_cpu_ms":wall-cpu})
                wall_totals[after["phase"]]+=wall
                cpu_totals[after["phase"]]+=cpu
            print("RESIDUAL_PRELUDE",json.dumps({
                "context":row["request"]["context"],"duration_s":row["lines"]["prelude_seconds"][0],
                "intervals":intervals,"scheduler_delta":report["scheduler_delta"],
                "cgroup_delta":report["cgroup_delta"],"timer_unchanged":True,
                "imports":report["imports"],
                "diagnostic_only":True,
            }))
        print("RESIDUAL_PHASE_TOTALS",json.dumps({
            "producers":12,"wall_ms":dict(wall_totals),"cpu_ms":dict(cpu_totals),
            "diagnostic_only":True,"performance_pass_claimed":False,
        }))
