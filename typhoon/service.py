"""Deterministic strength-and-track analogue retrieval."""

from __future__ import annotations

from typing import Iterable

from .config import (
    INTENSITY_COMPONENT_WEIGHTS,
    MAX_PATH_CANDIDATES,
    NORMALIZATION_SCALES,
    RELAXED_THRESHOLDS,
    SIMILARITY_WEIGHTS,
    STRICT_THRESHOLDS,
    TRACK_COMPONENT_WEIGHTS,
)
from .geometry import (
    circular_difference_deg,
    closest_zhejiang_index,
    compute_motion,
    distance_to_zhejiang_km,
    finite_numbers,
    select_approach_segment,
    track_dtw_km,
)
from .knowledge_base import TyphoonKnowledgeBase
from .report_knowledge import TyphoonReportRepository
from .repositories.contracts import (
    HistoricalTyphoonRepository,
    StormSurgeDataRepository,
    TyphoonReportDataRepository,
)
from .schema import normalize_current_typhoon
from .storm_surge import StormSurgeRepository
from .units import format_wind_from_knots
from .presentation import zhejiang_relation_text


def wind_category(wind_kt: float | None) -> tuple[int | None, str]:
    if wind_kt is None:
        return None, "未知"
    wind = float(wind_kt)
    if wind < 34:
        return 0, "热带低压"
    if wind < 48:
        return 1, "热带风暴"
    if wind < 64:
        return 2, "强热带风暴"
    if wind < 82:
        return 3, "台风"
    if wind < 100:
        return 4, "强台风"
    return 5, "超强台风"


def _clamp_distance(value: float | None, scale: float) -> float | None:
    if value is None:
        return None
    return min(1.0, abs(float(value)) / scale)


def _weighted_available(components: dict[str, float | None], weights: dict[str, float]) -> float:
    available = [(key, value) for key, value in components.items() if value is not None]
    if not available:
        return 1.0
    total_weight = sum(weights[key] for key, _ in available)
    return sum(weights[key] * float(value) for key, value in available) / total_weight


def _current_summary(current: dict) -> dict:
    points = current["track"]
    closest_index = closest_zhejiang_index(points)
    closest = points[closest_index]
    winds = finite_numbers(point.get("wind") for point in points)
    pressures = finite_numbers(point.get("pressure") for point in points)
    mean_speed, mean_direction = compute_motion(points)
    category_code, category_name = wind_category(max(winds) if winds else None)
    return {
        "sid": current["sid"],
        "name": current["name"],
        "source": current["source"],
        "issued_at": current.get("issued_at"),
        "max_wind": max(winds) if winds else None,
        "min_pressure": min(pressures) if pressures else None,
        "closest_distance_zhejiang": distance_to_zhejiang_km(closest["lat"], closest["lon"]),
        "closest_time_zhejiang": closest["time"],
        "closest_lat": closest["lat"],
        "closest_lon": closest["lon"],
        "wind_at_closest": closest.get("wind"),
        "pressure_at_closest": closest.get("pressure"),
        "mean_speed": mean_speed,
        "mean_direction": mean_direction,
        "category_code": category_code,
        "category": category_name,
        "track_point_count": len(points),
    }


def _difference(first, second):
    if first is None or second is None:
        return None
    return abs(float(first) - float(second))


def _passes_threshold(current: dict, candidate: dict, thresholds: dict) -> bool:
    wind_diff = _difference(current.get("max_wind"), candidate.get("max_wind"))
    pressure_diff = _difference(current.get("min_pressure"), candidate.get("min_pressure"))
    checks = []
    if wind_diff is not None:
        checks.append(wind_diff <= thresholds["wind_kt"])
    if pressure_diff is not None:
        checks.append(pressure_diff <= thresholds["pressure_hpa"])
    return bool(checks) and all(checks)


def _intensity_proxy(current: dict, candidate: dict) -> float:
    components = {
        "max_wind": _clamp_distance(
            _difference(current.get("max_wind"), candidate.get("max_wind")),
            NORMALIZATION_SCALES["wind_kt"],
        ),
        "min_pressure": _clamp_distance(
            _difference(current.get("min_pressure"), candidate.get("min_pressure")),
            NORMALIZATION_SCALES["pressure_hpa"],
        ),
        "closest_wind": _clamp_distance(
            _difference(current.get("wind_at_closest"), candidate.get("wind_at_closest")),
            NORMALIZATION_SCALES["wind_kt"],
        ),
    }
    return _weighted_available(components, INTENSITY_COMPONENT_WEIGHTS)


