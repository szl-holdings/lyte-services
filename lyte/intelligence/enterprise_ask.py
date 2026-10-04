"""Bounded citation-first answers over persisted enterprise projections."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from lyte.domain import OperationalEntityKind, Scope, TruthLabel, isoformat_z
from lyte.persistence import LyteStore

_ENTITY_LIMIT = 50
_RECEIPT_FALLBACK_LIMIT = 100
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BAD_STATES = ("DEGRADED", "WATCH", "AT_RISK", "BREACH", "FAIL")


@dataclass(frozen=True, slots=True)
class EnterpriseAnswer:
    question: str
    intent: str
    answer: str | None
    truth_label: str
    citations: tuple[dict[str, Any], ...]
    evidence_receipt_ids: tuple[str, ...]
    source_entities: tuple[str, ...]
    formula_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    recommended_next_review: str | None = None
    confidence: float = 0.97

    def __post_init__(self) -> None:
        if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 0.97:
            raise ValueError("confidence must be finite and between 0 and 0.97")

    def to_dict(self) -> dict[str, Any]:
        """Keep citations first so clients can render proof before prose."""

        return {
            "citations": [dict(item) for item in self.citations],
            "answer": self.answer,
            "question": self.question,
            "intent": self.intent,
            "truth_label": self.truth_label,
            "confidence": self.confidence,
            "confidence_basis": "DETERMINISTIC_SCOPED_QUERY",
            "evidence_receipt_ids": list(self.evidence_receipt_ids),
            "source_entities": list(self.source_entities),
            "formula_ids": list(self.formula_ids),
            "causality_claimed": False,
            "limitations": list(self.limitations),
            "recommended_next_review": self.recommended_next_review,
            "can_execute": False,
            "query_bounds": {
                "current_records_per_entity_kind": _ENTITY_LIMIT,
                "receipt_fallback_records": _RECEIPT_FALLBACK_LIMIT,
            },
        }


def _clean_question(question: str) -> tuple[str, str]:
    clean = " ".join(str(question).split())
    if not clean or len(clean) > 500:
        raise ValueError("question must contain 1-500 characters")
    return clean, re.sub(r"[^a-z0-9]+", " ", clean.lower()).strip()


def _intent(normalized: str) -> str:
    if "improved" in normalized and "action" in normalized:
        return "post_action_outcome"
    if "evidence" in normalized and "formula" in normalized:
        return "evidence_and_formulas"
    if "review first" in normalized or "operator review" in normalized:
        return "operator_review_priority"
    if "deployment" in normalized and "incident" in normalized:
        return "incident_deployment_correlation"
    if "agent" in normalized and ("expensive" in normalized or "unreliable" in normalized):
        return "agent_cost_reliability"
    if "changed" in normalized and "journey" in normalized:
        return "pre_journey_change"
    if "error budget" in normalized and "service" in normalized:
        return "highest_error_budget_burn"
    if "checkout" in normalized and ("revenue" in normalized or "risk" in normalized):
        return "checkout_revenue_risk"
    return "unsupported"


def _unavailable(
    question: str,
    intent: str,
    reason: str,
    *missing: str,
) -> EnterpriseAnswer:
    return EnterpriseAnswer(
        question=question,
        intent=intent,
        answer=None,
        truth_label=TruthLabel.UNAVAILABLE.value,
        citations=(),
        evidence_receipt_ids=(),
        source_entities=(),
        limitations=tuple(missing) + (reason,),
        confidence=0.0,
    )


def _available(rows: Iterable[Any]) -> list[Any]:
    accepted = {
        TruthLabel.MEASURED.value,
        TruthLabel.REPORTED.value,
        TruthLabel.MODELED.value,
    }
    return [row for row in rows if row.truth_label in accepted]


def _body_value(body: Mapping[str, Any], *names: str) -> Any:
    indicators = body.get("indicators")
    for name in names:
        candidate = indicators.get(name) if isinstance(indicators, Mapping) else None
        if candidate is None:
            candidate = body.get(name)
        if isinstance(candidate, Mapping) and "value" in candidate:
            label = candidate.get("truth_label")
            if label is not None and (
                not isinstance(label, str)
                or label
                not in {
                    TruthLabel.MEASURED.value,
                    TruthLabel.REPORTED.value,
                    TruthLabel.MODELED.value,
                }
            ):
                return None
            return candidate.get("value")
        if candidate is not None:
            return candidate
    return None


def _number(body: Mapping[str, Any], *names: str) -> float | None:
    value = _body_value(body, *names)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _strings(body: Mapping[str, Any], name: str) -> tuple[str, ...]:
    value = body.get(name)
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item.strip())


def _state(row: Any) -> str:
    value = row.body_json.get("state")
    return str(value).upper() if isinstance(value, str) else ""


def _degraded(row: Any) -> bool:
    state = _state(row)
    return any(marker in state for marker in _BAD_STATES)


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)


def _witness_time(value: Any) -> datetime | None:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def _record_time(row: Any) -> datetime | None:
    for key in ("at", "deployed_at", "started_at", "observed_at", "occurred_at"):
        parsed = _parse_time(row.body_json.get(key))
        if parsed is not None:
            return parsed
    return _parse_time(row.observed_at)


def _truth(rows: Iterable[Any], *, modeled: bool = False) -> str:
    rows = list(rows)
    labels = {row.truth_label for row in rows}
    for row in rows:
        indicators = row.body_json.get("indicators")
        if isinstance(indicators, Mapping):
            labels.update(
                label
                for indicator in indicators.values()
                if isinstance(indicator, Mapping)
                and isinstance(label := indicator.get("truth_label"), str)
            )
    if modeled or TruthLabel.MODELED.value in labels:
        return TruthLabel.MODELED.value
    if TruthLabel.REPORTED.value in labels:
        return TruthLabel.REPORTED.value
    return TruthLabel.MEASURED.value


def _compatible_receipt(row: Any, receipt: Any) -> bool:
    accepted = {
        TruthLabel.MEASURED.value: {TruthLabel.MEASURED.value},
        TruthLabel.REPORTED.value: {TruthLabel.MEASURED.value, TruthLabel.REPORTED.value},
        TruthLabel.MODELED.value: {
            TruthLabel.MEASURED.value,
            TruthLabel.REPORTED.value,
            TruthLabel.MODELED.value,
        },
    }
    return receipt.truth_label in accepted.get(row.truth_label, set())


def _formula_ids(row: Any) -> tuple[str, ...]:
    indicators = row.body_json.get("indicators")
    if not isinstance(indicators, Mapping):
        return ()
    result: list[str] = []
    for name, raw in indicators.items():
        if not isinstance(name, str) or not isinstance(raw, Mapping):
            continue
        metadata = raw.get("metadata")
        if isinstance(metadata, Mapping) and isinstance(metadata.get("formula"), str):
            result.append(name.removesuffix("_usd"))
    return tuple(dict.fromkeys(result))


class EnterpriseAskEngine:
    """Answer the supported intents from one mandatory tenant/workspace scope."""

    def __init__(self, store: LyteStore, scope: Scope) -> None:
        self.store = store
        self.scope = scope
        self._rows: dict[OperationalEntityKind, list[Any]] = {}
        self._receipt_fallback: list[Any] | None = None

    def _load(self, kind: OperationalEntityKind) -> list[Any]:
        if kind not in self._rows:
            self._rows[kind] = _available(
                self.store.list_operational(
                    self.scope,
                    entity_kind=kind,
                    limit=_ENTITY_LIMIT,
                )
            )
        return self._rows[kind]

    def _receipts_for(self, rows: Iterable[Any]) -> tuple[list[dict[str, Any]], list[str]]:
        rows = list(rows)
        selected = list(dict.fromkeys((row.entity_kind, row.entity_id) for row in rows))
        by_identity = {
            (row.entity_kind, row.entity_id): row
            for row in rows
            if (row.entity_kind, row.entity_id) in selected
        }
        receipts: dict[str, Any] = {}
        unresolved_refs: set[str] = set()
        for row in by_identity.values():
            for ref in row.evidence_refs[:20]:
                if _SHA256.fullmatch(ref):
                    receipt = self.store.get_receipt(self.scope, ref)
                    if receipt is not None:
                        receipts[receipt.record_hash] = receipt
                else:
                    unresolved_refs.add(ref)
        if unresolved_refs:
            if self._receipt_fallback is None:
                self._receipt_fallback = self.store.list_receipts(
                    self.scope,
                    limit=_RECEIPT_FALLBACK_LIMIT,
                )
            for receipt in self._receipt_fallback:
                if unresolved_refs.intersection(receipt.evidence_refs):
                    receipts[receipt.record_hash] = receipt
        if not receipts:
            return [], []

        citations: list[dict[str, Any]] = []
        for row in by_identity.values():
            row_receipts = sorted(
                receipt.record_hash
                for receipt in receipts.values()
                if _compatible_receipt(row, receipt)
                and (
                    receipt.record_hash in row.evidence_refs
                    or set(receipt.evidence_refs).intersection(row.evidence_refs)
                )
            )
            if not row_receipts:
                continue
            observed = _record_time(row) or _parse_time(row.created_at)
            citations.append(
                {
                    "citation_id": row.record_hash,
                    "title": row.name,
                    "source_type": re.sub(r"(?<!^)(?=[A-Z])", "_", row.entity_kind).lower(),
                    "source_ref": f"{row.entity_kind}:{row.entity_id}",
                    "observed_at": isoformat_z(observed) if observed is not None else None,
                    "truth_label": row.truth_label,
                    "evidence_receipt_ids": row_receipts,
                }
            )
        receipt_ids = sorted(
            {
                receipt_id
                for citation in citations
                for receipt_id in citation["evidence_receipt_ids"]
            }
        )
        return citations, receipt_ids

    def _answer(
        self,
        *,
        question: str,
        intent: str,
        text: str,
        rows: Iterable[Any],
        formula_ids: Iterable[str] = (),
        limitations: Iterable[str] = (),
        recommended_next_review: str | None = None,
        confidence: float = 0.97,
        modeled: bool = False,
    ) -> EnterpriseAnswer:
        evidence_rows = list(rows)
        citations, receipt_ids = self._receipts_for(evidence_rows)
        if not citations or not receipt_ids:
            return _unavailable(
                question,
                intent,
                "selected operational records are not bound to a persisted workspace receipt",
                "a receipt-linked evidence projection",
            )
        cited = {
            (
                item["source_ref"].split(":", 1)[0],
                item["source_ref"].split(":", 1)[1],
            )
            for item in citations
        }
        used_rows = [row for row in evidence_rows if (row.entity_kind, row.entity_id) in cited]
        if len(used_rows) != len(evidence_rows):
            return _unavailable(
                question,
                intent,
                "every selected operational input requires a compatible "
                "persisted workspace receipt",
                "complete receipt coverage for the selected evidence inputs",
            )
        return EnterpriseAnswer(
            question=question,
            intent=intent,
            answer=text,
            truth_label=_truth(used_rows, modeled=modeled),
            citations=tuple(citations),
            evidence_receipt_ids=tuple(receipt_ids),
            source_entities=tuple(f"{row.entity_kind}:{row.entity_id}" for row in used_rows),
            formula_ids=tuple(dict.fromkeys(formula_ids)),
            limitations=tuple(limitations),
            recommended_next_review=recommended_next_review,
            confidence=confidence,
        )

    def answer(self, question: str) -> EnterpriseAnswer:
        clean, normalized = _clean_question(question)
        intent = _intent(normalized)
        handler = getattr(self, f"_{intent}", None)
        if handler is None:
            return _unavailable(
                clean,
                "unsupported",
                "Ask Lyte supports only the documented deterministic question set",
                "a deterministic query definition for this question",
            )
        return handler(clean)

    def _checkout_revenue_risk(self, question: str) -> EnterpriseAnswer:
        outcomes = [
            row
            for row in self._load(OperationalEntityKind.BUSINESS_OUTCOME)
            if "checkout" in f"{row.entity_id} {row.name}".casefold()
        ]
        ranked = [
            (row, _number(row.body_json, "revenue_at_risk_usd", "revenue_at_risk"))
            for row in outcomes
        ]
        usable = [(row, value) for row, value in ranked if value is not None]
        if not usable:
            return _unavailable(
                question,
                "checkout_revenue_risk",
                "no receipt-backed checkout outcome contains a declared revenue-at-risk value",
                "checkout business outcome",
                "revenue_at_risk_usd",
            )
        row, value = max(usable, key=lambda item: item[1])
        assert value is not None
        text = (
            f"{row.name} reports ${value:,.2f} at risk in the current scoped projection. "
            "This is evidence-bound operational state, not a causal or guaranteed loss claim."
        )
        return self._answer(
            question=question,
            intent="checkout_revenue_risk",
            text=text,
            rows=(row,),
            formula_ids=_formula_ids(row) or ("revenue_at_risk",),
            limitations=("causality is not established by the outcome projection",),
            recommended_next_review="Review the receipt-linked outcome inputs and owning journey.",
            modeled=True,
        )

    def _highest_error_budget_burn(self, question: str) -> EnterpriseAnswer:
        ranked = [
            (row, _number(row.body_json, "error_budget_burn", "burn_rate"))
            for row in self._load(OperationalEntityKind.SERVICE)
        ]
        usable = [(row, value) for row, value in ranked if value is not None]
        if not usable:
            return _unavailable(
                question,
                "highest_error_budget_burn",
                "no service has receipt-backed error-budget burn evidence",
                "service error_budget_burn",
            )
        row, value = max(usable, key=lambda item: item[1])
        assert value is not None
        return self._answer(
            question=question,
            intent="highest_error_budget_burn",
            text=(
                f"{row.name} ({row.entity_id}) has the highest available error-budget burn "
                f"at {value:g}x among the bounded current service projections."
            ),
            rows=(row,),
            formula_ids=_formula_ids(row) or ("error_budget_burn_rate",),
            recommended_next_review=f"Review service {row.entity_id} and its receipt evidence.",
        )

    def _pre_journey_change(self, question: str) -> EnterpriseAnswer:
        journeys = [
            row for row in self._load(OperationalEntityKind.CUSTOMER_JOURNEY) if _degraded(row)
        ]
        deployments = self._load(OperationalEntityKind.DEPLOYMENT_EVENT)
        candidates: list[tuple[timedelta, Any, Any, datetime, datetime]] = []
        for journey in journeys:
            journey_time = _record_time(journey)
            if journey_time is None:
                continue
            service_ids = set(_strings(journey.body_json, "service_ids"))
            for deployment in deployments:
                deployment_time = _record_time(deployment)
                deployment_service = deployment.body_json.get("service_id")
                if deployment_time is None or deployment_time > journey_time:
                    continue
                if service_ids and deployment_service not in service_ids:
                    continue
                candidates.append(
                    (
                        journey_time - deployment_time,
                        journey,
                        deployment,
                        journey_time,
                        deployment_time,
                    )
                )
        if not candidates:
            return _unavailable(
                question,
                "pre_journey_change",
                "no receipt-backed deployment is temporally comparable to a degraded journey",
                "degraded journey timestamp",
                "preceding deployment timestamp",
            )
        delta, journey, deployment, journey_time, deployment_time = min(
            candidates, key=lambda item: item[0]
        )
        return self._answer(
            question=question,
            intent="pre_journey_change",
            text=(
                f"{deployment.name} at {isoformat_z(deployment_time)} preceded the degraded "
                f"{journey.name} observation at {isoformat_z(journey_time)} by "
                f"{int(delta.total_seconds() // 60)} minutes. Temporal order does not "
                "establish cause."
            ),
            rows=(deployment, journey),
            limitations=("temporal precedence and correlation are not causality",),
            recommended_next_review=(
                "Review the deployment diff and the journey evidence window together."
            ),
            confidence=0.85,
        )

    def _agent_cost_reliability(self, question: str) -> EnterpriseAnswer:
        ranked: list[tuple[float, Any, float, float]] = []
        for row in self._load(OperationalEntityKind.AGENT_TRACE_SUMMARY):
            cost = _number(row.body_json, "cost_per_trace_usd", "cost_usd")
            failure = _number(row.body_json, "tool_failure_rate", "failure_rate")
            success = _number(row.body_json, "success_rate")
            if failure is None and success is not None:
                failure = max(0.0, 1.0 - success)
            if cost is not None and failure is not None:
                ranked.append((cost * failure, row, cost, failure))
        if not ranked:
            return _unavailable(
                question,
                "agent_cost_reliability",
                "no agent projection contains both receipt-backed cost and reliability evidence",
                "agent cost_per_trace_usd",
                "agent tool_failure_rate or success_rate",
            )
        _, row, cost, failure = max(ranked, key=lambda item: item[0])
        return self._answer(
            question=question,
            intent="agent_cost_reliability",
            text=(
                f"{row.name} is the highest combined cost/reliability review candidate in the "
                f"bounded set: ${cost:,.4f} per trace and {failure:.2%} observed failure rate."
            ),
            rows=(row,),
            limitations=("the ranking compares only agents with both required measures",),
            recommended_next_review=f"Review agent {row.entity_id} tool failures and cost drivers.",
        )

    def _incident_deployment_correlation(self, question: str) -> EnterpriseAnswer:
        incidents = self._load(OperationalEntityKind.INCIDENT)
        deployments = self._load(OperationalEntityKind.DEPLOYMENT_EVENT)
        candidates: list[tuple[timedelta, Any, Any, datetime, datetime]] = []
        for incident in incidents:
            incident_time = _record_time(incident)
            if incident_time is None:
                continue
            service_ids = set(_strings(incident.body_json, "service_ids"))
            for deployment in deployments:
                deployment_time = _record_time(deployment)
                deployment_service = deployment.body_json.get("service_id")
                if deployment_time is None or deployment_time > incident_time:
                    continue
                if service_ids and deployment_service not in service_ids:
                    continue
                delta = incident_time - deployment_time
                if delta <= timedelta(hours=24):
                    candidates.append((delta, incident, deployment, incident_time, deployment_time))
        if not candidates:
            return _unavailable(
                question,
                "incident_deployment_correlation",
                "no receipt-backed deployment falls within the bounded 24-hour pre-incident window",
                "incident timestamp",
                "related service deployment timestamp",
            )
        delta, incident, deployment, incident_time, deployment_time = min(
            candidates, key=lambda item: item[0]
        )
        return self._answer(
            question=question,
            intent="incident_deployment_correlation",
            text=(
                f"{deployment.name} at {isoformat_z(deployment_time)} is the closest preceding "
                f"deployment for {incident.name}, which began at {isoformat_z(incident_time)} "
                f"({int(delta.total_seconds() // 60)} minutes later). It is a candidate "
                "factor only."
            ),
            rows=(deployment, incident),
            limitations=("the bounded temporal correlation does not prove causality",),
            recommended_next_review=(
                "Review the deployment change and incident evidence before attribution."
            ),
            confidence=0.8,
        )

    def _operator_review_priority(self, question: str) -> EnterpriseAnswer:
        recommendations = self._load(OperationalEntityKind.RECOMMENDATION)
        for row in recommendations:
            review = row.body_json.get("recommended_next_review")
            if isinstance(review, str) and review.strip():
                return self._answer(
                    question=question,
                    intent="operator_review_priority",
                    text=f"Review {row.name} first: {' '.join(review.split())}",
                    rows=(row,),
                    recommended_next_review=" ".join(review.split()),
                )
        decisions = [
            row
            for row in self._load(OperationalEntityKind.DECISION)
            if str(row.body_json.get("decision", "")).upper() == "REVIEW"
        ]
        if not decisions:
            return _unavailable(
                question,
                "operator_review_priority",
                "no receipt-backed recommendation or REVIEW decision exists",
                "operator review recommendation",
            )
        row = decisions[0]
        return self._answer(
            question=question,
            intent="operator_review_priority",
            text=f"Review {row.name} and its linked evidence first; the decision remains REVIEW.",
            rows=(row,),
            recommended_next_review=f"Inspect decision {row.entity_id} with a named human owner.",
        )

    def _evidence_and_formulas(self, question: str) -> EnterpriseAnswer:
        rows = [
            *self._load(OperationalEntityKind.BUSINESS_OUTCOME),
            *self._load(OperationalEntityKind.SERVICE),
            *self._load(OperationalEntityKind.CUSTOMER_JOURNEY),
        ]
        with_formulas = [(row, _formula_ids(row)) for row in rows]
        selected = [(row, formulas) for row, formulas in with_formulas if formulas]
        if not selected:
            return _unavailable(
                question,
                "evidence_and_formulas",
                "no receipt-backed projection declares formula metadata",
                "formula-bound service, journey, or outcome projection",
            )
        row, formulas = selected[0]
        return self._answer(
            question=question,
            intent="evidence_and_formulas",
            text=(
                f"{row.name} is bound to the declared formula(s) {', '.join(formulas)} and the "
                "receipt-linked source projection cited first in this response. Formulas do "
                "not authorize action."
            ),
            rows=(row,),
            formula_ids=formulas,
            limitations=("formula output is limited by the truth labels of its inputs",),
            recommended_next_review=(
                "Inspect the cited receipt and formula inputs before relying on the result."
            ),
            modeled=True,
        )

    def _post_action_outcome(self, question: str) -> EnterpriseAnswer:
        actions = [
            row
            for row in self._load(OperationalEntityKind.ACTION_REQUEST)
            if str(row.body_json.get("state", "")).upper() in {"EXECUTED", "COMPLETED"}
            and row.body_json.get("simulation") is False
            and row.truth_label == TruthLabel.MEASURED.value
        ]
        verifications = [
            row
            for row in self._load(OperationalEntityKind.OUTCOME_VERIFICATION)
            if str(row.body_json.get("state", "")).upper() in {"VERIFIED", "IMPROVED"}
            and row.body_json.get("production_outcome_claimed") is True
            and row.truth_label == TruthLabel.MEASURED.value
        ]
        selected: tuple[Any, Any] | None = None
        for action in actions:
            execution_hash = action.body_json.get("execution_receipt_hash")
            if not isinstance(execution_hash, str) or not _SHA256.fullmatch(execution_hash):
                continue
            execution = self.store.get_receipt(self.scope, execution_hash)
            if (
                execution is None
                or execution.truth_label != TruthLabel.MEASURED.value
                or execution.kind != "action.executed"
                or execution.subject_id != action.entity_id
                or execution_hash not in action.evidence_refs
                or execution.payload_json.get("state") != "EXECUTED"
                or execution.payload_json.get("simulation") is not False
            ):
                continue
            executed_at = _witness_time(execution.payload_json.get("executed_at"))
            if executed_at is None:
                continue
            for verification in verifications:
                body = verification.body_json
                outcome_hash = body.get("outcome_receipt_hash")
                if (
                    body.get("action_request_id") != action.entity_id
                    or body.get("execution_receipt_hash") != execution_hash
                    or not isinstance(outcome_hash, str)
                    or not _SHA256.fullmatch(outcome_hash)
                    or outcome_hash not in verification.evidence_refs
                ):
                    continue
                outcome = self.store.get_receipt(self.scope, outcome_hash)
                if (
                    outcome is None
                    or outcome.truth_label != TruthLabel.MEASURED.value
                    or outcome.kind != "outcome.verified"
                    or outcome.subject_id != verification.entity_id
                    or outcome.payload_json.get("action_request_id") != action.entity_id
                    or outcome.payload_json.get("execution_receipt_hash") != execution_hash
                    or outcome.payload_json.get("production_outcome_claimed") is not True
                    or outcome.payload_json.get("improved") is not True
                ):
                    continue
                window_start = _witness_time(outcome.payload_json.get("window_start"))
                window_end = _witness_time(outcome.payload_json.get("window_end"))
                if (
                    window_start is not None
                    and window_end is not None
                    and executed_at <= window_start < window_end
                ):
                    selected = (action, verification)
                    break
            if selected is not None:
                break
        if selected is None:
            return _unavailable(
                question,
                "post_action_outcome",
                "an improvement claim requires measured non-simulated execution and "
                "outcome receipts linked to a subsequent verification window",
                "measured execution receipt",
                "measured post-action outcome receipt",
                "action-to-execution-to-outcome window link",
            )
        action, verification = selected
        summary = verification.body_json.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            summary = verification.name
        return self._answer(
            question=question,
            intent="post_action_outcome",
            text=(
                f"After recorded execution of {action.name}, measured outcome evidence reports: "
                f"{' '.join(summary.split())}"
            ),
            rows=(action, verification),
            limitations=(
                "the statement is limited to the cited verification window",
                "stored receipt evidence is not independent certification",
            ),
            recommended_next_review="Continue monitoring the verified outcome window.",
            confidence=0.9,
        )


def ask_enterprise(store: LyteStore, scope: Scope, question: str) -> EnterpriseAnswer:
    return EnterpriseAskEngine(store, scope).answer(question)


__all__ = ["EnterpriseAnswer", "EnterpriseAskEngine", "ask_enterprise"]
