"""Composition root for the typhoon subsystem."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .application import TyphoonAnalysisApplication
from .conversation import AnalysisSessionStore, TyphoonConversationService
from .current_provider import CurrentTyphoonFileProvider
from .reporting import LLMReportGenerator, ReportCoordinator, TemplateReportGenerator
from .repositories import RepositoryBundle, build_repositories
from .service import TyphoonAnalogueService
from .settings import TyphoonSettings
from .rag import RagKnowledgeBase
from .web_search import WebSearchClient


@dataclass(frozen=True)
class TyphoonComponents:
    settings: TyphoonSettings
    repositories: RepositoryBundle
    service: TyphoonAnalogueService
    reports: ReportCoordinator
    application: TyphoonAnalysisApplication
    current_provider: CurrentTyphoonFileProvider
    rag: RagKnowledgeBase
    conversations: TyphoonConversationService
    web_search: WebSearchClient


def build_typhoon_components(
    settings: TyphoonSettings | None = None,
    *,
    model_caller: Callable | None = None,
) -> TyphoonComponents:
    resolved = settings or TyphoonSettings.from_env()
    repositories = build_repositories(resolved)
    service = TyphoonAnalogueService(
        knowledge_base=repositories.historical,
        storm_surge_repository=repositories.storm_surge,
        report_repository=repositories.reports,
    )
    reports = ReportCoordinator(
        template_generator=TemplateReportGenerator(),
        llm_generator=LLMReportGenerator(resolved, model_caller=model_caller),
    )
    rag = RagKnowledgeBase(
        resolved.rag_database_path,
        history_database_path=resolved.sqlite_database_path,
        semantic_model_caller=model_caller,
        semantic_model_name=resolved.report_model,
        semantic_timeout_seconds=resolved.llm_timeout_seconds,
    )
    try:
        rag.sync_historical_reports(resolved.sqlite_database_path)
    except Exception as exc:
        print(f"[Startup Warning] RAG历史报告同步失败: {exc}")
    sessions = AnalysisSessionStore()
    application = TyphoonAnalysisApplication(service, reports, resolved, sessions=sessions)
    web_search = WebSearchClient(
        enabled=resolved.web_search_enabled,
        provider=resolved.web_search_provider,
        timeout_seconds=resolved.web_search_timeout_seconds,
        max_results=resolved.web_search_max_results,
    )
    conversations = TyphoonConversationService(
        sessions=sessions,
        rag=rag,
        settings=resolved,
        model_caller=model_caller,
        web_search=web_search,
    )
    current_provider = CurrentTyphoonFileProvider(resolved.current_typhoon_path)
    return TyphoonComponents(
        settings=resolved,
        repositories=repositories,
        service=service,
        reports=reports,
        application=application,
        current_provider=current_provider,
        rag=rag,
        conversations=conversations,
        web_search=web_search,
    )
