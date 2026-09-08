(() => {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const API = Object.freeze({
    ask: "/api/lyte/v2/ask",
    build: "/api/build-info",
    sources: "/api/lyte/v2/sources",
  });
  const TRUTH_LABELS = new Set(["MEASURED", "REPORTED", "MODELED", "SAMPLE", "UNAVAILABLE"]);
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

  const graphNodes = Object.freeze([
    {
      id: "gateway",
      label: "API gateway",
      type: "service",
      x: 118,
      y: 145,
      state: "Elevated latency",
      truth: "SAMPLE",
      source: "OTLP evaluation fixture / gateway spans",
      formula: "lyte.error_budget_burn",
      receipt: "rcpt-sample-8f21",
      observed: "2026-09-04 10:34:19 UTC (SAMPLE)",
      summary: "p95 latency is 612 ms and the SAMPLE error rate is 2.8%.",
      impact: "The gateway is on the checkout critical path. Correlation is shown; causality is not claimed.",
      attention: true,
    },
    {
      id: "identity",
      label: "Identity policy",
      type: "service",
      x: 118,
      y: 325,
      state: "Nominal",
      truth: "SAMPLE",
      source: "OTLP evaluation fixture / identity spans",
      formula: "lyte.availability_sli",
      receipt: "rcpt-sample-d219",
      observed: "2026-09-04 10:34:25 UTC (SAMPLE)",
      summary: "The identity service remains within its SAMPLE objective at 84 ms p95.",
      impact: "It is present in the path but is not a leading candidate for the modeled impact.",
    },
    {
      id: "checkout",
      label: "Checkout journey",
      type: "journey",
      x: 350,
      y: 125,
      state: "Degraded",
      truth: "MODELED",
      source: "Journey fixture + declared conversion baseline",
      formula: "lyte.journey_health",
      receipt: "rcpt-model-bc77",
      observed: "2026-09-04 10:37:42 UTC (MODELED)",
      summary: "Checkout health is 72/100 after payment completion pressure.",
      impact: "The model connects service latency to completion pressure while retaining explicit assumptions.",
      attention: true,
    },
    {
      id: "support",
      label: "Support journey",
      type: "journey",
      x: 350,
      y: 330,
      state: "Watching",
      truth: "SAMPLE",
      source: "Customer journey evaluation fixture",
      formula: "lyte.journey_health",
      receipt: "rcpt-sample-61d0",
      observed: "2026-09-04 10:38:04 UTC (SAMPLE)",
      summary: "Support containment is being watched; escalation evidence is incomplete.",
      impact: "Incomplete escalation coverage prevents a measured customer-impact conclusion.",
    },
    {
      id: "policy-agent",
      label: "Policy agent",
      type: "agent",
      x: 570,
      y: 205,
      state: "Constrained",
      truth: "SAMPLE",
      source: "Policy evaluation trace fixture",
      formula: "szl.lambda_advisory (CONJECTURE_1)",
      receipt: "rcpt-sample-4c90",
      observed: "2026-09-04 10:41:08 UTC (SAMPLE)",
      summary: "Seven policy gates passed; consequential action remains human-controlled.",
      impact: "Hatun can recommend REVIEW, ABSTAIN, or DENY. It cannot execute production change.",
    },
    {
      id: "resolution-agent",
      label: "Resolution agent",
      type: "agent",
      x: 570,
      y: 355,
      state: "Retrying",
      truth: "SAMPLE",
      source: "Agent trace evaluation fixture",
      formula: "lyte.cost_per_success",
      receipt: "rcpt-sample-4c90",
      observed: "2026-09-04 10:41:08 UTC (SAMPLE)",
      summary: "Three retries increased the MODELED run cost to $4.82.",
      impact: "Retry cost and tool lineage are visible before an operator considers the recommendation.",
      attention: true,
    },
    {
      id: "revenue",
      label: "Revenue at risk",
      type: "outcome",
      x: 765,
      y: 105,
      state: "$184k estimate",
      truth: "MODELED",
      source: "SAMPLE sessions, conversion delta, and order value",
      formula: "lyte.revenue_at_risk",
      receipt: "rcpt-model-bc77",
      observed: "2026-09-04 10:37:42 UTC (MODELED)",
      summary: "$184k is modeled at risk in the current evaluation window.",
      impact: "This is an estimate with exposed inputs. It cannot authorize a rollback or financial action.",
      attention: true,
    },
    {
      id: "retention",
      label: "Retention pressure",
      type: "outcome",
      x: 765,
      y: 330,
      state: "Moderate estimate",
      truth: "MODELED",
      source: "Journey impact evaluation model",
      formula: "lyte.outcome_attainment",
      receipt: "rcpt-model-73be",
      observed: "2026-09-04 10:39:11 UTC (MODELED)",
      summary: "Moderate retention pressure is modeled; customer segment evidence is unavailable.",
      impact: "Missing segment evidence narrows what Lyte can conclude and is not silently replaced with zero.",
    },
  ]);

  const graphEdges = Object.freeze([
    ["gateway", "checkout", "SAMPLE"],
    ["identity", "checkout", "SAMPLE"],
    ["gateway", "support", "SAMPLE"],
    ["checkout", "revenue", "MODELED"],
    ["checkout", "policy-agent", "SAMPLE"],
    ["support", "resolution-agent", "SAMPLE"],
    ["policy-agent", "resolution-agent", "SAMPLE"],
    ["resolution-agent", "retention", "MODELED"],
    ["support", "retention", "MODELED"],
  ]);

  const receiptDetails = Object.freeze({
    "rcpt-sample-a031": {
      label: "Change receipt",
      truth: "SAMPLE",
      source: "GitHub change-event evaluation fixture",
      formula: "Not applicable",
      receipt: "rcpt-sample-a031 / a031…97cf",
      observed: "2026-09-04 10:31:04 UTC (SAMPLE)",
      summary: "A fixture records a gateway timeout-policy change from 800 ms to 400 ms.",
      impact: "The event precedes degradation in the scenario. Sequence is not proof of causality.",
    },
    "rcpt-sample-8f21": {
      label: "Signal receipt",
      truth: "SAMPLE",
      source: "OTLP evaluation fixture",
      formula: "lyte.error_budget_burn",
      receipt: "rcpt-sample-8f21 / 8f21…3ba2",
      observed: "2026-09-04 10:34:19 UTC (SAMPLE)",
      summary: "Checkout-core p95 crosses its SAMPLE service objective.",
      impact: "The signal provides technical evidence for operator review, not an automatic action basis.",
    },
    "rcpt-model-bc77": {
      label: "Impact receipt",
      truth: "MODELED",
      source: "Declared SAMPLE economic inputs",
      formula: "affected_sessions × baseline_conversion × observed_delta × order_value",
      receipt: "rcpt-model-bc77 / bc77…10e4",
      observed: "2026-09-04 10:37:42 UTC (MODELED)",
      summary: "The explicit formula produces a $184k revenue-at-risk estimate.",
      impact: "The model makes economic urgency inspectable while preserving uncertainty and human authority.",
    },
    "rcpt-sample-4c90": {
      label: "Decision receipt",
      truth: "SAMPLE",
      source: "Hatun evaluation fixture",
      formula: "szl.lambda_advisory / CONJECTURE_1_ADVISORY",
      receipt: "rcpt-sample-4c90 / 4c90…d131",
      observed: "2026-09-04 10:41:08 UTC (SAMPLE)",
      summary: "A simulated rollback request receives REVIEW and is not executed.",
      impact: "Consequential authority remains with a named human; effectors are disabled.",
    },
    "rcpt-sample-f129": {
      label: "Verification receipt",
      truth: "SAMPLE",
      source: "Post-action observation fixture",
      formula: "lyte.mean_time_to_recovery",
      receipt: "rcpt-sample-f129 / f129…a64e",
      observed: "2026-09-04 10:46:55 UTC (SAMPLE)",
      summary: "The fixture records recovery to 212 ms p95 and 0.2% error rate.",
      impact: "Recovery verification remains a distinct proof event rather than being inferred from the request.",
    },
  });

  const decisionDetails = Object.freeze({
    checkout: {
      label: "Protect checkout conversion",
      truth: "MODELED",
      source: "SAMPLE service and journey receipts",
      formula: "lyte.revenue_at_risk",
      receipt: "rcpt-model-bc77 + rcpt-sample-4c90",
      observed: "2026-09-04 10:41:08 UTC (evaluation)",
      summary: "Review the checkout regression and the simulated rollback request first.",
      impact: "Modeled revenue pressure is high, but the relationship is correlational and execution requires human approval.",
    },
    agent: {
      label: "Bound agent retry cost",
      truth: "MODELED",
      source: "Agent trace evaluation fixture",
      formula: "lyte.cost_per_success",
      receipt: "rcpt-sample-4c90",
      observed: "2026-09-04 10:41:08 UTC (SAMPLE)",
      summary: "Review the resolution agent's three retries and policy loop before increasing its budget.",
      impact: "Tool lineage and cost should be reviewed together; no budget or production state can be changed here.",
    },
    support: {
      label: "Watch support containment",
      truth: "UNAVAILABLE",
      source: "Customer segment evidence unavailable",
      formula: "lyte.journey_health",
      receipt: "UNAVAILABLE",
      observed: "UNAVAILABLE",
      summary: "The evaluation fixture suggests pressure, but escalation and segment evidence is incomplete.",
      impact: "Lyte abstains from a stronger conclusion until the missing evidence is connected.",
    },
  });

  const journeySteps = Object.freeze({
    discover: {
      name: "Discover",
      summary: "Acquisition traffic enters through the product catalog with no modeled material impact.",
      service: "catalog-edge",
      signal: "p95 146 ms",
      outcome: "No material impact",
      truth: "SAMPLE",
    },
    configure: {
      name: "Configure",
      summary: "Configuration completion is slightly below the SAMPLE baseline; no causal driver is established.",
      service: "configuration-core",
      signal: "completion 91.4%",
      outcome: "Watching abandonment",
      truth: "SAMPLE",
    },
    checkout: {
      name: "Checkout",
      summary: "Payment-path latency coincides with a modeled completion drop in the evaluation scenario.",
      service: "gateway → checkout-core",
      signal: "p95 884 ms / 1.9% error",
      outcome: "$184k revenue at risk",
      truth: "MODELED",
    },
    activate: {
      name: "Activate",
      summary: "Customers who completed checkout activated normally in the SAMPLE fixture.",
      service: "activation-worker",
      signal: "completion 98.7%",
      outcome: "No additional impact",
      truth: "SAMPLE",
    },
  });

  const playbackEvents = Object.freeze([
    {
      position: "10:31:04 · Change observed",
      kind: "SYSTEM CHANGE · SAMPLE",
      title: "Gateway timeout policy changed",
      copy: "Evaluation fixture sample-r17 changed the upstream timeout from 800 ms to 400 ms. No production action is represented.",
      source: "GitHub fixture",
      receipt: "rcpt-sample-a031",
    },
    {
      position: "10:34:19 · Objective crossed",
      kind: "SERVICE SIGNAL · SAMPLE",
      title: "Checkout p95 crossed the SAMPLE objective",
      copy: "Checkout-core p95 reached 884 ms and error-budget burn rose. The measurement is fictional evaluation data.",
      source: "OTLP fixture",
      receipt: "rcpt-sample-8f21",
    },
    {
      position: "10:37:42 · Impact modeled",
      kind: "BUSINESS OUTCOME · MODELED",
      title: "Revenue-at-risk estimate increased",
      copy: "The explicit SAMPLE inputs produced a $184k estimate. Customer segment evidence remains unavailable.",
      source: "Economic model",
      receipt: "rcpt-model-bc77",
    },
    {
      position: "10:41:08 · Review requested",
      kind: "HUMAN GATE · SAMPLE",
      title: "Simulated rollback request entered REVIEW",
      copy: "Hatun retained human authority. The rollback was proposed in the fixture and was not executed by Lyte.",
      source: "Hatun fixture",
      receipt: "rcpt-sample-4c90",
    },
    {
      position: "10:46:55 · Recovery verified",
      kind: "VERIFICATION · SAMPLE",
      title: "Expected recovery was separately observed",
      copy: "The post-action fixture reached 212 ms p95 and 0.2% error. This is expected SAMPLE recovery, not witnessed production state.",
      source: "Verification fixture",
      receipt: "rcpt-sample-f129",
    },
  ]);

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

    const edges = graphEdges.map(([sourceId, targetId, truth]) => {
      const source = nodeById(sourceId);
      const target = nodeById(targetId);
      const path = svgElement("path", {
        class: `lattice-edge${truth === "MODELED" ? " edge-modeled" : ""}`,
        d: edgePath(source, target),
        "data-source": sourceId,
        "data-target": targetId,
        "marker-end": "url(#edge-arrow)",
      });
      if (!isNodeVisible(source) || !isNodeVisible(target)) path.classList.add("is-hidden");
      return path;
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
    element.classList.remove("truth-measured", "truth-reported", "truth-modeled", "truth-sample", "truth-unavailable");
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
      if (restoreFocus && drawerReturnFocus instanceof HTMLElement) drawerReturnFocus.focus();
      drawerReturnFocus = null;
    }, motionQuery.matches ? 0 : 220);
    announce("Evidence detail closed.");
  }

  function openReceipt(id, trigger) {
    const detail = receiptDetails[id] || {
      label: "Receipt unavailable",
      truth: "UNAVAILABLE",
      summary: `No local evaluation detail is registered for ${String(id)}.`,
      source: "UNAVAILABLE",
      formula: "UNAVAILABLE",
      receipt: String(id || "UNAVAILABLE"),
      observed: "UNAVAILABLE",
      impact: "Lyte does not invent missing evidence.",
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
        headers: { Accept: "application/json", "Content-Type": "application/json" },
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
      const confidence = Number(payload?.confidence);
      const confidenceText = Number.isFinite(confidence) ? ` · confidence ${Math.max(0, Math.min(1, confidence)).toFixed(2)}` : "";
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
    const state = String(source?.state || "").toUpperCase();
    return ["CONNECTED", "CONFIGURED", "READY", "LIVE"].includes(state);
  }

  function sourceById(sources, id) {
    return sources.find((source) => String(source?.id || "").toLowerCase() === id);
  }

  function applySourceStatus(build, sourceCatalog) {
    const sources = Array.isArray(sourceCatalog?.sources) ? sourceCatalog.sources : [];
    const liveCount = sources.filter(isLiveSource).length;
    const revision = typeof build?.build?.revision === "string" && /^[0-9a-f]{40}$/i.test(build.build.revision)
      ? build.build.revision.toLowerCase()
      : null;
    const buildObserved = build?.build?.state === "OBSERVED" && Boolean(revision);
    const github = sourceById(sources, "github_actions");
    text("#sidebar-source-count", `${liveCount} LIVE`);
    text("#receipt-count", "05");

    const sourceTruth = one("#source-truth");
    applyTruthBadge(sourceTruth, buildObserved ? "MEASURED" : "UNAVAILABLE");
    text("#source-label", liveCount ? `${liveCount} live source${liveCount === 1 ? "" : "s"}` : "No live data source");
    text("#source-revision", revision ? `Build ${revision.slice(0, 12)}` : "Build revision unavailable");

    const light = one("#source-light");
    if (light) {
      light.classList.remove("status-unknown", "status-observed", "status-warning");
      light.classList.add(liveCount && buildObserved ? "status-observed" : buildObserved ? "status-warning" : "status-unknown");
    }
    text("#rail-source-title", liveCount ? `${liveCount} live source${liveCount === 1 ? "" : "s"} verified` : "No live data source verified");
    text(
      "#rail-source-copy",
      buildObserved
        ? `Build ${revision.slice(0, 12)} is source-bound. Data adapters remain ${liveCount ? "explicitly connected" : "unconfigured"}; the visible scenario is SAMPLE / MODELED.`
        : "Build identity or revision is unavailable. The visible scenario remains clearly marked SAMPLE / MODELED.",
    );

    if (github) {
      const state = String(github.state || "UNAVAILABLE");
      text("#github-source-copy", state === "AVAILABLE_UNCONFIGURED" ? "Read-only adapter available; connection not configured" : `Read-only adapter state: ${state}`);
      applyTruthBadge(one("#github-source-truth"), isLiveSource(github) ? "MEASURED" : "REPORTED");
    } else {
      text("#github-source-copy", "Read-only source catalog entry unavailable");
      applyTruthBadge(one("#github-source-truth"), "UNAVAILABLE");
    }
  }

  function applySourceUnavailable() {
    text("#sidebar-source-count", "0 LIVE");
    applyTruthBadge(one("#source-truth"), "UNAVAILABLE");
    text("#source-label", "Source status unavailable");
    text("#source-revision", "Build revision unavailable");
    text("#rail-source-title", "Source verification unavailable");
    text("#rail-source-copy", "The source APIs could not be verified. The visible scenario remains SAMPLE / MODELED and is not promoted to live.");
    text("#github-source-copy", "Read-only source not verified");
    applyTruthBadge(one("#github-source-truth"), "UNAVAILABLE");
  }

  async function loadSourceStatus() {
    const results = await Promise.allSettled([fetchJSON(API.build), fetchJSON(API.sources)]);
    if (results.some((result) => result.status === "fulfilled")) {
      const build = results[0].status === "fulfilled" ? results[0].value : null;
      const sources = results[1].status === "fulfilled" ? results[1].value : null;
      applySourceStatus(build, sources);
    } else {
      applySourceUnavailable();
    }
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
      const mappings = {
        availability: nodeById("checkout"),
        revenue: nodeById("revenue"),
        agents: decisionDetails.agent,
        incidents: receiptDetails["rcpt-sample-a031"],
      };
      tile.tabIndex = 0;
      tile.setAttribute("role", "button");
      tile.setAttribute("aria-label", `${one("header > span", tile)?.textContent || "Outcome"}. Open evidence.`);
      const activate = () => openDrawer(mappings[tile.dataset.outcome], tile);
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
    updateJourney("discover", false);
    updatePlayback(0);
    const requestedScene = window.location.hash.slice(1);
    setScene(requestedScene, { updateHistory: false, focusHeading: false });
    loadSourceStatus();
  }

  boot();
})();
