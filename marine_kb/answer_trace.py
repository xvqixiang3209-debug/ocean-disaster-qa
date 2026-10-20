"""Opt-in request-local diagnostics without prompt or evidence text in public output.

Callers own request boundaries with :func:`trace_context`. The context variable
keeps simultaneous requests isolated; it does not require changing model callers'
signatures. Sensitive recording belongs in a separately enabled local cassette.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import re
import threading
import time
from typing import Any, Iterator
from uuid import uuid4


_CURRENT: ContextVar["AnswerTrace | None"] = ContextVar("marine_answer_trace", default=None)
_CODE = re.compile(r"[a-z][a-z0-9_]{0,95}\Z")
_LABEL = re.compile(r"[A-Za-z0-9_.@:-]{1,128}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_PRIVATE_KEYS = re.compile(
    r"^(?:system|system_prompt|prompt|question|answer|raw|response|content|text|"
    r"messages|api_key|access_token|secret|password|cookie|authorization|"
    r"request_headers|response_headers)$", re.I,
)
_CREDENTIAL = re.compile(r"(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|Bearer\s+\S+)", re.I)

# Public exception messages must be known project/provider codes, not merely ASCII.
# New validators remain visible as exception types until their code is registered.
_ERROR_CODES = frozenset("""
blocked
boundary_requires_insufficient_answer
business_delivery_comparison_delta_mismatch
business_delivery_comparison_operand_mismatch
business_delivery_historical_jurisdiction_mismatch
business_delivery_missing_analogue
business_delivery_missing_citations
business_delivery_missing_comparison_delta
business_delivery_missing_comparison_sampling_scope
business_delivery_missing_coverage
business_delivery_missing_coverage_scope
business_delivery_missing_cyclone_attribution_scope
business_delivery_missing_elevation
business_delivery_missing_inundation_scope
business_delivery_missing_population
business_delivery_object_date_mismatch
business_delivery_object_range_mismatch
business_delivery_population_requires_valid_cells_scope
business_delivery_series_claim_contradiction
business_delivery_uncomputed_inundation_value
business_delivery_uncomputed_overall_disaster_comparison
business_delivery_unknown_typhoon_count_scope
business_delivery_unverified_active_typhoon_count
cassette_calls_still_running
cassette_closed
cassette_consumption_requires_replay
cassette_credentials_must_not_be_call_options
cassette_integrity_mismatch
cassette_invalid_error
cassette_invalid_header
cassette_invalid_outcome
cassette_invalid_record
cassette_invalid_value
cassette_missing_completion_footer
cassette_non_finite_number
cassette_path_outside_private_root
cassette_private_recording_not_enabled
cassette_record_count_mismatch
cassette_record_requires_live_dependency
cassette_replay_cannot_have_live_fallback
cassette_replay_exhausted
cassette_request_integrity_mismatch
cassette_request_not_recorded_or_exhausted
cassette_request_or_order_mismatch
cassette_response_integrity_mismatch
cassette_sequence_incomplete
cassette_streaming_not_supported
cassette_unconsumed_interactions
cassette_unreadable_or_truncated
cassette_unsupported_value_type
citation_repair_invalid_line
citation_repair_invalid_source
citation_repair_line_limit
citation_repair_missing_citation
citation_repair_missing_line_choices
citation_repair_unknown_passage
citation_repair_unsupported_lines
citation_without_supporting_quote
comparison_kept_previous_region
context_length_exceeded
coverage_review_unknown_span
coverage_review_unknown_target
current_version_not_verified
delivery_citation_not_in_span
delivery_requires_answer_span
delivery_span_not_in_answer
document_version_year_mismatch
empty_web_answer
explicit_activity_notice_not_found
external_model_disabled
failed
followup_not_resolved
full_delivery_has_gaps
general_explanation_cannot_invent_sources
general_explanation_contains_business_quantity
general_explanation_contains_live_claim
grid_download_too_large
grounding_claim_limit
grounding_incomplete_batch_review
grounding_incomplete_review
grounding_invalid_refs
grounding_invalid_source
grounding_invalid_status
grounding_missing_refs
grounding_quote_not_in_passage
grounding_unexpected_refs
grounding_unknown_claim
identity_changed
incomplete_delivery_review
incomplete_evaluation
index_shape
insufficient_answer_must_explain_gap
insufficient_quota
invalid_activity_rules
invalid_activity_screening_limit
invalid_answer_length
invalid_answer_structure
invalid_api_key
invalid_cassette_interaction_kind
invalid_cassette_mode
invalid_citation_repair_draft
invalid_coverage_review
invalid_day
invalid_delivery_gaps
invalid_delivery_review
invalid_delivery_status
invalid_evaluation
invalid_evidence_passage
invalid_evidence_quote
invalid_evidence_quotes
invalid_file_directory
invalid_general_explanation
invalid_grid_filename
invalid_grounding_draft
invalid_netcdf_download
invalid_normalization
invalid_official_activity_catalog
invalid_official_activity_policy
invalid_official_activity_rules
invalid_official_numeric_rule
invalid_operator_date_range
invalid_operator_time
invalid_operator_time_range
invalid_search_policy
invalid_source_as_of
invalid_statistics_years
invalid_typhoon_response
invalid_typhoon_snapshot_source
invalid_vocabulary
missing_evidence_quotes
missing_or_unknown_citation
model_not_found
no_web_evidence
nonfinite_index
notice_body_missing
notice_effective_date_reversed
notice_publication_time_missing
notice_schema_changed
official_body_schema_changed
official_body_too_large
official_body_url_not_allowed
official_list_schema_changed
official_list_unsuccessful
official_response_not_json
official_response_schema_changed
official_response_too_large
official_rule_recreational_fishing_exemption
official_rule_temperature_interval_boundary
operator_article_body_missing
operator_hours_not_found
operator_notice_not_found
operator_schedule_not_found
operator_supported_content_missing
partial_delivery_needs_specific_gap
per_object_time_changed
place_or_business_object_changed
quality_measurement_unavailable
quality_not_monitored_contradicts_measurements
quality_observation_after_publication
quality_observation_date_missing
quality_report_not_found
quality_row_incomplete
quality_row_not_unique
quality_site_not_registered
quality_temperature_missing
quality_temperature_out_of_range
quantity_not_supported_by_cited_text
query_cutoff_changed
query_time_changed
quote_not_in_source
quote_repair_invalid_json
quote_repair_invalid_selection
quote_repair_unknown_source
quote_repair_unsupported
rate_limit_exceeded
reader_cassette_mode_mismatch
recording_caller_requires_record_mode
replay_caller_requires_replay_mode
scenario_modality_changed
search_cassette_mode_mismatch
semantic_evidence_support_failed
source_content_type_not_html
source_http_unavailable
source_index_not_allowed
source_size_or_time_limit
source_too_large
source_url_not_allowed
statistics_outside_archive
subquestion_evidence_scope_mismatch
table_column_count_mismatch
table_numeric_cell_not_parsed
table_row_identity_missing
table_row_or_boundary_not_supported
uncited_quantity
unexpected_cache_destination
unexpected_download_host
unexpected_download_redirect
unknown_activity
unknown_delivery_part
unknown_evidence_passage
unknown_warning_hazard
unknown_warning_provider
unsupported
unsupported_insufficient_answer
zhejiang_service_token_missing
""".split())
_MAX_EVENTS = 256
_MAX_MODEL_CALLS = 128
_MAX_ITEMS = 64
_MAX_DEPTH = 8
_FIELD_KEYS = frozenset("""
    attempt attempt_index generation_attempts validation_error error_code error_type
    stage state status reason reason_code code validator verdict issues kind type target_id part_id
    issue_types issue_sha256 evidence_sha256 draft_sha256 result_sha256 part_rows_sha256
    validation_errors coverage_review_history completeness_error_code retained drop_rule
    claim_count retained_claims dropped_claims decision_counts history_index source_count
    candidate_count selected_count facet_count passage_count passages view_sha256
    passage_id sha256 matching views citations facet_ids source_ids id index count total
    supported unsupported irrelevant limitation decision before after trigger operation
    retained_count dropped_count calls elapsed_ms latency_ms hash source citation
    retained_sha256 dropped_sha256 target_hash part_rows review_summary answerability
    has_supported_quotes decisions claim_sha256 reference_error deterministic_error
    missing_information question prompt answer text raw response content reason
    truncated_source_count truncated_passage_count
    publishable delivery fid aid atom_ids substantive_atom_ids allowed_gap_atom_ids
    covered_outputs missing_outputs relation_review severity render_mode judge_consistency