def _matching_pool(current: dict, candidates: list[dict]) -> tuple[list[dict], str, dict | None]:
    if SIMILARITY_WEIGHTS["intensity"] == 0 and SIMILARITY_WEIGHTS["track"] > 0:
        return candidates, "path_only", None
    strict = [candidate for candidate in candidates if _passes_threshold(current, candidate, STRICT_THRESHOLDS)]
    if len(strict) >= 2:
        return strict, "strict", STRICT_THRESHOLDS
    relaxed = [candidate for candidate in candidates if _passes_threshold(current, candidate, RELAXED_THRESHOLDS)]
    if len(relaxed) >= 2:
        return relaxed, "relaxed", RELAXED_THRESHOLDS
    return candidates, "fallback", None


def _score_candidate(current: dict, candidate: dict, current_track: list[dict], historical_track: list[dict]) -> dict:
    target_track = select_approach_segment(historical_track, len(current_track))
    current_segment = select_approach_segment(current_track, len(current_track))
    dtw_km = track_dtw_km(current_segment, target_track)

    candidate_speed = candidate.get("mean_speed")
    candidate_direction = candidate.get("mean_direction")
    if candidate_speed is None or candidate_direction is None:
        derived_speed, derived_direction = compute_motion(target_track)
        candidate_speed = candidate_speed if candidate_speed is not None else derived_speed
        candidate_direction = candidate_direction if candidate_direction is not None else derived_direction

    intensity_components = {
        "max_wind": _clamp_distance(
            _difference(current.get("max_wind"), candidate.get("max_wind")),
            NORMALIZATION_SCALES["wind_kt"],
        ),
        "min_pressure": _clamp_distance(
            _difference(current.get("min_pressure"), candidate.get("min_pressure")),
            NORMALIZATION_SCALES["pressure_hpa"],
        ),
        "closest_wind": _clamp_distance(
            _difference(current.get("wind_at_closest"), candidate.get("wind_at_closest")),
            NORMALIZATION_SCALES["wind_kt"],
        ),
    }
    path_components = {
        "dtw": _clamp_distance(dtw_km, NORMALIZATION_SCALES["track_km"]),
        "direction": _clamp_distance(
            circular_difference_deg(current.get("mean_direction"), candidate_direction),
            NORMALIZATION_SCALES["direction_deg"],
        ),
        "speed": _clamp_distance(
            _difference(current.get("mean_speed"), candidate_speed),
            NORMALIZATION_SCALES["speed_kt"],
        ),
    }
    intensity_distance = _weighted_available(intensity_components, INTENSITY_COMPONENT_WEIGHTS)
    path_distance = _weighted_available(path_components, TRACK_COMPONENT_WEIGHTS)
    total_distance = (
        SIMILARITY_WEIGHTS["intensity"] * intensity_distance
        + SIMILARITY_WEIGHTS["track"] * path_distance
    )
    category_code, category_name = wind_category(candidate.get("max_wind"))
    result = dict(candidate)
    result.update(
        {
            "category_code": category_code,
            "category": category_name,
            "similarity_score": round(max(0.0, 100.0 * (1.0 - min(1.0, total_distance))), 2),
            "total_distance": round(total_distance, 6),
            "intensity_distance": round(intensity_distance, 6),
            "path_distance": round(path_distance, 6),
            "track_dtw_km": round(dtw_km, 2) if dtw_km is not None else None,
            "differences": {
                "max_wind_kt": _difference(current.get("max_wind"), candidate.get("max_wind")),
                "min_pressure_hpa": _difference(current.get("min_pressure"), candidate.get("min_pressure")),
                "closest_wind_kt": _difference(current.get("wind_at_closest"), candidate.get("wind_at_closest")),
                "mean_speed_kt": _difference(current.get("mean_speed"), candidate_speed),
                "mean_direction_deg": circular_difference_deg(current.get("mean_direction"), candidate_direction),
            },
            "components": {
                "intensity": intensity_components,
                "track": path_components,
            },
            "track": historical_track,
            "matched_segment": target_track,
        }
    )
    return result


