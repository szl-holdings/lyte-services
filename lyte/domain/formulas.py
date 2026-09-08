"""Evidence-labelled business-observability formulas.

Every missing required input produces an explicit ``UNAVAILABLE`` result. The
formulas never substitute zero for absence and never grant execution authority.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .truth import TruthLabel, TruthValue

TRUST_CEILING = 0.97


@dataclass(frozen=True, slots=True)
class FormulaResult:
    name: str
    value: float | None
    truth_label: TruthLabel
    expression: str
    unit: str = "dimensionless"
    inputs: Mapping[str, Any] = field(default_factory=dict)
    reason: str | None = None
    proof_status: str = "EXECUTABLE_DEFINITION"
    can_authorize: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "truth_label", TruthLabel(self.truth_label))
        object.__setattr__(self, "inputs", dict(self.inputs))
        if not isinstance(self.unit, str) or not self.unit.strip():
            raise ValueError("formula unit metadata is required")
        if self.truth_label is TruthLabel.UNAVAILABLE:
            if self.value is not None:
                raise ValueError("UNAVAILABLE formula values must be null")
            if not (self.reason or "").strip():
                raise ValueError("UNAVAILABLE formula values require a reason")
        else:
            if self.value is None or not math.isfinite(float(self.value)):
                raise ValueError("available formula values must be finite")
        if self.can_authorize:
            raise ValueError("Lyte formulas cannot authorize actions")

    @property
    def available(self) -> bool:
        return self.truth_label is not TruthLabel.UNAVAILABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "truth_label": self.truth_label.value,
            "expression": self.expression,
            "unit": self.unit,
            "inputs": dict(self.inputs),
            "reason": self.reason,
            "proof_status": self.proof_status,
            "can_authorize": False,
        }


def _finite(value: float | int, *, name: str, minimum: float | None = None) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric, not boolean")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and number < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return number


def _count(value: int, *, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric, not boolean")
    if not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _unavailable(
    name: str,
    expression: str,
    inputs: Mapping[str, Any],
    missing: list[str],
    *,
    unit: str = "dimensionless",
    proof_status: str = "EXECUTABLE_DEFINITION",
) -> FormulaResult:
    return FormulaResult(
        name=name,
        value=None,
        truth_label=TruthLabel.UNAVAILABLE,
        expression=expression,
        unit=unit,
        inputs=inputs,
        reason="missing required input(s): " + ", ".join(sorted(missing)),
        proof_status=proof_status,
    )


def availability_sli(good_events: int | None, total_events: int | None) -> FormulaResult:
    inputs = {"good_events": good_events, "total_events": total_events}
    missing = [key for key, value in inputs.items() if value is None]
    expression = "good_events / total_events"
    if missing:
        return _unavailable("availability_sli", expression, inputs, missing, unit="fraction")
    good = _count(good_events, name="good_events")  # type: ignore[arg-type]
    total = _count(total_events, name="total_events")  # type: ignore[arg-type]
    if total == 0:
        return _unavailable(
            "availability_sli",
            expression,
            inputs,
            ["nonzero total_events"],
            unit="fraction",
        )
    if good > total:
        raise ValueError("good_events cannot exceed total_events")
    return FormulaResult(
        "availability_sli",
        good / total,
        TruthLabel.MEASURED,
        expression,
        unit="fraction",
        inputs=inputs,
    )


def error_rate(bad_events: int | None, total_events: int | None) -> FormulaResult:
    """Return explicit bad-event rate without converting missing/zero data to zero."""

    inputs = {"bad_events": bad_events, "total_events": total_events}
    expression = "bad_events / total_events"
    missing = [key for key, value in inputs.items() if value is None]
    if missing:
        return _unavailable("error_rate", expression, inputs, missing, unit="fraction")
    bad = _count(bad_events, name="bad_events")  # type: ignore[arg-type]
    total = _count(total_events, name="total_events")  # type: ignore[arg-type]
    if total == 0:
        return _unavailable(
            "error_rate", expression, inputs, ["nonzero total_events"], unit="fraction"
        )
    if bad > total:
        raise ValueError("bad_events cannot exceed total_events")
    return FormulaResult(
        "error_rate",
        bad / total,
        TruthLabel.MEASURED,
        expression,
        unit="fraction",
        inputs=inputs,
    )


def allowed_bad_rate(target: float | None) -> FormulaResult:
    inputs = {"target": target}
    expression = "1 - target"
    if target is None:
        return _unavailable("allowed_bad_rate", expression, inputs, ["target"], unit="fraction")
    target_value = _finite(target, name="target", minimum=0.0)
    if target_value >= 1.0:
        raise ValueError("target must be less than 1")
    return FormulaResult(
        "allowed_bad_rate",
        1.0 - target_value,
        TruthLabel.MEASURED,
        expression,
        unit="fraction",
        inputs=inputs,
    )


def error_budget_burn_rate(
    good_events: int | None,
    total_events: int | None,
    target: float | None,
) -> FormulaResult:
    inputs = {
        "good_events": good_events,
        "total_events": total_events,
        "target": target,
    }
    expression = "(1 - availability_sli) / (1 - target)"
    missing = [key for key, value in inputs.items() if value is None]
    if missing:
        return _unavailable("error_budget_burn_rate", expression, inputs, missing, unit="x")
    target_value = _finite(target, name="target", minimum=0.0)  # type: ignore[arg-type]
    if target_value >= 1.0:
        raise ValueError("target must be less than 1")
    sli = availability_sli(good_events, total_events)
    if not sli.available:
        return FormulaResult(
            "error_budget_burn_rate",
            None,
            TruthLabel.UNAVAILABLE,
            expression,
            unit="x",
            inputs=inputs,
            reason=sli.reason,
        )
    return FormulaResult(
        "error_budget_burn_rate",
        (1.0 - float(sli.value)) / (1.0 - target_value),
        TruthLabel.MEASURED,
        expression,
        unit="x",
        inputs=inputs,
    )


def error_budget_remaining(
    good_events: int | None,
    total_events: int | None,
    target: float | None,
) -> FormulaResult:
    inputs = {
        "good_events": good_events,
        "total_events": total_events,
        "target": target,
    }
    expression = "max(0, 1 - error_budget_burn_rate)"
    burn = error_budget_burn_rate(good_events, total_events, target)
    if not burn.available:
        return FormulaResult(
            "error_budget_remaining",
            None,
            TruthLabel.UNAVAILABLE,
            expression,
            unit="fraction",
            inputs=inputs,
            reason=burn.reason,
        )
    return FormulaResult(
        "error_budget_remaining",
        max(0.0, 1.0 - float(burn.value)),
        TruthLabel.MEASURED,
        expression,
        unit="fraction",
        inputs=inputs,
    )


def change_failure_rate(failed_changes: int | None, total_changes: int | None) -> FormulaResult:
    inputs = {"failed_changes": failed_changes, "total_changes": total_changes}
    expression = "failed_changes / total_changes"
    missing = [key for key, value in inputs.items() if value is None]
    if missing:
        return _unavailable("change_failure_rate", expression, inputs, missing, unit="fraction")
    failed = _count(failed_changes, name="failed_changes")  # type: ignore[arg-type]
    total = _count(total_changes, name="total_changes")  # type: ignore[arg-type]
    if total == 0:
        return _unavailable(
            "change_failure_rate",
            expression,
            inputs,
            ["nonzero total_changes"],
            unit="fraction",
        )
    if failed > total:
        raise ValueError("failed_changes cannot exceed total_changes")
    return FormulaResult(
        "change_failure_rate",
        failed / total,
        TruthLabel.MEASURED,
        expression,
        unit="fraction",
        inputs=inputs,
    )


def requests_per_second(requests: int | None, window_seconds: float | None) -> FormulaResult:
    inputs = {"requests": requests, "window_seconds": window_seconds}
    expression = "requests / window_seconds"
    missing = [key for key, value in inputs.items() if value is None]
    if missing:
        return _unavailable(
            "requests_per_second", expression, inputs, missing, unit="requests/second"
        )
    request_count = _count(requests, name="requests")  # type: ignore[arg-type]
    seconds = _finite(window_seconds, name="window_seconds", minimum=0.0)  # type: ignore[arg-type]
    if seconds == 0:
        return _unavailable(
            "requests_per_second",
            expression,
            inputs,
            ["nonzero window_seconds"],
            unit="requests/second",
        )
    return FormulaResult(
        "requests_per_second",
        request_count / seconds,
        TruthLabel.MEASURED,
        expression,
        unit="requests/second",
        inputs=inputs,
    )


def mean_time_to_recovery(
    recovery_durations_seconds: list[float] | tuple[float, ...] | None,
) -> FormulaResult:
    inputs = {"recovery_durations_seconds": recovery_durations_seconds}
    expression = "sum(recovery_duration_seconds) / recovered_incidents"
    if recovery_durations_seconds is None:
        return _unavailable(
            "mean_time_to_recovery",
            expression,
            inputs,
            ["recovery_durations_seconds"],
            unit="seconds",
        )
    durations = [
        _finite(item, name="recovery_duration_seconds", minimum=0.0)
        for item in recovery_durations_seconds
    ]
    if not durations:
        return _unavailable(
            "mean_time_to_recovery",
            expression,
            inputs,
            ["at least one recovered incident"],
            unit="seconds",
        )
    return FormulaResult(
        "mean_time_to_recovery",
        sum(durations) / len(durations),
        TruthLabel.MEASURED,
        expression,
        unit="seconds",
        inputs=inputs,
    )


def apdex(
    satisfied: int | None,
    tolerated: int | None,
    total: int | None,
) -> FormulaResult:
    inputs = {"satisfied": satisfied, "tolerated": tolerated, "total": total}
    expression = "(satisfied + 0.5 * tolerated) / total"
    missing = [key for key, value in inputs.items() if value is None]
    if missing:
        return _unavailable("apdex", expression, inputs, missing, unit="score")
    satisfied_count = _count(satisfied, name="satisfied")  # type: ignore[arg-type]
    tolerated_count = _count(tolerated, name="tolerated")  # type: ignore[arg-type]
    total_count = _count(total, name="total")  # type: ignore[arg-type]
    if total_count == 0:
        return _unavailable("apdex", expression, inputs, ["nonzero total"], unit="score")
    if satisfied_count + tolerated_count > total_count:
        raise ValueError("satisfied plus tolerated cannot exceed total")
    return FormulaResult(
        "apdex",
        (satisfied_count + 0.5 * tolerated_count) / total_count,
        TruthLabel.MEASURED,
        expression,
        unit="score",
        inputs=inputs,
    )


def agent_success_rate(successful_traces: int | None, total_traces: int | None) -> FormulaResult:
    base = change_failure_rate(successful_traces, total_traces)
    return FormulaResult(
        "agent_success_rate",
        base.value,
        base.truth_label,
        "successful_traces / total_traces",
        unit="fraction",
        inputs={
            "successful_traces": successful_traces,
            "total_traces": total_traces,
        },
        reason=base.reason,
    )


def cost_per_success(cost_usd: float | None, successful_outcomes: int | None) -> FormulaResult:
    inputs = {"cost_usd": cost_usd, "successful_outcomes": successful_outcomes}
    expression = "cost_usd / successful_outcomes"
    missing = [key for key, value in inputs.items() if value is None]
    if missing:
        return _unavailable("cost_per_success", expression, inputs, missing, unit="USD/success")
    cost = _finite(cost_usd, name="cost_usd", minimum=0.0)  # type: ignore[arg-type]
    successes = _count(
        successful_outcomes,
        name="successful_outcomes",  # type: ignore[arg-type]
    )
    if successes == 0:
        return _unavailable(
            "cost_per_success",
            expression,
            inputs,
            ["nonzero successful_outcomes"],
            unit="USD/success",
        )
    return FormulaResult(
        "cost_per_success",
        cost / successes,
        TruthLabel.MEASURED,
        expression,
        unit="USD/success",
        inputs=inputs,
    )


def outcome_attainment(
    current: float | None, target: float | None, direction: str | None
) -> FormulaResult:
    inputs = {"current": current, "target": target, "direction": direction}
    expression = "current / target if higher_is_better else target / current"
    missing = [key for key, value in inputs.items() if value is None]
    if missing:
        return _unavailable("outcome_attainment", expression, inputs, missing, unit="fraction")
    current_value = _finite(current, name="current", minimum=0.0)  # type: ignore[arg-type]
    target_value = _finite(target, name="target", minimum=0.0)  # type: ignore[arg-type]
    if target_value == 0:
        raise ValueError("target must be greater than zero")
    normalized_direction = str(direction).strip().lower()
    if normalized_direction == "higher":
        score = current_value / target_value
    elif normalized_direction == "lower":
        if current_value == 0:
            score = 1.0
        else:
            score = target_value / current_value
    else:
        raise ValueError("direction must be 'higher' or 'lower'")
    return FormulaResult(
        "outcome_attainment",
        min(1.0, max(0.0, score)),
        TruthLabel.MODELED,
        expression,
        unit="fraction",
        inputs=inputs,
    )


def revenue_at_risk(
    volume: int | None,
    observed_conversion_rate: float | None,
    baseline_conversion_rate: float | None,
    average_order_value_usd: float | None,
) -> FormulaResult:
    inputs = {
        "volume": volume,
        "observed_conversion_rate": observed_conversion_rate,
        "baseline_conversion_rate": baseline_conversion_rate,
        "average_order_value_usd": average_order_value_usd,
    }
    expression = (
        "volume * max(0, baseline_conversion_rate - observed_conversion_rate) "
        "* average_order_value_usd"
    )
    missing = [key for key, value in inputs.items() if value is None]
    if missing:
        return _unavailable("revenue_at_risk", expression, inputs, missing, unit="USD")
    count = _count(volume, name="volume")  # type: ignore[arg-type]
    observed = _finite(observed_conversion_rate, name="observed_conversion_rate", minimum=0.0)
    baseline = _finite(baseline_conversion_rate, name="baseline_conversion_rate", minimum=0.0)
    aov = _finite(average_order_value_usd, name="average_order_value_usd", minimum=0.0)
    if observed > 1.0 or baseline > 1.0:
        raise ValueError("conversion rates must be at most 1")
    return FormulaResult(
        "revenue_at_risk",
        count * max(0.0, baseline - observed) * aov,
        TruthLabel.MODELED,
        expression,
        unit="USD",
        inputs=inputs,
        proof_status="DECLARED_MODEL",
    )


def lambda_advisory(
    axes: Mapping[str, float | int | None | TruthValue[float]],
    weights: Mapping[str, float] | None = None,
) -> FormulaResult:
    expression = "min(0.97, exp(sum(normalized_weight * ln(axis))))"
    if not axes:
        return _unavailable(
            "lambda_advisory",
            expression,
            {"axes": {}},
            ["at least one axis"],
            proof_status="CONJECTURE_1_ADVISORY",
        )
    clean: dict[str, float] = {}
    missing: list[str] = []
    for raw_name, raw_value in axes.items():
        if not isinstance(raw_name, str):
            raise ValueError("axis names must be strings")
        name = str(raw_name).strip()
        if not name:
            raise ValueError("axis names cannot be empty")
        if name in clean or name in missing:
            raise ValueError(f"duplicate normalized axis name: {name}")
        value: float | int | None
        if isinstance(raw_value, TruthValue):
            value = raw_value.value if raw_value.available else None
        else:
            value = raw_value
        if value is None:
            missing.append(name)
            continue
        number = _finite(value, name=f"axis.{name}", minimum=0.0)
        if number > 1.0:
            raise ValueError(f"axis.{name} must be at most 1")
        clean[name] = number
    inputs: dict[str, Any] = {
        "axes": {str(name).strip(): clean.get(str(name).strip()) for name in axes}
    }
    if missing:
        return _unavailable(
            "lambda_advisory",
            expression,
            inputs,
            [f"axis.{name}" for name in missing],
            proof_status="CONJECTURE_1_ADVISORY",
        )
    if weights is None:
        normalized = {name: 1.0 / len(clean) for name in clean}
    else:
        if set(weights) != set(clean):
            raise ValueError("weights must name exactly the supplied axes")
        raw_weights = {
            name: _finite(weights[name], name=f"weight.{name}", minimum=0.0) for name in clean
        }
        total = sum(raw_weights.values())
        if total <= 0:
            raise ValueError("weight sum must be positive")
        normalized = {name: value / total for name, value in raw_weights.items()}
    inputs["weights"] = normalized
    if any(clean[name] == 0 and normalized[name] > 0 for name in clean):
        score = 0.0
    else:
        score = math.exp(sum(normalized[name] * math.log(clean[name]) for name in clean))
    return FormulaResult(
        "lambda_advisory",
        min(TRUST_CEILING, score),
        TruthLabel.MODELED,
        expression,
        unit="score",
        inputs=inputs,
        proof_status="CONJECTURE_1_ADVISORY",
    )


def journey_health(
    stage_attainment: Mapping[str, float | int | None | TruthValue[float]],
    weights: Mapping[str, float] | None = None,
) -> FormulaResult:
    advisory = lambda_advisory(stage_attainment, weights)
    return FormulaResult(
        "journey_health",
        advisory.value,
        advisory.truth_label,
        advisory.expression,
        unit="score",
        inputs=advisory.inputs,
        reason=advisory.reason,
        proof_status="DECLARED_WEIGHTED_COMPOSITION",
    )
