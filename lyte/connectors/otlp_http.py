"""A strict, bounded OTLP/HTTP JSON subset for traces, metrics, and logs.

This module intentionally implements a documented subset rather than claiming
complete OTLP compatibility. It accepts the protobuf-JSON container names
``resourceSpans``, ``resourceMetrics``, and ``resourceLogs`` and these records:

* spans with scalar attributes, identifiers, timing, kind, and status;
* gauge/sum number points and histogram points without exemplars;
* log records with scalar bodies, severity, trace correlation, and attributes.

Scalar ``AnyValue`` supports string, bool, int64 decimal string, and finite
double. Arrays, maps, bytes, events, links, exemplars, and dropped-count fields
are rejected explicitly. Limits reject the whole request; nothing is silently
truncated. Sensitive attribute values are parsed, then removed before records
or receipts are created.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from lyte.domain import ReceiptDraft, Scope, TruthLabel, sha256_text

from .base import (
    IdempotencyClaim,
    PayloadValidationError,
    require_fields,
    require_list,
    require_object,
    strict_json_loads,
    strip_sensitive_attributes,
)

_TRACE_ID = re.compile(r"^[0-9a-fA-F]{32}$")
_SPAN_ID = re.compile(r"^[0-9a-fA-F]{16}$")
_UNSIGNED_INTEGER = re.compile(r"^(?:0|[1-9][0-9]{0,39})$")
_SIGNED_INTEGER = re.compile(r"^-?(?:0|[1-9][0-9]{0,39})$")
_SPAN_KINDS = {
    0: "SPAN_KIND_UNSPECIFIED",
    1: "SPAN_KIND_INTERNAL",
    2: "SPAN_KIND_SERVER",
    3: "SPAN_KIND_CLIENT",
    4: "SPAN_KIND_PRODUCER",
    5: "SPAN_KIND_CONSUMER",
}
_STATUS_CODES = {
    0: "STATUS_CODE_UNSET",
    1: "STATUS_CODE_OK",
    2: "STATUS_CODE_ERROR",
}
_TEMPORALITIES = {
    1: "AGGREGATION_TEMPORALITY_DELTA",
    2: "AGGREGATION_TEMPORALITY_CUMULATIVE",
}

OTLP_JSON_SUBSET = {
    "signals": ("traces", "metrics", "logs"),
    "attribute_values": ("stringValue", "boolValue", "intValue", "doubleValue"),
    "metric_types": ("gauge", "sum", "histogram"),
    "unsupported": (
        "arrayValue",
        "kvlistValue",
        "bytesValue",
        "span.events",
        "span.links",
        "metric.exemplars",
        "dropped counts",
    ),
}


@dataclass(frozen=True, slots=True)
class OtlpLimits:
    max_body_bytes: int = 1_000_000
    max_resources: int = 16
    max_scopes_per_resource: int = 32
    max_records: int = 5_000
    max_attributes_per_set: int = 64
    max_buckets: int = 256
    max_text_length: int = 4_096

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("max_body_bytes", self.max_body_bytes, 10_000_000),
            ("max_resources", self.max_resources, 128),
            ("max_scopes_per_resource", self.max_scopes_per_resource, 256),
            ("max_records", self.max_records, 50_000),
            ("max_attributes_per_set", self.max_attributes_per_set, 256),
            ("max_buckets", self.max_buckets, 2_048),
            ("max_text_length", self.max_text_length, 65_536),
        ):
            if not 1 <= value <= maximum:
                raise ValueError(f"{name} must be between 1 and {maximum}")


@dataclass(frozen=True, slots=True)
class OtlpRecord:
    signal: str
    name: str
    time_unix_nano: int
    resource_attributes: Mapping[str, Any]
    scope: Mapping[str, Any]
    attributes: Mapping[str, Any]
    data: Mapping[str, Any]
    truth_label: TruthLabel = TruthLabel.REPORTED

    def __post_init__(self) -> None:
        if self.signal not in {"traces", "metrics", "logs"}:
            raise ValueError("unsupported OTLP signal")
        if self.time_unix_nano < 0:
            raise ValueError("time_unix_nano must be non-negative")
        object.__setattr__(self, "resource_attributes", dict(self.resource_attributes))
        object.__setattr__(self, "scope", dict(self.scope))
        object.__setattr__(self, "attributes", dict(self.attributes))
        object.__setattr__(self, "data", dict(self.data))
        object.__setattr__(self, "truth_label", TruthLabel(self.truth_label))

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal,
            "name": self.name,
            "time_unix_nano": self.time_unix_nano,
            "resource_attributes": dict(self.resource_attributes),
            "scope": dict(self.scope),
            "attributes": dict(self.attributes),
            "data": dict(self.data),
            "truth_label": self.truth_label.value,
        }


@dataclass(frozen=True, slots=True)
class OtlpIngestBatch:
    scope: Scope
    signal: str
    records: tuple[OtlpRecord, ...]
    idempotency: IdempotencyClaim
    receipt: ReceiptDraft
    stripped_attribute_keys: tuple[str, ...] = ()

    @property
    def record_count(self) -> int:
        return len(self.records)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.scope.to_dict(),
            "signal": self.signal,
            "truth_label": TruthLabel.REPORTED.value,
            "record_count": self.record_count,
            "stripped_attribute_count": len(self.stripped_attribute_keys),
            "idempotency": self.idempotency.to_dict(),
            "records": [record.to_dict() for record in self.records],
        }


class OtlpJsonIngestor:
    """Validate and normalize one bounded OTLP JSON export request."""

    def __init__(self, *, limits: OtlpLimits | None = None) -> None:
        self.limits = limits or OtlpLimits()
        self._records: list[OtlpRecord] = []
        self._stripped: set[str] = set()
        self._lock = threading.Lock()

    def ingest(
        self,
        body: bytes,
        *,
        scope: Scope,
        idempotency_key: str,
    ) -> OtlpIngestBatch:
        # Parsing uses reusable bounded buffers; serialize calls when an app
        # deliberately shares an ingestor instance across request workers.
        with self._lock:
            return self._ingest_locked(
                body,
                scope=scope,
                idempotency_key=idempotency_key,
            )

    def _ingest_locked(
        self,
        body: bytes,
        *,
        scope: Scope,
        idempotency_key: str,
    ) -> OtlpIngestBatch:
        decoded = strict_json_loads(
            body,
            max_bytes=self.limits.max_body_bytes,
            path="otlp",
        )
        root = require_object(decoded, path="otlp")
        top_fields = set(root)
        expected = {"resourceSpans", "resourceMetrics", "resourceLogs"}
        if len(top_fields) != 1 or not top_fields.issubset(expected):
            unknown = sorted(top_fields - expected)
            if unknown:
                raise PayloadValidationError(
                    "otlp contains unsupported top-level field(s): " + ", ".join(unknown)
                )
            raise PayloadValidationError(
                "otlp must contain exactly one of resourceSpans, resourceMetrics, resourceLogs"
            )

        self._records = []
        self._stripped = set()
        top = next(iter(top_fields))
        if top == "resourceSpans":
            signal = "traces"
            self._parse_traces(root[top])
        elif top == "resourceMetrics":
            signal = "metrics"
            self._parse_metrics(root[top])
        else:
            signal = "logs"
            self._parse_logs(root[top])
        if not self._records:
            raise PayloadValidationError("otlp export must contain at least one record")

        payload_sha = hashlib.sha256(body).hexdigest()
        idempotency = IdempotencyClaim(idempotency_key, payload_sha)
        evidence_ref = f"otlp:{signal}:{payload_sha}"
        receipt = ReceiptDraft(
            kind="ingest.otlp.accepted",
            subject_type="telemetry_signal",
            subject_id=signal,
            payload={
                **scope.to_dict(),
                "signal": signal,
                "record_count": len(self._records),
                "payload_sha256": payload_sha,
                "idempotency_key_sha256": sha256_text(idempotency_key),
                "stripped_attribute_count": len(self._stripped),
                "subset": "bounded_scalar_v1",
            },
            truth_label=TruthLabel.REPORTED,
            evidence_refs=(evidence_ref,),
        )
        return OtlpIngestBatch(
            scope=scope,
            signal=signal,
            records=tuple(self._records),
            idempotency=idempotency,
            receipt=receipt,
            stripped_attribute_keys=tuple(sorted(self._stripped)),
        )

    def _append(self, record: OtlpRecord, *, path: str) -> None:
        if len(self._records) >= self.limits.max_records:
            raise PayloadValidationError(
                f"{path} would exceed the record limit of {self.limits.max_records}"
            )
        self._records.append(record)

    def _text(self, value: Any, *, path: str, required: bool = True) -> str:
        if not isinstance(value, str):
            raise PayloadValidationError(f"{path} must be a string")
        if required and not value:
            raise PayloadValidationError(f"{path} cannot be empty")
        if len(value) > self.limits.max_text_length:
            raise PayloadValidationError(f"{path} exceeds {self.limits.max_text_length} characters")
        return value

    @staticmethod
    def _unsigned(value: Any, *, path: str) -> int:
        if not isinstance(value, str) or not _UNSIGNED_INTEGER.fullmatch(value):
            raise PayloadValidationError(f"{path} must be an unsigned decimal string")
        parsed = int(value)
        if parsed > 2**64 - 1:
            raise PayloadValidationError(f"{path} exceeds uint64")
        return parsed

    @staticmethod
    def _signed(value: Any, *, path: str) -> int:
        if not isinstance(value, str) or not _SIGNED_INTEGER.fullmatch(value):
            raise PayloadValidationError(f"{path} must be a signed decimal string")
        parsed = int(value)
        if not -(2**63) <= parsed <= 2**63 - 1:
            raise PayloadValidationError(f"{path} exceeds int64")
        return parsed

    @staticmethod
    def _number(value: Any, *, path: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PayloadValidationError(f"{path} must be a JSON number")
        parsed = float(value)
        if not math.isfinite(parsed):
            raise PayloadValidationError(f"{path} must be finite")
        return parsed

    @staticmethod
    def _hex(value: Any, pattern: re.Pattern[str], *, path: str) -> str:
        if not isinstance(value, str) or not pattern.fullmatch(value) or int(value, 16) == 0:
            raise PayloadValidationError(f"{path} has an invalid hexadecimal identifier")
        return value.lower()

    def _any_value(self, value: Any, *, path: str) -> Any:
        item = require_object(value, path=path)
        allowed = frozenset({"stringValue", "boolValue", "intValue", "doubleValue"})
        require_fields(item, path=path, allowed=allowed)
        present = [name for name in allowed if name in item]
        if len(present) != 1:
            raise PayloadValidationError(f"{path} must contain exactly one supported scalar value")
        field = present[0]
        raw = item[field]
        if field == "stringValue":
            return self._text(raw, path=f"{path}.{field}", required=False)
        if field == "boolValue":
            if not isinstance(raw, bool):
                raise PayloadValidationError(f"{path}.{field} must be a boolean")
            return raw
        if field == "intValue":
            return self._signed(raw, path=f"{path}.{field}")
        return self._number(raw, path=f"{path}.{field}")

    def _attributes(self, value: Any, *, path: str) -> dict[str, Any]:
        if value is None:
            return {}
        raw_items = require_list(
            value,
            path=path,
            maximum=self.limits.max_attributes_per_set,
        )
        parsed: dict[str, Any] = {}
        for index, raw_item in enumerate(raw_items):
            item_path = f"{path}[{index}]"
            item = require_object(raw_item, path=item_path)
            require_fields(
                item,
                path=item_path,
                allowed=frozenset({"key", "value"}),
                required=frozenset({"key", "value"}),
            )
            key = self._text(item["key"], path=f"{item_path}.key")
            if key in parsed:
                raise PayloadValidationError(f"{path} contains duplicate attribute key {key!r}")
            parsed[key] = self._any_value(item["value"], path=f"{item_path}.value")
        clean, stripped = strip_sensitive_attributes(parsed)
        self._stripped.update(stripped)
        return clean

    def _resource(self, value: Any, *, path: str) -> dict[str, Any]:
        if value is None:
            return {}
        resource = require_object(value, path=path)
        require_fields(resource, path=path, allowed=frozenset({"attributes"}))
        return self._attributes(resource.get("attributes"), path=f"{path}.attributes")

    def _scope(self, value: Any, *, path: str) -> dict[str, Any]:
        if value is None:
            return {}
        scope = require_object(value, path=path)
        require_fields(
            scope,
            path=path,
            allowed=frozenset({"name", "version", "attributes"}),
        )
        normalized: dict[str, Any] = {}
        if "name" in scope:
            normalized["name"] = self._text(scope["name"], path=f"{path}.name")
        if "version" in scope:
            normalized["version"] = self._text(scope["version"], path=f"{path}.version")
        attributes = self._attributes(scope.get("attributes"), path=f"{path}.attributes")
        if attributes:
            normalized["attributes"] = attributes
        return normalized

    @staticmethod
    def _enum(
        value: Any,
        choices: Mapping[int, str],
        *,
        path: str,
        default: int,
    ) -> str:
        if value is None:
            return choices[default]
        if isinstance(value, bool):
            raise PayloadValidationError(f"{path} has an unsupported enum value")
        if isinstance(value, int) and value in choices:
            return choices[value]
        if isinstance(value, str) and value in choices.values():
            return value
        raise PayloadValidationError(f"{path} has an unsupported enum value")

    def _parse_traces(self, value: Any) -> None:
        resources = require_list(
            value, path="otlp.resourceSpans", maximum=self.limits.max_resources
        )
        for resource_index, raw_group in enumerate(resources):
            path = f"otlp.resourceSpans[{resource_index}]"
            group = require_object(raw_group, path=path)
            require_fields(
                group,
                path=path,
                allowed=frozenset({"resource", "scopeSpans", "schemaUrl"}),
                required=frozenset({"scopeSpans"}),
            )
            resource = self._resource(group.get("resource"), path=f"{path}.resource")
            if "schemaUrl" in group:
                self._text(group["schemaUrl"], path=f"{path}.schemaUrl")
            scopes = require_list(
                group["scopeSpans"],
                path=f"{path}.scopeSpans",
                maximum=self.limits.max_scopes_per_resource,
            )
            for scope_index, raw_scope_group in enumerate(scopes):
                scope_path = f"{path}.scopeSpans[{scope_index}]"
                scope_group = require_object(raw_scope_group, path=scope_path)
                require_fields(
                    scope_group,
                    path=scope_path,
                    allowed=frozenset({"scope", "spans", "schemaUrl"}),
                    required=frozenset({"spans"}),
                )
                scope = self._scope(scope_group.get("scope"), path=f"{scope_path}.scope")
                if "schemaUrl" in scope_group:
                    self._text(scope_group["schemaUrl"], path=f"{scope_path}.schemaUrl")
                spans = require_list(
                    scope_group["spans"],
                    path=f"{scope_path}.spans",
                    maximum=self.limits.max_records,
                )
                for span_index, raw_span in enumerate(spans):
                    self._parse_span(
                        raw_span,
                        path=f"{scope_path}.spans[{span_index}]",
                        resource=resource,
                        scope=scope,
                    )

    def _parse_span(
        self,
        value: Any,
        *,
        path: str,
        resource: Mapping[str, Any],
        scope: Mapping[str, Any],
    ) -> None:
        span = require_object(value, path=path)
        require_fields(
            span,
            path=path,
            allowed=frozenset(
                {
                    "traceId",
                    "spanId",
                    "parentSpanId",
                    "traceState",
                    "name",
                    "kind",
                    "startTimeUnixNano",
                    "endTimeUnixNano",
                    "attributes",
                    "status",
                }
            ),
            required=frozenset(
                {"traceId", "spanId", "name", "startTimeUnixNano", "endTimeUnixNano"}
            ),
        )
        trace_id = self._hex(span["traceId"], _TRACE_ID, path=f"{path}.traceId")
        span_id = self._hex(span["spanId"], _SPAN_ID, path=f"{path}.spanId")
        parent_id = None
        if "parentSpanId" in span and span["parentSpanId"] != "":
            parent_id = self._hex(span["parentSpanId"], _SPAN_ID, path=f"{path}.parentSpanId")
        start = self._unsigned(span["startTimeUnixNano"], path=f"{path}.startTimeUnixNano")
        end = self._unsigned(span["endTimeUnixNano"], path=f"{path}.endTimeUnixNano")
        if end < start:
            raise PayloadValidationError(f"{path}.endTimeUnixNano precedes startTimeUnixNano")
        status_value = span.get("status", {})
        status = require_object(status_value, path=f"{path}.status")
        require_fields(
            status,
            path=f"{path}.status",
            allowed=frozenset({"code", "message"}),
        )
        status_data = {
            "code": self._enum(
                status.get("code"), _STATUS_CODES, path=f"{path}.status.code", default=0
            )
        }
        if "message" in status:
            status_data["message"] = self._text(
                status["message"], path=f"{path}.status.message", required=False
            )
        data: dict[str, Any] = {
            "trace_id": trace_id,
            "span_id": span_id,
            "parent_span_id": parent_id,
            "start_time_unix_nano": start,
            "end_time_unix_nano": end,
            "duration_nano": end - start,
            "kind": self._enum(span.get("kind"), _SPAN_KINDS, path=f"{path}.kind", default=0),
            "status": status_data,
        }
        if "traceState" in span:
            data["trace_state"] = self._text(
                span["traceState"], path=f"{path}.traceState", required=False
            )
        self._append(
            OtlpRecord(
                signal="traces",
                name=self._text(span["name"], path=f"{path}.name"),
                time_unix_nano=end,
                resource_attributes=resource,
                scope=scope,
                attributes=self._attributes(span.get("attributes"), path=f"{path}.attributes"),
                data=data,
            ),
            path=path,
        )

    def _parse_metrics(self, value: Any) -> None:
        resources = require_list(
            value, path="otlp.resourceMetrics", maximum=self.limits.max_resources
        )
        for resource_index, raw_group in enumerate(resources):
            path = f"otlp.resourceMetrics[{resource_index}]"
            group = require_object(raw_group, path=path)
            require_fields(
                group,
                path=path,
                allowed=frozenset({"resource", "scopeMetrics", "schemaUrl"}),
                required=frozenset({"scopeMetrics"}),
            )
            resource = self._resource(group.get("resource"), path=f"{path}.resource")
            if "schemaUrl" in group:
                self._text(group["schemaUrl"], path=f"{path}.schemaUrl")
            scopes = require_list(
                group["scopeMetrics"],
                path=f"{path}.scopeMetrics",
                maximum=self.limits.max_scopes_per_resource,
            )
            for scope_index, raw_scope_group in enumerate(scopes):
                scope_path = f"{path}.scopeMetrics[{scope_index}]"
                scope_group = require_object(raw_scope_group, path=scope_path)
                require_fields(
                    scope_group,
                    path=scope_path,
                    allowed=frozenset({"scope", "metrics", "schemaUrl"}),
                    required=frozenset({"metrics"}),
                )
                scope = self._scope(scope_group.get("scope"), path=f"{scope_path}.scope")
                if "schemaUrl" in scope_group:
                    self._text(scope_group["schemaUrl"], path=f"{scope_path}.schemaUrl")
                metrics = require_list(
                    scope_group["metrics"],
                    path=f"{scope_path}.metrics",
                    maximum=self.limits.max_records,
                )
                for metric_index, metric in enumerate(metrics):
                    self._parse_metric(
                        metric,
                        path=f"{scope_path}.metrics[{metric_index}]",
                        resource=resource,
                        scope=scope,
                    )

    def _parse_metric(
        self,
        value: Any,
        *,
        path: str,
        resource: Mapping[str, Any],
        scope: Mapping[str, Any],
    ) -> None:
        metric = require_object(value, path=path)
        require_fields(
            metric,
            path=path,
            allowed=frozenset({"name", "description", "unit", "gauge", "sum", "histogram"}),
            required=frozenset({"name"}),
        )
        kinds = [kind for kind in ("gauge", "sum", "histogram") if kind in metric]
        if len(kinds) != 1:
            raise PayloadValidationError(f"{path} must contain exactly one supported metric type")
        name = self._text(metric["name"], path=f"{path}.name")
        metadata: dict[str, Any] = {}
        for field in ("description", "unit"):
            if field in metric:
                metadata[field] = self._text(metric[field], path=f"{path}.{field}", required=False)
        kind = kinds[0]
        body = require_object(metric[kind], path=f"{path}.{kind}")
        if kind == "gauge":
            require_fields(
                body,
                path=f"{path}.{kind}",
                allowed=frozenset({"dataPoints"}),
                required=frozenset({"dataPoints"}),
            )
            kind_metadata = metadata
        else:
            allowed = {"dataPoints", "aggregationTemporality"}
            required = {"dataPoints", "aggregationTemporality"}
            if kind == "sum":
                allowed.add("isMonotonic")
                required.add("isMonotonic")
            require_fields(
                body,
                path=f"{path}.{kind}",
                allowed=frozenset(allowed),
                required=frozenset(required),
            )
            temporality = self._enum(
                body["aggregationTemporality"],
                _TEMPORALITIES,
                path=f"{path}.{kind}.aggregationTemporality",
                default=1,
            )
            kind_metadata = {**metadata, "aggregation_temporality": temporality}
            if kind == "sum":
                if not isinstance(body["isMonotonic"], bool):
                    raise PayloadValidationError(f"{path}.sum.isMonotonic must be a boolean")
                kind_metadata["is_monotonic"] = body["isMonotonic"]
        points = require_list(
            body["dataPoints"],
            path=f"{path}.{kind}.dataPoints",
            maximum=self.limits.max_records,
        )
        for point_index, point in enumerate(points):
            point_path = f"{path}.{kind}.dataPoints[{point_index}]"
            if kind == "histogram":
                normalized = self._histogram_point(point, path=point_path)
            else:
                normalized = self._number_point(point, path=point_path)
            attributes = normalized.pop("attributes")
            time_unix_nano = normalized.pop("time_unix_nano")
            self._append(
                OtlpRecord(
                    signal="metrics",
                    name=name,
                    time_unix_nano=time_unix_nano,
                    resource_attributes=resource,
                    scope=scope,
                    attributes=attributes,
                    data={"metric_type": kind, **kind_metadata, **normalized},
                ),
                path=point_path,
            )

    def _number_point(self, value: Any, *, path: str) -> dict[str, Any]:
        point = require_object(value, path=path)
        require_fields(
            point,
            path=path,
            allowed=frozenset(
                {"attributes", "startTimeUnixNano", "timeUnixNano", "asDouble", "asInt", "flags"}
            ),
            required=frozenset({"timeUnixNano"}),
        )
        representations = [field for field in ("asDouble", "asInt") if field in point]
        if len(representations) != 1:
            raise PayloadValidationError(f"{path} must contain exactly one of asDouble or asInt")
        representation = representations[0]
        number = (
            self._number(point[representation], path=f"{path}.{representation}")
            if representation == "asDouble"
            else self._signed(point[representation], path=f"{path}.{representation}")
        )
        result: dict[str, Any] = {
            "attributes": self._attributes(point.get("attributes"), path=f"{path}.attributes"),
            "time_unix_nano": self._unsigned(point["timeUnixNano"], path=f"{path}.timeUnixNano"),
            "value": number,
            "value_encoding": representation,
        }
        if "startTimeUnixNano" in point:
            result["start_time_unix_nano"] = self._unsigned(
                point["startTimeUnixNano"], path=f"{path}.startTimeUnixNano"
            )
            if result["start_time_unix_nano"] > result["time_unix_nano"]:
                raise PayloadValidationError(f"{path}.startTimeUnixNano exceeds timeUnixNano")
        if "flags" in point:
            if isinstance(point["flags"], bool) or not isinstance(point["flags"], int):
                raise PayloadValidationError(f"{path}.flags must be an integer")
            result["flags"] = point["flags"]
        return result

    def _histogram_point(self, value: Any, *, path: str) -> dict[str, Any]:
        point = require_object(value, path=path)
        require_fields(
            point,
            path=path,
            allowed=frozenset(
                {
                    "attributes",
                    "startTimeUnixNano",
                    "timeUnixNano",
                    "count",
                    "sum",
                    "bucketCounts",
                    "explicitBounds",
                    "min",
                    "max",
                    "flags",
                }
            ),
            required=frozenset({"timeUnixNano", "count", "bucketCounts", "explicitBounds"}),
        )
        counts_raw = require_list(
            point["bucketCounts"], path=f"{path}.bucketCounts", maximum=self.limits.max_buckets
        )
        bounds_raw = require_list(
            point["explicitBounds"], path=f"{path}.explicitBounds", maximum=self.limits.max_buckets
        )
        counts = [
            self._unsigned(raw, path=f"{path}.bucketCounts[{index}]")
            for index, raw in enumerate(counts_raw)
        ]
        bounds = [
            self._number(raw, path=f"{path}.explicitBounds[{index}]")
            for index, raw in enumerate(bounds_raw)
        ]
        if len(counts) != len(bounds) + 1:
            raise PayloadValidationError(f"{path}.bucketCounts must have one more item than bounds")
        if any(right <= left for left, right in zip(bounds, bounds[1:], strict=False)):
            raise PayloadValidationError(f"{path}.explicitBounds must be strictly increasing")
        count = self._unsigned(point["count"], path=f"{path}.count")
        if sum(counts) != count:
            raise PayloadValidationError(f"{path}.bucketCounts do not sum to count")
        result: dict[str, Any] = {
            "attributes": self._attributes(point.get("attributes"), path=f"{path}.attributes"),
            "time_unix_nano": self._unsigned(point["timeUnixNano"], path=f"{path}.timeUnixNano"),
            "count": count,
            "bucket_counts": counts,
            "explicit_bounds": bounds,
        }
        if "startTimeUnixNano" in point:
            result["start_time_unix_nano"] = self._unsigned(
                point["startTimeUnixNano"], path=f"{path}.startTimeUnixNano"
            )
            if result["start_time_unix_nano"] > result["time_unix_nano"]:
                raise PayloadValidationError(f"{path}.startTimeUnixNano exceeds timeUnixNano")
        for field in ("sum", "min", "max"):
            if field in point:
                result[field] = self._number(point[field], path=f"{path}.{field}")
        if "min" in result and "max" in result and result["min"] > result["max"]:
            raise PayloadValidationError(f"{path}.min exceeds max")
        if "flags" in point:
            if isinstance(point["flags"], bool) or not isinstance(point["flags"], int):
                raise PayloadValidationError(f"{path}.flags must be an integer")
            result["flags"] = point["flags"]
        return result

    def _parse_logs(self, value: Any) -> None:
        resources = require_list(value, path="otlp.resourceLogs", maximum=self.limits.max_resources)
        for resource_index, raw_group in enumerate(resources):
            path = f"otlp.resourceLogs[{resource_index}]"
            group = require_object(raw_group, path=path)
            require_fields(
                group,
                path=path,
                allowed=frozenset({"resource", "scopeLogs", "schemaUrl"}),
                required=frozenset({"scopeLogs"}),
            )
            resource = self._resource(group.get("resource"), path=f"{path}.resource")
            if "schemaUrl" in group:
                self._text(group["schemaUrl"], path=f"{path}.schemaUrl")
            scopes = require_list(
                group["scopeLogs"],
                path=f"{path}.scopeLogs",
                maximum=self.limits.max_scopes_per_resource,
            )
            for scope_index, raw_scope_group in enumerate(scopes):
                scope_path = f"{path}.scopeLogs[{scope_index}]"
                scope_group = require_object(raw_scope_group, path=scope_path)
                require_fields(
                    scope_group,
                    path=scope_path,
                    allowed=frozenset({"scope", "logRecords", "schemaUrl"}),
                    required=frozenset({"logRecords"}),
                )
                scope = self._scope(scope_group.get("scope"), path=f"{scope_path}.scope")
                if "schemaUrl" in scope_group:
                    self._text(scope_group["schemaUrl"], path=f"{scope_path}.schemaUrl")
                records = require_list(
                    scope_group["logRecords"],
                    path=f"{scope_path}.logRecords",
                    maximum=self.limits.max_records,
                )
                for record_index, record in enumerate(records):
                    self._parse_log_record(
                        record,
                        path=f"{scope_path}.logRecords[{record_index}]",
                        resource=resource,
                        scope=scope,
                    )

    def _parse_log_record(
        self,
        value: Any,
        *,
        path: str,
        resource: Mapping[str, Any],
        scope: Mapping[str, Any],
    ) -> None:
        record = require_object(value, path=path)
        require_fields(
            record,
            path=path,
            allowed=frozenset(
                {
                    "timeUnixNano",
                    "observedTimeUnixNano",
                    "severityNumber",
                    "severityText",
                    "body",
                    "attributes",
                    "flags",
                    "traceId",
                    "spanId",
                }
            ),
            required=frozenset({"timeUnixNano", "body"}),
        )
        timestamp = self._unsigned(record["timeUnixNano"], path=f"{path}.timeUnixNano")
        data: dict[str, Any] = {"body": self._any_value(record["body"], path=f"{path}.body")}
        if "observedTimeUnixNano" in record:
            data["observed_time_unix_nano"] = self._unsigned(
                record["observedTimeUnixNano"], path=f"{path}.observedTimeUnixNano"
            )
        if "severityNumber" in record:
            severity = record["severityNumber"]
            if (
                isinstance(severity, bool)
                or not isinstance(severity, int)
                or not 0 <= severity <= 24
            ):
                raise PayloadValidationError(
                    f"{path}.severityNumber must be an integer from 0 to 24"
                )
            data["severity_number"] = severity
        if "severityText" in record:
            data["severity_text"] = self._text(
                record["severityText"], path=f"{path}.severityText", required=False
            )
        if "flags" in record:
            if isinstance(record["flags"], bool) or not isinstance(record["flags"], int):
                raise PayloadValidationError(f"{path}.flags must be an integer")
            data["flags"] = record["flags"]
        if "traceId" in record:
            data["trace_id"] = self._hex(record["traceId"], _TRACE_ID, path=f"{path}.traceId")
        if "spanId" in record:
            if "traceId" not in record:
                raise PayloadValidationError(f"{path}.spanId requires traceId")
            data["span_id"] = self._hex(record["spanId"], _SPAN_ID, path=f"{path}.spanId")
        name = str(data.get("severity_text") or "log")
        self._append(
            OtlpRecord(
                signal="logs",
                name=name,
                time_unix_nano=timestamp,
                resource_attributes=resource,
                scope=scope,
                attributes=self._attributes(record.get("attributes"), path=f"{path}.attributes"),
                data=data,
            ),
            path=path,
        )


# Common spelling retained for callers that use the protocol acronym as a word.
OTLPJSONIngestor = OtlpJsonIngestor

__all__ = [
    "OTLPJSONIngestor",
    "OTLP_JSON_SUBSET",
    "OtlpIngestBatch",
    "OtlpJsonIngestor",
    "OtlpLimits",
    "OtlpRecord",
]
