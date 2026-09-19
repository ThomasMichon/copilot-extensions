"""Execute the real extension's discovery/rebinding code with a stub SDK."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_extension_recovers_missing_token_and_rebinding_without_a_new_session():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    extension = Path(__file__).parents[1] / "extensions" / "agent-bridge" / "extension.mjs"
    script = r"""
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(process.argv[1], 'utf8').replace(/^import .*;\r?\n/gm, '');
const files = new Map();
const base = 'bridge-config';
files.set(path.join(base, 'active.json'), JSON.stringify({active:{port:10001}}));
const calls = [], timers = [];
let joins = 0, sends = 0;
const proc = {
  env:{SESSION_ID:'session-one',AGENT_BRIDGE_CONFIG_DIR:base,
       AGENT_BRIDGE_NATIVE_EXECUTION_ID:'execution-one',AGENT_BRIDGE_NATIVE_GENERATION:'generation-one'},
  platform:'linux',pid:77,cwd:()=>'/workspaces/example-web',
  stderr:{write(){}},on(){},once(){},
};
const session = {on(){},send:async()=>{sends++;},log(){}};
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
const run = new AsyncFunction('existsSync','readFileSync','join','basename','homedir','release',
  'execFileSync','execSync','approveAll','joinSession','process','fetch',
  'setInterval','clearInterval','setTimeout','clearTimeout',
  source + '\nreturn {state,register};');
(async()=>{
 const api = await run(
  p=>files.has(p),p=>files.get(p),path.join,path.basename,()=>'/home/example',()=>'',()=>'',()=>'',
  ()=>{},async()=>{joins++;return session;},proc,
  async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({messages:[]})};},
  f=>{timers.push(f);return {unref(){}};},()=>{},()=>({unref(){}}),()=>{});
 if(timers.length!==3) throw Error('missing retry timers while token absent');
 files.set(path.join(base,'auth.yaml'),'token: token-one');
 await api.register();
 files.set(path.join(base,'active.json'),JSON.stringify({active:{port:10002}}));
 files.set(path.join(base,'auth.yaml'),'token: token-two');
 await api.register();
 const last = calls.at(-1);
 const body = JSON.parse(last.options.body);
 if(!last.url.includes(':10002/') || last.options.headers.Authorization!=='Bearer token-two')
   throw Error('endpoint or token was not rebound');
 if(body.execution_id!=='execution-one' || body.execution_generation!=='generation-one')
   throw Error('execution identity was lost');
 if(joins!==1 || sends!==0) throw Error('rebinding created/drove a second session');
 console.log(JSON.stringify({registered:api.state.registered,joins,sends}));
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", script, str(extension)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"registered": True, "joins": 1, "sends": 0}