def _analogue_analysis(analogue: dict) -> str:
    differences = analogue.get("differences") or {}
    surge = analogue.get("storm_surge") or {}
    surge_kind = ("缺少关联实测" if surge.get("origin")=="missing_observation" else
                  "历史实测关联" if not surge.get("is_estimate") else "缺少可核对实测")
    report = analogue.get("historical_report") or {}
    report_text = _historical_report_text(report)
    return (
        f"路径DTW距离约{_fmt(analogue.get('track_dtw_km'), 'km')}，"
        f"平均移动方向差约{_fmt(differences.get('mean_direction_deg'), '°')}，"
        f"纯路径相似度为{float(analogue.get('similarity_score') or 0):.2f}；"
        f"最大风速约{format_wind_from_knots(analogue.get('max_wind'))}，"
        f"最低气压约{_fmt(analogue.get('min_pressure'), 'hPa')}，"
        f"{zhejiang_relation_text(analogue)}；"
        f"风暴潮为{surge_kind}，{surge.get('summary', '暂无可用信息')}。"
        f"{report_text}"
    )


def _compact_text(value: str | None, maximum: int = 180) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= maximum else text[:maximum].rstrip() + "…"


def _historical_report_text(report: dict) -> str:
    if not report:
        return ""
    details = []
    if report.get("max_significant_wave_m") is not None:
        station = f"（{report['max_significant_wave_station']}）" if report.get("max_significant_wave_station") else ""
        details.append(f"最大有效波高约{float(report['max_significant_wave_m']):.2f} m{station}")
    if report.get("wave_warning_level"):
        details.append(f"海浪最高达到{report['wave_warning_level']}预警")
    if report.get("historical_impact_level"):
        details.append(f"报告影响等级为{report['historical_impact_level']}")
    if report.get("direct_economic_loss_cny") is not None:
        details.append(f"直接经济损失约{float(report['direct_economic_loss_cny']) / 100_000_000:.2f}亿元")
    if report.get("deaths") is not None:
        details.append(f"死亡{int(report['deaths'])}人")
    disaster = _compact_text(report.get("disaster_summary"), 120)
    if disaster:
        details.append(f"灾情摘录：{disaster}")
    return f" 历史总结报告记载：{'；'.join(details)}。" if details else " 历史总结报告已关联，但未抽取到更多定量影响指标。"


def _report_surge_assessment(report: dict | None) -> dict | None:
    if not report or not any(
        report.get(key) is not None
        for key in ("max_surge_m", "max_water_level_m", "storm_surge_warning_level", "historical_impact_level")
    ):
        return None
    warning = report.get("storm_surge_warning_level")
    max_surge_station = report.get("max_surge_station") or (
        "报告未注明" if report.get("max_surge_m") is not None else None
    )
    max_water_station = report.get("max_water_level_station") or (
        "报告未注明" if report.get("max_water_level_m") is not None else None
    )
    severity = report.get("historical_impact_level")
    if not severity:
        severity = {"蓝色": "轻微", "黄色": "一般", "橙色": "严重", "红色": "严重"}.get(warning)
    if not severity and report.get("max_surge_m") is not None:
        surge = float(report["max_surge_m"])
        severity = "严重" if surge >= 1.5 else "一般" if surge >= 0.5 else "轻微"
    severity = severity or "一般"
    details = []
    if report.get("max_surge_m") is not None:
        station = f"（站点：{max_surge_station}）"
        details.append(f"最大风暴增水约{float(report['max_surge_m']):.2f} m{station}")
    if report.get("max_water_level_m") is not None:
        station = f"站点：{max_water_station}，"
        details.append(
            f"最高潮位约{float(report['max_water_level_m']):.2f} m（{station}沿用原报告高程基准）"
        )
    if warning:
        details.append(f"最高实况为{warning}警戒/预警")
    stations = [
        value for value in (max_surge_station, max_water_station) if value
    ]
    return {
        "severity": severity,
        "is_estimate": False,
        "confidence": "high" if report.get("link_confidence") == "high" else "medium",
        "origin": "post_event_report",
        "basis": report.get("impact_level_basis") or "浙江台风影响总结报告中的实测指标和警戒级别",
        "summary": f"{'，'.join(details)}，历史影响综合判断为{severity}",
        "warning_level": warning,
        "max_surge_m": report.get("max_surge_m"),
        "max_surge_station": max_surge_station,
        "max_surge_stations": [max_surge_station] if max_surge_station else [],
        "max_water_level_m": report.get("max_water_level_m"),
        "max_water_level_station": max_water_station,
        "max_water_level_stations": [max_water_station] if max_water_station else [],
        "alert_exceedance_m": None,
        "stations": list(dict.fromkeys(stations)),
        "sources": [report.get("source_file")] if report.get("source_file") else [],
        "record_count": 1,
    }