""".split())
_ERROR_CODES = _ERROR_CODES | frozenset("""
answer_call_budget_exceeded answer_time_budget_exceeded
document_identity_contains_body_fact document_identity_date_mismatch document_identity_date_role_unspecified
document_identity_name_mismatch document_identity_not_in_card document_identity_unverified_tail
document_identity_requires_v2_card
facet_ground_budget_exhausted frozen_sibling_mutated invalid_typed_grounding_draft judge_inconsistent
rejected_atom_not_substring scope_note_contains_quantity synthesis_comparison_incomplete
synthesis_comparison_not_identity_proof synthesis_conclusion_not_entailed synthesis_condition_or_negation
synthesis_incomparable_relations synthesis_invalid_premises synthesis_invalid_relation_slot synthesis_new_quantity
synthesis_not_allowed_for_facet synthesis_objects_incomplete synthesis_premise_has_condition
synthesis_premise_has_qualified_relation synthesis_premise_not_grounded synthesis_relation_not_in_premise
synthesis_unchecked_conclusion synthesis_unchecked_relation synthesis_unknown_comparison_field
typed_grounding_atom_limit typed_grounding_draft_length typed_grounding_facet_reassignment
typed_grounding_fact_status typed_grounding_incomplete_review typed_grounding_invalid_refs
typed_grounding_invalid_source typed_grounding_invalid_status typed_grounding_invalid_type
typed_grounding_limitation_assertion typed_grounding_limitation_status typed_grounding_not_a_limitation
typed_grounding_quote_not_in_passage typed_grounding_unasked_gap typed_grounding_unexpected_refs
typed_grounding_unknown_atom typed_grounding_unknown_facet typed_grounding_unknown_passage
relation_vector_mismatch relation_mixed_vector_context relation_health_source_required
relation_actor_role_mismatch relation_guard_recheck_required
relation_unasked_chemical_dose
""".split())
_PUBLIC_VALUES = _ERROR_CODES | frozenset("""
typed_grounding typed_publication typed_answer facet_regenerate
document_identity direct_fact synthesis calculation gap scope_note
grounded with_gaps supported unsupported irrelevant limitation
judge_recheck facet_search facet_regen facet_ground
""".split()) | frozenset("""
    normalize crag_eval crag_eval_corrective generate coverage_review claim_grounding
    completeness citation_repair citation_format_repair quote_repair guidance answer retrieval web_read
    web_search local_search unknown stage_failure evidence_view validation request
    full partial insufficient supported unsupported irrelevant limitation pass revise
    unavailable pending direct_fact document_identity synthesis calculation gap scope_note
    unasked_gap unsupported_claim contradiction ambiguous correct incorrect
    retained dropped accepted rejected limitation_has_citation limitation_vocab_miss
    no_claim_text unsupported_status no_supported_refs crag_source_remap draft_validation
    claim_retention reference_check_failed deterministic_check_failed
    limitation_has_quantities limitation_scope_miss model_unsupported model_irrelevant
    model_rejected_by_reference_check model_rejected_by_deterministic_check
    rejected_by_reference_check rejected_by_deterministic_check
    crag_candidates crag_selected generation
