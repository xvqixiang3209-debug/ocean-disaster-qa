"""LLM-assisted qualitative interpretation that cannot replace numeric facts."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable

from ..service import format_analogue_report
from ..units import knots_to_mps, wind_force_level_from_knots
from ..settings import TyphoonSettings
from .contracts import ReportDraft


class LLMReportValidationError(ValueError):
    pass


SYSTEM_PROMPT = """你是台风历史类比分析助手。后端已经完成排名、路径计算和风暴潮事实查询。
你只能补充定性解释，不能改变排名、台风身份、相似度、DTW距离、风速、气压、潮位、增水、站点、等级或数据来源。
输出必须是严格JSON，不要使用Markdown代码块。格式如下：
{
  "overview": "不含阿拉伯数字的定性概述",
  "interpretations": [
    {"rank": 1, "sid": "与输入完全一致的SID", "text": "不含阿拉伯数字的定性解释"}
  ],
  "conclusion": "不含阿拉伯数字的定性结论"
}
interpretations必须与输入案例逐一对应，不得增删、合并或换序。所有数值事实会由程序自动拼接，因此overview、text和conclusion中禁止出现阿拉伯数字。没有实测风暴潮数据时，只能说明存在不确定性，不得补造潮位、增水、站点或预警。"""


def _evidence(result: dict) -> dict:
    current = result.get("current") or {}
    analogues = []
    for item in result.get("analogues", []):
        surge = item.get("storm_surge") or {}
        historical_report = item.get("historical_report") or {}
        analogues.append(
            {
                "rank": item.get("rank"),
                "sid": item.get("sid"),
                "season": item.get("season"),
                "name": item.get("name"),
                "category": item.get("category"),
                "similarity_score": item.get("similarity_score"),
                "track_dtw_km": item.get("track_dtw_km"),
                "max_wind_ms": knots_to_mps(item.get("max_wind")),
                "wind_force_level": wind_force_level_from_knots(item.get("max_wind")),
                "min_pressure_hpa": item.get("min_pressure"),
                "closest_distance_zhejiang_km": item.get("closest_distance_zhejiang"),
                "differences": item.get("differences"),
                "storm_surge": {
                    "origin": surge.get("origin"),
                    "is_estimate": surge.get("is_estimate"),
                    "severity": surge.get("severity"),
                    "summary": surge.get("summary"),
                    "max_surge_m": surge.get("max_surge_m"),
                    "max_surge_station": surge.get("max_surge_station"),
                    "max_water_level_m": surge.get("max_water_level_m"),
                    "max_water_level_station": surge.get("max_water_level_station"),
                },
                "historical_report": {
                    "available": bool(historical_report),
                    "source_type": historical_report.get("source_type"),
                    "link_confidence": historical_report.get("link_confidence"),
                    "qc_status": historical_report.get("qc_status"),
                    "landfall_location": historical_report.get("landfall_location"),
                    "max_significant_wave_m": historical_report.get("max_significant_wave_m"),
                    "max_significant_wave_station": historical_report.get("max_significant_wave_station"),
                    "wave_warning_level": historical_report.get("wave_warning_level"),
                    "historical_impact_level": historical_report.get("historical_impact_level"),
                    "disaster_summary": historical_report.get("disaster_summary"),
                },
            }
        )
    return {
        "instruction": "仅补充定性解读；事实字段只读",
        "current": {
            "sid": current.get("sid"),
            "name": current.get("name"),
            "category": current.get("category"),
            "max_wind_ms": knots_to_mps(current.get("max_wind")),
            "wind_force_level": wind_force_level_from_knots(current.get("max_wind")),
            "min_pressure_hpa": current.get("min_pressure"),
            "closest_distance_zhejiang_km": current.get("closest_distance_zhejiang"),
            "storm_surge": current.get("storm_surge"),
        },
        "analogues": analogues,
        "method": result.get("method"),
    }


def _parse_json(content: str) -> dict:
    text = str(content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMReportValidationError("大模型没有返回有效JSON") from exc
    if not isinstance(parsed, dict):
        raise LLMReportValidationError("大模型返回值必须是JSON对象")
    return parsed


def _qualitative_text(value, field: str, *, maximum: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise LLMReportValidationError(f"大模型缺少{field}")
    if len(text) > maximum:
        raise LLMReportValidationError(f"大模型{field}过长")
    if re.search(r"\d", text):
        raise LLMReportValidationError(f"大模型{field}包含数值，可能改写事实")
    return text


def _validate_payload(payload: dict, result: dict) -> tuple[str, dict[str, str], str]:
    expected = result.get("analogues", [])
    items = payload.get("interpretations")
    if not isinstance(items, list) or len(items) != len(expected):
        raise LLMReportValidationError("大模型案例数量与检索结果不一致")
    analyses: dict[str, str] = {}
    for expected_item, actual in zip(expected, items):
        if not isinstance(actual, dict):
            raise LLMReportValidationError("大模型案例解释必须是对象")
        if actual.get("rank") != expected_item.get("rank"):
            raise LLMReportValidationError("大模型修改了案例排名")
        sid = str(actual.get("sid") or "")
        if sid != str(expected_item.get("sid") or ""):
            raise LLMReportValidationError("大模型修改了案例SID或顺序")
        analyses[sid] = _qualitative_text(actual.get("text"), "案例解释", maximum=220)
    overview = _qualitative_text(payload.get("overview"), "概述", maximum=320)
    conclusion = _qualitative_text(payload.get("conclusion"), "结论", maximum=320)
    return overview, analyses, conclusion


class LLMReportGenerator:
    def __init__(
        self,
        settings: TyphoonSettings,
        model_caller: Callable | None = None,
    ):
        self.settings = settings
        if model_caller is None:
            from model_layer import call_model

            model_caller = call_model
        self.model_caller = model_caller

    def generate(self, result: dict, model_name: str | None = None) -> ReportDraft:
        selected_model = model_name or self.settings.report_model
        evidence = _evidence(result)
        response = self.model_caller(
            selected_model,
            SYSTEM_PROMPT,
            json.dumps(evidence, ensure_ascii=False, separators=(",", ":")),
            stream=False,
            temperature=self.settings.llm_temperature,
            top_p=self.settings.llm_top_p,
            timeout=self.settings.llm_timeout_seconds,
            max_retries=0,
            thinking="disabled",
        )
        overview, interpretations, conclusion = _validate_payload(_parse_json(response), result)

        enriched = copy.deepcopy(result)
        analyses_by_sid: dict[str, str] = {}
        for item in enriched.get("analogues", []):
            sid = str(item["sid"])
            factual = str(item.get("analysis") or "").strip()
            qualitative = interpretations[sid]
            combined = f"{factual} 大模型辅助解读：{qualitative}"
            item["analysis"] = combined
            analyses_by_sid[sid] = combined
        report = format_analogue_report(enriched)
        report += f"\n\n大模型辅助概述：{overview}\n\n大模型辅助结论：{conclusion}"
        return ReportDraft(
            report=report,
            analyses_by_sid=analyses_by_sid,
            used_mode="llm",
            model=selected_model,
            metadata={
                "llm_scope": "qualitative_interpretation_only",
                "numeric_facts_mutable": False,
                "llm_payload_validation": "pass",
            },
        )
