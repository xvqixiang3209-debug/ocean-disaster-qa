"""Application service orchestrating retrieval, reporting, validation and API shaping."""

from __future__ import annotations

import time
import uuid

from .reporting import ReportCoordinator
from .conversation import AnalysisSessionStore
from .service import TyphoonAnalogueService
from .settings import TyphoonSettings
from .validation import validate_analysis_result


KNOWLEDGE_BASE_PUBLIC_FIELDS = {
    "available",
    "zhejiang_candidate_count",
    "storm_count",
    "point_count",
    "basin",
    "schema_version",
    "built_at",
    "storm_surge_imported_at",
    "storm_surge_event_count",
    "storm_surge_station_count",
    "storm_surge_observation_count",
    "storm_surge_linked_event_count",
    "storm_surge_time_overlap_count",
    "storm_surge_time_nearby_count",
    "storm_surge_time_conflict_count",
    "storm_surge_schema_version",
    "typhoon_report_imported_at",
    "typhoon_report_report_count",
    "typhoon_report_case_count",
    "typhoon_report_linked_case_count",
    "typhoon_report_pending_track_count",
    "typhoon_report_metric_count",
    "typhoon_report_unit_inferred_count",
    "typhoon_report_qc_issue_count",
    "typhoon_report_qc_pass_count",
    "typhoon_report_qc_review_count",
    "typhoon_report_qc_fail_count",
    "typhoon_report_schema_version",
}

STORM_SURGE_PUBLIC_FIELDS = {
    "available",
    "record_count",
    "event_count",
    "linked_storm_count",
    "observation_count",
    "time_match_counts",
    "fallback",
}

REPORT_KNOWLEDGE_PUBLIC_FIELDS = {
    "available",
    "report_count",
    "case_count",
    "linked_case_count",
    "pending_track_count",
    "metric_count",
    "unit_inferred_count",
    "qc_issue_count",
    "season_start",
    "season_end",
    "qc_counts",
}


def _public_status(status: dict, allowed: set[str], expose_paths: bool) -> dict:
    if expose_paths:
        return dict(status)
    return {key: value for key, value in status.items() if key in allowed}


class TyphoonAnalysisApplication:
    def __init__(
        self,
        service: TyphoonAnalogueService,
        reports: ReportCoordinator,
        settings: TyphoonSettings,
        sessions: AnalysisSessionStore | None = None,
    ):
        self.service = service
        self.reports = reports
        self.settings = settings
        self.sessions = sessions or AnalysisSessionStore()

    def analyze(
        self,
        payload: dict,
        *,
        top_k: int = 10,
        request_id: str | None = None,
        report_mode: str | None = None,
        model_name: str | None = None,
        compact: bool = False,
    ) -> dict:
        started = time.perf_counter()
        resolved_mode = self.settings.resolve_report_mode(report_mode)
        result = self.service.find_similar(payload, top_k=top_k)
        if compact:
            names = "、".join(
                f"第{item['rank']}名{item.get('season')}年{item.get('name')}"
                for item in result.get("analogues", [])
            )
            result["report"] = (
                f"已完成路径相似检索：{names}。请选择一个历史台风，系统将只展示该台风详情，"
                "随后可在对话框继续询问路径、强度、风暴潮、海浪和灾情。"
            )
            draft_used_mode = "retrieval"
            draft_model = None
            draft_metadata = {"compact": True, "fallback": False, "fallback_reason": None}
        else:
            draft = self.reports.generate(result, resolved_mode, model_name=model_name)
            for item in result.get("analogues", []):
                sid = str(item.get("sid") or "")
                if sid in draft.analyses_by_sid:
                    item["analysis"] = draft.analyses_by_sid[sid]
            result["report"] = draft.report
            draft_used_mode = draft.used_mode
            draft_model = draft.model
            draft_metadata = draft.metadata

        validation = validate_analysis_result(result)
        result["request_id"] = str(request_id or f"TY-{uuid.uuid4().hex[:16]}")
        result["api_version"] = "1.1"
        result["report_meta"] = {
            "requested_mode": resolved_mode,
            "used_mode": draft_used_mode,
            "model": draft_model,
            "fallback": bool(draft_metadata.get("fallback")),
            "fallback_reason": draft_metadata.get("fallback_reason"),
            "fact_validation": validation.status,
            "validation_warnings": list(validation.warnings),
            "policy": "algorithm_and_database_facts_are_immutable",
            **{
                key: value
                for key, value in draft_metadata.items()
                if key not in {"fallback", "fallback_reason"}
            },
        }
        result["knowledge_base"] = _public_status(
            result.get("knowledge_base") or {},
            KNOWLEDGE_BASE_PUBLIC_FIELDS,
            self.settings.expose_internal_paths,
        )
        result["storm_surge_data"] = _public_status(
            result.get("storm_surge_data") or {},
            STORM_SURGE_PUBLIC_FIELDS,
            self.settings.expose_internal_paths,
        )
        result["report_knowledge"] = _public_status(
            result.get("report_knowledge") or {},
            REPORT_KNOWLEDGE_PUBLIC_FIELDS,
            self.settings.expose_internal_paths,
        )
        result["processing_ms"] = round((time.perf_counter() - started) * 1000, 2)
        self.sessions.put(result["request_id"], result)
        if compact:
            for item in result.get("analogues", []):
                for field in ("analysis", "historical_report", "track", "matched_segment", "differences", "components"):
                    item.pop(field, None)
                surge = item.get("storm_surge") or {}
                item["storm_surge"] = {
                    "severity": surge.get("severity"),
                    "is_estimate": bool(surge.get("is_estimate")),
                }
        return result

    def public_status(self) -> dict:
        return {
            "database_backend": self.settings.database_backend,
            "knowledge_base": _public_status(
                self.service.knowledge_base.status(),
                KNOWLEDGE_BASE_PUBLIC_FIELDS,
                self.settings.expose_internal_paths,
            ),
            "storm_surge": _public_status(
                self.service.storm_surge_repository.status(),
                STORM_SURGE_PUBLIC_FIELDS,
                self.settings.expose_internal_paths,
            ),
            "report_knowledge": _public_status(
                self.service.report_repository.status(),
                REPORT_KNOWLEDGE_PUBLIC_FIELDS,
                self.settings.expose_internal_paths,
            ),
            "reporting": {
                "configured_mode": self.settings.report_mode,
                "model": self.settings.report_model if self.settings.report_mode == "llm" else None,
                "llm_timeout_seconds": self.settings.llm_timeout_seconds,
                "request_override_enabled": self.settings.allow_request_report_override,
                "fallback": "template",
            },
        }
