"""Deterministic links between RAG chunks and IBTrACS storm identities.

The language model may help split or summarize an uploaded document, but it is
never trusted to invent an IBTrACS SID.  This module resolves only identities
that can be checked against the structured history database.  It recognises:

* a literal IBTrACS SID;
* an annual typhoon designation (for example ``1909号台风``) already linked by
  a reviewed post-event report;
* a year plus an unambiguous English or Chinese storm name; and
* a designation followed by an unambiguous name from the same inferred year.

Links are many-to-many.  That matters for a text chunk which straddles two case
chapters, and for chapters which deliberately discuss two interacting storms.
Chapter identities are propagated forward until a later case heading is found.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence


METHOD_VERSION = "deterministic-v2-legacy-candidates"
AUTO_ACCEPT_THRESHOLD = 0.95
CONTEXT_CONFIDENCE = 0.90

_SID_RE = re.compile(r"(?<![A-Z0-9])((?:18|19|20)\d{2}\d{3}[NS]\d{5})(?![A-Z0-9])", re.I)
_COMPACT_CODE_RE = re.compile(
    r"(?<!\d)(?P<yy>\d{2})\s*(?P<number>\d{2})\s*号(?:\s*台风)?"
)
_LONG_CODE_RE = re.compile(
    r"(?<!\d)(?P<year>(?:19|20)\d{2})\s*年?\s*(?:第\s*)?"
    r"(?P<number>\d{1,2})\s*号(?:\s*台风)?"
)
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?:\s*年)?(?!\d)")
_CASE_HEADING_PREFIX_RE = re.compile(r"^\s*(?:\d{1,3}\s*[）).、]\s*)?")
_CASE_HEADING_SUFFIX_RE = re.compile(r"台风.{0,50}?(?:风暴潮|灾害|概况|过程)")
_CONTENTS_MARKERS = ("目录", "contents")


@dataclass(frozen=True)
class StormRecord:
    sid: str
    season: int | None
    name: str
    start_time: str = ""
    end_time: str = ""


@dataclass(frozen=True)
class ChunkLink:
    """One auditable chunk-to-storm relationship."""

    chunk_id: int
    sid: str
    confidence: float
    link_method: str
    matched_text: str
    review_status: str
    method_version: str = METHOD_VERSION
    is_primary: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _Evidence:
    sid: str
    confidence: float
    method: str
    matched_text: str
    start: int
    end: int
    is_heading: bool = False


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return bool(connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _normalize_name(value: object) -> str:
    return re.sub(r"[^A-Z0-9\u3400-\u9fff]", "", str(value or "").upper())


def _clean_excerpt(value: object, maximum: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:maximum]


def _metadata(chunk: Mapping) -> dict:
    value = chunk.get("metadata")
    if isinstance(value, Mapping):
        return dict(value)
    value = chunk.get("metadata_json")
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value or "{}")
            return dict(parsed) if isinstance(parsed, Mapping) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _document_metadata(chunk: Mapping) -> dict:
    value = chunk.get("document_metadata")
    if isinstance(value, Mapping):
        return dict(value)
    value = chunk.get("document_metadata_json") or chunk.get("document_metadata_json_text")
    if isinstance(value, str):
        try:
            parsed = json.loads(value or "{}")
            return dict(parsed) if isinstance(parsed, Mapping) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _review_status(confidence: float) -> str:
    return "auto_accepted" if confidence >= AUTO_ACCEPT_THRESHOLD else "needs_review"


def _alias_pattern(alias: str) -> re.Pattern | None:
    alias = str(alias or "").strip()
    if not alias:
        return None
    if re.search(r"[\u3400-\u9fff]", alias):
        return re.compile(re.escape(alias), re.I)
    pieces = [piece for piece in re.split(r"[^A-Z0-9]+", alias.upper()) if piece]
    if not pieces:
        return None
    middle = r"[\s_.-]*"
    return re.compile(r"(?<![A-Z0-9])" + middle.join(map(re.escape, pieces)) + r"(?![A-Z0-9])", re.I)


class TyphoonEntityLinker:
    """Resolve chunk text against a structured history SQLite database."""

    def __init__(self, history_database_path: str | Path):
        self.history_database_path = Path(history_database_path).resolve()
        self.storms: dict[str, StormRecord] = {}
        self.code_to_sids: dict[str, set[str]] = defaultdict(set)
        self.year_number_to_sids: dict[tuple[int, int], set[str]] = defaultdict(set)
        self.year_alias_to_sids: dict[tuple[int, str], set[str]] = defaultdict(set)
        self.aliases_by_year: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        self._alias_patterns: dict[str, re.Pattern] = {}
        self._load_catalogue()

    def _add_alias(self, sid: str, season: int | None, alias: object) -> None:
        cleaned = str(alias or "").strip()
        normalized = _normalize_name(cleaned)
        if not sid or season is None or not normalized or normalized == "UNNAMED":
            return
        self.year_alias_to_sids[(int(season), normalized)].add(sid)
        self.aliases_by_year[int(season)][cleaned].add(sid)
        pattern = _alias_pattern(cleaned)
        if pattern is not None:
            self._alias_patterns.setdefault(cleaned, pattern)

    def _load_catalogue(self) -> None:
        if not self.history_database_path.exists():
            raise FileNotFoundError(f"历史台风数据库不存在: {self.history_database_path}")
        with closing(sqlite3.connect(self.history_database_path)) as connection:
            connection.row_factory = sqlite3.Row
            if not _table_exists(connection, "storms"):
                raise ValueError("历史台风数据库缺少 storms 表")
            for row in connection.execute(
                "SELECT sid, season, name, start_time, end_time FROM storms"
            ):
                sid = str(row["sid"] or "").strip()
                season = int(row["season"]) if row["season"] is not None else None
                record = StormRecord(
                    sid=sid,
                    season=season,
                    name=str(row["name"] or "").strip(),
                    start_time=str(row["start_time"] or ""),
                    end_time=str(row["end_time"] or ""),
                )
                self.storms[sid] = record
                self._add_alias(sid, season, record.name)

            if _table_exists(connection, "typhoon_report_cases"):
                columns = _columns(connection, "typhoon_report_cases")
                required = {"sid", "season_resolved", "storm_number", "storm_code"}
                if required.issubset(columns):
                    select_names = [name for name in ("storm_name_cn", "storm_name_en") if name in columns]
                    select_sql = ", ".join(required | set(select_names))
                    for row in connection.execute(
                        f"SELECT {select_sql} FROM typhoon_report_cases WHERE sid IS NOT NULL"
                    ):
                        sid = str(row["sid"] or "").strip()
                        if sid not in self.storms:
                            continue
                        season = int(row["season_resolved"]) if row["season_resolved"] is not None else None
                        number = int(row["storm_number"]) if row["storm_number"] is not None else None
                        code = re.sub(r"\D", "", str(row["storm_code"] or ""))
                        if len(code) == 4:
                            self.code_to_sids[code].add(sid)
                        if season is not None and number is not None:
                            self.year_number_to_sids[(season, number)].add(sid)
                        for name_column in select_names:
                            self._add_alias(sid, season, row[name_column])

            # Optional curated alias tables are supported without requiring one
            # particular migration name.  Only a validated SID is accepted.
            for table in ("storm_aliases", "typhoon_aliases"):
                if not _table_exists(connection, table):
                    continue
                columns = _columns(connection, table)
                sid_column = "sid" if "sid" in columns else "storm_sid" if "storm_sid" in columns else None
                alias_column = next((name for name in ("alias", "name", "alias_name") if name in columns), None)
                if not sid_column or not alias_column:
                    continue
                for row in connection.execute(
                    f'SELECT "{sid_column}", "{alias_column}" FROM "{table}"'
                ):
                    sid = str(row[0] or "").strip()
                    record = self.storms.get(sid)
                    if record:
                        self._add_alias(sid, record.season, row[1])

    @staticmethod
    def _code(year: int, number: int) -> str:
        return f"{year % 100:02d}{number:02d}"

    def _possible_years(self, two_digit_year: int) -> tuple[int, ...]:
        candidates = (1900 + two_digit_year, 2000 + two_digit_year)
        available = {record.season for record in self.storms.values()}
        return tuple(year for year in candidates if year in available)

    @staticmethod
    def _looks_like_case_heading(text: str, start: int, end: int) -> bool:
        if any(marker in text[:500].lower() for marker in _CONTENTS_MARKERS):
            return False
        line_start = text.rfind("\n", 0, start) + 1
        line_end = text.find("\n", end)
        if line_end < 0:
            line_end = min(len(text), end + 160)
        line = text[line_start:line_end].strip()
        before = text[line_start:start]
        after = text[end:min(len(text), end + 100)]
        if len(before) > 24 or not _CASE_HEADING_PREFIX_RE.match(before):
            return False
        if re.match(r"^\s*\d{1,3}\s*[）).、]", line) and _CASE_HEADING_SUFFIX_RE.search(line):
            return True
        return start <= 360 and bool(re.search(r"台风\s*[“\"（(]", after))

    def _name_evidence_in_window(
        self, text: str, *, years: Iterable[int], start: int, end: int, heading_start: int
    ) -> list[_Evidence]:
        window = text[start:end]
        found: list[_Evidence] = []
        for year in years:
            for alias, sids in self.aliases_by_year.get(int(year), {}).items():
                pattern = self._alias_patterns.get(alias)
                match = pattern.search(window) if pattern else None
                if not match:
                    continue
                absolute_start = start + match.start()
                absolute_end = start + match.end()
                if len(sids) == 1:
                    sid = next(iter(sids))
                    found.append(_Evidence(
                        sid=sid,
                        confidence=0.98,
                        method="year_number_name",
                        matched_text=_clean_excerpt(text[heading_start:absolute_end]),
                        start=heading_start,
                        end=absolute_end,
                        is_heading=self._looks_like_case_heading(text, heading_start, absolute_end),
                    ))
                else:
                    for sid in sorted(sids):
                        found.append(_Evidence(
                            sid=sid,
                            confidence=0.70,
                            method="ambiguous_year_name",
                            matched_text=_clean_excerpt(text[heading_start:absolute_end]),
                            start=heading_start,
                            end=absolute_end,
                        ))
        return found

    def _detect(self, chunk: Mapping) -> list[_Evidence]:
        text = str(chunk.get("content") or chunk.get("text") or "")
        metadata = {**_document_metadata(chunk), **_metadata(chunk)}
        evidence: list[_Evidence] = []

        explicit_values: list[str] = []
        for key in ("sid", "storm_sid"):
            if metadata.get(key):
                explicit_values.append(str(metadata[key]))
        for key in ("sids", "storm_sids"):
            value = metadata.get(key)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                explicit_values.extend(str(item) for item in value)
        for sid in dict.fromkeys(explicit_values):
            if sid in self.storms:
                evidence.append(_Evidence(
                    sid=sid,
                    confidence=1.0,
                    method="validated_metadata_sid",
                    matched_text=sid,
                    start=0,
                    end=0,
                    is_heading=True,
                ))

        candidate_sid = str(metadata.get("candidate_sid") or "").strip()
        candidate_level = str(metadata.get("candidate_link_confidence") or "").lower()
        if candidate_sid in self.storms and candidate_level in {"medium", "low"}:
            confidence = 0.82 if candidate_level == "medium" else 0.65
            evidence.append(_Evidence(
                sid=candidate_sid,
                confidence=confidence,
                method="legacy_event_time_track_candidate",
                matched_text=f"候选SID（{candidate_level}，待复核）",
                start=0,
                end=0,
                is_heading=True,
            ))

        for match in _SID_RE.finditer(text.upper()):
            sid = match.group(1).upper()
            if sid in self.storms:
                evidence.append(_Evidence(
                    sid=sid,
                    confidence=1.0,
                    method="literal_ibtracs_sid",
                    matched_text=match.group(0),
                    start=match.start(),
                    end=match.end(),
                    is_heading=self._looks_like_case_heading(text, match.start(), match.end()),
                ))

        code_matches: list[tuple[re.Match, int, int, str]] = []
        for match in _LONG_CODE_RE.finditer(text):
            year, number = int(match.group("year")), int(match.group("number"))
            code_matches.append((match, year, number, self._code(year, number)))
        occupied = [(match.start(), match.end()) for match, *_ in code_matches]
        for match in _COMPACT_CODE_RE.finditer(text):
            if any(left <= match.start() < right for left, right in occupied):
                continue
            yy, number = int(match.group("yy")), int(match.group("number"))
            code = f"{yy:02d}{number:02d}"
            known = self.code_to_sids.get(code, set())
            if known:
                years = tuple(
                    self.storms[sid].season for sid in known if self.storms[sid].season is not None
                )
                year = int(years[0]) if len(set(years)) == 1 else 2000 + yy
            else:
                possible = self._possible_years(yy)
                year = possible[-1] if possible else 2000 + yy
            code_matches.append((match, year, number, code))

        for match, year, number, code in code_matches:
            known = set(self.code_to_sids.get(code, set()))
            known.update(self.year_number_to_sids.get((year, number), set()))
            if len(known) == 1:
                sid = next(iter(known))
                evidence.append(_Evidence(
                    sid=sid,
                    confidence=0.99,
                    method="reviewed_annual_designation",
                    matched_text=_clean_excerpt(match.group(0)),
                    start=match.start(),
                    end=match.end(),
                    is_heading=self._looks_like_case_heading(text, match.start(), match.end()),
                ))
            elif len(known) > 1:
                for sid in sorted(known):
                    evidence.append(_Evidence(
                        sid=sid,
                        confidence=0.70,
                        method="ambiguous_annual_designation",
                        matched_text=_clean_excerpt(match.group(0)),
                        start=match.start(),
                        end=match.end(),
                    ))
            else:
                possible_years = self._possible_years(int(code[:2])) or (year,)
                evidence.extend(self._name_evidence_in_window(
                    text,
                    years=possible_years,
                    start=max(0, match.start() - 40),
                    end=min(len(text), match.end() + 180),
                    heading_start=match.start(),
                ))

        # A year and name pair may occur without an annual designation.  Keep
        # the distance bounded so a year in a bibliography cannot identify an
        # unrelated name at the other end of a long chunk.
        for year_match in _YEAR_RE.finditer(text):
            year = int(year_match.group(1))
            for alias, sids in self.aliases_by_year.get(year, {}).items():
                pattern = self._alias_patterns.get(alias)
                left = max(0, year_match.start() - 160)
                right = min(len(text), year_match.end() + 160)
                alias_match = pattern.search(text[left:right]) if pattern else None
                if not alias_match:
                    continue
                absolute_start = left + alias_match.start()
                absolute_end = left + alias_match.end()
                if len(sids) == 1:
                    evidence.append(_Evidence(
                        sid=next(iter(sids)),
                        confidence=0.97,
                        method="unambiguous_year_name",
                        matched_text=_clean_excerpt(text[min(year_match.start(), absolute_start):max(year_match.end(), absolute_end)]),
                        start=min(year_match.start(), absolute_start),
                        end=max(year_match.end(), absolute_end),
                        is_heading=False,
                    ))
                else:
                    for sid in sorted(sids):
                        evidence.append(_Evidence(
                            sid=sid,
                            confidence=0.70,
                            method="ambiguous_year_name",
                            matched_text=_clean_excerpt(alias_match.group(0)),
                            start=absolute_start,
                            end=absolute_end,
                        ))

        # Keep the strongest evidence for each SID.  If strengths tie, heading
        # evidence wins because it can safely drive forward propagation.
        strongest: dict[str, _Evidence] = {}
        for item in evidence:
            previous = strongest.get(item.sid)
            if previous is None or (item.confidence, item.is_heading) > (previous.confidence, previous.is_heading):
                strongest[item.sid] = item
        return sorted(strongest.values(), key=lambda item: (item.start, -item.confidence, item.sid))

    @staticmethod
    def _logical_document_key(chunk: Mapping) -> str:
        metadata = {**_document_metadata(chunk), **_metadata(chunk)}
        return str(
            metadata.get("original_sha256")
            or metadata.get("logical_document_id")
            or chunk.get("document_id")
            or "__single_document__"
        )

    @staticmethod
    def _sort_key(chunk: Mapping) -> tuple[int, int, int]:
        metadata = {**_document_metadata(chunk), **_metadata(chunk)}
        try:
            part = int(metadata.get("part_index") or 0)
        except (TypeError, ValueError):
            part = 0
        try:
            index = int(chunk.get("chunk_index") or 0)
        except (TypeError, ValueError):
            index = 0
        return part, index, int(chunk.get("chunk_id") or 0)

    def link_chunks(self, chunks: Iterable[Mapping]) -> list[ChunkLink]:
        """Return deterministic links for chunks, including chapter context."""

        grouped: dict[str, list[Mapping]] = defaultdict(list)
        for chunk in chunks:
            if chunk.get("chunk_id") is None:
                raise ValueError("每个切片必须包含 chunk_id")
            grouped[self._logical_document_key(chunk)].append(chunk)

        result: list[ChunkLink] = []
        for items in grouped.values():
            active: list[_Evidence] = []
            for chunk in sorted(items, key=self._sort_key):
                chunk_id = int(chunk["chunk_id"])
                text = str(chunk.get("content") or chunk.get("text") or "")
                direct = self._detect(chunk)
                headings = [item for item in direct if item.is_heading]
                by_sid: dict[str, ChunkLink] = {}

                for item in direct:
                    by_sid[item.sid] = ChunkLink(
                        chunk_id=chunk_id,
                        sid=item.sid,
                        confidence=item.confidence,
                        link_method=item.method,
                        matched_text=item.matched_text,
                        review_status=_review_status(item.confidence),
                        is_primary=item.is_heading,
                    )

                if headings:
                    first_heading = min(item.start for item in headings)
                    # A chunk can contain the tail of the prior chapter before a
                    # new heading.  Preserve that older SID as a secondary link.
                    if active and first_heading > 40 and text[:first_heading].strip():
                        for item in active:
                            by_sid.setdefault(item.sid, ChunkLink(
                                chunk_id=chunk_id,
                                sid=item.sid,
                                confidence=CONTEXT_CONFIDENCE,
                                link_method="preceding_chapter_boundary",
                                matched_text=_clean_excerpt(text[:first_heading]),
                                review_status="needs_review",
                                is_primary=False,
                            ))

                    latest = max(item.start for item in headings)
                    # Codes sharing the same heading (e.g. 1709 and 1710) are
                    # retained together; earlier headings in a boundary chunk
                    # do not leak into the next chunk.
                    active = [item for item in headings if latest - item.start <= 100]
                elif active:
                    for item in active:
                        by_sid.setdefault(item.sid, ChunkLink(
                            chunk_id=chunk_id,
                            sid=item.sid,
                            confidence=CONTEXT_CONFIDENCE,
                            link_method="chapter_context_propagation",
                            matched_text=item.matched_text,
                            review_status="needs_review",
                            is_primary=True,
                        ))

                # Event-level synthetic documents carry a reviewed identity in
                # metadata. Apply that identity to every chunk in the same
                # event document, including chunks which contain only numeric
                # station rows. This is metadata propagation, not inference.
                metadata = {**_document_metadata(chunk), **_metadata(chunk)}
                document_sid = str(metadata.get("sid") or "").strip()
                candidate_sid = str(metadata.get("candidate_sid") or "").strip()
                candidate_level = str(metadata.get("candidate_link_confidence") or "").lower()
                if document_sid in self.storms:
                    by_sid.setdefault(document_sid, ChunkLink(
                        chunk_id=chunk_id,
                        sid=document_sid,
                        confidence=1.0,
                        link_method="validated_document_metadata_sid",
                        matched_text=document_sid,
                        review_status="auto_accepted",
                        is_primary=True,
                    ))
                elif candidate_sid in self.storms and candidate_level in {"medium", "low"}:
                    confidence = 0.82 if candidate_level == "medium" else 0.65
                    by_sid.setdefault(candidate_sid, ChunkLink(
                        chunk_id=chunk_id,
                        sid=candidate_sid,
                        confidence=confidence,
                        link_method="legacy_document_candidate_propagation",
                        matched_text=f"候选SID（{candidate_level}，待复核）",
                        review_status="needs_review",
                        is_primary=True,
                    ))

                result.extend(sorted(by_sid.values(), key=lambda item: (not item.is_primary, -item.confidence, item.sid)))
        return result

    def link_rag_database(
        self,
        rag_database_path: str | Path,
        *,
        document_id: str | None = None,
        dry_run: bool = False,
        include_links: bool = False,
    ) -> dict:
        """Link existing RAG chunks and optionally persist the relationships."""

        rag_path = Path(rag_database_path).resolve()
        if not rag_path.exists():
            raise FileNotFoundError(f"RAG数据库不存在: {rag_path}")
        ensure_link_schema(rag_path)
        with closing(sqlite3.connect(rag_path)) as connection:
            connection.row_factory = sqlite3.Row
            sql = """
                SELECT c.chunk_id, c.chunk_index, c.content, c.metadata_json,
                       d.document_id, d.filename,
                       d.metadata_json AS document_metadata_json
                FROM knowledge_chunks c
                JOIN knowledge_documents d ON d.document_id = c.document_id
            """
            params: tuple = ()
            if document_id:
                sql += " WHERE d.document_id = ?"
                params = (document_id,)
            rows = [dict(row) for row in connection.execute(sql, params)]
        links = self.link_chunks(rows)
        if not dry_run:
            persist_links(rag_path, links, chunk_ids=[int(row["chunk_id"]) for row in rows])
        linked_chunks = {item.chunk_id for item in links}
        summary = {
            "dry_run": bool(dry_run),
            "method_version": METHOD_VERSION,
            "chunk_count": len(rows),
            "linked_chunk_count": len(linked_chunks),
            "unlinked_chunk_count": len(rows) - len(linked_chunks),
            "link_count": len(links),
            "auto_accepted_count": sum(item.review_status == "auto_accepted" for item in links),
            "needs_review_count": sum(item.review_status == "needs_review" for item in links),
        }
        if include_links:
            summary["links"] = [item.to_dict() for item in links]
        return summary


LINK_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_chunk_storm_links (
    chunk_id INTEGER NOT NULL,
    sid TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    link_method TEXT NOT NULL,
    matched_text TEXT NOT NULL,
    review_status TEXT NOT NULL CHECK (review_status IN ('auto_accepted', 'needs_review', 'rejected')),
    method_version TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (chunk_id, sid),
    FOREIGN KEY (chunk_id) REFERENCES knowledge_chunks(chunk_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chunk_storm_links_sid
    ON knowledge_chunk_storm_links(sid, review_status, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_chunk_storm_links_chunk
    ON knowledge_chunk_storm_links(chunk_id, is_primary DESC);
"""


