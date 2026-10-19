"""Reconcile numeric facts from multiple sources without destroying provenance.

This module deliberately does not update any source record.  It produces a
derived comparison view which callers may use in prompts or user-facing
answers while retaining every original value and its source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import re
from typing import Any, Iterable, Mapping, Sequence


RECONCILIATION_STATUSES = frozenset({"exact", "micro_variation", "conflict", "incomparable"})

SOURCE_QUALITY = {
    "official_observation": 4,
    "official_post_event": 3,
    "monograph": 2,
    "news": 1,
}

DEFAULT_TOLERANCES = {
    # SI units.  Aliases are normalized before this table is consulted.
    "storm_surge": Decimal("0.02"),
    "highest_water_level": Decimal("0.02"),
    "significant_wave_height": Decimal("0.05"),
    "max_wave_height": Decimal("0.05"),
    "min_pressure": Decimal("1"),
    "max_wind": Decimal("0.5"),
}

_METRIC_ALIASES = {
    "max_surge": "storm_surge",
    "max_surge_m": "storm_surge",
    "maximum_storm_surge": "storm_surge",
    "storm_surge_max": "storm_surge",
    "最大增水": "storm_surge",
    "max_water_level": "highest_water_level",
    "max_water_level_m": "highest_water_level",
    "highest_tide_level": "highest_water_level",
    "最高潮位": "highest_water_level",
    "minimum_pressure": "min_pressure",
    "min_pressure_hpa": "min_pressure",
    "maximum_wind": "max_wind",
    "max_wind_ms": "max_wind",
}

_UNIT_ALIASES = {
    "米": "m",
    "metre": "m",
    "meter": "m",
    "meters": "m",
    "厘米": "cm",
    "百帕": "hpa",
    "hpa": "hpa",
    "m/s": "m/s",
    "米/秒": "m/s",
    "kt": "kt",
    "knot": "kt",
    "knots": "kt",
}

_SOURCE_TYPE_ALIASES = {
    "official_observation": "official_observation",
    "historical_record": "official_observation",
    "官方观测": "official_observation",
    "实测记录": "official_observation",
    "official_post_event": "official_post_event",
    "post_event_report": "official_post_event",
    "浙江台风影响总结报告": "official_post_event",
    "官方灾后总结": "official_post_event",
    "monograph": "monograph",
    "专著": "monograph",
    "news": "news",
    "新闻": "news",
}

_UNKNOWN_MARKERS = frozenset({"", "unknown", "unk", "未知", "不明", "未注明", "null", "none", "-"})


def _clean_dimension(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip()).casefold()


def _normalized_metric(value: Any) -> str:
    cleaned = _clean_dimension(value)
    return _METRIC_ALIASES.get(cleaned, cleaned)


def _normalized_unit(value: Any) -> str:
    cleaned = _clean_dimension(value)
    return _UNIT_ALIASES.get(cleaned, cleaned)


def _normalized_station(value: Any) -> str:
    cleaned = _clean_dimension(value)
    if cleaned in _UNKNOWN_MARKERS or cleaned in {"站点未知", "报告未注明"}:
        return ""
    return re.sub(r"(?:国家基本站|海洋站|验潮站|潮位站|站)$", "", cleaned).strip()


def _normalized_source_type(value: Any) -> str:
    cleaned = _clean_dimension(value)
    return _SOURCE_TYPE_ALIASES.get(cleaned, cleaned or "unknown")


def _is_unknown(value: str) -> bool:
    return _clean_dimension(value) in _UNKNOWN_MARKERS


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, ValueError) as exc:
        raise ValueError(f"事实数值无效: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"事实数值必须是有限数: {value!r}")
    return result


def _decimal_places(value: Any, parsed: Decimal) -> int:
    """Return source precision rather than binary floating-point precision."""
    if isinstance(value, str):
        text = value.strip().lower()
        if "e" not in text and "." in text:
            return len(text.rsplit(".", 1)[1])
        try:
            return max(0, -Decimal(text).as_tuple().exponent)
        except InvalidOperation:
            pass
    return max(0, -parsed.as_tuple().exponent)


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


@dataclass(frozen=True)
class NumericFact:
    """One immutable source assertion.

    ``raw`` is a copied source mapping.  Reconciliation never changes it.
    """

    sid: str
    metric: str
    value: Decimal
    unit: str
    station: str = ""
    time_scope: str = ""
    datum: str = ""
    observation_kind: str = ""
    source_type: str = "unknown"
    source_id: str = ""
    source_title: str = ""
    source_locator: str = ""
    precision: int = 0
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def from_mapping(cls, fact: Mapping[str, Any]) -> "NumericFact":
        if not isinstance(fact, Mapping):
            raise TypeError("事实必须是映射对象")
        raw_value = fact.get("value", fact.get("value_si"))
        if raw_value is None:
            raise ValueError("事实缺少 value 或 value_si")
        value = _decimal(raw_value)
        metric = fact.get("metric", fact.get("metric_type", fact.get("field_name", "")))
        unit = fact.get("unit", fact.get("unit_si", fact.get("source_unit", "")))
        return cls(
            sid=str(fact.get("sid") or "").strip(),
            metric=_normalized_metric(metric),
            value=value,
            unit=_normalized_unit(unit),
            station=str(fact.get("station", fact.get("station_name", "")) or "").strip(),
            time_scope=str(fact.get("time_scope", fact.get("observed_time_text", "")) or "").strip(),
            datum=str(fact.get("datum", fact.get("vertical_datum", "")) or "").strip(),
            observation_kind=str(
                fact.get("observation_kind", fact.get("value_kind", fact.get("measurement_type", ""))) or ""
            ).strip(),
            source_type=_normalized_source_type(fact.get("source_type") or "unknown"),
            source_id=str(fact.get("source_id", fact.get("report_id", "")) or "").strip(),
            source_title=str(fact.get("source_title", fact.get("document_title", "")) or "").strip(),
            source_locator=str(fact.get("source_locator", fact.get("source_excerpt", "")) or "").strip(),
            precision=_decimal_places(raw_value, value),
            raw=dict(fact),
        )

    @property
    def comparable_key(self) -> tuple[str, str, str, str, str, str, str]:
        return (
            _clean_dimension(self.sid),
            _normalized_metric(self.metric),
            _normalized_station(self.station),
            _normalized_unit(self.unit),
            _clean_dimension(self.time_scope),
            _clean_dimension(self.datum),
            _clean_dimension(self.observation_kind),
        )

    @property
    def source_quality(self) -> int:
        return SOURCE_QUALITY.get(_clean_dimension(self.source_type), 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sid": self.sid,
            "metric": self.metric,
            "value": float(self.value),
            "value_text": format(self.value, "f"),
            "unit": self.unit,
            "station": self.station or None,
            "time_scope": self.time_scope or None,
            "datum": self.datum or None,
            "observation_kind": self.observation_kind or None,
            "source_type": self.source_type,
            "source_quality": self.source_quality,
            "source_id": self.source_id or None,
            "source_title": self.source_title or None,
            "source_locator": self.source_locator or None,
            "precision": self.precision,
            "raw": _json_value(dict(self.raw)),
        }


@dataclass(frozen=True)
class ReconciliationPolicy:
    tolerances: Mapping[str, Decimal] = field(default_factory=lambda: dict(DEFAULT_TOLERANCES))
    # A missing vertical datum makes water-related values unsafe to average.
    datum_sensitive_metrics: frozenset[str] = frozenset({"storm_surge", "highest_water_level"})

    def tolerance_for(self, metric: str) -> Decimal:
        normalized = _normalized_metric(metric)
        for configured_metric, value in self.tolerances.items():
            if _normalized_metric(configured_metric) == normalized:
                return _decimal(value)
        return Decimal("0")


@dataclass(frozen=True)
class ReconciledFact:
    status: str
    facts: tuple[NumericFact, ...]
    tolerance: Decimal | None
    minimum: Decimal | None
    maximum: Decimal | None
    representative: Decimal | None
    representative_precision: int | None
    preferred_source_index: int | None
    reason: str
    differing_dimensions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in RECONCILIATION_STATUSES:
            raise ValueError(f"未知归并状态: {self.status}")

    @property
    def representative_text(self) -> str | None:
        if self.representative is None or self.representative_precision is None:
            return None
        return f"{self.representative:.{self.representative_precision}f}"

    @property
    def preferred_fact(self) -> NumericFact | None:
        if self.preferred_source_index is None:
            return None
        return self.facts[self.preferred_source_index]

    def to_dict(self, *, include_raw: bool = True) -> dict[str, Any]:
        first = self.facts[0] if self.facts else None
        sources = []
        for fact in self.facts:
            item = fact.to_dict()
            if not include_raw:
                item.pop("raw", None)
            sources.append(item)
        return {
            "status": self.status,
            "sid": first.sid if first else None,
            "metric": first.metric if first else None,
            "station": first.station or None if first else None,
            "unit": first.unit if first else None,
            "time_scope": first.time_scope or None if first else None,
            "datum": first.datum or None if first else None,
            "observation_kind": first.observation_kind or None if first else None,
            "tolerance": float(self.tolerance) if self.tolerance is not None else None,
            "minimum": float(self.minimum) if self.minimum is not None else None,
            "maximum": float(self.maximum) if self.maximum is not None else None,
            "representative": float(self.representative) if self.representative is not None else None,
            "representative_text": self.representative_text,
            "representative_is_rounded": self.status == "micro_variation",
            "preferred_source_index": self.preferred_source_index,
            "reason": self.reason,
            "differing_dimensions": list(self.differing_dimensions),
            "sources": sources,
        }


def _preferred_index(facts: Sequence[NumericFact]) -> int | None:
    if not facts:
        return None
    return max(range(len(facts)), key=lambda index: (facts[index].source_quality, -index))


def _differing_dimensions(facts: Sequence[NumericFact]) -> tuple[str, ...]:
    dimensions = ("sid", "metric", "station", "unit", "time_scope", "datum", "observation_kind")
    keys = [fact.comparable_key for fact in facts]
    return tuple(name for index, name in enumerate(dimensions) if len({key[index] for key in keys}) > 1)


def reconcile_numeric_facts(
    facts: Iterable[NumericFact | Mapping[str, Any]],
    *,
    policy: ReconciliationPolicy | None = None,
) -> ReconciledFact:
    """Compare facts asserted to describe one metric under one exact scope.

    Different SID/metric/station/unit/time/datum/observation-kind values are
    never combined.  Missing datum for datum-sensitive metrics is also marked
    incomparable, even if all sources omit it.
    """

    selected = tuple(item if isinstance(item, NumericFact) else NumericFact.from_mapping(item) for item in facts)
    if not selected:
        raise ValueError("至少需要一条事实")
    policy = policy or ReconciliationPolicy()
    preferred = _preferred_index(selected)
    different = _differing_dimensions(selected)
    if different:
        return ReconciledFact(
            status="incomparable",
            facts=selected,
            tolerance=None,
            minimum=None,
            maximum=None,
            representative=None,
            representative_precision=None,
            preferred_source_index=preferred,
            reason="口径字段不一致，不进行合并或平均。",
            differing_dimensions=different,
        )

    metric = selected[0].metric
    if (
        len(selected) > 1
        and _normalized_metric(metric) in policy.datum_sensitive_metrics
        and _is_unknown(selected[0].datum)
    ):
        return ReconciledFact(
            status="incomparable",
            facts=selected,
            tolerance=None,
            minimum=None,
            maximum=None,
            representative=None,
            representative_precision=None,
            preferred_source_index=preferred,
            reason="高程或潮位基准未知，不进行平均。",
            differing_dimensions=("datum",),
        )

    values = [fact.value for fact in selected]
    minimum, maximum = min(values), max(values)
    tolerance = policy.tolerance_for(metric)
    spread = maximum - minimum
    precision = min(fact.precision for fact in selected)
    quantum = Decimal(1).scaleb(-precision)

    if spread == 0:
        representative = values[preferred or 0].quantize(quantum, rounding=ROUND_HALF_UP)
        status = "exact"
        reason = "各来源在相同口径下数值一致。"
    elif spread <= tolerance:
        mean = sum(values, Decimal("0")) / Decimal(len(values))
        representative = mean.quantize(quantum, rounding=ROUND_HALF_UP)
        status = "micro_variation"
        reason = "差异在配置容差内；展示舍入后的约值和范围，原始来源值全部保留。"
    else:
        representative = None
        precision = None
        status = "conflict"
        reason = "差异超过配置容差，不合并数值。"

    return ReconciledFact(
        status=status,
        facts=selected,
        tolerance=tolerance,
        minimum=minimum,
        maximum=maximum,
        representative=representative,
        representative_precision=precision,
        preferred_source_index=preferred,
        reason=reason,
    )


def serialize_reconciliation_for_prompt(
    result: ReconciledFact | Iterable[ReconciledFact],
    *,
    indent: int | None = None,
) -> str:
    """Return lossless JSON for an LLM prompt, including source provenance."""

    payload: Any
    if isinstance(result, ReconciledFact):
        payload = result.to_dict(include_raw=True)
    else:
        payload = [item.to_dict(include_raw=True) for item in result]
    return json.dumps(payload, ensure_ascii=False, indent=indent, sort_keys=True)


def format_reconciliation_for_answer(result: ReconciledFact, *, metric_label: str | None = None) -> str:
    """Create a conservative Chinese answer sentence from a derived result."""

    if not result.facts:
        return "没有可用事实。"
    fact = result.facts[0]
    label = metric_label or fact.metric
    station = f"{fact.station}站" if fact.station else ""
    unit = fact.unit
    ordered_sources = sorted(
        enumerate(result.facts),
        key=lambda pair: (-pair[1].source_quality, pair[0]),
    )
    source_values = "、".join(
        f"{item.source_title or item.source_id or item.source_type}记录为{format(item.value, 'f')}{item.unit}"
        for _, item in ordered_sources
    )
    if result.status == "exact":
        return f"{station}{label}为{result.representative_text}{unit}；{source_values}。"
    if result.status == "micro_variation":
        return (
            f"{station}{label}约{result.representative_text}{unit}。{source_values}，"
            f"差异为{format((result.maximum or Decimal(0)) - (result.minimum or Decimal(0)), 'f')}{unit}，"
            "属于容差内的微小差异；原始数值及来源均予以保留。"
        )
    if result.status == "conflict":
        return f"{station}{label}存在超出容差的来源冲突：{source_values}；暂不合并。"
    differing = "、".join(result.differing_dimensions)
    return f"{station}{label}的资料口径不可直接比较（{differing}）：{source_values}；暂并列保留。"


__all__ = [
    "DEFAULT_TOLERANCES",
    "RECONCILIATION_STATUSES",
    "SOURCE_QUALITY",
    "NumericFact",
    "ReconciledFact",
    "ReconciliationPolicy",
    "format_reconciliation_for_answer",
    "reconcile_numeric_facts",
    "serialize_reconciliation_for_prompt",
]
