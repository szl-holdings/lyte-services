/* Browser-boundary unit tests. Normative fixtures are not measured model evidence. */
import test from "node:test";
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {parseValues, decodeInspection} from "../lyte/ui/forecast.mjs";

const hash = (text) => createHash("sha256").update(text).digest("hex");
function fixture() {
  const request = {signal_id:"sample.test",values:[1,2,3],horizon:1,quantiles:[0.1,0.5,0.9],provider:"baseline",
    risk:{threshold:10,direction:"above",alert_level:0.8}};
  const receipt = {contract:"szl.lyte.forecast-loom/v1",provider:"szl.robust-drift/v1",signal_id:request.signal_id,
    horizon:1,quantiles:request.quantiles,input_sha256:"a".repeat(64),raw_input_sha256:"b".repeat(64),output_sha256:"c".repeat(64)};
  const risk = {contract:"szl.lyte.forecast-risk-window/v1",signal_id:request.signal_id,provider:receipt.provider,
    rule:request.risk,event_semantics:"STRICT_GREATER_THAN",time_unit:"FORECAST_STEPS_NOT_WALL_CLOCK",
    steps:[{step:1,model_implied_breach_lower:0.1,model_implied_breach_upper:0.5,median_crosses:false}],
    earliest_possible_alert_step:null,earliest_supported_alert_step:null,first_median_crossing_step:null,
    any_breach_over_horizon:{lower:0.1,upper:0.5,method:"MARGINAL_MAX_LOWER_UNION_SUM_UPPER_NO_INDEPENDENCE_ASSUMPTION"},
    forecast_output_sha256:receipt.output_sha256,forecast_receipt_sha256:"d".repeat(64),report_sha256:"e".repeat(64),
    calibration_status:"NOT_ESTABLISHED",production_admitted:false,execution_authority:"NONE"};
  const payload = {schema:"szl.lyte.forecast-inspection/v1",source:{repository:"szl-holdings/lyte-services",revision:"a".repeat(40)},
    request,forecast:{receipt,points:[{step:1,quantiles:{q10:5,q50:10,q90:15}}],risk_window:risk,execution_authority:"NONE"},
    persisted:false,execution_authority:"NONE",input_provenance:"CALLER_SUPPLIED_NOT_INDEPENDENTLY_VERIFIED"};
  return {payload,request};
}
function wrap(payload) {
  const canonical_json = JSON.stringify(payload);
  return {schema:"szl.lyte.forecast-inspection-envelope/v1",canonical_json,sha256:hash(canonical_json),
    hash_semantics:"CONTENT_INTEGRITY_NOT_SIGNATURE_OR_ACCURACY"};
}

test("parse finite observations, null, unicode whitespace, exponent and negative zero", () => {
  assert.deepEqual(parseValues(" [1, null, -.5, 2e2, -0] "),[1,null,-.5,200,-0]);
});
for (const text of ["", "1,,2", "NaN", "Infinity", "1e999", "true", "1 2", "1,", "nullish", "[1", "1]", "1,2;3"]) {
  test(`reject malformed numeric input ${JSON.stringify(text)}`, () => assert.throws(() => parseValues(text)));
}
test("context workload bound", () => assert.throws(() => parseValues(Array(8193).fill("1").join(","))));
test("valid conditional bounds and strict median tie", async () => {
  const {payload,request} = fixture(); assert.deepEqual(await decodeInspection(wrap(payload),request),payload);
});
test("exact bytes survive Python-style 1.0 and -0.0 spelling", async () => {
  const {payload,request} = fixture(); const envelope = wrap(payload);
  envelope.canonical_json = envelope.canonical_json.replace('"values":[1,2,3]', '"values":[1.0,2.0,3.0]');
  envelope.sha256 = hash(envelope.canonical_json);
  assert.deepEqual((await decodeInspection(envelope,request)).request.values,[1,2,3]);
});
test("tampered bytes never display", async () => {
  const {payload,request} = fixture(); const envelope = wrap(payload);
  envelope.canonical_json += " "; await assert.rejects(decodeInspection(envelope,request),/digest/);
});
for (const [field,value] of [["production_admitted",true],["production_admitted",0],["execution_authority","EXECUTE"],
  ["calibration_status","CALIBRATED"],["time_unit","HOURS"],["first_median_crossing_step",1],
  ["earliest_possible_alert_step",1],["earliest_supported_alert_step",1],["forecast_output_sha256","f".repeat(64)]]) {
  test(`self-rehashed false ${field} rejected (${String(value)})`, async () => {
    const {payload,request} = fixture(); payload.forecast.risk_window[field] = value;
    await assert.rejects(decodeInspection(wrap(payload),request));
  });
}
test("self-rehashed false model bounds rejected", async () => {
  const {payload,request} = fixture(); payload.forecast.risk_window.steps[0].model_implied_breach_upper = .9;
  await assert.rejects(decodeInspection(wrap(payload),request));
});
test("self-rehashed crossing quantiles rejected", async () => {
  const {payload,request} = fixture(); payload.forecast.points[0].quantiles.q50 = 99;
  await assert.rejects(decodeInspection(wrap(payload),request));
});
test("old runtime with no risk object fails", async () => {
  const {payload,request} = fixture(); delete payload.forecast.risk_window;
  await assert.rejects(decodeInspection(wrap(payload),request));
});
test("a different request cannot reuse a valid envelope", async () => {
  const {payload,request} = fixture(); const envelope = wrap(payload); request.risk.threshold = 5;
  await assert.rejects(decodeInspection(envelope,request));
});
test("unbound source fails", async () => {
  const {payload,request} = fixture(); payload.source.revision = null;
  await assert.rejects(decodeInspection(wrap(payload),request));
});