def ensure_link_schema(rag_database_path: str | Path) -> None:
    path = Path(rag_database_path).resolve()
    with closing(sqlite3.connect(path, timeout=20)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        if not _table_exists(connection, "knowledge_chunks"):
            raise ValueError("RAG数据库缺少 knowledge_chunks 表")
        connection.executescript(LINK_SCHEMA)
        connection.commit()


def persist_links(
    rag_database_path: str | Path,
    links: Iterable[ChunkLink],
    *,
    chunk_ids: Iterable[int] | None = None,
) -> int:
    """Atomically replace links for the supplied chunks and return row count."""

    path = Path(rag_database_path).resolve()
    ensure_link_schema(path)
    materialized = list(links)
    affected = sorted({int(item.chunk_id) for item in materialized} | {int(value) for value in (chunk_ids or [])})
    now = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(path, timeout=20)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            for offset in range(0, len(affected), 900):
                batch = affected[offset:offset + 900]
                placeholders = ",".join("?" for _ in batch)
                connection.execute(
                    f"DELETE FROM knowledge_chunk_storm_links WHERE chunk_id IN ({placeholders})",
                    batch,
                )
            connection.executemany(
                """
                INSERT INTO knowledge_chunk_storm_links (
                    chunk_id, sid, confidence, link_method, matched_text,
                    review_status, method_version, is_primary, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item.chunk_id, item.sid, float(item.confidence), item.link_method,
                        item.matched_text, item.review_status, item.method_version,
                        1 if item.is_primary else 0, now,
                    )
                    for item in materialized
                ],
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return len(materialized)


def _main() -> int:
    parser = argparse.ArgumentParser(description="为RAG切片确定性关联IBTrACS SID")
    parser.add_argument("--history-db", required=True, type=Path)
    parser.add_argument("--rag-db", required=True, type=Path)
    parser.add_argument("--document-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-links", action="store_true")
    args = parser.parse_args()
    result = TyphoonEntityLinker(args.history_db).link_rag_database(
        args.rag_db,
        document_id=args.document_id,
        dry_run=args.dry_run,
        include_links=args.include_links,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
