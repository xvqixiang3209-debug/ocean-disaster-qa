"""Select report mode and guarantee deterministic fallback."""

from __future__ import annotations

from .contracts import ReportDraft
from .llm import LLMReportGenerator
from .template import TemplateReportGenerator


class ReportCoordinator:
    def __init__(
        self,
        template_generator: TemplateReportGenerator,
        llm_generator: LLMReportGenerator,
    ):
        self.template_generator = template_generator
        self.llm_generator = llm_generator

    def generate(self, result: dict, requested_mode: str, model_name: str | None = None) -> ReportDraft:
        if requested_mode == "template":
            return self.template_generator.generate(result)
        if requested_mode != "llm":
            raise ValueError("report_mode 仅支持 template 或 llm")
        try:
            return self.llm_generator.generate(result, model_name=model_name)
        except Exception as exc:
            fallback = self.template_generator.generate(result)
            metadata = dict(fallback.metadata)
            attempted_model = model_name or self.llm_generator.settings.report_model
            reason = str(exc).replace("\r", " ").replace("\n", " ")[:240]
            metadata.update(
                {
                    "requested_mode": "llm",
                    "fallback": True,
                    "fallback_reason": f"{type(exc).__name__}: {reason}",
                }
            )
            return ReportDraft(
                report=fallback.report,
                analyses_by_sid=fallback.analyses_by_sid,
                used_mode="template",
                model=attempted_model,
                metadata=metadata,
            )
