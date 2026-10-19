"""Deterministic report implementation used as the safety baseline."""

from __future__ import annotations

from ..service import format_analogue_report
from .contracts import ReportDraft


class TemplateReportGenerator:
    def generate(self, result: dict, model_name: str | None = None) -> ReportDraft:
        analyses = {
            str(item["sid"]): str(item.get("analysis") or "")
            for item in result.get("analogues", [])
        }
        return ReportDraft(
            report=format_analogue_report(result),
            analyses_by_sid=analyses,
            used_mode="template",
            metadata={"fact_source": "algorithm_and_database"},
        )

