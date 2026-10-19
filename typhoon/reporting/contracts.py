"""Contracts shared by report implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ReportDraft:
    report: str
    analyses_by_sid: dict[str, str]
    used_mode: str
    model: str | None = None
    metadata: dict = field(default_factory=dict)


class ReportGenerator(Protocol):
    def generate(self, result: dict, model_name: str | None = None) -> ReportDraft: ...

