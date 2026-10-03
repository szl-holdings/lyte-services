"""Execute syntax checks against the canonical external browser program."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "lyte" / "ui" / "app.js"
HTML = ROOT / "lyte" / "ui" / "index.html"


def test_shipped_external_javascript_parses() -> None:
    node = shutil.which("node")
    assert node is not None, "Node.js is required to verify the browser program"
    checked = subprocess.run(  # noqa: S603
        [node, "--check", str(SCRIPT)],
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr


def test_frontend_hydrates_every_operating_lens_from_scoped_apis() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for route in (
        "/api/build-info",
        "/api/lyte/v2/sources",
        "/api/lyte/v2/services",
        "/api/lyte/v2/journeys",
        "/api/lyte/v2/outcomes",
        "/api/lyte/v2/agents",
        "/api/lyte/v2/incidents",
        "/api/lyte/v2/decisions",
        "/api/lyte/v2/playback",
        "/api/lyte/v2/receipts",
    ):
        assert f'"{route}"' in source
    assert "Promise.allSettled" in source
    assert "validatePage(name, payload)" in source
    assert 'workspace.mode = "UNAVAILABLE"' in source
    assert 'payload.data_mode === "REAL" ? "REAL_ONLY" : "SAMPLE"' in source


def test_frontend_uses_persisted_hashes_and_has_no_pseudo_receipts() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    html = HTML.read_text(encoding="utf-8")
    combined = source + html
    assert "rcpt-sample" not in combined
    assert "rcpt-model" not in combined
    assert "$184" not in combined
    assert "184k" not in combined
    assert "const HASH = /^[0-9a-f]{64}$/i" in source
    assert "validHash(record?.record_hash)" in source
    assert "No offline or hand-authored record was substituted." in source
    assert "REAL_ONLY; no sample fallback" in source


def test_agent_and_ask_truth_are_taken_from_api_payloads() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "createBadge(record.truth_label)" in source
    assert "decisionDetails.agent = detailFromRecord(first)" in source
    assert 'TRUTH_LABELS.has(payload?.truth_label) ? payload.truth_label : "UNAVAILABLE"' in source


def test_source_paint_cannot_mint_measured_from_reachability() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'return ["CONNECTED", "CONFIGURED", "READY", "LIVE"].includes(state);' not in source
    assert 'return truth === "MEASURED"' in source
    assert 'buildObserved ? "MEASURED"' not in source
    assert 'isLiveSource(github) ? "MEASURED" : "REPORTED"' not in source
    assert 'measuredCount ? "MEASURED" : "UNAVAILABLE"' in source
    assert '"0 BLOCKED"' in source
    assert "`${liveCount} LIVE`" not in source


def _run_browser_functions(program: str) -> None:
    """Exercise the shipped functions in Node without altering the production script."""
    node = shutil.which("node")
    assert node is not None
    harness = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const dom = {};
const context = {
  AbortController, Date, URL, console,
  document: {
    documentElement: {dataset:{}}, body: {classList:{remove(){}}},
    querySelector: selector => dom[selector] || null, querySelectorAll: () => []
  },
  window: { matchMedia: () => ({matches:false}), setTimeout, clearTimeout },
  fetch: (...args) => context.mockFetch(...args),
};
const source = fs.readFileSync(SCRIPT_PATH, 'utf8');
const instrumented = source.replace(/\s+boot\(\);\s*\}\)\(\);\s*$/, `
globalThis.testFunctions = {
  indicatorNumber, readingNumber, stepSignal, validateScope, scopeHeaders,
  loadPage, isLiveSource, closeDrawer, validTruth,
  setReturnFocus(value){drawerReturnFocus=value;}
};
})();`);
assert.notEqual(source, instrumented, 'the shipped boot must be isolated for function testing');
vm.runInNewContext(instrumented, context);
const api = context.testFunctions;
const hash = index => index.toString(16).padStart(64, '0');
(async () => { TEST_PROGRAM })().catch(error => { console.error(error); process.exitCode = 1; });
"""
    harness = harness.replace("SCRIPT_PATH", json.dumps(str(SCRIPT))).replace(
        "TEST_PROGRAM", program
    )
    checked = subprocess.run(  # noqa: S603
        [node, "-e", harness],
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_unavailable_numbers_cannot_turn_into_zero_or_truthful_metrics() -> None:
    _run_browser_functions(r"""
for (const value of [null, undefined, '', '0', true, Infinity, NaN]) {
  assert.equal(api.readingNumber({value, truth_label:'REPORTED'}), null);
}
assert.equal(api.readingNumber({value:0, truth_label:'UNAVAILABLE'}), null);
assert.equal(api.readingNumber({value:42, truth_label:'ROADMAP'}), null);
const unavailable={value:null,truth_label:'UNAVAILABLE'};
assert.equal(api.indicatorNumber({body:{indicators:{latency:unavailable}}}, 'latency'), null);
assert.equal(api.readingNumber({value:0, truth_label:'MEASURED'}), 0);
assert.equal(api.readingNumber({value:0.61, truth_label:'SAMPLE'}), 0.61);
assert.equal(api.stepSignal({indicators:{completion_rate:unavailable}}),
  'completion rate UNAVAILABLE');
assert.equal(api.validTruth('ROADMAP'), 'ROADMAP');
""")


def test_scoped_access_and_bounded_pagination_use_the_shipped_request_contract() -> None:
    _run_browser_functions(r"""
const tenant='11111111-1111-4111-8111-111111111111';
const workspace='22222222-2222-4222-8222-222222222222';
assert.throws(() => api.validateScope('test\r\nheader',tenant,workspace));
assert.throws(() => api.validateScope('test-token','not-a-uuid',workspace));
const headers=api.scopeHeaders(api.validateScope('test-token',tenant,workspace));
assert.equal(headers.Authorization,'Bearer test-token');
assert.equal(headers['X-Lyte-Tenant-ID'],tenant);
assert.equal(headers['X-Lyte-Workspace-ID'],workspace);
const calls=[];
context.mockFetch=async (url,options) => {
  const offset=Number(new URL(url,'http://localhost').searchParams.get('offset'));
  calls.push({url,headers:options.headers});
  return {ok:true,headers:{get:()=> 'application/json'},json:async()=>({
    schema:'szl.lyte.operational-page/v1',data_mode:'REAL',offset,next_offset:offset+100,
    items:Array.from({length:100},(_,index)=>({record_hash:hash(offset+index),body:{}}))
  })};
};
const page=await api.loadPage('services',headers);
assert.equal(page.items.length,500);
assert.equal(page.mode,'REAL_ONLY');
assert.equal(page.truncated,true);
assert.equal(calls.length,5);
assert.equal(calls[4].headers.Authorization,'Bearer test-token');
assert.equal(calls[4].headers['X-Lyte-Workspace-ID'],workspace);
context.mockFetch=async()=>({ok:true,headers:{get:()=> 'application/json'},json:async()=>({
  schema:'szl.lyte.operational-page/v1',data_mode:'REAL',offset:0,next_offset:0,items:[]
})});
await assert.rejects(api.loadPage('services',headers),/invalid next offset/);
""")
    source = SCRIPT.read_text(encoding="utf-8")
    for storage in ("localStorage", "sessionStorage", "indexedDB", "document.cookie"):
        assert storage not in source
    assert "if (generation !== workspaceLoadGeneration) return;" in source
    assert "A scoped request returned sample data" in source


def test_source_observation_requires_fresh_receipt_and_svg_focus_returns() -> None:
    _run_browser_functions(r"""
assert.equal(api.isLiveSource({truth_label:'MEASURED',state:'CONFIGURED'}),false);
const observation={truth_label:'MEASURED',state:'OBSERVED',observed_at:new Date().toISOString()};
assert.equal(api.isLiveSource(observation),false);
const source={...observation,receipt_id:hash(1)};
assert.equal(api.isLiveSource(source),true);
const stale=new Date(Date.now()-16*60*1000).toISOString();
assert.equal(api.isLiveSource({...source,observed_at:stale}),false);
assert.equal(api.isLiveSource({...source,truth_label:'REPORTED'}),false);
let focused=false;
const svgNode={isConnected:true,focus(){focused=true;}};
api.setReturnFocus(svgNode);
dom['#signal-drawer']={hidden:false,classList:{remove(){}}};
context.window.setTimeout=fn=>{fn();return 1;};
api.closeDrawer();
assert.equal(focused,true);
assert.equal(dom['#signal-drawer'].hidden,true);
""")


def test_browser_start_waits_for_transient_locked_debug_marker(tmp_path, monkeypatch) -> None:
    from tools.capture_ui_evidence import _wait_for_debug_port

    marker = tmp_path / "DevToolsActivePort"
    marker.write_text("18762\n/browser/test\n", encoding="utf-8")
    original_read = Path.read_text
    attempts = 0

    def read_marker(path: Path, *args, **kwargs):
        nonlocal attempts
        if path == marker:
            attempts += 1
            if attempts == 1:
                raise PermissionError("transient Chromium startup lock")
        return original_read(path, *args, **kwargs)

    class RunningBrowser:
        def poll(self):
            return None

    monkeypatch.setattr(Path, "read_text", read_marker)
    assert _wait_for_debug_port(tmp_path, RunningBrowser()) == 18762
    assert attempts == 2


def test_browser_shutdown_uses_observed_pid_after_launcher_has_exited(monkeypatch) -> None:
    from tools.capture_ui_evidence import _stop_browser_process

    class ExitedLauncher:
        pid = 111

        def poll(self):
            return 0

        def wait(self, timeout):
            return 0

    commands = []
    monkeypatch.setattr(shutil, "which", lambda command: "taskkill.exe")
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: commands.append(command))
    _stop_browser_process(
        ExitedLauncher(),
        graceful_close_requested=False,
        platform_name="nt",
        browser_pid=222,
    )
    assert commands == [["taskkill.exe", "/PID", "222", "/T", "/F"]]
