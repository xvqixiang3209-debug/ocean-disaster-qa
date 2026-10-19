"""Storage contracts used by the typhoon domain service."""

from __future__ import annotations

from typing import Iterable, Protocol


class HistoricalTyphoonRepository(Protocol):
    @property
    def available(self) -> bool: ...

    def status(self) -> dict: ...

    def candidate_summaries(
        self,
        exclude_sid: str | None = None,
        *,
        require_intensity: bool = True,
        require_complete_intensity: bool = False,
        require_named: bool = False,
    ) -> list[dict]: ...

    def get_storm(self, sid: str, include_track: bool = True) -> dict | None: ...

    def get_tracks(self, sids: Iterable[str]) -> dict[str, list[dict]]: ...


class StormSurgeDataRepository(Protocol):
    def status(self) -> dict: ...

    def assess(
        self,
        storm: dict,
        track: list[dict] | None = None,
        provided: dict | list[dict] | None = None,
    ) -> dict: ...


class TyphoonReportDataRepository(Protocol):
    @property
    def available(self) -> bool: ...

    def status(self) -> dict: ...

    def lookup(self, storm: dict) -> dict | None: ...
