"""Explicit, private recording and strict offline replay of QA dependencies.

This module never loads configuration or imports an HTTP/model SDK. Replayers
have no live fallback. Raw requests/responses belong only in a local private
cassette, never in public answer traces or a repository fixture.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import threading
from typing import Any


DEFAULT_PRIVATE_ROOT = Path(__file__).resolve().parents[1] / "tmp" / "replay"
SCHEMA_VERSION = 1
_SENSITIVE_OPTIONS = re.compile(r"^(?:api_?key|access_token|refresh_token|password|secret|authorization|headers)$", re.I)
_INTERACTION_KIND = re.compile(r"[a-z][a-z0-9_]{0,31}")


class CassetteError(RuntimeError):
    """A cassette is unavailable, inconsistent, or cannot satisfy a replay."""


class CassetteMismatch(CassetteError):
    pass


class CassetteIncomplete(CassetteError):
    pass


class CassettePrivacyError(CassetteError):
    pass


class RecordedCallError(CassetteError):
    """A recorded custom dependency exception; never dynamically import types."""

    def __init__(self, error_type: str, code: str):
        self.error_type, self.code = error_type, code
        super().__init__(f"recorded_dependency_error:{error_type}:{code}")


def _wire(value: Any) -> Any:
    """A small typed JSON codec, without pickle or arbitrary object hooks."""
    if value is None or isinstance(value, (str, bool, int)):
        return ["scalar", value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CassetteError("cassette_non_finite_number")
        return ["scalar", value]
    if isinstance(value, (tuple, list)):
        return ["tuple" if isinstance(value, tuple) else "list", [_wire(v) for v in value]]
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        return ["dict", [[k, _wire(value[k])] for k in sorted(value)]]
    raise CassetteError("cassette_unsupported_value_type")


def _unwire(value: Any) -> Any:
    if not isinstance(value, list) or len(value) != 2:
        raise CassetteError("cassette_invalid_value")
    kind, payload = value
    if not isinstance(kind, str):
        raise CassetteError("cassette_invalid_value")
    if kind == "scalar" and (payload is None or isinstance(payload, (str, bool, int, float))):
        if isinstance(payload, float) and not math.isfinite(payload):
            raise CassetteError("cassette_invalid_value")
        return payload
    if kind in {"tuple", "list"} and isinstance(payload, list):
        items = [_unwire(item) for item in payload]
        return tuple(items) if kind == "tuple" else items
    if kind == "dict" and isinstance(payload, list):
        result = {}
        for entry in payload:
            if (not isinstance(entry, list) or len(entry) != 2
                    or not isinstance(entry[0], str) or entry[0] in result):
                raise CassetteError("cassette_invalid_value")
            result[entry[0]] = _unwire(entry[1])
        return result
    raise CassetteError("cassette_invalid_value")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _safe_error(exc: Exception) -> dict:
    # Keep machine codes for deterministic control flow, never arbitrary raw
    # exception text (an SDK exception may contain an authorization header).
    text = str(exc)
    code = text if re.fullmatch(r"[a-z][a-z0-9_]{0,100}", text) else "dependency_call_failed"
    return {"type": type(exc).__name__, "code": code}


def _raise_recorded(error: dict) -> None:
    error_type, code = error.get("type", "Exception"), error.get("code", "dependency_call_failed")
    known = {"ValueError": ValueError, "RuntimeError": RuntimeError,
             "TimeoutError": TimeoutError, "ConnectionError": ConnectionError,
             "OSError": OSError, "TypeError": TypeError}
    if error_type in known:
        raise known[error_type](code)
    raise RecordedCallError(error_type, code)


class ModelCassette:
    """One append-only local journal for model and reader interactions.

    Recording is opt-in twice: mode='record' and allow_sensitive_recording=True.
    Destinations must remain below private_root; the default is app/tmp/replay.
    An existing file is never overwritten. A success footer is written only by
    close() / a successful context manager exit after all interactions finish.

    ordering='ordered' validates global request order. 'per_key' permits
    concurrent independent requests to reorder but preserves repeated identical
    request order. Both modes require every requested fingerprint to exist.
    """

    def __init__(self, path, *, mode: str = "replay", allow_sensitive_recording: bool = False,
                 private_root=None, ordering: str = "ordered"):
        if mode not in {"record", "replay"} or ordering not in {"ordered", "per_key"}:
            raise ValueError("invalid_cassette_mode")
        self.path = Path(path).expanduser().resolve()
        self.mode, self.ordering = mode, ordering
        self._lock = threading.RLock()
        self._file = None
        self._closed = False
        self._next_seq = 0
        self._inflight = set()
        self._written = {}
        self._records = []
        self._cursor = 0
        self._used = set()
        self._queues = defaultdict(deque)
        self._write_failed = False
        if mode == "record":
            if not allow_sensitive_recording:
                raise CassettePrivacyError("cassette_private_recording_not_enabled")
            root = Path(private_root or DEFAULT_PRIVATE_ROOT).expanduser().resolve()
            if self.path == root or not self.path.is_relative_to(root):
                raise CassettePrivacyError("cassette_path_outside_private_root")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # x-mode prevents accidental truncation/reuse by concurrent writers.
            self._file = self.path.open("x", encoding="utf-8", newline="\n")
            try:
                os.chmod(self.path, 0o600)
                self._append({"record_type": "header", "schema_version": SCHEMA_VERSION,
                              "created_at": datetime.now(timezone.utc).isoformat(),
                              "contains_private_raw_data": True})
            except BaseException:
                self._file.close()
                self._closed = True
                raise
        else:
            self._load()

    def _append(self, row: dict) -> None:
        try:
            self._file.write(_json(row) + "\n")
            self._file.flush()
            os.fsync(self._file.fileno())
        except BaseException:
            self._write_failed = True
            raise

    def _load(self) -> None:
        try:
            raw = self.path.read_text(encoding="utf-8")
            if not raw.endswith("\n"):
                raise CassetteIncomplete("cassette_missing_completion_footer")
            rows = [json.loads(line) for line in raw.splitlines()]
        except (OSError, UnicodeError, ValueError) as exc:
            raise CassetteIncomplete("cassette_unreadable_or_truncated") from exc
        if (len(rows) < 2 or not all(isinstance(row, dict) for row in rows)
                or rows[0].get("record_type") != "header"
                or type(rows[0].get("schema_version")) is not int
                or rows[0].get("schema_version") != SCHEMA_VERSION):
            raise CassetteError("cassette_invalid_header")
        footer = rows[-1]
        if footer.get("record_type") != "complete":
            raise CassetteIncomplete("cassette_missing_completion_footer")
        records = rows[1:-1]
        if type(footer.get("count")) is not int or footer.get("count") != len(records):
            raise CassetteIncomplete("cassette_record_count_mismatch")
        if any(row.get("record_type") != "interaction" for row in records):
            raise CassetteError("cassette_invalid_record")
        if (any(type(row.get("seq")) is not int for row in records)
                or sorted(row["seq"] for row in records) != list(range(len(records)))):
            raise CassetteIncomplete("cassette_sequence_incomplete")
        records.sort(key=lambda row: row["seq"])
        try:
            records_hash = _sha(records)
        except (TypeError, ValueError) as exc:
            raise CassetteError("cassette_invalid_value") from exc
        if footer.get("records_sha256") != records_hash:
            raise CassetteError("cassette_integrity_mismatch")
        for row in records:
            if (not isinstance(row.get("kind"), str)
                    or not _INTERACTION_KIND.fullmatch(row["kind"])
                    or row.get("request_sha256") != _sha(row.get("request"))):
                raise CassetteError("cassette_request_integrity_mismatch")
            _unwire(row["request"])
            if row.get("outcome") == "return":
                if row.get("response_sha256") != _sha(row.get("response")):
                    raise CassetteError("cassette_response_integrity_mismatch")
                _unwire(row["response"])
            elif row.get("outcome") == "error":
                if (not isinstance(row.get("error"), dict)
                        or not isinstance(row["error"].get("type"), str)
                        or not isinstance(row["error"].get("code"), str)):
                    raise CassetteError("cassette_invalid_error")
            else:
                raise CassetteError("cassette_invalid_outcome")
            self._queues[(row["kind"], row["request_sha256"])].append(row)
        self._records = records

    def invoke(self, kind: str, request: Any, live=None) -> Any:
        if not isinstance(kind, str) or not _INTERACTION_KIND.fullmatch(kind):
            raise ValueError("invalid_cassette_interaction_kind")
        wire_request = _wire(request)
        fingerprint = _sha(wire_request)
        with self._lock:
            if self._closed:
                raise CassetteError("cassette_closed")
            if self.mode == "replay":
                if live is not None:
                    raise CassetteError("cassette_replay_cannot_have_live_fallback")
                if self.ordering == "ordered":
                    if self._cursor >= len(self._records):
                        raise CassetteMismatch("cassette_replay_exhausted")
                    row = self._records[self._cursor]
                    if row["kind"] != kind or row["request_sha256"] != fingerprint:
                        raise CassetteMismatch("cassette_request_or_order_mismatch")
                    self._cursor += 1
                else:
                    queue = self._queues[(kind, fingerprint)]
                    if not queue:
                        raise CassetteMismatch("cassette_request_not_recorded_or_exhausted")
                    row = queue.popleft()
                self._used.add(row["seq"])
                if row["outcome"] == "error":
                    _raise_recorded(row["error"])
                return _unwire(row["response"])
            if not callable(live):
                raise CassetteError("cassette_record_requires_live_dependency")
            seq = self._next_seq
            self._next_seq += 1
            self._inflight.add(seq)
        row = {"record_type": "interaction", "seq": seq, "kind": kind,
               "request_sha256": fingerprint, "request": wire_request}
        try:
            response = live()
            wire_response = _wire(response)
            row.update(outcome="return", response=wire_response,
                       response_sha256=_sha(wire_response))
        except Exception as exc:
            row.update(outcome="error", error=_safe_error(exc))
            with self._lock:
                self._append(row)
                self._written[seq] = row
                self._inflight.remove(seq)
            raise
        except BaseException:
            # Interrupted calls cannot be represented as a complete recording.
            with self._lock:
                self._write_failed = True
                self._inflight.remove(seq)
            raise
        with self._lock:
            self._append(row)
            self._written[seq] = row
            self._inflight.remove(seq)
        return response

    def assert_consumed(self) -> None:
        if self.mode != "replay":
            raise CassetteError("cassette_consumption_requires_replay")
        if len(self._used) != len(self._records):
            raise CassetteMismatch("cassette_unconsumed_interactions")

    def summary(self) -> dict:
        """Metadata only: no prompts, URLs, responses, or private error text."""
        rows = self._records if self.mode == "replay" else list(self._written.values())
        return {"schema_version": SCHEMA_VERSION, "mode": self.mode,
                "interactions": len(rows), "consumed": len(self._used),
                "model_calls": sum(row["kind"] == "model" for row in rows),
                "reader_calls": sum(row["kind"] == "reader" for row in rows),
                "search_calls": sum(row["kind"] == "search" for row in rows),
                "dependency_errors": sum(row["outcome"] == "error" for row in rows)}

    def close(self, *, complete: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            if self.mode == "replay" and complete:
                self.assert_consumed()
            if self.mode == "record":
                if self._inflight:
                    raise CassetteIncomplete("cassette_calls_still_running")
                try:
                    if complete and not self._write_failed:
                        rows = [self._written[i] for i in range(self._next_seq)]
                        self._append({"record_type": "complete", "count": len(rows),
                                      "records_sha256": _sha(rows)})
                finally:
                    self._file.close()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close(complete=exc_type is None)


def _model_request(args, kwargs):
    if kwargs.get("stream", True) is not False:
        raise CassetteError("cassette_streaming_not_supported")
    if any(_SENSITIVE_OPTIONS.search(key) for key in kwargs):
        raise CassettePrivacyError("cassette_credentials_must_not_be_call_options")
    return {"args": args, "kwargs": kwargs}


class RecordingCaller:
    def __init__(self, caller, cassette: ModelCassette):
        if cassette.mode != "record" or not callable(caller):
            raise ValueError("recording_caller_requires_record_mode")
        self.caller, self.cassette = caller, cassette
        try:
            self.__signature__ = inspect.signature(caller)
        except (TypeError, ValueError):
            pass

    def __call__(self, *args, **kwargs):
        request = _model_request(args, kwargs)
        return self.cassette.invoke("model", request, lambda: self.caller(*args, **kwargs))


class ReplayCaller:
    def __init__(self, cassette: ModelCassette):
        if cassette.mode != "replay":
            raise ValueError("replay_caller_requires_replay_mode")
        self.cassette = cassette
        model_requests = [_unwire(row["request"]) for row in cassette._records if row["kind"] == "model"]
        if any(not isinstance(request, dict) or not isinstance(request.get("kwargs"), dict)
               or not isinstance(request.get("args"), tuple) for request in model_requests):
            raise CassetteError("cassette_invalid_record")
        self.supports_return_usage = any(request["kwargs"].get("return_usage") is True
                                       for request in model_requests)
        parameters = [inspect.Parameter("args", inspect.Parameter.VAR_POSITIONAL)]
        if self.supports_return_usage:
            parameters.append(inspect.Parameter("return_usage", inspect.Parameter.KEYWORD_ONLY, default=False))
        parameters.append(inspect.Parameter("kwargs", inspect.Parameter.VAR_KEYWORD))
        self.__signature__ = inspect.Signature(parameters)

    def __call__(self, *args, **kwargs):
        return self.cassette.invoke("model", _model_request(args, kwargs))


class ReaderCassette:
    """Record/replay reader.enrich, including failed reads and read timestamps.

    Replay construction requires reader=None, making live fallback impossible.
    Place around PublicReferenceReader to also freeze its reviewed-cache result.
    Search and local retrieval must be independently frozen by an offline harness;
    wrapping only a reader cannot make a whole QA service network-free.
    """

    def __init__(self, reader, cassette: ModelCassette):
        if (cassette.mode == "replay" and reader is not None
                or cassette.mode == "record" and not callable(getattr(reader, "enrich", None))):
            raise ValueError("reader_cassette_mode_mismatch")
        self.reader, self.cassette = reader, cassette

    def enrich(self, rows):
        live = (lambda: self.reader.enrich(rows)) if self.cassette.mode == "record" else None
        return self.cassette.invoke("reader", {"rows": rows}, live)

    def status(self):
        return {"enabled": True, "cassette_mode": self.cassette.mode,
                "live_fallback": False, "content_scope": "recorded_reader_result"}


class SearchCassette:
    """Freeze public search outcomes as well as reader bodies, with no fallback."""

    def __init__(self, search_client, cassette: ModelCassette):
        if (cassette.mode == "replay" and search_client is not None
                or cassette.mode == "record"
                and not callable(getattr(search_client, "search_with_status", None))):
            raise ValueError("search_cassette_mode_mismatch")
        self.search_client, self.cassette = search_client, cassette
        self.enabled = True

    def search_with_status(self, query, *, max_results=None, subject="", aliases=()):
        from dataclasses import asdict
        from typhoon.web_search import SearchOutcome
        request = {"query": query, "max_results": max_results, "subject": subject, "aliases": aliases}
        live = None
        if self.cassette.mode == "record":
            live = lambda: asdict(self.search_client.search_with_status(
                query, max_results=max_results, subject=subject, aliases=aliases))
        row = self.cassette.invoke("search", request, live)
        return SearchOutcome(**row)

    def search(self, query, *, max_results=None):
        return self.search_with_status(query, max_results=max_results).results
