/* SZL presentation only. Forecasting and authority stay in the Python service. */
const API = "/api/lyte/v2/forecast/inspect";
const Q = [0.1, 0.5, 0.9];
const KEYS = ["q10", "q50", "q90"];
const SAMPLE = "40, 42, 41, 45, 48, 51, 53, 55";
const finite = (value) => typeof value === "number" && Number.isFinite(value);
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const assert = (ok, message) => { if (!ok) throw new Error(message); };
const first = (rows, predicate) => rows.find(predicate)?.step ?? null;
const close = (a, b) => finite(a) && Math.abs(a - b) <= 1e-12;

export function parseValues(text) {
  assert(typeof text === "string" && text.length <= 200000, "Input is too large.");
  const trimmed = text.trim();
  assert(trimmed.startsWith("[") === trimmed.endsWith("]"), "Observation brackets must be balanced.");
  const tokens = (trimmed.startsWith("[") ? trimmed.slice(1, -1) : trimmed).split(",");
  assert(tokens.length >= 1 && tokens.length <= 8192, "Use 1 to 8,192 observations.");
  return tokens.map((token) => {
    const value = token.trim();
    if (value === "null") return null;
    assert(/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(value),
      "Observations must be numbers or null; empty values are not zero.");
    const number = Number(value);
    assert(finite(number), "Every observed value must be finite.");
    return number;
  });
}

