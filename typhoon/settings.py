"""Runtime settings for the typhoon application layer."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

from .config import (
    DEFAULT_CURRENT_TYPHOON,
    DEFAULT_DATABASE,
    DEFAULT_RAG_DATABASE,
    DEFAULT_STORM_SURGE_DATA,
    PROJECT_DIR,
)
from .web_search import normalize_provider


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 必须是数字") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"环境变量 {name} 必须位于 {minimum} 至 {maximum} 之间")
    return parsed


@dataclass(frozen=True)
class TyphoonSettings:
    """Configuration boundary between deployment and business code."""

    database_backend: str = "sqlite"
    sqlite_database_path: Path = DEFAULT_DATABASE
    rag_database_path: Path = DEFAULT_RAG_DATABASE
    storm_surge_data_path: Path = DEFAULT_STORM_SURGE_DATA
    current_typhoon_path: Path = DEFAULT_CURRENT_TYPHOON
    report_mode: str = "llm"
    report_model: str = "deepseek-v4-flash"
    llm_timeout_seconds: float = 20.0
    llm_temperature: float = 0.2
    llm_top_p: float = 0.8
    web_search_enabled: bool = True
    web_search_provider: str = "auto"
    web_search_timeout_seconds: float = 8.0
    web_search_max_results: int = 5
    web_read_enabled: bool = True
    web_read_timeout_seconds: float = 12.0
    web_read_max_pages: int = 3
    web_read_max_chars: int = 48000
    web_summary_timeout_seconds: float = 40.0
    allow_request_report_override: bool = False
    expose_internal_paths: bool = False

    @classmethod
    def from_env(cls) -> "TyphoonSettings":
        if load_dotenv is not None:
            load_dotenv(PROJECT_DIR / ".env", override=False)
        backend = os.getenv("TYPHOON_DB_TYPE", "sqlite").strip().lower()
        report_mode = os.getenv("TYPHOON_REPORT_MODE", "llm").strip().lower()
        if backend not in {"sqlite", "mysql"}:
            raise ValueError("TYPHOON_DB_TYPE 仅支持 sqlite 或 mysql")
        if report_mode not in {"template", "llm"}:
            raise ValueError("TYPHOON_REPORT_MODE 仅支持 template 或 llm")
        return cls(
            database_backend=backend,
            sqlite_database_path=Path(
                os.getenv("TYPHOON_SQLITE_PATH", str(DEFAULT_DATABASE))
            ).expanduser(),
            rag_database_path=Path(
                os.getenv("TYPHOON_RAG_SQLITE_PATH", str(DEFAULT_RAG_DATABASE))
            ).expanduser(),
            storm_surge_data_path=Path(
                os.getenv("TYPHOON_STORM_SURGE_PATH", str(DEFAULT_STORM_SURGE_DATA))
            ).expanduser(),
            current_typhoon_path=Path(
                os.getenv("TYPHOON_CURRENT_FILE", str(DEFAULT_CURRENT_TYPHOON))
            ).expanduser(),
            report_mode=report_mode,
            report_model=os.getenv("TYPHOON_REPORT_MODEL", "deepseek-v4-flash").strip(),
            llm_timeout_seconds=_float_env("TYPHOON_LLM_TIMEOUT_SECONDS", 20.0, 0.5, 120.0),
            llm_temperature=_float_env("TYPHOON_LLM_TEMPERATURE", 0.2, 0.0, 2.0),
            llm_top_p=_float_env("TYPHOON_LLM_TOP_P", 0.8, 0.0, 1.0),
            web_search_enabled=_bool_env("TYPHOON_WEB_SEARCH_ENABLED", True),
            web_search_provider=normalize_provider(os.getenv("TYPHOON_WEB_SEARCH_PROVIDER", "auto")),
            web_search_timeout_seconds=_float_env("TYPHOON_WEB_SEARCH_TIMEOUT_SECONDS", 8.0, 1.0, 30.0),
            web_search_max_results=max(1, min(int(os.getenv("TYPHOON_WEB_SEARCH_MAX_RESULTS", "5")), 10)),
            web_read_enabled=_bool_env("TYPHOON_WEB_READ_ENABLED", True),
            web_read_timeout_seconds=_float_env("TYPHOON_WEB_READ_TIMEOUT_SECONDS", 12.0, 1.0, 30.0),
            web_read_max_pages=max(1, min(int(os.getenv("TYPHOON_WEB_READ_MAX_PAGES", "3")), 5)),
            web_read_max_chars=max(1000, min(int(os.getenv("TYPHOON_WEB_READ_MAX_CHARS", "48000")), 48000)),
            web_summary_timeout_seconds=_float_env("TYPHOON_WEB_SUMMARY_TIMEOUT_SECONDS", 40.0, 1.0, 90.0),
            allow_request_report_override=_bool_env("TYPHOON_ALLOW_REPORT_OVERRIDE", False),
            expose_internal_paths=_bool_env("TYPHOON_EXPOSE_INTERNAL_PATHS", False),
        )

    def resolve_report_mode(self, requested: str | None) -> str:
        if requested and self.allow_request_report_override:
            mode = requested.strip().lower()
            if mode not in {"template", "llm"}:
                raise ValueError("report_mode 仅支持 template 或 llm")
            return mode
        return self.report_mode
