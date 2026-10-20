"""Strict offline QA replay of private P0 recordings, without server or config.

Only recorded model/local/public-retrieval dependencies are installed. Socket
connections and DNS are blocked. Unknown requests fail; matching is never
relaxed and no live fallback is available. Clock values are reconstructed from
the exact recorded prompts, while authorization uses a separately hashed private
snapshot. This replays the QA pipeline, not HTTP authentication/session handling.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from contextlib import contextmanager
from datetime import datetime as RealDatetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import socket
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT
if '--frozen-source' in sys.argv:
    offset=sys.argv.index('--frozen-source')
    SOURCE_ROOT=Path(sys.argv[offset+1]).resolve()
    if SOURCE_ROOT.name != 'source_snapshot' or not SOURCE_ROOT.is_relative_to((ROOT/'tmp/replay').resolve()):
        raise SystemExit('frozen source must be a local recorded source_snapshot')
sys.path.insert(0, str(SOURCE_ROOT))

from marine_kb.replay import CassetteMismatch, ModelCassette, ReplayCaller, _sha, _unwire, _wire
from marine_kb.answer_trace import trace_context

CN = timezone(timedelta(hours=8))


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


@contextmanager
def network_blocked():
    attempts = []
    def blocked(*args, **kwargs):
        attempts.append({'operation': 'socket_connection_or_dns', 'blocked': True})
        raise RuntimeError('offline_replay_network_blocked')
    with patch.object(socket.socket, 'connect', blocked), patch.object(socket.socket, 'connect_ex', blocked), \
         patch.object(socket, 'create_connection', blocked), patch.object(socket, 'getaddrinfo', blocked):
        yield attempts


class EmptyStore:
    """Unexpected database access is a replay failure, never a database read."""
    def __getattr__(self, name):
        raise RuntimeError('offline_replay_unrecorded_store_access')


def frozen_datetime(stamp):
    fixed = RealDatetime.fromisoformat(stamp).astimezone(CN)
    class FrozenDatetime(RealDatetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz is not None else fixed.replace(tzinfo=None)
    return FrozenDatetime


def passage_fingerprint(passages):
    return tuple((row.get('id'), digest(row.get('text', ''))) for row in passages)


def dictionary_signature(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class RecordedDictionaryOrder:
    """Restore transport-lost dict order from equal recorded prompt objects.

    The v1 cassette codec sorts dictionary keys, unlike its original live return
    values. Some nested mappings are serialized directly into JSON prompts.
    Restore only an exact value-equal mapping whose original key order appears
    unambiguously in a recorded prompt. No source value or request matching is
    altered. Ambiguity is retained as an explicit limitation.
    """
    def __init__(self):
        self.orders = {}
        self.ambiguous = set()
        self.restored = 0

    def observe(self, value):
        if isinstance(value, dict):
            signature, order = dictionary_signature(value), tuple(value)
            if signature in self.orders and self.orders[signature] != order:
                self.ambiguous.add(signature)
            self.orders[signature] = order
            for item in value.values():
                self.observe(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self.observe(item)

    def restore(self, value):
        if isinstance(value, dict):
            signature = dictionary_signature(value)
            order = self.orders.get(signature)
            if signature in self.ambiguous:
                order = None
            if order and tuple(value) != order:
                self.restored += 1
            return {key: self.restore(value[key]) for key in (order or value.keys())}
        if isinstance(value, (list, tuple)):
            items = [self.restore(item) for item in value]
            return tuple(items) if isinstance(value, tuple) else items
        return value


def recorded_inputs(cassette):
    from marine_kb.qa import STRUCTURED_ANSWER_PROMPT
    names, dates = set(), []
    clocks = defaultdict(deque)
    dictionary_order = RecordedDictionaryOrder()
    clock_rows = 0
    for row in cassette._records:
        if row['kind'] != 'model':
            continue
        request = _unwire(row['request'])
        args = request.get('args', ())
        if len(args) != 3:
            raise ValueError('offline_replay_model_request_shape')
        model, system, prompt = args
        names.add(model)
        try:
            payload = json.loads(prompt)
        except (TypeError, ValueError):
            continue
        dictionary_order.observe(payload)
        if system == STRUCTURED_ANSWER_PROMPT and payload.get('date'):
            dates.append(payload['date'])
        # _prepare calls assess_page for local candidates. Their exact clock
        # inputs appear in the following evaluator's applicability metadata.
        # Freeze time only, then execute the unchanged applicability rules.
        for document in payload.get('documents', []):
            if not isinstance(document, dict):
                continue
            signature = passage_fingerprint(document.get('passages', []))
            for app in document.get('applicability', []):
                if isinstance(app, dict) and app.get('query_as_of'):
                    key = (document.get('title') or '', app.get('question'), signature)
                    clocks[key].append(app['query_as_of'])
                    clock_rows += 1
    if len(names) != 1 or not dates or len(set(dates)) != 1:
        raise ValueError('offline_replay_missing_or_inconsistent_model_clock')
    return {'model_name': next(iter(names)), 'qa_date': dates[0], 'clocks': clocks,
            'recorded_applicability_clock_rows': clock_rows, 'dictionary_order': dictionary_order}


class RecordedSupplement:
    enabled = True
    def __init__(self, cassette, dictionary_order):
        self.cassette, self.dictionary_order = cassette, dictionary_order

    def fetch(self, question, **options):
        value = self.cassette.invoke('public_retrieval', {'question': question, 'options': options})
        return self.dictionary_order.restore(value)


def verify_authorization(run_dir):
    from marine_kb.model_authorization import EvidenceAuthorization
    path = run_dir / 'authorization_snapshot.json'
    receipt = read_json(run_dir / 'authorization_snapshot_receipt.json')
    if hashlib.sha256(path.read_bytes()).hexdigest() != receipt.get('sha256'):
        raise ValueError('offline_replay_authorization_snapshot_hash_mismatch')
    if receipt.get('source_mtime_before_run_started') is not True:
        raise ValueError('offline_replay_authorization_not_frozen')
    return EvidenceAuthorization(path), {
        'sha256': receipt['sha256'], 'source_mtime_before_run_started': True,
        'capture_note': 'Read-only snapshot copied after run start; source mtime predates run. Not an independently recorded authorization decision.',
    }


def verify_sources(run_dir):
    manifest = read_json(run_dir / 'run_source_snapshot.json')
    hashes = manifest.get('source_sha256')
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError('offline_replay_source_snapshot_missing')
    for name, expected in hashes.items():
        path = (SOURCE_ROOT / name).resolve()
        if not path.is_relative_to(SOURCE_ROOT) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('offline_replay_source_snapshot_mismatch')
    return {'verified_files': len(hashes),
            'manifest_sha256': hashlib.sha256((run_dir / 'run_source_snapshot.json').read_bytes()).hexdigest()}


def replay_case(run_dir, case_id):
    run_dir = Path(run_dir).resolve()
    expected = read_json(run_dir / (case_id + '.result.json'))
    case = expected['case']
    original_answer = (expected.get('reply') or {}).get('answer') or ''
    journal = ModelCassette(run_dir / (case_id + '.cassette.jsonl'), ordering='per_key')
    output = {'id': case_id, 'mode': 'offline_strict_qa_pipeline_replay',
        'live_fallback': False, 'network_blocked': True, 'http_session_replayed': False,
        'recorded_answer_sha256': digest(original_answer), 'success': False,
        'request_mismatches': []}
    invoke = journal.invoke
    def checked_invoke(kind, request, *args, **kwargs):
        try:
            return invoke(kind, request, *args, **kwargs)
        except CassetteMismatch as exc:
            pending = [row for row in journal._records if row['kind'] == kind and row['seq'] not in journal._used]
            candidate = pending[0] if pending else None
            mismatch = {'kind': kind, 'code': str(exc), 'request_sha256': _sha(_wire(request)),
                        'next_recorded_request_sha256': candidate.get('request_sha256') if candidate else None}
            if candidate and kind == 'model':
                previous = _unwire(candidate['request'])
                left, right = previous.get('args', ()), request.get('args', ())
                mismatch['system_equal'] = len(left) > 1 and len(right) > 1 and left[1] == right[1]
                mismatch['model_equal'] = bool(left and right and left[0] == right[0])
                mismatch['kwargs_equal'] = previous.get('kwargs') == request.get('kwargs')
                if len(left) > 2 and len(right) > 2:
                    try:
                        old_payload, new_payload = json.loads(left[2]), json.loads(right[2])
                        mismatch['prompt_json_values_equal'] = old_payload == new_payload
                        mismatch['different_prompt_top_level_keys'] = [key for key in sorted(set(old_payload) | set(new_payload))
                            if old_payload.get(key) != new_payload.get(key)]
                    except (TypeError, ValueError):
                        mismatch['prompt_json_values_equal'] = False
            output['request_mismatches'].append(mismatch)
            raise
    journal.invoke = checked_invoke
    try:
        with network_blocked() as network_attempts:
            output['source_snapshot'] = verify_sources(run_dir)
            import marine_kb.qa as qa_module
            import marine_kb.crag as crag_module
            inputs = recorded_inputs(journal)
            authorization, auth_receipt = verify_authorization(run_dir)
            qa = qa_module.MarineQuestionService(EmptyStore(), model_caller=ReplayCaller(journal),
                model_name=inputs['model_name'], model_provider='deepseek', external_enabled=True,
                model_configured=True, evidence_authorizer=authorization.allows,
                require_model_answer=True, structured_answers=True, search_policy='always',
                general_explanations=True, crag_enabled=True,
                web_supplement=RecordedSupplement(journal, inputs['dictionary_order']))
            qa.coverage_review_enabled = True
            def local(question, normalized, queries, parts, meta, dynamic, sid):
                value = journal.invoke('local_retrieval', {'question': question, 'normalized': normalized,
                    'queries': queries, 'parts': parts, 'dynamic': dynamic, 'sid': sid})
                value = inputs['dictionary_order'].restore(value)
                meta.update(value['metadata'])
                return value['hits']
            qa._collect_local_hits = local
            original_assess = crag_module.assess_page
            used_clocks = []
            def timed_assess(question, page, dynamic=False, now=None, subquestion=None):
                body = str(page.get('content') or page.get('text') or '')
                signature = passage_fingerprint(qa_module._evidence_passages(body[:3000]))
                key = (page.get('title') or '', subquestion, signature)
                times = inputs['clocks'].get(key)
                if now is not None or not times:
                    raise RuntimeError('offline_replay_applicability_clock_not_recorded')
                stamp = times.popleft()
                used_clocks.append(stamp)
                return original_assess(question, page, dynamic=dynamic, now=stamp, subquestion=subquestion)
            with patch.object(qa_module, 'datetime', frozen_datetime(inputs['qa_date'])), \
                 patch.object(crag_module, 'datetime', frozen_datetime(inputs['qa_date'])), \
                 patch.object(crag_module, 'assess_page', timed_assess), trace_context(enabled=True):
                plan = qa.prepare_question(case['question'], history=[], selected_context=None)
                result = qa.answer(case['question'], allow_web=True, question_plan=plan)
            qa_answer = result.get('answer') or ''
            meta = result.get('answer_meta') or {}
            replay_http_status = 503 if meta.get('used_mode') == 'model_error' else 200
            # Match chat_api's answer/error publication boundary. A recorded
            # HTTP 503 has no final answer; its error body must match instead.
            answer = qa_answer if replay_http_status == 200 else ''
            recorded_projection = {'http_status': expected['http_status'], 'answer': original_answer,
                                   'error': (expected.get('reply') or {}).get('error')}
            replay_projection = {'http_status': replay_http_status, 'answer': answer,
                                 'error': qa_answer if replay_http_status == 503 else None}
            output.update(replayed_answer_sha256=digest(answer),
                answer_hash_matches=digest(answer) == digest(original_answer),
                recorded_http_status=expected['http_status'], replayed_http_status=replay_http_status,
                recorded_response_projection_sha256=digest(json.dumps(recorded_projection, ensure_ascii=False, sort_keys=True)),
                replayed_response_projection_sha256=digest(json.dumps(replay_projection, ensure_ascii=False, sort_keys=True)),
                response_projection_matches=recorded_projection == replay_projection,
                recorded_model_label=(expected.get('reply') or {}).get('answerability'),
                replayed_model_label=(result.get('answer_meta') or {}).get('answerability'),
                authorization_snapshot=auth_receipt,
                clock_restoration={'qa_date': inputs['qa_date'],
                    'method': 'exact_recorded_prompt_date_and_per_candidate_applicability_timestamps',
                    'applicability_calls': len(used_clocks)},
                dictionary_order_restoration={'restored_mappings': inputs['dictionary_order'].restored,
                    'ambiguous_templates': len(inputs['dictionary_order'].ambiguous),
                    'method': 'value_equal_dictionary_order_from_exact_recorded_prompt_objects'},
                replay_trace=(result.get('answer_meta') or {}).get('trace'),
                network_attempts_blocked=len(network_attempts))
            original_trace = (expected.get('reply') or {}).get('trace') or {}
            replay_trace = (result.get('answer_meta') or {}).get('trace') or {}
            def trace_errors(trace):
                return [{'stage': call.get('stage'), 'error_code': call.get('error_code')}
                        for call in trace.get('model_calls', []) if call.get('error_code')]
            original_errors, replay_errors = trace_errors(original_trace), trace_errors(replay_trace)
            output['trace_error_code_comparison'] = {'recorded': original_errors, 'replayed': replay_errors,
                'matches': original_errors == replay_errors,
                'note': 'Replay does not dynamically import SDK exception classes. A recorded unknown dependency exception is raised as RecordedCallError; public exception-derived codes can therefore differ. Final HTTP answer/error and exact request consumption are checked separately.'}
            forbidden = [name for name in ('server', 'config', 'model_layer') if name in sys.modules]
            output['forbidden_runtime_modules_loaded'] = forbidden
            if forbidden:
                raise ValueError('offline_replay_forbidden_runtime_module_loaded')
            journal.assert_consumed()
            if not output['answer_hash_matches']:
                raise ValueError('offline_replay_final_answer_hash_mismatch')
            if not output['response_projection_matches']:
                raise ValueError('offline_replay_http_answer_error_projection_mismatch')
            if output['request_mismatches']:
                raise ValueError('offline_replay_unknown_requests_were_rejected')
            if network_attempts:
                raise ValueError('offline_replay_unexpected_network_attempt')
            output['success'] = True
    except Exception as exc:
        output['failure'] = {'type': type(exc).__name__, 'code': str(exc)
            if str(exc).isascii() and len(str(exc)) <= 140 else 'offline_replay_failed'}
    finally:
        output['cassette'] = journal.summary()
        output['unconsumed_by_kind'] = dict(Counter(row['kind'] for row in journal._records
                                                   if row['seq'] not in journal._used))
        journal.close(complete=False)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--case-id', action='append')
    parser.add_argument('--frozen-source',type=Path,help='Exact recorded P0 source snapshot; no new-code quality claim.')
    args = parser.parse_args(argv)
    run_dir = args.run_dir.resolve()
    if args.frozen_source and args.frozen_source.resolve() != run_dir/'source_snapshot':
        parser.error('frozen source must belong to this recording')
    if not run_dir.is_relative_to((ROOT / 'tmp' / 'replay').resolve()):
        parser.error('--run-dir must remain in app/tmp/replay')
    plan = read_json(run_dir / 'run_plan.json')
    case_ids = args.case_id or [case['case_id'] for case in plan['cases']]
    if len(case_ids) != len(set(case_ids)) or any(key not in plan['case_ids'] for key in case_ids):
        parser.error('unknown or repeated case id')
    for key in case_ids:
        if not (run_dir / (key + '.result.json')).is_file():
            parser.error('case capture not completed: ' + key)
    rows = [replay_case(run_dir, key) for key in case_ids]
    report = {'generated_at': RealDatetime.now(CN).isoformat(timespec='seconds'),
        'source_root':str(SOURCE_ROOT), 'frozen_legacy_source':SOURCE_ROOT != ROOT,
        'scope': 'Strict offline QA pipeline replay, no server/config/database loading and no live model/web calls.',
        'success_definition': 'All exact dependency requests consumed; final HTTP status and published answer/error match. This is not an equality assertion for elapsed time, SDK exception classes, or prospective answer quality.',
        'transactions': len(rows), 'passed': sum(row['success'] for row in rows),
        'failed': sum(not row['success'] for row in rows), 'items': rows}
    destination = run_dir / ('offline_replay_' + ('_'.join(case_ids) if len(case_ids) <= 2 else 'batch') + '.json')
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(destination), 'transactions': len(rows), 'passed': report['passed'],
                      'failed': report['failed'], 'results': [{'id': row['id'], 'success': row['success'],
                       'failure': row.get('failure'), 'unconsumed_by_kind': row['unconsumed_by_kind']} for row in rows]}, ensure_ascii=False))
    return 0 if report['failed'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