export async function decodeInspection(envelope, requested) {
  assert(envelope?.schema === "szl.lyte.forecast-inspection-envelope/v1", "Unsupported response envelope.");
  assert(envelope.hash_semantics === "CONTENT_INTEGRITY_NOT_SIGNATURE_OR_ACCURACY", "Unexpected evidence semantics.");
  assert(typeof envelope.canonical_json === "string" && envelope.canonical_json.length <= 1000000,
    "Missing or oversized evidence.");
  assert(/^[a-f0-9]{64}$/.test(envelope.sha256 ?? ""), "Invalid response digest.");
  assert(globalThis.crypto?.subtle, "This browser needs a secure context to verify response bytes.");
  const bytes = new TextEncoder().encode(envelope.canonical_json);
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)))
    .map((byte) => byte.toString(16).padStart(2, "0")).join("");
  assert(digest === envelope.sha256, "Response bytes do not match the content digest.");
  const payload = JSON.parse(envelope.canonical_json);
  assert(payload.schema === "szl.lyte.forecast-inspection/v1" && payload.persisted === false
    && payload.execution_authority === "NONE"
    && payload.input_provenance === "CALLER_SUPPLIED_NOT_INDEPENDENTLY_VERIFIED", "Unexpected inspection authority.");
  assert(payload.source?.repository === "szl-holdings/lyte-services"
    && /^[a-f0-9]{40}$/.test(payload.source.revision), "Source identity is unavailable.");
  const input = payload.request;
  for (const key of ["signal_id", "values", "horizon", "quantiles", "provider"]) {
    assert(same(input?.[key], requested[key]), "Response belongs to a different request.");
  }
  for (const key of ["threshold", "direction", "alert_level"]) {
    assert(input?.risk?.[key] === requested.risk[key], "Response belongs to a different risk rule.");
  }
  assert(input.provider === "baseline" && same(input.quantiles, Q), "Unsupported workbench provider or quantiles.");
  const forecast = payload.forecast;
  const risk = forecast?.risk_window;
  assert(forecast?.execution_authority === "NONE" && risk?.execution_authority === "NONE"
    && risk?.production_admitted === false && risk?.calibration_status === "NOT_ESTABLISHED",
    "The response changes the advisory boundary.");
  assert(risk.contract === "szl.lyte.forecast-risk-window/v1"
    && risk.time_unit === "FORECAST_STEPS_NOT_WALL_CLOCK", "Unsupported risk contract.");
  const receipt = forecast.receipt;
  assert(receipt?.contract === "szl.lyte.forecast-loom/v1" && receipt.provider === "szl.robust-drift/v1"
    && receipt.signal_id === input.signal_id && receipt.horizon === input.horizon
    && same(receipt.quantiles, Q), "Unexpected forecast receipt.");
  assert(risk.signal_id === input.signal_id && risk.provider === receipt.provider,
    "Risk and forecast identities differ.");
  for (const key of ["threshold", "direction", "alert_level"]) {
    assert(risk.rule?.[key] === input.risk[key], "Risk rule binding differs.");
  }
  assert(risk.event_semantics === (input.risk.direction === "above" ? "STRICT_GREATER_THAN" : "STRICT_LESS_THAN"),
    "Strict threshold semantics differ.");
  assert(Array.isArray(forecast.points) && forecast.points.length === input.horizon
    && Array.isArray(risk.steps) && risk.steps.length === input.horizon, "Incomplete forecast horizon.");
  const expected = forecast.points.map((point, index) => {
    const row = point.quantiles;
    assert(point.step === index + 1 && same(Object.keys(row ?? {}).sort(), [...KEYS].sort()), "Malformed forecast step.");
    const values = KEYS.map((key) => row[key]);
    assert(values.every(finite) && values[0] <= values[1] && values[1] <= values[2], "Nonfinite or crossing quantiles.");
    let lo = 0, hi = 1;
    for (let i = 0; i < Q.length; i++) {
      if (input.risk.direction === "above") {
        const complement = (100 - Math.round(Q[i] * 100)) / 100;
        if (values[i] <= input.risk.threshold) hi = Math.min(hi, complement);
        else lo = Math.max(lo, complement);
      } else if (values[i] < input.risk.threshold) lo = Math.max(lo, Q[i]);
      else hi = Math.min(hi, Q[i]);
    }
    const crosses = input.risk.direction === "above" ? row.q50 > input.risk.threshold : row.q50 < input.risk.threshold;
    const observed = risk.steps[index];
    assert(observed?.step === index + 1 && close(observed.model_implied_breach_lower, lo)
      && close(observed.model_implied_breach_upper, hi) && observed.median_crosses === crosses,
      "Risk bounds do not match the model quantiles.");
    return {step: index + 1, lo, hi, crosses};
  });
  assert(risk.earliest_possible_alert_step === first(expected, (row) => row.hi >= input.risk.alert_level)
    && risk.earliest_supported_alert_step === first(expected, (row) => row.lo >= input.risk.alert_level)
    && risk.first_median_crossing_step === first(expected, (row) => row.crosses), "Alert windows do not match the forecast.");
  assert(close(risk.any_breach_over_horizon?.lower, Math.max(...expected.map((row) => row.lo)))
    && close(risk.any_breach_over_horizon?.upper, Math.min(1, expected.reduce((sum, row) => sum + row.hi, 0)))
    && risk.any_breach_over_horizon?.method === "MARGINAL_MAX_LOWER_UNION_SUM_UPPER_NO_INDEPENDENCE_ASSUMPTION",
    "Horizon bounds changed their dependence assumptions.");
  for (const key of ["input_sha256", "raw_input_sha256", "output_sha256"]) {
    assert(/^[a-f0-9]{64}$/.test(receipt[key] ?? ""), "Missing forecast content digest.");
  }
  assert(risk.forecast_output_sha256 === receipt.output_sha256
    && /^[a-f0-9]{64}$/.test(risk.forecast_receipt_sha256 ?? "")
    && /^[a-f0-9]{64}$/.test(risk.report_sha256 ?? ""), "Missing risk evidence bindings.");
  return payload;
}