""".split())
_ERROR_TYPE_NAMES = frozenset("""
    Exception BaseException ValueError RuntimeError TypeError KeyError AttributeError
    TimeoutError ConnectionError OSError IOError PermissionError FileNotFoundError
    AssertionError IndexError LookupError UnicodeError JSONDecodeError
    APIConnectionError APITimeoutError APIStatusError AuthenticationError RateLimitError
    BadRequestError NotFoundError PermissionDeniedError InternalServerError OpenAIError
    CassetteError CassetteReplayError
""".split())


def sha256_text(value: Any) -> str:
    """Hash exact text; structured values use a stable JSON representation."""
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def trace_enabled() -> bool:
    return os.environ.get("MARINE_TRACE", "false").strip().lower() in {"1", "true", "yes", "on"}


def error_code(exc: BaseException) -> str:
    """Keep controlled validator codes, never API error messages or credentials."""
    for value in (getattr(exc, "code", None), str(exc)):
        if isinstance(value, str) and value in _ERROR_CODES:
            return value
    if type(exc).__name__ not in _ERROR_TYPE_NAMES:
        return "model_call_failed"
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", type(exc).__name__)
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()
    return name if _CODE.fullmatch(name) else "model_call_failed"


def normalize_usage(usage: Any) -> dict[str, int | None]:
    """Expose provider counts only. Unavailable or invalid counts remain null."""
    result = {}
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(field) if isinstance(usage, dict) else getattr(usage, field, None)
        result[field] = value if type(value) is int and value >= 0 else None
    return result


def _label(value: Any, fallback: str) -> str:
    trusted = isinstance(value, str) and (value in _PUBLIC_VALUES or _HASH.fullmatch(value)
        or re.fullmatch(r"[A-Z][A-Z0-9_]*@[a-f0-9]{4,64}", value))
    return value if trusted and not _CREDENTIAL.search(value) else fallback


def _public(value: Any, key: str = "", depth: int = 0) -> Any:
    """Trace events expose structural codes and hashes, not arbitrary prose."""
    if depth > _MAX_DEPTH:
        return {"redacted": True, "truncated": True}
    if _PRIVATE_KEYS.fullmatch(key):
        return {"sha256": sha256_text(value), "redacted": True}
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        identifier = key in {"id", "part_id", "target_id", "passage_id", "citation", "index"}
        if (value in _PUBLIC_VALUES or value in _ERROR_TYPE_NAMES or _HASH.fullmatch(value)
                or (identifier and re.fullmatch(r"[a-z]{0,2}[0-9]{1,8}", value))) and not _CREDENTIAL.search(value):
            return value
        return {"sha256": sha256_text(value), "redacted": True}
    if isinstance(value, dict):
        result = {}
        for index, (raw_key, item) in enumerate(value.items()):
            if index >= _MAX_ITEMS:
                result["_truncated_items"] = len(value) - _MAX_ITEMS
                break
            if not isinstance(raw_key, (str, int)):
                continue
            item_key = str(raw_key)
            public_key = item_key if item_key in _FIELD_KEYS or re.fullmatch(r"[0-9]{1,8}", item_key) else "key_sha256_" + sha256_text(item_key)[:16]
            result[public_key] = _public(item, item_key, depth + 1)
        return result
    if isinstance(value, (tuple, list)):
        result = [_public(item, key, depth + 1) for item in value[:_MAX_ITEMS]]
        if len(value) > _MAX_ITEMS:
            result.append({"_truncated_items": len(value) - _MAX_ITEMS})
        return result
    return {"sha256": sha256_text(value), "redacted": True}


@dataclass
class CallRecord:
    call_id: int
    stage: str
    attempt: int
    system_id: str
    system_sha256: str
    prompt_sha256: str
    response_sha256: str | None = None
    latency_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    error_code: str | None = None


class AnswerTrace:
    """One request's diagnostics. Off mode records nothing and serializes to {}."""

    def __init__(self, *, enabled: bool = True, trace_id: str | None = None):
        self.enabled = bool(enabled)
        self.trace_id = _label(trace_id, uuid4().hex)
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._started = time.perf_counter()
        self._calls: list[CallRecord] = []
        self._events: list[dict[str, Any]] = []
        self._dropped_events = 0
        self._dropped_model_calls = 0
        self._lock = threading.RLock()

    def event(self, name: str, **fields: Any) -> None:
        if not self.enabled:
            return
        with self._lock:
            if len(self._events) >= _MAX_EVENTS:
                self._dropped_events += 1
                return
            self._events.append({
                "event": _label(name, "unknown"),
                "elapsed_ms": round((time.perf_counter() - self._started) * 1000),
                "fields": _public(fields),
            })

    record_event = event

    def record_evidence(self, stage: str, evidence: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        """Record the actual stage view, retaining passage identity and text hash.

        No cross-stage equality is assumed here: a selected subset is legitimate.
        Integration code can compare the returned maps for intended equal views.
        """
        views: dict[str, list[dict[str, Any]]] = {}
        if not self.enabled:
            return views
        truncated_passages = 0
        for source in evidence[:_MAX_ITEMS]:
            if not isinstance(source, dict):
                continue
            citation = source.get("citation", source.get("id"))
            if type(citation) is not int and not (isinstance(citation, str) and re.fullmatch(r"[0-9]{1,8}", citation)):
                continue
            rows = []
            declared_passages = source.get("passages", []) or []
            if not isinstance(declared_passages, (list, tuple)):
                declared_passages = []
            truncated_passages += max(0, len(declared_passages) - _MAX_ITEMS)
            for passage in declared_passages[:_MAX_ITEMS]:
                if not isinstance(passage, dict):
                    continue
                pid = passage.get("id", passage.get("passage_id"))
                if type(pid) is not int and not (isinstance(pid, str) and re.fullmatch(r"[0-9]{1,8}", pid)):
                    pid = None
                rows.append({"passage_id": pid, "sha256": sha256_text(passage.get("text", ""))})
            views[str(citation)] = rows
        self.event("evidence_view", stage=stage, passages=views, view_sha256=sha256_text(views),
                   truncated_source_count=max(0, len(evidence) - _MAX_ITEMS),
                   truncated_passage_count=truncated_passages)
        return views

    def to_dict(self) -> dict[str, Any]:
        if not self.enabled:
            return {}
        with self._lock:
            # JSON round-trip returns a detached, JSON-safe snapshot.
            result = {
                "schema_version": 1,
                "trace_id": self.trace_id,
                "started_at": self.started_at,
                "elapsed_ms": round((time.perf_counter() - self._started) * 1000),
                "model_calls": [asdict(call) for call in self._calls],
                "events": self._events,
                "truncated": {"events": self._dropped_events, "model_calls": self._dropped_model_calls},
            }
            return json.loads(json.dumps(result, ensure_ascii=False))

    as_dict = to_dict


def current_trace() -> AnswerTrace | None:
    return _CURRENT.get()


@contextmanager
def trace_context(*, enabled: bool | None = None, trace: AnswerTrace | None = None) -> Iterator[AnswerTrace]:
    """Reuse a surrounding request trace; restore it even when the request fails."""
    active = current_trace()
    if trace is None and active is not None and enabled is not False:
        yield active
        return
    selected = trace if trace is not None else AnswerTrace(enabled=trace_enabled() if enabled is None else enabled)
    token = _CURRENT.set(selected if selected.enabled else None)
    try:
        yield selected
    finally:
        _CURRENT.reset(token)


class _CallHandle:
    def __init__(self, trace: AnswerTrace | None, record: CallRecord | None):
        self.trace = trace
        self.record = record

    def complete(self, text: Any, usage: Any = None) -> None:
        if self.trace is None or self.record is None:
            return
        with self.trace._lock:
            self.record.response_sha256 = sha256_text(text)
            for name, value in normalize_usage(usage).items():
                setattr(self.record, name, value)


@contextmanager
def trace_model_call(stage: str, system: str, prompt: str, *, attempt: int = 1,
                     system_id: str | None = None) -> Iterator[_CallHandle]:
    """Observe one call without altering result, kwargs, exceptions or retry policy."""
    trace = current_trace()
    if trace is None or not trace.enabled:
        yield _CallHandle(None, None)
        return
    started = time.perf_counter()
    system_hash = sha256_text(system)
    with trace._lock:
        if len(trace._calls) >= _MAX_MODEL_CALLS:
            trace._dropped_model_calls += 1
            record = None
        else:
            record = CallRecord(
                call_id=len(trace._calls) + 1,
                stage=_label(stage, "unknown"),
                attempt=attempt if type(attempt) is int and attempt > 0 else 1,
                system_id=_label(system_id, "prompt@" + system_hash[:8]),
                system_sha256=system_hash,
                prompt_sha256=sha256_text(prompt),
            )
            trace._calls.append(record)
    if record is None:
        yield _CallHandle(None, None)
        return
    try:
        yield _CallHandle(trace, record)
    except BaseException as exc:
        with trace._lock:
            record.error_code = error_code(exc)
        raise
    finally:
        with trace._lock:
            record.latency_ms = round((time.perf_counter() - started) * 1000)