class TyphoonAnalogueService:
    def __init__(
        self,
        knowledge_base: HistoricalTyphoonRepository | None = None,
        storm_surge_repository: StormSurgeDataRepository | None = None,
        report_repository: TyphoonReportDataRepository | None = None,
    ):
        self.knowledge_base = knowledge_base or TyphoonKnowledgeBase()
        self.storm_surge_repository = storm_surge_repository or StormSurgeRepository()
        self.report_repository = report_repository or TyphoonReportRepository()

    def find_similar(self, payload: dict, top_k: int = 10) -> dict:
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 10:
            raise ValueError('top_k 必须是1至10之间的整数')
        current = normalize_current_typhoon(payload)
        current_summary = _current_summary(current)
        path_only = SIMILARITY_WEIGHTS["intensity"] == 0 and SIMILARITY_WEIGHTS["track"] > 0
        candidates = self.knowledge_base.candidate_summaries(
            exclude_sid=current["sid"],
            require_intensity=not path_only,
            require_complete_intensity=True,
            require_named=True,
        )
        if not candidates:
            raise RuntimeError("知识库中没有可参与匹配的有效历史台风")

        pool, stage, thresholds = _matching_pool(current_summary, candidates)
        if not path_only:
            pool.sort(key=lambda candidate: _intensity_proxy(current_summary, candidate))
            pool = pool[:MAX_PATH_CANDIDATES]
        tracks = self.knowledge_base.get_tracks(candidate["sid"] for candidate in pool)
        scored = [
            _score_candidate(current_summary, candidate, current["track"], tracks.get(candidate["sid"], []))
            for candidate in pool
            if tracks.get(candidate["sid"])
        ]
        scored.sort(key=lambda candidate: (candidate["total_distance"], -candidate.get("season", 0)))
        analogues = scored[:top_k]
        if not analogues:
            raise RuntimeError("没有有效历史路径，无法完成类比")
        for rank, analogue in enumerate(analogues, start=1):
            analogue["rank"] = rank
            historical_report = self.report_repository.lookup(analogue)
            surge_assessment = self.storm_surge_repository.assess(
                analogue,
                analogue.get("track", []),
            )
            report_assessment = _report_surge_assessment(historical_report)
            if report_assessment and surge_assessment.get("is_estimate"):
                surge_assessment = report_assessment
            elif report_assessment:
                surge_assessment["post_event_report"] = report_assessment
            analogue["historical_report"] = historical_report
            analogue["storm_surge"] = surge_assessment
            analogue["analysis"] = _analogue_analysis(analogue)

        current_result = {**current_summary, "track": current["track"]}
        supplied=payload.get('current_typhoon',payload)
        context=supplied.get('track_context') if isinstance(supplied,dict) else None
        if isinstance(context,dict):
            current_result['track_context']={key:context[key] for key in
                ('track_kind','agency','issued_at','track_start','track_end','observed_point_count','forecast_point_count') if key in context}
        if isinstance(supplied,dict) and isinstance(supplied.get('data_status'),dict):
            current_result['data_status']={key:supplied['data_status'][key] for key in
                ('retrieved_at','cache_stale','historical_snapshot','provisional_units','timezone_assumed','wind_input_assumed') if key in supplied['data_status']}
        current_result["storm_surge"] = self.storm_surge_repository.assess(
            current_summary,
            current["track"],
            provided=current.get("storm_surge"),
        )
        current_report = self.report_repository.lookup(current_summary)
        current_report_assessment = _report_surge_assessment(current_report)
        if current_report_assessment and current_result["storm_surge"].get("is_estimate"):
            current_result["storm_surge"] = current_report_assessment
        current_result["historical_report"] = current_report

        return {
            "current": current_result,
            "analogues": analogues,
            "method": {
                "version": "1.3",
                "matching_stage": stage,
                "thresholds": thresholds,
                "weights": SIMILARITY_WEIGHTS,
                "intensity_components": INTENSITY_COMPONENT_WEIGHTS,
                "track_components": TRACK_COMPONENT_WEIGHTS,
                "candidate_count": len(candidates),
                "scored_candidate_count": len(scored),
                "input_track": current_result.get('track_context') or {'track_kind':'supplied_path','agency':current.get('source')},
                "note": "纯路径模式：具备正式名称、最大风速和最低气压的浙江影响候选参与路径评分；强度仅用于文本解释，不参与排名。",
            },
            "knowledge_base": self.knowledge_base.status(),
            "storm_surge_data": self.storm_surge_repository.status(),
            "report_knowledge": self.report_repository.status(),
        }