function boot() {
  const $ = (id) => document.getElementById(id);
  const form = $("forecast-form");
  if (!form) return;
  let generation = 0, controller = null, current = null, exportEnvelope = null;
  const fmt = (value) => new Intl.NumberFormat("en", {maximumSignificantDigits: 5}).format(value);
  const percent = (value) => `${Math.round(value * 100)}%`;
  const stepLabel = (step) => step === null ? "Not established" : `Step ${step}`;
  function busy(value) {
    $("run").disabled = value;
    $("result-panel").setAttribute("aria-busy", String(value));
  }
  function invalidate() {
    generation += 1;
    controller?.abort();
    current = null; exportEnvelope = null;
    $("forecast-output").hidden = true;
    $("result-panel").classList.remove("error");
    $("status").textContent = "Inputs changed. Submit to calculate a new forecast.";
    $("input-origin").textContent = "CALLER-SUPPLIED";
    busy(false);
  }
  // Input edits invalidate results immediately, including in-flight requests.
  form.addEventListener("input", invalidate);
  $("reset-sample").addEventListener("click", () => {
    invalidate(); form.reset(); $("values").value = SAMPLE;
    $("input-origin").textContent = "SAMPLE";
  });
  function svg(tag, attributes, text) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    if (text !== undefined) node.textContent = text;
    $("chart-content").append(node);
    return node;
  }
  function draw() {
    const {request, forecast} = current;
    const group = $("chart-content"); group.replaceChildren();
    const series = request.values, points = forecast.points, threshold = request.risk.threshold;
    const all = [...series.filter(finite), threshold, ...points.flatMap((p) => KEYS.map((key) => p.quantiles[key]))];
    // Scale first so even very large finite magnitudes do not overflow max-min.
    const magnitude = Math.max(1, ...all.map(Math.abs));
    const min = Math.min(...all.map((v) => v / magnitude)), max = Math.max(...all.map((v) => v / magnitude));
    const span = max - min || 1;
    const x = (i) => 60 + (i / Math.max(1, series.length + points.length - 1)) * 630;
    const y = (v) => 255 - ((v / magnitude - min) / span) * 210;
    svg("line", {x1:60,y1:255,x2:690,y2:255,class:"chart-axis"});
    svg("text", {x:60,y:282,class:"chart-label"}, "History → future steps");
    svg("text", {x:8,y:45,class:"chart-label"}, fmt(max * magnitude));
    svg("text", {x:8,y:255,class:"chart-label"}, fmt(min * magnitude));
    // Missing historical observations remain visible as gaps, not invented data.
    let path = "", continuing = false;
    for (let i = 0; i < series.length; i++) {
      if (series[i] === null) { continuing = false; continue; }
      path += `${continuing ? "L" : "M"}${x(i)},${y(series[i])} `; continuing = true;
    }
    svg("path", {d:path,class:"chart-history"});
    const coords = (key) => points.map((p,i) => `${x(series.length+i)},${y(p.quantiles[key])}`);
    svg("polygon", {points:[...coords("q10"),...coords("q90").reverse()].join(" "),class:"chart-ribbon"});
    svg("polyline", {points:coords("q50").join(" "),class:"chart-median"});
    svg("line", {x1:60,x2:690,y1:y(threshold),y2:y(threshold),class:"chart-threshold"});
    const cursor = x(series.length + Number($("step").value) - 1);
    svg("line", {x1:cursor,x2:cursor,y1:35,y2:260,class:"chart-current"});
  }
  function inspectStep() {
    if (!current) return;
    const index = Number($("step").value) - 1;
    const point = current.forecast.points[index], risk = current.forecast.risk_window.steps[index];
    $("step-detail").textContent = `Step ${index + 1}: median ${fmt(point.quantiles.q50)}; model-implied strict breach bounds ${percent(risk.model_implied_breach_lower)}–${percent(risk.model_implied_breach_upper)}. Not calibrated.`;
    draw();
  }
  $("step").addEventListener("input", inspectStep);
  async function boundedJson(response) {
    assert(response.body, "The service returned no response body.");
    const reader = response.body.getReader(), chunks = []; let length = 0;
    try {
      while (true) {
        const {done,value} = await reader.read(); if (done) break;
        length += value.byteLength;
        assert(length <= 1500000, "The service response exceeds the workbench limit.");
        chunks.push(value);
      }
    } finally { await reader.cancel(); }
    const bytes = new Uint8Array(length); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return JSON.parse(new TextDecoder("utf-8", {fatal:true}).decode(bytes));
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const origin = $("input-origin").textContent; invalidate(); $("input-origin").textContent = origin;
    const ticket = generation; controller = new AbortController();
    const active = controller;
    const timeout = setTimeout(() => active.abort(), 20000);
    busy(true); $("status").textContent = "Computing in Python and checking response bytes…";
    try {
      const requested = {
        signal_id: $("signal").value.trim(), values: parseValues($("values").value),
        horizon: Number($("horizon").value), quantiles: Q, provider:"baseline",
        risk: {threshold:Number($("threshold").value),direction:$("direction").value,alert_level:Number($("alert").value)},
      };
      assert(requested.signal_id.length > 0 && requested.signal_id.length <= 256, "Enter a signal name.");
      assert(Number.isInteger(requested.horizon) && requested.horizon >= 1 && requested.horizon <= 1024, "Use 1 to 1,024 future steps.");
      assert(finite(requested.risk.threshold) && finite(requested.risk.alert_level)
        && requested.risk.alert_level > 0 && requested.risk.alert_level <= 1, "Enter a finite threshold and alert level in (0, 1].");
      const response = await fetch(API, {method:"POST",headers:{"Content-Type":"application/json","Accept":"application/json"},
        body:JSON.stringify(requested),signal:active.signal,credentials:"omit",cache:"no-store",redirect:"error"});
      assert(response.ok, response.status === 503 ? "Source or forecast capability unavailable. Nothing was admitted." : `Forecast unavailable (HTTP ${response.status}). Check inputs and deployed version.`);
      const envelope = await boundedJson(response);
      const payload = await decodeInspection(envelope, requested);
      if (ticket !== generation) return;
      current = payload; exportEnvelope = envelope;
      $("possible").textContent = stepLabel(payload.forecast.risk_window.earliest_possible_alert_step);
      $("supported").textContent = stepLabel(payload.forecast.risk_window.earliest_supported_alert_step);
      $("median-crossing").textContent = stepLabel(payload.forecast.risk_window.first_median_crossing_step);
      $("source").textContent = payload.source.revision;
      $("digest").textContent = envelope.sha256;
      $("evidence-json").textContent = envelope.canonical_json;
      $("step").max = String(requested.horizon); $("step").value = "1";
      const tbody = $("forecast-table"); tbody.replaceChildren();
      payload.forecast.points.forEach((point, i) => {
        const row = document.createElement("tr"), risk = payload.forecast.risk_window.steps[i];
        [String(point.step),...KEYS.map((key) => fmt(point.quantiles[key])),`${percent(risk.model_implied_breach_lower)}–${percent(risk.model_implied_breach_upper)}`]
          .forEach((text, index) => { const cell = document.createElement(index === 0 ? "th" : "td"); if (index === 0) cell.scope = "row"; cell.textContent = text; row.append(cell); });
        tbody.append(row);
      });
      $("status").textContent = "Python forecast returned. Response content digest and risk semantics match; calibration is not established.";
      $("forecast-output").hidden = false; inspectStep();
    } catch (error) {
      if (ticket !== generation) return;
      current = null; exportEnvelope = null; $("forecast-output").hidden = true;
      $("result-panel").classList.add("error");
      $("status").textContent = error.name === "AbortError" ? "Request stopped or timed out. No stale result is shown." : error.message;
    } finally { clearTimeout(timeout); if (ticket === generation) busy(false); }
  });
  $("export").addEventListener("click", () => {
    if (!exportEnvelope) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(exportEnvelope,null,2)],{type:"application/json"}));
    const link = document.createElement("a"); link.href = url; link.download = "lyte-forecast-evidence.json";
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}
if (typeof document !== "undefined") boot();
