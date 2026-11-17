"""Execute the shipped drawer contract in Node; no replacement application logic."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "lyte/ui/app.js"


def execute(program: str) -> None:
    node = shutil.which("node")
    assert node is not None, "Node required for the shipped frontend contract"
    harness = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const dom = {};
function element() {
  return {hidden:false, textContent:'', children:[], classList:{add(){},remove(){}},
    replaceChildren(...children){this.children=children;},
    append(...children){this.children.push(...children);},
    addEventListener(){}, setAttribute(){}};
}
for(const name of ['receipt-inspection','receipt-inspection-status','receipt-inspection-checks',
  'receipt-inspection-references','signal-drawer']) dom['#'+name]=element();
const context = {
  AbortController, Date, URL, console,
  document:{documentElement:{dataset:{}},body:{classList:{remove(){}}},
    querySelector:selector=>dom[selector]||null, querySelectorAll:()=>[],
    createElement:()=>element()},
  window:{matchMedia:()=>({matches:false}),setTimeout,clearTimeout},
  fetch:(...args)=>context.mockFetch(...args),
};
const source=fs.readFileSync(SCRIPT_PATH,'utf8');
const instrumented=source.replace(/\s+boot\(\);\s*\}\)\(\);\s*$/, `
globalThis.inspectionTest={validateReceiptInspection,renderReceiptInspection,loadReceiptInspection,
  setGeneration(value){receiptInspectionGeneration=value;},
  setWorkspace(value){workspaceLoadGeneration=value;}};
})();`);
assert.notEqual(source,instrumented,'must isolate actual boot, not a replacement renderer');
vm.runInNewContext(instrumented,context);
const api=context.inspectionTest;
const hash='a'.repeat(64), ref='b'.repeat(64);
const record=()=>({schema:'szl.lyte.receipt-inspection/v1',record_hash:hash,status:'PARTIAL',
  record_integrity:'VERIFIED_LOCAL',can_authorize:false,can_execute:false,effectors_enabled:false,
  reference_limit:32,expected_references:1,inspected_references:1,verified_references:1,
  coverage_fraction:1,coverage_state:'COMPLETE_LOCAL',limitations:['SIGNATURE_NOT_VERIFIED'],
  inspected_at:'2026-11-16T00:00:00Z',references:[{record_hash:ref,reference_sha256:'c'.repeat(64),
    state:'VERIFIED_LOCAL',reason:'CANONICAL_PROJECTION_MATCHES'}]});
const response=payload=>({ok:true,status:200,headers:{get:()=> 'application/json'},
  json:async()=>payload});
(async()=>{TEST_PROGRAM})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    harness = harness.replace("SCRIPT_PATH", json.dumps(str(SCRIPT)))
    harness = harness.replace("TEST_PROGRAM", program)
    result = subprocess.run(  # noqa: S603
        [node, "-e", harness], text=True, encoding="utf-8", capture_output=True,
        check=False, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_shipped_contract_accepts_exact_non_authorizing_record():
    execute("assert.equal(api.validateReceiptInspection(record(),hash),true);")


@pytest.mark.parametrize("change", [
    "p.record_hash='f'.repeat(64)", "p.schema='unknown'", "p.status='VERIFIED'",
    "p.can_authorize=true", "p.can_execute=true", "p.effectors_enabled=true",
    "p.reference_limit=1000", "p.expected_references=0", "p.verified_references=2",
    "p.references=[]", "p.references[0].state='APPROVED'",
    "p.references[0].record_hash='javascript:alert(1)'",
    "p.references[0].reference_sha256='bad'", "p.limitations=[{}]",
    "p.coverage_fraction=0.5", "p.coverage_fraction=NaN",
    "p.coverage_state='EMPTY_POPULATION'",
])
def test_wrong_binding_or_unsupported_success_is_rejected(change):
    execute(f"const p=record();{change};assert.equal(api.validateReceiptInspection(p,hash),false);")


def test_closed_drawer_does_not_receive_delayed_result():
    execute("""
api.setGeneration(1);api.setWorkspace(1);
let resolve;context.mockFetch=()=>new Promise(done=>{resolve=done;});
const pending=api.loadReceiptInspection(hash,1,1);
dom['#signal-drawer'].hidden=true;
dom['#receipt-inspection-status'].textContent='CLOSED';
resolve(response(record()));await pending;
assert.equal(dom['#receipt-inspection-status'].textContent,'CLOSED');
""")


@pytest.mark.parametrize("boundary", ["api.setGeneration(2)", "api.setWorkspace(2)"])
def test_old_response_cannot_paint_a_different_selection_or_workspace(boundary):
    execute("""
api.setGeneration(1);api.setWorkspace(1);
let resolve;context.mockFetch=()=>new Promise(done=>{resolve=done;});
const pending=api.loadReceiptInspection(hash,1,1);
""" + boundary + ";" + """
dom['#receipt-inspection-status'].textContent='NEW_SCOPE';
resolve(response(record()));await pending;
assert.equal(dom['#receipt-inspection-status'].textContent,'NEW_SCOPE');
""")


def test_no_cached_green_state_on_http_failure():
    execute("""
api.setGeneration(1);api.setWorkspace(1);
context.mockFetch=async(_url,options)=>{
 assert.equal(options.cache,'no-store');return {ok:false,status:503};
};
await api.loadReceiptInspection(hash,1,1);
assert.match(dom['#receipt-inspection-status'].textContent,/UNAVAILABLE/);
assert.equal(dom['#receipt-inspection-checks'].children.length,0);
""")


def test_diagnostic_strings_use_text_not_html():
    execute("""
const p=record();p.references[0].reason='<img src=x onerror=alert(1)>';
api.renderReceiptInspection(p,hash);
const reference=dom['#receipt-inspection-references'].children[0];
assert.equal(reference.children[2].textContent,' — <img src=x onerror=alert(1)>');
assert.equal(reference.children[2].innerHTML,undefined);
assert.match(dom['#receipt-inspection-status'].textContent,/not action approval/);
""")