def format_analogue_report(result: dict) -> str:
    current = result["current"]
    analogues = result["analogues"][:10]
    analogue_count = len(analogues)
    current_surge = current.get("storm_surge") or {}
    analogue_names = "、".join(
        f"第{item.get('rank')}名{item.get('season')}年{item.get('name')}" for item in analogues
    )
    lines = [
        "台风历史类比分析简报",
        "",
        "排名依据：路径权重100%，强度不参与排名，仅用于案例差异解释。",
        "",
        f"检索概览：当前台风{current['name']}的{analogue_count}个路径相似案例依次为：{analogue_names}。"
        f"当前最大风速约{format_wind_from_knots(current.get('max_wind'))}，最低气压约{_fmt(current.get('min_pressure'), 'hPa')}，"
        f"{zhejiang_relation_text(current)}。"
        f"{current_surge.get('summary', '风暴潮影响暂无法判断')}。",
        "",
        f"{analogue_count}个历史相似案例逐条解析：",
    ]
    context=current.get('track_context') or {}
    if context:
        kind=context.get('track_kind')
        source=('已发生路径' if kind=='observed' else str(context.get('agency') or '指定')+'机构预报路径（含当前记录锚点）' if kind=='forecast' else '外部提供路径')
        lines[4:4]=['本次输入：'+source+'；起报或最新记录时刻：'+str(context.get('issued_at') or '未提供')+'。','']
    if (current.get('data_status') or {}).get('provisional_units'):
        lines.extend(['','接口风速单位沿用当前m/s解释转换为匹配器输入，尚待业务方核定；路径排名不使用强度权重。'])
    for analogue in analogues:
        lines.extend([
            f"{analogue['rank']}. {analogue['season']}年{analogue['name']}："
            f"{analogue.get('analysis') or _analogue_analysis(analogue)}",
            "",
        ])

    measured=sum(1 for item in analogues if not item.get('storm_surge',{}).get('is_estimate')
                 and item.get('storm_surge',{}).get('record_count',0)>0)
    conclusion=(f"{analogue_count}个相似案例中，{measured}个有关联实测或报告记录，{analogue_count-measured}个缺少可核对的风暴潮记录。"
                "以上交付路径相似结果及已有历史事实；本次浙江影响另由指定批次的客观风浪、潮位预报分析，不从路径排名推定灾害轻重。")
    lines.extend([
        f"结论：{conclusion}",
        "",
        "说明：历史增水、潮位和灾损保留对应站点、事件与来源；没有关联记录时保留缺项，不按零处理，也不以中心强度和距离估计风暴潮等级。",
        "本简报仅用于研究和辅助分析，不替代自然资源、气象及应急部门发布的预报预警和防灾指令。",
    ])
    return "\n".join(lines).strip()


def _fmt(value, unit: str) -> str:
    if value is None:
        return "无数据"
    return f"{float(value):.1f} {unit}"
