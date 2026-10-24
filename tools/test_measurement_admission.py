"""Closed measurement admission: unsupported native operations never become empty success."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools.coverage_guided_selection import measurement_launch as launch
from tools.test_measurement_launch import TestMeasurementContainer as _SharedCases


@pytest.mark.parametrize("event",["os.fork","os.forkpty","os.exec","os.posix_spawn","os.system"])
def test_unissued_native_lifecycle_invalidates_collection(monkeypatch,event):
    from types import SimpleNamespace
    registry=SimpleNamespace(failures=[])
    monkeypatch.setattr(launch,"_registry",registry)
    launch._audit(event,())
    assert registry.failures


def test_renamed_native_descendant_cannot_be_ignored(monkeypatch):
    from tools.coverage_guided_selection import child_audit
    failures=[]
    monkeypatch.setenv(child_audit.ENV,"owned-context")
    monkeypatch.setattr(child_audit,"_python",os.path.abspath(sys.executable))
    monkeypatch.setattr(child_audit,"error",lambda reason:failures.append(reason))
    child_audit._event("subprocess.Popen",(
        "opaque-native",["opaque-native","--version"],None,{child_audit.ENV:"owned-context"},
    ))
    assert failures==["unmanaged native executable inherited child instrumentation"]


@pytest.mark.parametrize("event",["subprocess.Popen","os.system","os.exec"])
def test_instrumented_child_cannot_erase_context_to_escape_admission(monkeypatch,event):
    from tools.coverage_guided_selection import child_audit
    failures=[]
    monkeypatch.delenv(child_audit.ENV,raising=False)
    monkeypatch.setattr(child_audit,"_child",True)
    monkeypatch.setattr(child_audit,"_python",os.path.abspath(sys.executable))
    monkeypatch.setattr(child_audit,"error",lambda reason:failures.append(reason))
    args=("opaque-native",["opaque-native","-c","pass"],None,{}) if event=="subprocess.Popen" else ()
    child_audit._event(event,args)
    assert failures


@pytest.mark.parametrize("informational",[False,True])
def test_unwrapped_native_launch_requires_issuance_except_known_no_code(monkeypatch,informational):
    from types import SimpleNamespace
    python=os.path.normcase(os.path.abspath(sys.executable))
    registry=SimpleNamespace(python=python,failures=[])
    monkeypatch.setattr(launch,"_registry",registry)
    argv=[python,"--version"] if informational else [python,"-I","-S","-c","pass"]
    launch._audit("subprocess.Popen",(python,argv,None,{}))
    assert bool(registry.failures) is not informational


def test_missing_uv_entry_is_an_explicit_refusal_not_empty_success(tmp_path):
    activation=tmp_path/"activation.py"
    activation.write_text("def activate(payload):pass\n")
    registry=launch.Registry(tmp_path/"tickets",sys.executable,activation,"offline::uv-cache")
    slot=registry.root/("a"*64)
    slot.mkdir()
    registry.requests.append(slot)
    registry._issued[slot]={"kind":"uv-slot"}
    with pytest.raises(ValueError,match="UV issued native entry absent"):
        registry.verify()


@pytest.mark.parametrize("operation",["run","source-build","cache-option","different-python"])
def test_closed_uv_contract_never_admits_general_operations(tmp_path,operation):
    wheel=tmp_path/"fixture.whl"
    wheel.write_bytes(b"fixture")
    python=os.path.normcase(os.path.abspath(sys.executable))
    argv=["uv","--no-config","pip","install","--python",python,"--target",str(tmp_path/"sdk"),
          "--offline","--no-index","--no-deps",str(wheel)]
    if operation=="run":
        argv=["uv","--no-config","run","--offline",python,"-c","pass"]
    elif operation=="source-build":
        argv[-1]=str(tmp_path)
        argv.append("--no-build-isolation")
    elif operation=="cache-option":
        argv.append("--refresh")
    else:
        argv[argv.index("--python")+1]=str(tmp_path/"fresh-copy")
    original=list(argv)
    with pytest.raises(ValueError):
        launch.uv_wheel_contract(argv,{},python)
    assert argv==original and argv.count("--no-config")==1


@pytest.mark.skipif(os.environ.get("CGS_ISOLATED_MEASUREMENT")!="1",
                    reason="real native admission proofs require the verified isolated container")
class TestMeasurementAdmission:
    _activation=_SharedCases._activation
    _plan=_SharedCases._plan

    def _sdk(self,tmp_path):
        source=Path(__import__("fleet_manager").__file__).parent
        copied=tmp_path/"copied-sdk"
        shutil.copytree(source,copied/"fleet_manager")
        program=("import sys;sys.path.insert(0,"+repr(str(copied))+");"
                 "from fleet_manager.planning import canonical;"
                 "assert canonical({'admission':True})==b'{\"admission\":true}';"
                 "print('SDK-REAL')")
        return copied,program

    def test_unregistered_copied_interpreter_preserves_application_but_refuses(self,tmp_path):
        copied,program=self._sdk(tmp_path)
        native=tmp_path/"opaque-native"
        shutil.copyfile(Path(sys.executable).resolve(),native)
        native.chmod(0o700)
        assert native.read_bytes()==Path(sys.executable).resolve().read_bytes()
        registry=launch.Registry(
            tmp_path/"tickets",sys.executable,self._activation(tmp_path),"offline::unregistered-copy",
            measurement_plan=lambda root,payload:self._plan(root,payload,copied),
        )
        argv=[str(native),"-I","-S","-B","-c",program]
        registry.install()
        try:
            result=subprocess.run(argv,capture_output=True,text=True)
        finally:
            registry.close()
        assert result.returncode==0 and result.stdout=="SDK-REAL\n",result.stderr
        assert result.args==argv and not registry.requests
        with pytest.raises(ValueError,match="unregistered native executable"):
            registry.verify()

    def test_declared_fresh_copied_interpreter_retains_real_sdk_signatures_and_identity(self,tmp_path):
        native=tmp_path/"declared-native"
        original=Path(sys.executable).resolve()
        shutil.copyfile(original,native)
        native.chmod(0o700)
        expected=hashlib.sha256(original.read_bytes()).hexdigest()
        assert hashlib.sha256(native.read_bytes()).hexdigest()==expected
        _SharedCases.test_generated_offline_signature_is_verified_by_copied_sdk(
            self,tmp_path,interpreter=native,
        )
        assert hashlib.sha256(native.read_bytes()).hexdigest()==expected

    @pytest.mark.parametrize("mode",["captured-popen","shell","uv-run","direct-spawn","os-system"])
    def test_opaque_or_unissued_execution_preserves_sdk_but_refuses(self,tmp_path,mode):
        import shlex
        copied,program=self._sdk(tmp_path)
        uv=shutil.which("uv")
        assert uv
        registry=launch.Registry(
            tmp_path/"tickets",sys.executable,self._activation(tmp_path),"offline::"+mode,
            measurement_plan=lambda root,payload:self._plan(root,payload,copied),uv=uv,
        )
        original_popen=subprocess.Popen
        argv=[sys.executable,"-I","-S","-B","-c",program]
        registry.install()
        try:
            if mode=="captured-popen":
                child=original_popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                stdout,stderr=child.communicate(timeout=15)
                assert child.returncode==0 and stdout=="SDK-REAL\n",stderr
            elif mode=="direct-spawn":
                pid=os.posix_spawn(sys.executable,argv,dict(os.environ))
                _,status=os.waitpid(pid,0)
                assert os.waitstatus_to_exitcode(status)==0
            elif mode=="os-system":
                assert os.waitstatus_to_exitcode(os.system(shlex.join(argv)))==0
            else:
                command=(shlex.join(argv) if mode=="shell" else
                         [uv,"--no-config","run","--offline","--no-project","--no-sync",
                          "--python",sys.executable,*argv])
                result=subprocess.run(command,shell=mode=="shell",capture_output=True,text=True)
                assert result.returncode==0 and result.stdout=="SDK-REAL\n",result.stderr
        finally:
            registry.close()
        with pytest.raises(ValueError,match="unsupported|no parent-issued"):
            registry.verify()

    @pytest.mark.parametrize("distribution",["directory","sdist"])
    def test_real_sdk_source_build_is_preserved_but_not_admitted(self,tmp_path,distribution):
        from tools.coverage_guided_selection import standalone as s
        uv=shutil.which("uv")
        assert uv
        target=tmp_path/"source-install"
        source=s.REPO/"fleet-manager"
        if distribution=="sdist":
            output=tmp_path/"sdist"
            output.mkdir()
            built=subprocess.run([sys.executable,"-I","-c",
                "from setuptools.build_meta import build_sdist;build_sdist("+repr(str(output))+")"],
                cwd=source,capture_output=True,text=True)
            assert built.returncode==0,built.stderr
            source=next(output.glob("*.tar.gz"))
        registry=launch.Registry(
            tmp_path/"tickets",sys.executable,self._activation(tmp_path),"offline::source-build",uv=uv,
        )
        argv=[uv,"--no-config","pip","install","--python",sys.executable,"--target",str(target),
              "--offline","--no-index","--no-deps","--no-build-isolation",str(source)]
        registry.install()
        try:
            result=subprocess.run(argv,capture_output=True,text=True)
        finally:
            registry.close()
        assert result.returncode==0,result.stderr
        assert result.args==argv and argv.count("--no-config")==1
        mapping,hashes=s.verified_source_mapping(s.REPO,target/"fleet_manager",target)
        assert mapping and set(mapping.values()) == set(hashes)
        with pytest.raises(ValueError,match="outside the closed|not admitted|no parent-issued"):
            registry.verify()

    def test_warm_wheel_uv_retains_real_issued_entries_not_cache_only_claims(self,tmp_path):
        from tools.coverage_guided_selection import standalone as s
        uv=shutil.which("uv")
        assert uv
        output=tmp_path/"wheel"
        output.mkdir()
        built=subprocess.run([sys.executable,"-I","-c",
            "from setuptools.build_meta import build_wheel;build_wheel("+repr(str(output))+")"],
            cwd=s.REPO/"fleet-manager",capture_output=True,text=True)
        assert built.returncode==0,built.stderr
        wheel=next(output.glob("*.whl"))
        original=Path(__import__("fleet_manager").__file__).parent.parent
        registry=launch.Registry(
            tmp_path/"tickets",sys.executable,self._activation(tmp_path),"offline::warm-cache",
            measurement_plan=lambda root,payload:self._plan(root,payload,original),uv=uv,
        )
        argv=[uv,"--no-config","pip","install","--python",sys.executable,"--target",str(tmp_path/"sdk"),
              "--offline","--no-index","--no-deps",str(wheel)]
        environment=dict(os.environ,UV_CACHE_DIR=str(tmp_path/"uv-cache"))
        registry.install()
        try:
            first=subprocess.run(argv,env=environment,capture_output=True,text=True)
            assert first.returncode==0,first.stderr
            assert any(row.get("uv_entries")==1 for row in registry.verify())
            second=subprocess.run(argv,env=environment,capture_output=True,text=True)
            assert second.returncode==0,second.stderr
        finally:
            registry.close()
        assert first.args==second.args==argv
        records=registry.verify()
        assert sum(row.get("uv_entries",0) for row in records)==2
        assert sum(bool(row["request"].get("parent_uv")) for row in records)==2

    def test_native_cache_only_uv_operation_preserved_but_refused(self,tmp_path):
        uv=shutil.which("uv")
        assert uv
        registry=launch.Registry(
            tmp_path/"tickets",sys.executable,self._activation(tmp_path),"offline::cache-only",uv=uv,
        )
        cache=tmp_path/"cache"
        argv=[uv,"--no-config","cache","dir"]
        registry.install()
        try:
            result=subprocess.run(argv,env=dict(os.environ,UV_CACHE_DIR=str(cache)),
                                  capture_output=True,text=True)
        finally:
            registry.close()
        assert result.returncode==0 and Path(result.stdout.strip())==cache,result.stderr
        assert result.args==argv and not registry.requests
        with pytest.raises(ValueError,match="only explicit offline wheel|no parent-issued"):
            registry.verify()

    @pytest.mark.parametrize("context",["inherited","explicit-empty","removed"])
    def test_renamed_descendant_executes_sdk_but_invalidates_parent_measurement(self,tmp_path,context):
        copied,program=self._sdk(tmp_path)
        native=tmp_path/"opaque-descendant"
        shutil.copyfile(Path(sys.executable).resolve(),native)
        native.chmod(0o700)
        inner=[str(native),"-I","-S","-B","-c",program]
        environment=",env={}" if context=="explicit-empty" else ""
        prefix="import os;os.environ.pop('_CGS_CHILD_MANIFEST',None);" if context=="removed" else ""
        outer=(prefix+"import subprocess;result=subprocess.run("+repr(inner)+environment+",capture_output=True,text=True);"
               "assert result.returncode==0,result.stderr;assert result.stdout=='SDK-REAL\\n'")
        registry=launch.Registry(
            tmp_path/"tickets",sys.executable,self._activation(tmp_path),"offline::descendant",
            measurement_plan=lambda root,payload:self._plan(root,payload,copied),
        )
        registry.install()
        try:
            result=subprocess.run([sys.executable,"-I","-S","-B","-c",outer],
                                  capture_output=True,text=True)
        finally:
            registry.close()
        assert result.returncode==0,result.stderr
        with pytest.raises(ValueError,match="unmanaged native executable|stripped native child coverage context"):
            registry.verify()
