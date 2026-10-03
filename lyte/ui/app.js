(() => {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const API = Object.freeze({
    ask: "/api/lyte/v2/ask",
    build: "/api/build-info",
    sources: "/api/lyte/v2/sources",
    services: "/api/lyte/v2/services",
    journeys: "/api/lyte/v2/journeys",
    outcomes: "/api/lyte/v2/outcomes",
    agents: "/api/lyte/v2/agents",
    incidents: "/api/lyte/v2/incidents",
    decisions: "/api/lyte/v2/decisions",
    playback: "/api/lyte/v2/playback",
    receipts: "/api/lyte/v2/receipts",
  });
  const TRUTH_LABELS = new Set(["MEASURED", "REPORTED", "DECLARED", "SIMULATED", "SAMPLE", "MODELED", "ROADMAP", "UNKNOWN", "UNAVAILABLE", "BLOCKED"]);
  const NUMERIC_TRUTH_LABELS = new Set(["MEASURED", "REPORTED", "MODELED", "SAMPLE"]);
  const PAGE_LIMIT = 100;
  const MAX_PAGES = 5;
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const SCENES = new Set(["executive", "services", "journeys", "agents", "incidents"]);
  const root = document.documentElement;
  const body = document.body;
  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  const mobileQuery = window.matchMedia("(max-width: 900px)");

  const one = (selector, parent = document) => parent.querySelector(selector);
  const all = (selector, parent = document) => Array.from(parent.querySelectorAll(selector));
  const text = (selector, value, parent = document) => {
    const element = one(selector, parent);
    if (element) element.textContent = value;
  };
  const announce = (message) => text("#app-live", message);

  const HASH = /^[0-9a-f]{64}$/i;
  const unavailableDetail = (label, summary) => ({
    label,
    truth: "UNAVAILABLE",
    source: "Canonical scoped API unavailable",
    formula: "UNAVAILABLE",
    receipt: "UNAVAILABLE",
    observed: "UNAVAILABLE",
    summary,
    impact: "No offline or hand-authored record was substituted.",
  });
  let graphNodes = [];
  let graphEdges = [];
  const receiptDetails = Object.create(null);
  const decisionDetails = {
    checkout: unavailableDetail("Decision evidence unavailable", "The decisions API has not been verified."),
    agent: unavailableDetail("Agent evidence unavailable", "The agents API has not been verified."),
    support: unavailableDetail("Support evidence unavailable", "No supported decision record is available."),
  };
  const journeySteps = Object.create(null);
  let playbackEvents = [];
  const recordsByHash = new Map();
  const outcomeDetails = Object.create(null);
  const workspace = {
    mode: "UNAVAILABLE",
    build: null,
    sources: null,
    services: [],
    journeys: [],
    outcomes: [],
    agents: [],
    incidents: [],
    decisions: [],
    playback: [],
    receipts: [],
    failures: new Set(),
    truncated: new Set(),
  };
  // Credentials live only in this closure until the workspace is cleared or the page closes.
  let requestScope = null;
  let workspaceLoadGeneration = 0;

  let activeGraphFilter = "all";
  let selectedNodeId = null;
  let drawerReturnFocus = null;
  let playbackTimer = null;
  let askController = null;

  function svgElement(name, attributes = {}) {
    const element = document.createElementNS(SVG_NS, name);
    Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
    return element;
  }

  function nodeById(id) {
    return graphNodes.find((node) => node.id === id);
  }

  function isNodeVisible(node) {
    return activeGraphFilter === "all" || node.type === activeGraphFilter || node.type === "outcome";
  }

  function edgePath(source, target) {
    const distance = Math.max(45, Math.abs(target.x - source.x) * 0.48);
    return `M ${source.x} ${source.y} C ${source.x + distance} ${source.y}, ${target.x - distance} ${target.y}, ${target.x} ${target.y}`;
  }

  function previewNode(node) {
    const preview = one("#lattice-preview");
    if (preview) preview.textContent = `${node.label} · ${node.state} · ${node.truth} · ${node.source}`;
  }

  function resetNodePreview() {
    const selected = selectedNodeId ? nodeById(selectedNodeId) : null;
    if (selected) {
      previewNode(selected);
    } else {
      text("#lattice-preview", "Focus or point to a node for compact evidence.");
    }
  }

  function renderGraph() {
    const edgeLayer = one("#lattice-edges");
    const nodeLayer = one("#lattice-nodes");
    if (!edgeLayer || !nodeLayer) return;

    const edges = graphEdges.flatMap(([sourceId, targetId, truth]) => {
      const source = nodeById(sourceId);
      const target = nodeById(targetId);
      if (!source || !target) return [];
      const path = svgElement("path", {
        class: `lattice-edge${truth === "MODELED" ? " edge-modeled" : ""}`,
        d: edgePath(source, target),
        "data-source": sourceId,
        "data-target": targetId,
        "marker-end": "url(#edge-arrow)",
      });
      if (!isNodeVisible(source) || !isNodeVisible(target)) path.classList.add("is-hidden");
      return [path];
    });
    edgeLayer.replaceChildren(...edges);

    const nodes = graphNodes.map((node) => {
      const group = svgElement("g", {
        class: `lattice-node node-${node.type}${node.attention ? " node-attention" : ""}`,
        transform: `translate(${node.x} ${node.y})`,
        tabindex: isNodeVisible(node) ? "0" : "-1",
        role: "button",
        "aria-label": `${node.label}, ${node.type}, ${node.state}, truth ${node.truth}. Open evidence.`,
        "data-node-id": node.id,
      });
      if (!isNodeVisible(node)) group.classList.add("is-hidden");
      if (selectedNodeId === node.id) group.classList.add("is-selected");

      const title = svgElement("title");
      title.textContent = `${node.label}: ${node.summary} Source: ${node.source}. Truth: ${node.truth}.`;
      const halo = svgElement("circle", { class: "node-halo", r: 36 });
      const core = svgElement("circle", { class: "node-core", r: 22 });
      const type = svgElement("text", { class: "node-type-label", y: -46 });
      type.textContent = node.type.toUpperCase();
      const label = svgElement("text", { class: "node-label", y: 51 });
      label.textContent = node.label;
      group.append(title, halo, core, type, label);
      group.addEventListener("mouseenter", () => previewNode(node));
      group.addEventListener("mouseleave", resetNodePreview);
      group.addEventListener("focus", () => previewNode(node));
      group.addEventListener("click", () => openDrawer(node, group));
      group.addEventListener("keydown", graphKeydown);
      return group;
    });
    nodeLayer.replaceChildren(...nodes);
  }

  function graphKeydown(event) {
    const current = nodeById(event.currentTarget.dataset.nodeId);
    const visible = graphNodes.filter(isNodeVisible);
    if (!current || !visible.length) return;

    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openDrawer(current, event.currentTarget);
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      const target = event.key === "Home" ? visible[0] : visible[visible.length - 1];
      one(`[data-node-id="${target.id}"]`)?.focus();
      return;
    }

    const directions = {
      ArrowLeft: [-1, 0],
      ArrowRight: [1, 0],
      ArrowUp: [0, -1],
      ArrowDown: [0, 1],
    };
    const direction = directions[event.key];
    if (!direction) return;
    event.preventDefault();
    const candidates = visible
      .filter((candidate) => candidate.id !== current.id)
      .map((candidate) => {
        const dx = candidate.x - current.x;
        const dy = candidate.y - current.y;
        const projection = dx * direction[0] + dy * direction[1];
        const lateral = Math.abs(dx * direction[1] - dy * direction[0]);
        return { candidate, projection, score: projection + lateral * 1.8 };
      })
      .filter(({ projection }) => projection > 0)
      .sort((a, b) => a.score - b.score);
    if (candidates[0]) one(`[data-node-id="${candidates[0].candidate.id}"]`)?.focus();
  }

  function setGraphFilter(filter, trigger) {
    if (!["all", "service", "journey", "agent"].includes(filter)) return;
    activeGraphFilter = filter;
    all("[data-lattice-filter]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.latticeFilter === filter));
    });
    renderGraph();
    const firstVisible = one(".lattice-node:not(.is-hidden)");
    if (trigger && firstVisible) firstVisible.focus();
    announce(`Signal Lattice filtered to ${filter === "all" ? "all layers" : `${filter} and outcome layers`}.`);
  }

  function normalizeDetail(detail) {
    return {
      label: String(detail?.label || "Evidence detail"),
      truth: TRUTH_LABELS.has(detail?.truth) ? detail.truth : "UNAVAILABLE",
      source: String(detail?.source || "UNAVAILABLE"),
      formula: String(detail?.formula || "UNAVAILABLE"),
      receipt: String(detail?.receipt || "UNAVAILABLE"),
      observed: String(detail?.observed || "UNAVAILABLE"),
      summary: String(detail?.summary || "No evidence summary is available."),
      impact: String(detail?.impact || "No supported impact statement is available."),
      type: String(detail?.type || "Signal evidence"),
    };
  }

  function applyTruthBadge(element, label) {
    if (!element) return;
    const truth = TRUTH_LABELS.has(label) ? label : "UNAVAILABLE";
    element.classList.remove(...[...TRUTH_LABELS].map((value) => `truth-${value.toLowerCase()}`));
    element.classList.add(`truth-${truth.toLowerCase()}`);
    element.textContent = truth;
  }

  function openDrawer(rawDetail, trigger) {
    const detail = normalizeDetail(rawDetail);
    const drawer = one("#signal-drawer");
    const scrim = one("#drawer-scrim");
    if (!drawer || !scrim) return;
    drawerReturnFocus = trigger || document.activeElement;
    selectedNodeId = rawDetail?.id || selectedNodeId;
    all(".lattice-node").forEach((node) => node.classList.toggle("is-selected", node.dataset.nodeId === selectedNodeId));
    text("#drawer-kicker", detail.type === "Signal evidence" ? detail.type : `${detail.type} evidence`);
    text("#drawer-title", detail.label);
    text("#drawer-summary", detail.summary);
    text("#drawer-source", detail.source);
    text("#drawer-formula", detail.formula);
    text("#drawer-receipt", detail.receipt);
    text("#drawer-observed", detail.observed);
    text("#drawer-impact", detail.impact);
    applyTruthBadge(one("#drawer-truth"), detail.truth);
    drawer.hidden = false;
    scrim.hidden = false;
    body.classList.add("drawer-open");
    window.requestAnimationFrame(() => {
      drawer.classList.add("is-open");
      scrim.classList.add("is-open");
      one("#drawer-close")?.focus();
    });
    announce(`${detail.label} evidence opened. Truth label ${detail.truth}.`);
  }

  function closeDrawer({ restoreFocus = true } = {}) {
    const drawer = one("#signal-drawer");
    const scrim = one("#drawer-scrim");
    if (!drawer || drawer.hidden) return;
    drawer.classList.remove("is-open");
    scrim?.classList.remove("is-open");
    body.classList.remove("drawer-open");
    window.setTimeout(() => {
      drawer.hidden = true;
      if (scrim) scrim.hidden = true;
      if (restoreFocus && drawerReturnFocus?.isConnected && typeof drawerReturnFocus.focus === "function") drawerReturnFocus.focus();
      drawerReturnFocus = null;
    }, motionQuery.matches ? 0 : 220);
    announce("Evidence detail closed.");
  }

  async function openReceipt(id, trigger) {
    const hash = validHash(id);
    const generation = workspaceLoadGeneration;
    let detail = hash ? receiptDetails[hash] : null;
    if (hash && !detail) {
      try {
        const receipt = await fetchJSON(`${API.receipts}/${hash}`, { headers: scopeHeaders() });
        if (generation !== workspaceLoadGeneration) return;
        if (receipt?.schema === "szl.lyte.receipt-view/v1" && validHash(receipt.record_hash) === hash) {
          detail = detailFromReceipt(receipt);
          receiptDetails[hash] = detail;
        }
      } catch (_error) {
        if (generation !== workspaceLoadGeneration) return;
      }
    }
    detail ||= {
      label: "Receipt unavailable",
      truth: "UNAVAILABLE",
      summary: "The persisted receipts API did not return this exact 64-character hash.",
      source: "UNAVAILABLE",
      formula: "UNAVAILABLE",
      receipt: validHash(id) || "UNAVAILABLE",
      observed: "UNAVAILABLE",
      impact: "Lyte does not invent or expand missing evidence from a pseudo-identifier.",
    };
    openDrawer(detail, trigger);
  }

  function setScene(scene, { updateHistory = true, focusHeading = true } = {}) {
    const nextScene = SCENES.has(scene) ? scene : "executive";
    all("[data-scene]").forEach((section) => {
      const active = section.dataset.scene === nextScene;
      section.hidden = !active;
      section.classList.toggle("is-active", active);
    });
    all(".primary-nav [data-scene-target]").forEach((button) => {
      if (button.dataset.sceneTarget === nextScene) {
        button.setAttribute("aria-current", "page");
      } else {
        button.removeAttribute("aria-current");
      }
    });
    if (updateHistory && window.location.hash !== `#${nextScene}`) {
      window.history.pushState({ scene: nextScene }, "", `#${nextScene}`);
    }
    closeMobileNav({ restoreFocus: false });
    if (focusHeading) {
      const heading = one(`#scene-${nextScene} h1`);
      if (heading) {
        heading.tabIndex = -1;
        heading.focus({ preventScroll: true });
      }
    }
    announce(`${one(`#scene-${nextScene} h1`)?.textContent || nextScene} scene opened.`);
  }

  function syncMobileNav() {
    const sidebar = one("#primary-sidebar");
    const menu = one("#mobile-menu");
    if (!sidebar || !menu) return;
    if (!mobileQuery.matches) {
      sidebar.inert = false;
      sidebar.classList.remove("is-open");
      menu.setAttribute("aria-expanded", "false");
    } else if (!sidebar.classList.contains("is-open")) {
      sidebar.inert = true;
    }
  }

  function toggleMobileNav() {
    const sidebar = one("#primary-sidebar");
    const menu = one("#mobile-menu");
    if (!sidebar || !menu) return;
    const opening = !sidebar.classList.contains("is-open");
    sidebar.classList.toggle("is-open", opening);
    sidebar.inert = !opening;
    menu.setAttribute("aria-expanded", String(opening));
    if (opening) one(".primary-nav button", sidebar)?.focus();
    announce(`Navigation ${opening ? "opened" : "closed"}.`);
  }

  function closeMobileNav({ restoreFocus = true } = {}) {
    if (!mobileQuery.matches) return;
    const sidebar = one("#primary-sidebar");
    const menu = one("#mobile-menu");
    if (!sidebar || !sidebar.classList.contains("is-open")) return;
    sidebar.classList.remove("is-open");
    sidebar.inert = true;
    menu?.setAttribute("aria-expanded", "false");
    if (restoreFocus) menu?.focus();
  }

  function updateJourney(stepId, trigger) {
    const step = journeySteps[stepId];
    if (!step) return;
    all("[data-journey-step]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.journeyStep === stepId));
    });
    text("#journey-step-name", step.name);
    text("#journey-step-summary", step.summary);
    text("#journey-service", step.service);
    text("#journey-signal", step.signal);
    text("#journey-outcome", step.outcome);
    applyTruthBadge(one("#journey-truth"), step.truth);
    if (trigger) announce(`${step.name} journey step selected. ${step.summary}`);
  }

  function filterServices(filter) {
    if (!["all", "attention", "changed"].includes(filter)) return;
    all("[data-service-filter]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.serviceFilter === filter));
    });
    let count = 0;
    all("[data-service-state]").forEach((card) => {
      const visible = filter === "all" || card.dataset.serviceState.split(/\s+/).includes(filter);
      card.hidden = !visible;
      if (visible) count += 1;
    });
    announce(`${count} service cards shown for ${filter} filter.`);
  }

  function updatePlayback(rawIndex, { focusEvent = false } = {}) {
    if (!playbackEvents.length) {
      text("#playback-position", "UNAVAILABLE");
      return;
    }
    const index = Math.max(0, Math.min(playbackEvents.length - 1, Number(rawIndex) || 0));
    const event = playbackEvents[index];
    const scrubber = one("#incident-scrubber");
    if (scrubber) scrubber.value = String(index);
    all("[data-playback-index]").forEach((button) => {
      const active = Number(button.dataset.playbackIndex) === index;
      if (active) button.setAttribute("aria-current", "step");
      else button.removeAttribute("aria-current");
      if (active && focusEvent) button.focus();
    });
    text("#playback-position", event.position);
    text("#playback-kind", event.kind);
    text("#playback-event-title", event.title);
    text("#playback-event-copy", event.copy);
    text("#playback-source", event.source);
    text("#playback-receipt", event.receipt);
    announce(`${event.position}. ${event.title}.`);
  }

  function setPlaybackState(playing) {
    const toggle = one("#playback-toggle");
    if (!toggle) return;
    if (!playbackEvents.length) playing = false;
    if (playbackTimer) {
      window.clearInterval(playbackTimer);
      playbackTimer = null;
    }
    toggle.setAttribute("aria-pressed", String(playing));
    const icon = one("span:first-child", toggle);
    const label = one("span:last-child", toggle);
    if (icon) icon.textContent = playing ? "Ⅱ" : "▶";
    if (label) label.textContent = playing ? "Pause" : "Play";
    if (!playing) return;

    if (Number(one("#incident-scrubber")?.value || 0) >= playbackEvents.length - 1) updatePlayback(0);
    playbackTimer = window.setInterval(() => {
      if (document.hidden) {
        setPlaybackState(false);
        return;
      }
      const current = Number(one("#incident-scrubber")?.value || 0);
      if (current >= playbackEvents.length - 1) {
        setPlaybackState(false);
        announce("Incident playback complete.");
      } else {
        updatePlayback(current + 1);
      }
    }, motionQuery.matches ? 2200 : 1600);
  }

  function openDialog(id) {
    const dialog = document.getElementById(id);
    if (!(dialog instanceof HTMLDialogElement)) return;
    if (!dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
    if (id === "ask-dialog") window.setTimeout(() => one("#ask-input")?.focus(), 0);
  }

  function closeDialog(id) {
    const dialog = document.getElementById(id);
    if (!(dialog instanceof HTMLDialogElement) || !dialog.open) return;
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  }

  async function fetchJSON(url, options = {}, timeoutMs = 8000) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(url, {
        credentials: "same-origin",
        ...options,
        headers: { Accept: "application/json", ...(options.headers || {}) },
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.toLowerCase().includes("application/json")) throw new Error("non-JSON response");
      return await response.json();
    } finally {
      window.clearTimeout(timer);
    }
  }

  function scopeHeaders(scope = requestScope) {
    return scope ? {
      Authorization: `Bearer ${scope.token}`,
      "X-Lyte-Tenant-ID": scope.tenant,
      "X-Lyte-Workspace-ID": scope.workspace,
    } : {};
  }

  function validateScope(token, tenant, workspaceId) {
    if (typeof token !== "string" || !/^[\x21-\x7e]{1,8192}$/.test(token)) {
      throw new Error("Enter a valid bearer token without spaces or control characters.");
    }
    if (!UUID.test(tenant) || !UUID.test(workspaceId)) {
      throw new Error("Tenant and workspace must both be UUIDs.");
    }
    return { token, tenant: tenant.toLowerCase(), workspace: workspaceId.toLowerCase() };
  }

  function changeWorkspaceScope(scope) {
    requestScope = scope;
    workspaceLoadGeneration += 1;
    if (askController) askController.abort();
    setPlaybackState(false);
    closeDrawer({ restoreFocus: false });
    clearDrawerEvidence();
    workspace.mode = "UNAVAILABLE";
    ["services", "journeys", "outcomes", "agents", "incidents", "decisions", "playback", "receipts"].forEach((name) => { workspace[name] = []; });
    workspace.failures.clear();
    workspace.truncated.clear();
    selectedNodeId = null;
    hydrateWorkspace();
    renderAskUnavailable("No answer requested for the current workspace.");
    text("#scope-session-status", scope
      ? "Scoped access is being checked. The token is held only in this page's memory."
      : "Scoped access cleared. Public SAMPLE data loads only when the server explicitly enables it.");
    loadWorkspaceData();
  }

  function clearDrawerEvidence() {
    ["#drawer-title", "#drawer-summary", "#drawer-source", "#drawer-formula", "#drawer-receipt", "#drawer-observed", "#drawer-impact"].forEach((selector) => text(selector, "UNAVAILABLE"));
    applyTruthBadge(one("#drawer-truth"), "UNAVAILABLE");
  }

  function validTruth(value) {
    const label = String(value || "").toUpperCase();
    return TRUTH_LABELS.has(label) ? label : "UNAVAILABLE";
  }

  function validHash(value) {
    return typeof value === "string" && HASH.test(value) ? value.toLowerCase() : null;
  }

  function safeItems(value) {
    return Array.isArray(value) ? value : [];
  }

  function indicator(record, name) {
    const value = record?.body?.indicators?.[name];
    return value && typeof value === "object" ? value : null;
  }

  function indicatorNumber(record, name) {
    return readingNumber(indicator(record, name));
  }

  function readingNumber(reading) {
    const value = reading?.value;
    return NUMERIC_TRUTH_LABELS.has(reading?.truth_label) && typeof value === "number" && Number.isFinite(value) ? value : null;
  }

  function formatInteger(value) {
    return Number.isFinite(value) ? Math.round(value).toLocaleString("en-US") : "UNAVAILABLE";
  }

  function formatPercent(value, digits = 1) {
    return Number.isFinite(value) ? `${(value * 100).toFixed(digits)}%` : "UNAVAILABLE";
  }

  function formatMoney(value) {
    return Number.isFinite(value)
      ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(value)
      : "UNAVAILABLE";
  }

  function formatTime(value, { includeDate = false } = {}) {
    if (typeof value !== "string") return "UNAVAILABLE";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.valueOf())) return "UNAVAILABLE";
    const iso = parsed.toISOString();
    return includeDate ? `${iso.slice(0, 10)} ${iso.slice(11, 19)} UTC` : iso.slice(11, 19);
  }

  function shortHash(value) {
    const hash = validHash(value);
    return hash ? `${hash.slice(0, 10)}…${hash.slice(-8)}` : "UNAVAILABLE";
  }

  function evidenceSource(record) {
    const refs = safeItems(record?.evidence_refs).filter((value) => typeof value === "string" && value.trim());
    return refs.length ? refs.join(" · ") : "UNAVAILABLE";
  }

  function firstFormula(record) {
    const indicators = record?.body?.indicators;
    if (!indicators || typeof indicators !== "object") return "UNAVAILABLE";
    for (const value of Object.values(indicators)) {
      const formula = value?.metadata?.formula;
      if (typeof formula === "string" && formula.trim()) return formula.trim();
    }
    return "UNAVAILABLE";
  }

  function recordSummary(record) {
    const body = record?.body || {};
    switch (record?.entity_kind) {
      case "Service": {
        const p95 = indicatorNumber(record, "p95_latency_ms");
        const availability = indicatorNumber(record, "availability");
        const errorRate = indicatorNumber(record, "error_rate");
        const observations = [
          Number.isFinite(p95) ? `p95 ${formatInteger(p95)} ms` : null,
          Number.isFinite(availability) ? `availability ${formatPercent(availability, 2)}` : null,
          Number.isFinite(errorRate) ? `error rate ${formatPercent(errorRate, 1)}` : null,
        ].filter(Boolean);
        return `${record.name} is ${String(body.state || "UNAVAILABLE")}.${observations.length ? ` ${observations.join("; ")}.` : ""}`;
      }
      case "CustomerJourney": {
        const observed = indicatorNumber(record, "observed_completion_rate");
        const baseline = indicatorNumber(record, "baseline_completion_rate");
        return `${record.name} is ${String(body.state || "UNAVAILABLE")}. Completion is ${formatPercent(observed)} against a ${formatPercent(baseline)} baseline.`;
      }
      case "BusinessOutcome": {
        const risk = indicatorNumber(record, "revenue_at_risk_usd");
        return `${record.name} is ${String(body.state || "UNAVAILABLE")}; ${formatMoney(risk)} is returned by the scoped outcome indicator.`;
      }
      case "AgentTraceSummary": {
        const success = indicatorNumber(record, "success_rate");
        const cost = indicatorNumber(record, "cost_per_trace_usd");
        return `${record.name} is ${String(body.state || "UNAVAILABLE")}; success ${formatPercent(success)} and cost ${Number.isFinite(cost) ? `$${cost.toFixed(2)} per trace` : "UNAVAILABLE"}.`;
      }
      case "Incident":
        return `${record.name} is ${String(body.state || "UNAVAILABLE")} at severity ${String(body.severity || "UNAVAILABLE")}.`;
      case "Decision":
        return `${record.name} returned ${String(body.decision || "UNAVAILABLE")} under ${String(body.authority_mode || "UNAVAILABLE")}; execution authority is ${body.can_execute === true ? "enabled" : "not granted"}.`;
      case "ReplaySnapshot":
        return safeItems(body.changes).length ? safeItems(body.changes).join(" ") : `${record.name} has no described change.`;
      default:
        return `${String(record?.name || "Operational record")} is available from the canonical scoped API.`;
    }
  }

  function recordImpact(record) {
    const body = record?.body || {};
    switch (record?.entity_kind) {
      case "BusinessOutcome":
        return "The estimate exposes its inputs and cannot authorize a financial or production action.";
      case "Decision":
        return body.can_execute === true
          ? "This record reports execution capability; named authority and policy still require independent review."
          : "The persisted decision preserves human authority and does not execute a consequential action.";
      case "Incident":
        return "Candidate factors remain correlation or sequence evidence unless the record explicitly proves causality.";
      case "ReplaySnapshot":
        return "Playback preserves chronology; chronology alone is not a causal or production-outcome claim.";
      case "AgentTraceSummary":
        return "Cost, latency, failure flags, and lineage are visible without granting the agent silent authority.";
      default:
        return "This persisted scoped record supports inspection; it is not an automatic action basis.";
    }
  }

  function detailFromRecord(record) {
    const hash = validHash(record?.record_hash);
    return {
      id: hash ? `record-${hash.slice(0, 16)}` : undefined,
      label: String(record?.name || "Operational evidence"),
      truth: validTruth(record?.truth_label),
      source: evidenceSource(record),
      formula: firstFormula(record),
      receipt: hash || "UNAVAILABLE",
      observed: formatTime(record?.observed_at || record?.created_at, { includeDate: true }),
      summary: recordSummary(record),
      impact: recordImpact(record),
      type: String(record?.entity_kind || "Operational record"),
    };
  }

  function detailFromReceipt(receipt) {
    const hash = validHash(receipt?.record_hash);
    const payloadHash = validHash(receipt?.payload_sha256);
    return {
      id: hash ? `receipt-${hash.slice(0, 16)}` : undefined,
      label: `${String(receipt?.kind || "Persisted receipt")} · ${String(receipt?.subject_id || "UNAVAILABLE")}`,
      truth: validTruth(receipt?.truth_label),
      source: evidenceSource(receipt),
      formula: payloadHash ? `payload sha256 ${payloadHash}` : "UNAVAILABLE",
      receipt: hash || "UNAVAILABLE",
      observed: formatTime(receipt?.created_at, { includeDate: true }),
      summary: `Append-only sequence ${Number.isInteger(receipt?.sequence) ? receipt.sequence : "UNAVAILABLE"} for ${String(receipt?.subject_type || "subject")} ${String(receipt?.subject_id || "UNAVAILABLE")}.`,
      impact: "This hash is returned by the persisted receipts API; it is not a hand-authored pseudo-identifier.",
      type: "Receipt",
    };
  }

  function registerEvidenceDetails() {
    recordsByHash.clear();
    Object.keys(receiptDetails).forEach((key) => delete receiptDetails[key]);
    const operational = [
      ...workspace.services,
      ...workspace.journeys,
      ...workspace.outcomes,
      ...workspace.agents,
      ...workspace.incidents,
      ...workspace.decisions,
      ...workspace.playback,
    ];
    operational.forEach((record) => {
      const hash = validHash(record?.record_hash);
      if (!hash) return;
      recordsByHash.set(hash, record);
      receiptDetails[hash] = detailFromRecord(record);
    });
    workspace.receipts.forEach((receipt) => {
      const hash = validHash(receipt?.record_hash);
      if (!hash) return;
      recordsByHash.set(hash, receipt);
      receiptDetails[hash] = detailFromReceipt(receipt);
    });
  }

  function nodePosition(index, count) {
    if (count <= 1) return 235;
    return 105 + (index * 260) / (count - 1);
  }

  function buildGraph() {
    const groups = [
      ["service", 120, workspace.services.slice(0, 4)],
      ["journey", 345, workspace.journeys.slice(0, 4)],
      ["agent", 565, workspace.agents.slice(0, 4)],
      ["outcome", 770, workspace.outcomes.slice(0, 4)],
    ];
    const ids = new Map();
    graphNodes = groups.flatMap(([type, x, records]) => records.map((record, index) => {
      const hash = validHash(record.record_hash);
      const id = `${type}-${hash?.slice(0, 16) || index}`;
      ids.set(`${type}:${record.entity_id}`, id);
      const detail = detailFromRecord(record);
      const state = String(record.body?.state || "UNAVAILABLE");
      return {
        ...detail,
        id,
        label: String(record.name || record.entity_id),
        type,
        x,
        y: nodePosition(index, records.length),
        state,
        attention: /DEGRADED|AT_RISK|WATCH|FAILED|BREACHED|INVESTIGATING/i.test(state),
      };
    }));
    const edges = [];
    const addEdge = (source, target, truth) => {
      if (!source || !target || source === target) return;
      const key = `${source}>${target}`;
      if (!edges.some((edge) => `${edge[0]}>${edge[1]}` === key)) edges.push([source, target, validTruth(truth)]);
    };
    workspace.services.forEach((record) => {
      const target = ids.get(`service:${record.entity_id}`);
      safeItems(record.body?.dependencies).forEach((dependency) => addEdge(ids.get(`service:${dependency}`), target, record.truth_label));
    });
    workspace.journeys.forEach((record) => {
      const target = ids.get(`journey:${record.entity_id}`);
      safeItems(record.body?.service_ids).forEach((service) => addEdge(ids.get(`service:${service}`), target, record.truth_label));
    });
    workspace.agents.forEach((record) => {
      const target = ids.get(`agent:${record.entity_id}`);
      safeItems(record.body?.service_ids).forEach((service) => addEdge(ids.get(`service:${service}`), target, record.truth_label));
    });
    workspace.outcomes.forEach((record) => {
      const target = ids.get(`outcome:${record.entity_id}`);
      safeItems(record.body?.journey_ids).forEach((journey) => addEdge(ids.get(`journey:${journey}`), target, record.truth_label));
      safeItems(record.body?.service_ids).forEach((service) => addEdge(ids.get(`service:${service}`), target, record.truth_label));
    });
    graphEdges = edges;
  }

  function createElement(tag, value = "", className = "") {
    const element = document.createElement(tag);
    if (value !== "") element.textContent = String(value);
    if (className) element.className = className;
    return element;
  }

  function createBadge(label) {
    const badge = createElement("span", "", "truth-badge");
    applyTruthBadge(badge, validTruth(label));
    return badge;
  }

  function unavailableListItem(message) {
    const item = document.createElement("li");
    item.append(createElement("span", message));
    return item;
  }

  function renderLatticeTable() {
    const table = one("#lattice-table-body");
    if (!table) return;
    if (!graphNodes.length) {
      const row = document.createElement("tr");
      const heading = createElement("th", "Canonical graph");
      heading.scope = "row";
      const value = createElement("td", "UNAVAILABLE; no offline graph was substituted.");
      value.colSpan = 4;
      row.append(heading, value);
      table.replaceChildren(row);
      return;
    }
    const rows = graphNodes.map((node) => {
      const row = document.createElement("tr");
      const heading = createElement("th", node.label);
      heading.scope = "row";
      row.append(
        heading,
        createElement("td", node.type),
        createElement("td", node.state),
        createElement("td", node.truth),
        createElement("td", shortHash(node.receipt)),
      );
      return row;
    });
    table.replaceChildren(...rows);
  }

  function setOutcomeTile(key, { title, value, copy, truth, detail }) {
    const tile = one(`[data-outcome="${key}"]`);
    if (!tile) return;
    text("header > span:first-child", title, tile);
    applyTruthBadge(one("header .truth-badge", tile), validTruth(truth));
    text(":scope > strong", value, tile);
    text(":scope > p", copy, tile);
    tile.setAttribute("aria-label", `${title}: ${value}. Truth ${validTruth(truth)}. Open evidence.`);
    outcomeDetails[key] = detail || unavailableDetail(`${title} unavailable`, `${copy} No offline value was substituted.`);
  }

  function renderExecutive() {
    const outcome = workspace.outcomes[0];
    const agent = workspace.agents[0];
    const service = workspace.services[0];
    const incident = workspace.incidents[0];
    const revenue = indicatorNumber(outcome, "revenue_at_risk_usd");
    const availability = indicatorNumber(service, "availability");
    const agentSuccess = indicatorNumber(agent, "success_rate");

    setOutcomeTile("revenue", outcome ? {
      title: String(outcome.name || "Revenue outcome"),
      value: formatMoney(revenue),
      copy: `${String(outcome.body?.state || "UNAVAILABLE")} · scoped outcome indicator`,
      truth: indicator(outcome, "revenue_at_risk_usd")?.truth_label,
      detail: detailFromRecord(outcome),
    } : {
      title: "Revenue outcome",
      value: "UNAVAILABLE",
      copy: "Outcomes API unavailable or empty.",
      truth: "UNAVAILABLE",
    });
    setOutcomeTile("agents", agent ? {
      title: String(agent.name || "Agent operations"),
      value: formatPercent(agentSuccess),
      copy: `success rate · ${String(agent.body?.state || "UNAVAILABLE")}`,
      truth: indicator(agent, "success_rate")?.truth_label,
      detail: detailFromRecord(agent),
    } : {
      title: "Agent operations",
      value: "UNAVAILABLE",
      copy: "Agents API unavailable or empty.",
      truth: "UNAVAILABLE",
    });
    setOutcomeTile("availability", service ? {
      title: String(service.name || "Service availability"),
      value: formatPercent(availability, 2),
      copy: `${String(service.body?.state || "UNAVAILABLE")} · scoped service record`,
      truth: indicator(service, "availability")?.truth_label,
      detail: detailFromRecord(service),
    } : {
      title: "Service availability",
      value: "UNAVAILABLE",
      copy: "Services API unavailable or empty.",
      truth: "UNAVAILABLE",
    });
    setOutcomeTile("incidents", incident ? {
      title: String(incident.name || "Incident posture"),
      value: String(incident.body?.severity || "UNAVAILABLE"),
      copy: `${String(incident.body?.state || "UNAVAILABLE")} · ${workspace.incidents.length} scoped incident record${workspace.incidents.length === 1 ? "" : "s"}`,
      truth: incident.truth_label,
      detail: detailFromRecord(incident),
    } : {
      title: "Incident posture",
      value: "UNAVAILABLE",
      copy: "Incidents API unavailable or empty.",
      truth: "UNAVAILABLE",
    });

    const attention = graphNodes.some((node) => node.attention);
    text("#executive-posture", graphNodes.length ? (attention ? "Attention required" : "No alert asserted") : "UNAVAILABLE");
    text(
      "#executive-posture-copy",
      graphNodes.length
        ? `${workspace.mode} records hydrated from canonical scoped APIs${workspace.failures.size ? `; ${workspace.failures.size} API surface${workspace.failures.size === 1 ? " is" : "s are"} UNAVAILABLE` : ""}.`
        : "Canonical operating records are unavailable; no offline graph was substituted.",
    );
  }

  function serviceErrorRate(record) {
    const direct = indicatorNumber(record, "error_rate");
    if (Number.isFinite(direct)) return direct;
    const errors = indicatorNumber(record, "errors");
    const requests = indicatorNumber(record, "requests");
    return Number.isFinite(errors) && Number.isFinite(requests) && requests > 0 ? errors / requests : null;
  }

  function renderServices() {
    const container = one("#service-cards");
    if (!container) return;
    if (!workspace.services.length) {
      const card = createElement("article", "", "service-card");
      const top = createElement("div", "", "service-card-top");
      top.append(createElement("span", "—", "service-symbol"), createBadge("UNAVAILABLE"));
      card.append(top, createElement("h3", "Service data unavailable"), createElement("p", "No offline service fixture was substituted."));
      container.replaceChildren(card);
      applyTruthBadge(one("#services-truth"), "UNAVAILABLE");
      text("#services-stat", "0 / 0");
      text("#services-stat-copy", "services API unavailable or empty");
      text("#services-freshness", "UNAVAILABLE");
      return;
    }
    let attentionCount = 0;
    let changedCount = 0;
    let withinObjective = 0;
    const cards = workspace.services.map((record) => {
      const state = String(record.body?.state || "UNAVAILABLE");
      const attention = /DEGRADED|FAILED|BREACHED|WATCH/i.test(state);
      const changed = safeItems(record.body?.deployment_refs).length > 0;
      if (attention) attentionCount += 1;
      if (changed) changedCount += 1;
      if (/HEALTHY|NOMINAL|READY/i.test(state)) withinObjective += 1;
      const card = createElement("article", "", `service-card ${attention ? "state-attention" : "state-healthy"}`);
      card.dataset.serviceState = [attention ? "attention" : "healthy", changed ? "changed" : ""].filter(Boolean).join(" ");
      const top = createElement("div", "", "service-card-top");
      const symbol = String(record.name || record.entity_id || "S").split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();
      top.append(createElement("span", symbol, "service-symbol"), createBadge(record.truth_label));
      const p95 = indicatorNumber(record, "p95_latency_ms");
      const errorRate = serviceErrorRate(record);
      const dependencies = safeItems(record.body?.dependencies);
      const definitions = document.createElement("dl");
      [["p95", Number.isFinite(p95) ? `${formatInteger(p95)} ms` : "UNAVAILABLE"], ["Error", formatPercent(errorRate)], ["Depends", dependencies.length ? dependencies.join(", ") : "None declared"]].forEach(([term, value]) => {
        const row = document.createElement("div");
        row.append(createElement("dt", term), createElement("dd", value));
        definitions.append(row);
      });
      const button = createElement("button", "Inspect persisted service record");
      button.type = "button";
      button.addEventListener("click", () => openDrawer(detailFromRecord(record), button));
      card.append(top, createElement("h3", record.name), createElement("p", `${state} · ${safeItems(record.body?.incident_refs).length} linked incident(s)`), definitions, button);
      return card;
    });
    container.replaceChildren(...cards);
    applyTruthBadge(one("#services-truth"), workspace.services.every((row) => validTruth(row.truth_label) === "SAMPLE") ? "SAMPLE" : "REPORTED");
    text("#services-stat", `${withinObjective} / ${workspace.services.length}`);
    text("#services-stat-copy", "services explicitly healthy or nominal");
    text("#service-count-all", workspace.services.length);
    text("#service-count-attention", attentionCount);
    text("#service-count-changed", changedCount);
    text("#services-freshness", `${workspace.mode} · ${formatTime(workspace.services[0]?.observed_at, { includeDate: true })}`);
  }

  function renderServiceEvents() {
    const rail = one("#service-event-rail");
    if (!rail) return;
    if (!workspace.playback.length) {
      rail.replaceChildren(unavailableListItem("UNAVAILABLE; playback API unavailable or empty."));
      return;
    }
    const items = workspace.playback.slice(0, 3).map((record) => {
      const item = document.createElement("li");
      const copy = createElement("span");
      copy.append(createElement("strong", String(record.body?.state || record.name)), createElement("small", safeItems(record.body?.changes)[0] || "No change description"));
      item.append(createElement("time", formatTime(record.body?.observed_at)), copy, createBadge(record.truth_label));
      return item;
    });
    rail.replaceChildren(...items);
  }

  function stepSignal(step) {
    const entries = Object.entries(step?.indicators || {});
    if (!entries.length) return "UNAVAILABLE";
    return entries.map(([name, reading]) => {
      const value = readingNumber(reading);
      if (!Number.isFinite(value)) return `${name.replaceAll("_", " ")} UNAVAILABLE`;
      if (name.endsWith("_rate")) return `${name.replaceAll("_", " ")} ${formatPercent(value)}`;
      if (name.endsWith("_ms")) return `${name.replaceAll("_", " ")} ${formatInteger(value)} ms`;
      return `${name.replaceAll("_", " ")} ${value}`;
    }).join(" · ");
  }

  function renderJourneys() {
    const list = one("#journey-steps");
    Object.keys(journeySteps).forEach((key) => delete journeySteps[key]);
    const journey = workspace.journeys[0];
    const outcomeIds = safeItems(journey?.body?.outcome_ids);
    const outcome = workspace.outcomes.find((record) => outcomeIds.includes(record.entity_id));
    if (!journey || !list) {
      list?.replaceChildren(unavailableListItem("UNAVAILABLE; journeys API unavailable or empty."));
      applyTruthBadge(one("#journeys-truth"), "UNAVAILABLE");
      text("#journeys-stat", "UNAVAILABLE");
      text("#journeys-stat-copy", "journey API unavailable or empty");
      text("#journey-score", "—");
      text("#journey-score-copy", "/ 100 · UNAVAILABLE");
      text("#journey-canvas-title", "Journey pressure map unavailable");
      ["#journey-step-name", "#journey-step-summary", "#journey-service", "#journey-signal", "#journey-outcome", "#formula-expression", "#rail-formula", "#formula-copy", "#rail-formula-copy"].forEach((selector) => text(selector, "UNAVAILABLE"));
      ["#journey-truth", "#formula-truth", "#rail-formula-truth"].forEach((selector) => applyTruthBadge(one(selector), "UNAVAILABLE"));
      one("#coverage-list")?.replaceChildren(unavailableListItem("No scoped journey evidence available."));
      text("#coverage-count", "0 refs");
      decisionDetails.checkout = unavailableDetail("Journey evidence unavailable", "No journey record is available in this workspace.");
      return;
    }
    const revenue = indicatorNumber(outcome, "revenue_at_risk_usd");
    const outcomeLabel = Number.isFinite(revenue) ? `${formatMoney(revenue)} revenue at risk` : "Linked outcome unavailable";
    const steps = safeItems(journey.body?.steps);
    const items = steps.map((step, index) => {
      const id = String(step.step_id || `step-${index}`);
      const completion = readingNumber(step?.indicators?.completion_rate);
      journeySteps[id] = {
        name: String(step.name || id),
        summary: `${String(step.name || id)} is ${String(step.state || "UNAVAILABLE")}. ${stepSignal(step)}.`,
        service: safeItems(step.service_ids).length ? safeItems(step.service_ids).join(" → ") : "UNAVAILABLE",
        signal: stepSignal(step),
        outcome: outcomeLabel,
        truth: validTruth(step.truth_label),
      };
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.journeyStep = id;
      button.setAttribute("aria-pressed", String(index === 0));
      const indexLabel = createElement("span", String(index + 1).padStart(2, "0"), "step-index");
      const label = createElement("span");
      label.append(createElement("strong", step.name || id), createElement("small", step.state || "UNAVAILABLE"));
      const stateClass = /DEGRADED|FAILED/i.test(String(step.state)) ? "state-bad" : /WATCH/i.test(String(step.state)) ? "state-watch" : "state-good";
      const state = createElement("span", Number.isFinite(completion) ? Math.round(completion * 100) : "—", `step-state ${stateClass}`);
      button.append(indexLabel, label, state);
      button.addEventListener("click", () => updateJourney(id, true));
      item.append(button);
      return item;
    });
    list.replaceChildren(...(items.length ? items : [unavailableListItem("No journey steps were returned by the canonical record.")]));
    const health = indicatorNumber(journey, "journey_health");
    text("#journey-score", Number.isFinite(health) ? Math.round(health * 100) : "—");
    text("#journey-score-copy", `/ 100 · ${validTruth(indicator(journey, "journey_health")?.truth_label)}`);
    text("#journey-canvas-title", String(journey.name || "Journey pressure map"));
    applyTruthBadge(one("#journeys-truth"), outcome ? outcome.truth_label : journey.truth_label);
    text("#journeys-stat", Number.isFinite(revenue) ? formatMoney(revenue) : "UNAVAILABLE");
    text("#journeys-stat-copy", outcome ? "linked scoped outcome" : "linked outcome unavailable");
    if (steps[0]) updateJourney(String(steps[0].step_id || "step-0"), false);

    const formulaReading = indicator(outcome, "revenue_at_risk_usd");
    const formula = typeof formulaReading?.metadata?.formula === "string" ? formulaReading.metadata.formula : "UNAVAILABLE";
    const formulaTruth = validTruth(formulaReading?.truth_label);
    text("#formula-expression", formula);
    text("#rail-formula", formula);
    applyTruthBadge(one("#formula-truth"), formulaTruth);
    applyTruthBadge(one("#rail-formula-truth"), formulaTruth);
    const inputCount = formulaReading?.metadata?.inputs && typeof formulaReading.metadata.inputs === "object"
      ? Object.keys(formulaReading.metadata.inputs).length
      : 0;
    const formulaCopy = formula === "UNAVAILABLE"
      ? "The canonical outcome record does not expose a formula."
      : `${inputCount} declared inputs · can_authorize ${String(formulaReading?.metadata?.can_authorize === true)}`;
    text("#formula-copy", formulaCopy);
    text("#rail-formula-copy", formulaCopy);

    const refs = [...new Set([...safeItems(journey.evidence_refs), ...safeItems(outcome?.evidence_refs)])];
    const coverage = one("#coverage-list");
    if (coverage) {
      const refItems = refs.map((ref) => {
        const item = document.createElement("li");
        item.append(createElement("span", ref), createBadge(String(ref).startsWith("model:") ? "MODELED" : journey.truth_label));
        return item;
      });
      coverage.replaceChildren(...(refItems.length ? refItems : [unavailableListItem("No evidence references returned.")]));
    }
    text("#coverage-count", `${refs.length} ref${refs.length === 1 ? "" : "s"}`);
    decisionDetails.checkout = outcome ? detailFromRecord(outcome) : detailFromRecord(journey);
  }

  function renderDecisions() {
    const list = one("#decision-list");
    const authority = one("#authority-evidence");
    if (!list) return;
    if (!workspace.decisions.length) {
      list.replaceChildren(unavailableListItem("Decision API unavailable or empty; no local decision was substituted."));
      applyTruthBadge(one("#decision-truth"), "UNAVAILABLE");
      text("#authority-state", "UNAVAILABLE");
      text("#authority-copy", "The decisions API has not been verified.");
      authority?.replaceChildren(unavailableListItem("Decision record UNAVAILABLE"));
      return;
    }
    const items = workspace.decisions.map((record, index) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      const body = record.body || {};
      const label = createElement("span");
      label.append(createElement("strong", record.name), createElement("small", `${String(body.decision || "UNAVAILABLE")} · ${String(body.authority_mode || "UNAVAILABLE")}`));
      button.append(createElement("span", String(index + 1).padStart(2, "0"), "decision-rank"), label, createElement("span", String(body.decision || "Review"), "risk-watch"));
      button.addEventListener("click", () => openDrawer(detailFromRecord(record), button));
      item.append(button);
      return item;
    });
    list.replaceChildren(...items);
    const first = workspace.decisions[0];
    const body = first.body || {};
    applyTruthBadge(one("#decision-truth"), first.truth_label);
    text("#authority-state", String(body.authority_mode || "UNAVAILABLE").replaceAll("_", " "));
    text("#authority-copy", body.can_execute === true
      ? "The decision record reports execution capability; independent authorization remains required."
      : "The persisted decision grants no execution authority; consequential action remains human-controlled.");
    if (authority) {
      const rows = [
        ["Decision", String(body.decision || "UNAVAILABLE")],
        ["Can authorize", String(body.can_authorize === true)],
        ["Can execute", String(body.can_execute === true)],
        ["Record hash", shortHash(first.record_hash)],
      ].map(([label, value], index) => {
        const item = document.createElement("li");
        item.className = index < 2 ? "is-complete" : index === 2 ? "is-current" : "";
        const copy = createElement("p");
        copy.append(createElement("strong", label), createElement("small", value));
        item.append(createElement("span", String(index + 1).padStart(2, "0")), copy);
        return item;
      });
      authority.replaceChildren(...rows);
    }
  }

  function renderAgents() {
    const list = one("#agent-list");
    const lineage = one("#agent-lineage");
    if (!list) return;
    if (!workspace.agents.length) {
      const row = createElement("div", "", "agent-row");
      row.append(createElement("span", "—", "agent-glyph"), createElement("span", "Agent data unavailable"), createElement("span", "UNAVAILABLE"), createBadge("UNAVAILABLE"));
      list.replaceChildren(row);
      applyTruthBadge(one("#agents-truth"), "UNAVAILABLE");
      applyTruthBadge(one("#agent-lineage-truth"), "UNAVAILABLE");
      text("#agents-stat", "UNAVAILABLE");
      text("#agents-stat-copy", "agents API unavailable or empty");
      text("#agents-freshness", "UNAVAILABLE");
      text("#tool-trace-title", "Agent lineage unavailable");
      decisionDetails.agent = unavailableDetail("Agent evidence unavailable", "No agent record is available in this workspace.");
      lineage?.replaceChildren(unavailableListItem("No offline agent run was substituted."));
      return;
    }
    const rows = workspace.agents.map((record) => {
      const success = indicatorNumber(record, "success_rate");
      const cost = indicatorNumber(record, "cost_per_trace_usd");
      const p95 = indicatorNumber(record, "p95_latency_ms");
      const row = createElement("div", "", `agent-row ${/WATCH|FAILED/i.test(String(record.body?.state)) ? "agent-warning" : ""}`);
      const identity = createElement("span");
      identity.append(createElement("strong", record.name), createElement("small", `${String(record.body?.state || "UNAVAILABLE")} · ${safeItems(record.body?.flags).join(", ") || "no flags"}`));
      const metrics = createElement("span");
      metrics.append(createElement("b", `${formatPercent(success)} success`), createElement("small", `${Number.isFinite(cost) ? `$${cost.toFixed(2)}/trace` : "UNAVAILABLE"} · p95 ${Number.isFinite(p95) ? `${formatInteger(p95)} ms` : "UNAVAILABLE"}`));
      const button = createElement("button", "Inspect persisted agent record");
      button.type = "button";
      button.addEventListener("click", () => openDrawer(detailFromRecord(record), button));
      row.append(createElement("span", String(record.name || "A")[0].toUpperCase(), "agent-glyph"), identity, metrics, createBadge(record.truth_label), button);
      return row;
    });
    list.replaceChildren(...rows);
    const first = workspace.agents[0];
    const success = indicatorNumber(first, "success_rate");
    applyTruthBadge(one("#agents-truth"), first.truth_label);
    applyTruthBadge(one("#agent-lineage-truth"), first.truth_label);
    text("#agents-stat", formatPercent(success));
    text("#agents-stat-copy", "success rate from scoped agent record");
    text("#agents-freshness", `${workspace.mode} · ${workspace.agents.length} record${workspace.agents.length === 1 ? "" : "s"}`);
    text("#tool-trace-title", `${first.name} / ${first.entity_id}`);
    const lineageValues = [
      ["State", String(first.body?.state || "UNAVAILABLE")],
      ["Services", safeItems(first.body?.service_ids).join(", ") || "UNAVAILABLE"],
      ["Flags", safeItems(first.body?.flags).join(", ") || "None declared"],
      ["Record hash", validHash(first.record_hash) || "UNAVAILABLE"],
    ];
    if (lineage) {
      const items = lineageValues.map(([label, value]) => {
        const item = document.createElement("li");
        const copy = createElement("span");
        copy.append(createElement("b", label), createElement("small", value));
        item.append(createElement("time", "API"), copy, createElement("em", "OBSERVE"));
        return item;
      });
      lineage.replaceChildren(...items);
    }
    decisionDetails.agent = detailFromRecord(first);
  }

  function playbackEvent(record) {
    const body = record?.body || {};
    const observed = body.observed_at || record?.observed_at;
    const state = String(body.state || "UNAVAILABLE");
    return {
      position: `${formatTime(observed)} · ${state.replaceAll("_", " ")}`,
      kind: `${state.replaceAll("_", " ")} · ${validTruth(record?.truth_label)}`,
      title: String(record?.name || "Playback record"),
      copy: safeItems(body.changes).length ? safeItems(body.changes).join(" ") : "No change description was returned.",
      source: evidenceSource(record),
      receipt: validHash(record?.record_hash) || "UNAVAILABLE",
      observed,
      truth: validTruth(record?.truth_label),
    };
  }

  function renderIncidents() {
    const incident = workspace.incidents[0];
    if (!incident) {
      applyTruthBadge(one("#incidents-truth"), "UNAVAILABLE");
      text("#incidents-stat", "UNAVAILABLE");
      text("#incidents-stat-copy", "incidents API unavailable or empty");
      text("#playback-title", "Incident playback unavailable");
    } else {
      applyTruthBadge(one("#incidents-truth"), incident.truth_label);
      text("#incidents-stat", String(incident.body?.severity || "UNAVAILABLE"));
      text("#incidents-stat-copy", `${String(incident.body?.state || "UNAVAILABLE")} · ${incident.entity_id}`);
      text("#playback-title", String(incident.name || incident.entity_id));
      outcomeDetails.incidents = detailFromRecord(incident);
    }
    renderPlayback();
  }

  function renderPlayback() {
    const list = one("#playback-events");
    const scrubber = one("#incident-scrubber");
    const toggle = one("#playback-toggle");
    playbackEvents = [...workspace.playback]
      .sort((left, right) => Number(left.body?.offset_minutes || 0) - Number(right.body?.offset_minutes || 0))
      .map(playbackEvent);
    if (!playbackEvents.length) {
      list?.replaceChildren(unavailableListItem("UNAVAILABLE; playback API unavailable or empty."));
      if (scrubber) {
        scrubber.max = "0";
        scrubber.value = "0";
        scrubber.disabled = true;
      }
      if (toggle) toggle.disabled = true;
      text("#playback-position", "UNAVAILABLE");
      text("#playback-kind", "UNAVAILABLE");
      text("#playback-event-title", "No playback record verified");
      text("#playback-event-copy", "No offline incident timeline was substituted.");
      text("#playback-source", "UNAVAILABLE");
      text("#playback-receipt", "UNAVAILABLE");
      text("#playback-start", "UNAVAILABLE");
      text("#playback-end", "UNAVAILABLE");
      one("#verification-details")?.replaceChildren();
      applyTruthBadge(one("#verification-truth"), "UNAVAILABLE");
      return;
    }
    if (scrubber) {
      scrubber.max = String(playbackEvents.length - 1);
      scrubber.value = "0";
      scrubber.disabled = false;
    }
    if (toggle) toggle.disabled = false;
    const items = playbackEvents.map((event, index) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.playbackIndex = String(index);
      if (index === 0) button.setAttribute("aria-current", "step");
      const label = createElement("span");
      label.append(createElement("strong", event.title), createElement("small", event.kind));
      button.append(createElement("time", formatTime(event.observed)), label, createBadge(event.truth));
      button.addEventListener("click", () => {
        setPlaybackState(false);
        updatePlayback(index);
      });
      item.append(button);
      return item;
    });
    list?.replaceChildren(...items);
    text("#playback-start", formatTime(playbackEvents[0].observed));
    text("#playback-end", formatTime(playbackEvents[playbackEvents.length - 1].observed));
    updatePlayback(0);
    const last = playbackEvents[playbackEvents.length - 1];
    applyTruthBadge(one("#verification-truth"), last.truth);
    const details = one("#verification-details");
    if (details) {
      const values = [
        ["Last state", last.kind],
        ["Observation", formatTime(last.observed, { includeDate: true })],
        ["Record hash", last.receipt],
        ["Production outcome", "Not claimed by playback"],
      ].map(([term, value]) => {
        const row = document.createElement("div");
        row.append(createElement("dt", term), createElement("dd", value));
        return row;
      });
      details.replaceChildren(...values);
    }
  }

  function renderReceipts() {
    const tape = one("#evidence-tape");
    if (!tape) return;
    text("#receipt-count", workspace.receipts.length);
    if (!workspace.receipts.length) {
      tape.replaceChildren(unavailableListItem("UNAVAILABLE; receipts API unavailable or empty. No pseudo-receipt was substituted."));
      return;
    }
    const items = workspace.receipts.map((receipt) => {
      const hash = validHash(receipt.record_hash);
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      if (hash) button.dataset.receipt = hash;
      const label = createElement("span");
      label.append(createElement("strong", String(receipt.kind || "Persisted receipt")), createElement("small", String(receipt.subject_id || "UNAVAILABLE")));
      button.append(createElement("time", formatTime(receipt.created_at)), label, createBadge(receipt.truth_label), createElement("code", shortHash(hash)));
      button.addEventListener("click", () => openReceipt(hash, button));
      item.append(button);
      return item;
    });
    tape.replaceChildren(...items);
  }

  function renderProofCoverage() {
    const records = [
      ...workspace.services,
      ...workspace.journeys,
      ...workspace.outcomes,
      ...workspace.agents,
      ...workspace.incidents,
      ...workspace.decisions,
      ...workspace.playback,
      ...workspace.receipts,
    ].filter((record) => validHash(record.record_hash));
    text("#proof-coverage-summary", `${records.length} persisted hash${records.length === 1 ? "" : "es"}`);
    const ring = one("#proof-coverage-ring");
    if (ring) {
      ring.setAttribute("aria-label", `${records.length} API records have persisted 64-character hashes; no completeness percentage is asserted`);
      text("span", "—", ring);
    }
    const counts = new Map();
    records.forEach((record) => counts.set(validTruth(record.truth_label), (counts.get(validTruth(record.truth_label)) || 0) + 1));
    const legend = one("#proof-coverage-legend");
    if (legend) {
      const items = [...counts.entries()].map(([truth, count]) => {
        const item = document.createElement("li");
        item.append(createElement("span", "", `legend-dot dot-${truth.toLowerCase()}`), document.createTextNode(`${truth} records `), createElement("b", count));
        return item;
      });
      legend.replaceChildren(...(items.length ? items : [unavailableListItem("Canonical evidence UNAVAILABLE")]));
    }
  }

  function renderSourceCatalog(sourceCatalog) {
    const container = one("#source-list");
    if (!container) return;
    const sources = safeItems(sourceCatalog?.sources);
    if (!sources.length) {
      const article = document.createElement("article");
      const copy = createElement("div");
      copy.append(createElement("strong", "Source catalog unavailable"), createElement("small", "Canonical source API not verified"));
      article.append(createElement("span", "—", "source-icon"), copy, createBadge("UNAVAILABLE"));
      container.replaceChildren(article);
      return;
    }
    const articles = sources.map((source) => {
      const article = document.createElement("article");
      const name = String(source.id || "source").replaceAll("_", " ");
      const initials = name.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();
      const copy = createElement("div");
      copy.append(createElement("strong", name), createElement("small", `${String(source.state || "UNAVAILABLE")} · ${String(source.mode || "UNAVAILABLE")} · ${isLiveSource(source) ? "API reports a receipted observation within 15 minutes" : "Declared configuration; no current receipted observation returned"}`));
      article.append(createElement("span", initials, "source-icon"), copy, createBadge(source.truth_label));
      return article;
    });
    container.replaceChildren(...articles);
  }

  function renderModeStatus() {
    root.dataset.dataMode = workspace.mode;
    const sample = workspace.mode === "SAMPLE";
    const real = workspace.mode === "REAL_ONLY";
    const truth = sample ? "SAMPLE" : real ? "REPORTED" : "UNAVAILABLE";
    applyTruthBadge(one("#sidebar-truth"), truth);
    applyTruthBadge(one("#workspace-truth"), truth);
    text("#scope-mode", sample ? "Persisted deterministic SAMPLE" : real ? "REAL_ONLY; no sample fallback" : "Canonical data UNAVAILABLE");
    text("#sidebar-data-mode", workspace.mode);
    text("#workspace-data-mode", workspace.mode);
    text("#scope-name", requestScope ? "Authenticated scoped workspace" : sample ? "Public SAMPLE workspace" : "Current workspace");
    const truncated = [...workspace.truncated];
    const graphShown = graphNodes.length;
    const graphLoaded = workspace.services.length + workspace.journeys.length + workspace.agents.length + workspace.outcomes.length;
    text("#workspace-coverage", workspace.mode === "UNAVAILABLE"
      ? "Operating records unavailable; no prior workspace data is retained."
      : `${truncated.length ? `Partial results: ${truncated.join(", ")} reached the ${PAGE_LIMIT * MAX_PAGES}-record loading limit. ` : "All returned pages loaded. "}Graph shows ${graphShown} of ${graphLoaded} loaded service, journey, agent, and outcome records (up to four per layer).`);
    text(
      "#sidebar-truth-copy",
      sample
        ? "Fictional evaluation records are served from the same persistence APIs as real data and remain explicitly SAMPLE / MODELED."
        : real
          ? "REAL_ONLY mode is active. Missing APIs stay UNAVAILABLE; no sample fixture is substituted."
          : "Canonical scoped APIs could not establish one data mode. No offline fixture was substituted.",
    );
  }

  function validatePage(name, payload) {
    const expectedSchema = name === "receipts" ? "szl.lyte.receipt-page/v1" : "szl.lyte.operational-page/v1";
    if (!payload || payload.schema !== expectedSchema || !Array.isArray(payload.items)) {
      throw new Error(`${name} returned an unsupported page contract`);
    }
    if (!new Set(["SAMPLE", "REAL"]).has(payload.data_mode)) {
      throw new Error(`${name} returned an unsupported data mode`);
    }
    if (payload.items.some((item) => !validHash(item?.record_hash))) {
      throw new Error(`${name} returned a record without a persisted SHA-256 hash`);
    }
    if (name !== "receipts" && payload.items.some((item) => !item.body || typeof item.body !== "object" || Array.isArray(item.body))) {
      throw new Error(`${name} returned an unsupported operational record`);
    }
    return { items: payload.items, mode: payload.data_mode === "REAL" ? "REAL_ONLY" : "SAMPLE" };
  }

  async function loadPage(name, headers) {
    const items = [];
    const hashes = new Set();
    let offset = 0;
    let mode = null;
    for (let pageIndex = 0; pageIndex < MAX_PAGES; pageIndex += 1) {
      const payload = await fetchJSON(`${API[name]}?limit=${PAGE_LIMIT}&offset=${offset}`, { headers });
      const page = validatePage(name, payload);
      if (payload.offset !== offset || page.items.length > PAGE_LIMIT || (mode && mode !== page.mode)) {
        throw new Error(`${name} returned inconsistent pagination or data mode`);
      }
      mode = page.mode;
      page.items.forEach((record) => {
        const hash = validHash(record.record_hash);
        if (!hashes.has(hash)) { items.push(record); hashes.add(hash); }
      });
      if (payload.next_offset === null) return { items, mode, truncated: false };
      if (!Number.isSafeInteger(payload.next_offset) || payload.next_offset !== offset + page.items.length || payload.next_offset <= offset) {
        throw new Error(`${name} returned an invalid next offset`);
      }
      offset = payload.next_offset;
    }
    return { items, mode, truncated: true };
  }

  function hydrateWorkspace() {
    registerEvidenceDetails();
    buildGraph();
    renderModeStatus();
    renderExecutive();
    renderGraph();
    renderLatticeTable();
    renderServices();
    renderServiceEvents();
    renderJourneys();
    renderDecisions();
    renderAgents();
    renderIncidents();
    renderReceipts();
    renderProofCoverage();
    renderSourceCatalog(workspace.sources);
  }

  async function loadWorkspaceData() {
    const generation = ++workspaceLoadGeneration;
    root.dataset.workspaceState = "loading";
    const headers = scopeHeaders();
    const names = ["build", "sources", "services", "journeys", "outcomes", "agents", "incidents", "decisions", "playback", "receipts"];
    const results = await Promise.allSettled(names.map((name) => name === "build" || name === "sources" ? fetchJSON(API[name]) : loadPage(name, headers)));
    if (generation !== workspaceLoadGeneration) return;
    const modes = new Set();
    workspace.failures.clear();
    workspace.truncated.clear();
    names.forEach((name, index) => {
      const result = results[index];
      if (result.status !== "fulfilled") {
        workspace.failures.add(name);
        if (!new Set(["build", "sources"]).has(name)) workspace[name] = [];
        else workspace[name] = null;
        return;
      }
      if (name === "build" || name === "sources") {
        workspace[name] = result.value;
        return;
      }
      try {
        const page = result.value;
        if (requestScope && page.mode !== "REAL_ONLY") throw new Error("A scoped request returned sample data");
        workspace[name] = page.items;
        modes.add(page.mode);
        if (page.truncated) workspace.truncated.add(name);
      } catch (_error) {
        workspace[name] = [];
        workspace.failures.add(name);
      }
    });
    if (modes.size === 1) {
      workspace.mode = [...modes][0];
    } else {
      workspace.mode = "UNAVAILABLE";
      workspace.failures.add("data-mode");
      ["services", "journeys", "outcomes", "agents", "incidents", "decisions", "playback", "receipts"].forEach((name) => {
        workspace[name] = [];
      });
    }
    hydrateWorkspace();
    if (workspace.build || workspace.sources) applySourceStatus(workspace.build, workspace.sources);
    else applySourceUnavailable();
    root.dataset.workspaceState = "settled";
    if (requestScope) text("#scope-session-status", workspace.mode === "REAL_ONLY"
      ? "Scoped APIs accepted this workspace. The token remains in page memory until cleared or the page closes."
      : "Scoped APIs did not establish access. Records remain UNAVAILABLE; no public sample was substituted.");
    announce(
      workspace.mode === "UNAVAILABLE"
        ? "Canonical workspace data is unavailable. No offline sample was substituted."
        : `${workspace.mode} workspace data loaded from canonical scoped APIs${workspace.failures.size ? " with unavailable surfaces" : ""}.`,
    );
  }

  function flattenAnswerReferences(payload) {
    const references = [];
    const groups = [
      ["receipt", payload.evidence_receipt_ids],
      ["entity", payload.source_entities],
      ["formula", payload.formula_ids],
      ["limit", payload.limitations],
    ];
    groups.forEach(([prefix, values]) => {
      if (!Array.isArray(values)) return;
      values.slice(0, 12).forEach((value) => {
        if (typeof value === "string" && value.trim()) references.push(`${prefix}: ${value.trim().slice(0, 220)}`);
        else if (value && typeof value === "object") references.push(`${prefix}: ${JSON.stringify(value).slice(0, 220)}`);
      });
    });
    if (payload.recommended_next_review) references.push(`next review: ${String(payload.recommended_next_review).slice(0, 220)}`);
    return references;
  }

  function renderAskUnavailable(message) {
    applyTruthBadge(one("#ask-truth"), "UNAVAILABLE");
    text("#ask-result-state", "Answer API unavailable");
    text("#ask-answer", message);
    const list = one("#ask-sources");
    if (list) {
      const item = document.createElement("li");
      item.textContent = "No evidence references returned. No answer was synthesized locally.";
      list.replaceChildren(item);
    }
  }

  async function askLyte(question) {
    const result = one("#ask-result");
    const submit = one("#ask-form button[type='submit']");
    if (!result) return;
    if (askController) askController.abort();
    const controller = new AbortController();
    askController = controller;
    const timeout = window.setTimeout(() => controller.abort("timeout"), 10000);
    result.setAttribute("aria-busy", "true");
    if (submit) submit.disabled = true;
    applyTruthBadge(one("#ask-truth"), "UNAVAILABLE");
    text("#ask-result-state", "Checking evidence…");
    text("#ask-answer", "Lyte is querying the deterministic answer engine.");
    try {
      const response = await fetch(API.ask, {
        method: "POST",
        credentials: "same-origin",
        headers: { Accept: "application/json", "Content-Type": "application/json", ...scopeHeaders() },
        body: JSON.stringify({ question }),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.toLowerCase().includes("application/json")) throw new Error("non-JSON response");
      const payload = await response.json();
      const truth = TRUTH_LABELS.has(payload?.truth_label) ? payload.truth_label : "UNAVAILABLE";
      const answer = typeof payload?.answer === "string" && payload.answer.trim()
        ? payload.answer.trim().slice(0, 5000)
        : "The answer engine returned no supported answer.";
      const confidence = typeof payload?.confidence === "number" ? payload.confidence : null;
      const confidenceText = Number.isFinite(confidence) ? ` · confidence ${Math.max(0, Math.min(0.97, confidence)).toFixed(2)}` : "";
      applyTruthBadge(one("#ask-truth"), truth);
      text("#ask-result-state", `Deterministic answer${confidenceText}`);
      text("#ask-answer", answer);
      const references = flattenAnswerReferences(payload);
      const list = one("#ask-sources");
      if (list) {
        const items = (references.length ? references : ["No evidence references were returned."]).map((reference) => {
          const item = document.createElement("li");
          item.textContent = reference;
          return item;
        });
        list.replaceChildren(...items);
      }
      announce(`Ask Lyte answered with truth label ${truth}.`);
    } catch (error) {
      if (error?.name === "AbortError" && controller.signal.reason !== "timeout") return;
      renderAskUnavailable("The deterministic Ask Lyte API could not be reached. No local guess or external model fallback was used.");
      announce("Ask Lyte is unavailable. No answer was fabricated.");
    } finally {
      window.clearTimeout(timeout);
      if (askController === controller) {
        result.setAttribute("aria-busy", "false");
        if (submit) submit.disabled = false;
        askController = null;
      }
    }
  }

  function isLiveSource(source) {
    const truth = String(source?.truth_label || source?.truth || "").toUpperCase();
    const observed = typeof source?.observed_at === "string" ? Date.parse(source.observed_at) : NaN;
    const age = Date.now() - observed;
    return truth === "MEASURED"
      && ["OBSERVED", "OBSERVED_PARTIAL"].includes(source?.state)
      && Boolean(validHash(source?.receipt_id || source?.record_hash))
      && Number.isFinite(age) && age >= 0 && age <= 15 * 60 * 1000;
  }

  function applySourceStatus(build, sourceCatalog) {
    const sources = Array.isArray(sourceCatalog?.sources) ? sourceCatalog.sources : [];
    const measuredCount = sources.filter(isLiveSource).length;
    const revision = typeof build?.build?.revision === "string" && /^[0-9a-f]{40}$/i.test(build.build.revision)
      ? build.build.revision.toLowerCase()
      : null;
    const buildObserved = build?.build?.state === "OBSERVED" && Boolean(revision);
    text("#sidebar-source-count", measuredCount ? `${measuredCount} MEASURED` : "0 BLOCKED");

    const sourceTruth = one("#source-truth");
    applyTruthBadge(sourceTruth, measuredCount ? "MEASURED" : "UNAVAILABLE");
    text("#source-label", measuredCount ? `${measuredCount} recent source observation${measuredCount === 1 ? "" : "s"}` : "No witnessed source success");
    text("#source-revision", revision ? `Build ${revision.slice(0, 12)}` : "Build revision unavailable");

    const light = one("#source-light");
    if (light) {
      light.classList.remove("status-unknown", "status-observed", "status-warning");
      light.classList.add(measuredCount ? "status-observed" : "status-unknown");
    }
    text("#rail-source-title", measuredCount ? `${measuredCount} recent receipted observation${measuredCount === 1 ? "" : "s"}` : "Source success remains UNAVAILABLE");
    text(
      "#rail-source-copy",
      buildObserved
        ? `Build ${revision.slice(0, 12)} is reachable and source-bound. Reachability does not establish source success; ${measuredCount ? "the catalog reports receipted observations within 15 minutes" : "the catalog only declares configuration"}. Operational pages remain ${workspace.mode}.`
        : `Build identity or revision is unavailable. Operational pages remain ${workspace.mode}; no offline source identity was substituted.`,
    );
  }

  function applySourceUnavailable() {
    text("#sidebar-source-count", "0 BLOCKED");
    applyTruthBadge(one("#source-truth"), "UNAVAILABLE");
    text("#source-label", "Source status unavailable");
    text("#source-revision", "Build revision unavailable");
    text("#rail-source-title", "Source verification unavailable");
    text("#rail-source-copy", "The build and source APIs could not be verified. No local source status was substituted.");
  }

  function updateAnimationState() {
    root.dataset.animate = String(!document.hidden && !motionQuery.matches);
  }

  function bindInteractions() {
    all("[data-scene-target]").forEach((button) => {
      button.addEventListener("click", () => setScene(button.dataset.sceneTarget));
    });
    all("[data-lattice-filter]").forEach((button) => {
      button.addEventListener("click", () => setGraphFilter(button.dataset.latticeFilter, true));
    });
    all("[data-inspect-node]").forEach((button) => {
      button.addEventListener("click", () => {
        const node = nodeById(button.dataset.inspectNode);
        if (node) openDrawer(node, button);
      });
    });
    all("[data-decision]").forEach((button) => {
      button.addEventListener("click", () => openDrawer(decisionDetails[button.dataset.decision], button));
    });
    all("[data-receipt]").forEach((button) => {
      button.addEventListener("click", () => openReceipt(button.dataset.receipt, button));
    });
    all("[data-journey-step]").forEach((button) => {
      button.addEventListener("click", () => updateJourney(button.dataset.journeyStep, true));
    });
    all("[data-service-filter]").forEach((button) => {
      button.addEventListener("click", () => filterServices(button.dataset.serviceFilter));
    });
    all("[data-playback-index]").forEach((button) => {
      button.addEventListener("click", () => {
        setPlaybackState(false);
        updatePlayback(button.dataset.playbackIndex);
      });
    });
    all("[data-open-source]").forEach((button) => button.addEventListener("click", () => openDialog("source-dialog")));
    all("[data-open-ask]").forEach((button) => button.addEventListener("click", () => openDialog("ask-dialog")));
    all("[data-dialog-close]").forEach((button) => {
      button.addEventListener("click", () => closeDialog(button.dataset.dialogClose));
    });
    all("dialog").forEach((dialog) => {
      dialog.addEventListener("click", (event) => {
        if (event.target === dialog) dialog.close();
      });
    });

    one("#source-button")?.addEventListener("click", () => openDialog("source-dialog"));
    one("#scope-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      const tokenInput = one("#scope-token");
      try {
        const scope = validateScope(tokenInput?.value || "", one("#scope-tenant")?.value.trim() || "", one("#scope-workspace")?.value.trim() || "");
        if (tokenInput) tokenInput.value = "";
        changeWorkspaceScope(scope);
      } catch (error) {
        if (tokenInput) tokenInput.value = "";
        text("#scope-session-status", error.message);
      }
    });
    one("#scope-clear")?.addEventListener("click", () => {
      one("#scope-form")?.reset();
      changeWorkspaceScope(null);
    });
    window.addEventListener("pagehide", () => {
      requestScope = null;
      workspaceLoadGeneration += 1;
      one("#scope-form")?.reset();
      if (askController) askController.abort();
      workspace.mode = "UNAVAILABLE";
      ["services", "journeys", "outcomes", "agents", "incidents", "decisions", "playback", "receipts"].forEach((name) => { workspace[name] = []; });
      setPlaybackState(false);
      closeDrawer({ restoreFocus: false });
      clearDrawerEvidence();
      hydrateWorkspace();
      renderAskUnavailable("No answer retained after leaving the page.");
    });
    window.addEventListener("pageshow", (event) => { if (event.persisted) loadWorkspaceData(); });
    one("#ask-open")?.addEventListener("click", () => openDialog("ask-dialog"));
    one("#mobile-menu")?.addEventListener("click", toggleMobileNav);
    one("#drawer-close")?.addEventListener("click", () => closeDrawer());
    one("#drawer-scrim")?.addEventListener("click", () => closeDrawer());
    one("#incident-scrubber")?.addEventListener("input", (event) => {
      setPlaybackState(false);
      updatePlayback(event.currentTarget.value);
    });
    one("#playback-toggle")?.addEventListener("click", (event) => {
      setPlaybackState(event.currentTarget.getAttribute("aria-pressed") !== "true");
    });
    one("#ask-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      const input = one("#ask-input");
      const question = input?.value.trim();
      if (!question || question.length < 3) {
        input?.focus();
        return;
      }
      askLyte(question);
    });
    all("[data-ask-prompt]").forEach((button) => {
      button.addEventListener("click", () => {
        const input = one("#ask-input");
        if (!input) return;
        input.value = button.dataset.askPrompt;
        one("#ask-form")?.requestSubmit();
      });
    });

    all("[data-outcome]").forEach((tile) => {
      tile.tabIndex = 0;
      tile.setAttribute("role", "button");
      tile.setAttribute("aria-label", `${one("header > span", tile)?.textContent || "Outcome"}. Open evidence.`);
      const activate = () => openDrawer(
        outcomeDetails[tile.dataset.outcome] || unavailableDetail("Outcome evidence unavailable", "The required scoped API has not been verified."),
        tile,
      );
      tile.addEventListener("click", activate);
      tile.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          activate();
        }
      });
    });

    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        openDialog("ask-dialog");
      } else if (event.key === "Escape") {
        if (!one("#signal-drawer")?.hidden) closeDrawer();
        else closeMobileNav();
      }
    });
    document.addEventListener("click", (event) => {
      const sidebar = one("#primary-sidebar");
      if (!mobileQuery.matches || !sidebar?.classList.contains("is-open")) return;
      if (!sidebar.contains(event.target) && !one("#mobile-menu")?.contains(event.target)) closeMobileNav({ restoreFocus: false });
    });
    window.addEventListener("popstate", () => setScene(window.location.hash.slice(1), { updateHistory: false }));
    document.addEventListener("visibilitychange", () => {
      updateAnimationState();
      if (document.hidden) setPlaybackState(false);
    });
    motionQuery.addEventListener?.("change", updateAnimationState);
    mobileQuery.addEventListener?.("change", syncMobileNav);
  }

  function boot() {
    root.dataset.enhanced = "true";
    updateAnimationState();
    renderGraph();
    bindInteractions();
    syncMobileNav();
    const requestedScene = window.location.hash.slice(1);
    setScene(requestedScene, { updateHistory: false, focusHeading: false });
    loadWorkspaceData();
  }

  boot();
})();
