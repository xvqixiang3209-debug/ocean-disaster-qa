"""Create storage adapters without leaking deployment concerns into the domain."""

from __future__ import annotations

from dataclasses import dataclass

from ..knowledge_base import TyphoonKnowledgeBase
from ..report_knowledge import TyphoonReportRepository
from ..settings import TyphoonSettings
from ..storm_surge import StormSurgeRepository
from .contracts import HistoricalTyphoonRepository, StormSurgeDataRepository, TyphoonReportDataRepository


class RepositoryConfigurationError(RuntimeError):
    """Raised when a configured persistence backend is not ready."""


@dataclass(frozen=True)
class RepositoryBundle:
    historical: HistoricalTyphoonRepository
    storm_surge: StormSurgeDataRepository
    reports: TyphoonReportDataRepository


def build_repositories(settings: TyphoonSettings) -> RepositoryBundle:
    if settings.database_backend == "sqlite":
        historical = TyphoonKnowledgeBase(settings.sqlite_database_path)
        storm_surge = StormSurgeRepository(settings.storm_surge_data_path)
        reports = TyphoonReportRepository(settings.sqlite_database_path)
        return RepositoryBundle(historical=historical, storm_surge=storm_surge, reports=reports)

    raise RepositoryConfigurationError(
        "已选择 MySQL 数据源，但当前尚缺政务云 MySQL 表结构和字段映射。"
        "请先提供 storms、track_points、storm_surge_events、storm_surge_stations "
        "storm_surge_observations、typhoon_reports 及 typhoon_report_cases "
        "的数据字典，再实现 MySQL 仓储适配器。"
    )
