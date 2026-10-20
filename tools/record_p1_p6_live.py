"""Private P1–P6 live evaluation; dry-run unless three explicit gates agree.

24 means complete HTTP QA transactions, not 24 model calls. Questions and case
IDs are copied verbatim from the authorized P0 run_plan. This tool creates an
independent Flask test client; it never restarts or contacts a running service.
Raw prompts, source passages and responses remain under local tmp/replay.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_PLAN = ROOT / 'tmp/replay/p0_20261020_005001_b167cdca/run_plan.json'
EXPECTED_IDS = ('M1', 'M2', 'M3', 'C1', 'C2', 'C3', 'A1', 'A2', 'A3', 'G1', 'G2', 'G3',
                'G3_extra_1', 'G3_extra_2', 'G3_extra_3', 'A2_extra_1', 'A2_extra_2', 'A2_extra_3',
                'C3_extra_1', 'C3_extra_2', 'C3_extra_3', 'M1_extra_1', 'M1_extra_2', 'M1_extra_3')
ENVIRONMENT = {
    'MARINE_FACET_CONTRACT': 'true', 'MARINE_EVIDENCE_UNITS': 'true',
    'MARINE_TYPED_GROUNDING': 'on', 'MARINE_SCOPE_GATE': 'true',
    'MARINE_RELATION_GUARD': 'on', 'MARINE_CONNECTOR_V2': 'true',
    'MARINE_FORECAST_WORKER': 'false', 'MARINE_WARNING_WORKER': 'false',
    'MARINE_TRACE': 'true',
}
SOURCE_DIRS = ('marine_kb', 'forecast_runtime', 'spatial_runtime', 'typhoon', 'ocean_forecast')
EXCLUDED_DIRS = {'__pycache__', '.git', 'node_modules', 'cache', '.pytest_cache'}


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def load_case_plan(path):
    """No normalization, whitespace trimming, date rewrite, or replacement."""
    raw = Path(path).read_bytes()
    data = json.loads(raw.decode('utf-8-sig'))
    cases = data.get('cases')
    if not isinstance(cases, list) or len(cases) != 24:
        raise ValueError('p1_p6_requires_exactly_twenty_four_p0_cases')
    if tuple(row.get('case_id') for row in cases) != EXPECTED_IDS:
        raise ValueError('p1_p6_p0_case_order_or_identity_changed')
    if any(not isinstance(row.get('question'), str) or not row['question'] or len(row['question']) > 2000 for row in cases):
        raise ValueError('p1_p6_invalid_p0_question')
    by_id = {row['case_id']: row for row in cases}
    for key in ('G3', 'A2', 'C3', 'M1'):
        if any(by_id[f'{key}_extra_{i}']['question'] != by_id[key]['question'] for i in range(1, 4)):
            raise ValueError('p1_p6_p0_repeated_question_changed')
    return cases, digest(raw)


def runtime_files(root=ROOT):
    """Static executable/configuration bytes; mutable databases are not code.

    Excludes .env, credentials, live SQLite/NC grids, reader/search caches and
    outputs. Includes every Python business module, frontend implementation,
    static domain rules/public-reference assets, test source and this runner.
    """
    root = Path(root).resolve()
    paths = set(root.glob('*.py'))
    paths.update(root.glob('start*.ps1'))
    for folder in SOURCE_DIRS:
        paths.update((root / folder).rglob('*.py'))
    for folder, suffixes in [('frontend', {'.html', '.js', '.css'}),
                             ('data/domain', {'.json', '.txt'}),
                             ('data/public_references', {'.json', '.txt', '.pdf'}),
                             ('tests', {'.py', '.json'})]:
        paths.update(path for path in (root / folder).rglob('*') if path.suffix.lower() in suffixes)
    for relative in ('VERSION.txt', 'requirements.txt', 'data/requirements/catalog.json',
                     'data/region_info.json', 'data/region_mapping.json',
                     'data/forecast_runtime/assumptions.json',
                     'data/marine_kb/model_evidence_authorizations.json',
                     'typhoon/data/jma_storm_number_sid_2001_2016.json',
                     'tools/record_p1_p6_live.py'):
        paths.add(root / relative)
    return sorted(path for path in paths if path.is_file() and path.is_relative_to(root) and
                  not any(part in EXCLUDED_DIRS for part in path.relative_to(root).parts) and
                  path.name != '.env')


def source_hashes(root=ROOT):
    root = Path(root).resolve()
    return {path.relative_to(root).as_posix(): digest(path.read_bytes()) for path in runtime_files(root)}


def capture_sources(destination, phase, root=ROOT):
    root = Path(root).resolve()
    folder = Path(destination) / ('source_snapshot' if phase == 'before' else 'source_snapshot_after')
    hashes = {}
    for path in runtime_files(root):
        relative = path.relative_to(root)
        data = path.read_bytes()
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        hashes[relative.as_posix()] = digest(data)
    manifest = {'captured_at': utcnow(), 'phase': phase, 'source_sha256': hashes,
                'file_count': len(hashes), 'snapshot_directory': folder.name,
                'scope': 'Static runtime source/configuration, domain rules, test source, and evaluation runner; no .env.',
                'mutable_assets_excluded': ['SQLite and NC business data', 'reader/search caches', 'logs and output files'],
                'flags': dict(ENVIRONMENT)}
    write_json(Path(destination) / f'run_source_snapshot.{phase}.json', manifest)
    if phase == 'before':
        write_json(Path(destination) / 'run_source_snapshot.json', manifest)
    return hashes


def compare_sources(before, after):
    return {'stable': before == after,
            'added': sorted(set(after) - set(before)), 'removed': sorted(set(before) - set(after)),
            'changed': sorted(name for name in before.keys() & after.keys() if before[name] != after[name]),
            'before_files': len(before), 'after_files': len(after)}


@contextmanager
def forced_environment():
    original = {name: os.environ.get(name) for name in ENVIRONMENT}
    os.environ.update(ENVIRONMENT)
    try:
        yield
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def load_independent_server():
    if 'server' in sys.modules:
        raise RuntimeError('p1_p6_requires_fresh_separate_process')
    # The normal application handles its own configuration. This runner never
    # opens, copies, prints, edits or exports .env or model credentials.
    from server import app, marine_qa
    return app, marine_qa


def record_run(cases, plan_path, plan_hash, *, root=ROOT, server_factory=None):
    from marine_kb.replay import ModelCassette, RecordingCaller
    root = Path(root).resolve()
    private_root = root / 'tmp/replay'
    destination = private_root / ('p1_p6_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8])
    destination.mkdir(parents=True, exist_ok=False)
    started = utcnow()
    summary = {'mode': 'live_private_recording', 'qa_transactions_authorized': 24,
               'qa_transactions_planned': len(cases), 'model_calls_authorized_as_qa_transactions': False,
               'authorization': 'User explicitly authorized 24 complete QA transactions; each may contain multiple paid model calls.',
               'automatic_transaction_retries': 0, 'source_p0_run_plan': str(plan_path),
               'source_p0_run_plan_sha256': plan_hash, 'cases': cases, 'flags': dict(ENVIRONMENT),
               'started_at': started, 'private_raw_recording': True,
               'note': 'Live comparison, not same-webpage causal replay and not expert acceptance. No public raw-data export.'}
    write_json(destination / 'run_plan.json', summary)
    attempted, results = 0, []
    stability = None
    before = None
    with forced_environment():
        before = capture_sources(destination, 'before', root)
        app = qa = None
        original_caller = original_fetch = original_local = None
        try:
            app, qa = (server_factory or load_independent_server)()
            if not qa.external_enabled or not qa.model_configured:
                raise RuntimeError('p1_p6_external_model_not_configured_or_enabled')
            if not qa.web_supplement or not qa.web_supplement.enabled:
                raise RuntimeError('p1_p6_official_web_not_enabled')
            original_caller, original_fetch = qa.model_caller, qa.web_supplement.fetch
            original_local = qa._collect_local_hits
            print(json.dumps({'private_output': str(destination), 'planned_qa_transactions': 24}, ensure_ascii=False), flush=True)
            for index, case in enumerate(cases, 1):
                if source_hashes(root) != before:
                    raise RuntimeError('p1_p6_source_changed_during_recording')
                mark = utcnow()
                write_json(destination / (case['case_id'] + '.started.json'), {
                    'case_id': case['case_id'], 'started_at': mark, 'attempt': 1,
                    'automatic_transaction_retry': False, 'question_sha256': digest(case['question'].encode())})
                started_tick = time.monotonic()
                record = {'case': case, 'started_at': mark, 'automatic_transaction_retries': 0,
                          'http_transaction_attempted': False}
                path = destination / (case['case_id'] + '.cassette.jsonl')
                with ModelCassette(path, mode='record', allow_sensitive_recording=True, private_root=private_root) as journal:
                    qa.model_caller = RecordingCaller(original_caller, journal)
                    def fetch(question, **options):
                        return journal.invoke('public_retrieval', {'question': question, 'options': options},
                                              lambda: original_fetch(question, **options))
                    def local(question, normalized, queries, parts, meta, dynamic, sid):
                        def live():
                            hits = original_local(question, normalized, queries, parts, meta, dynamic, sid)
                            return {'hits': hits, 'metadata': dict(meta)}
                        value = journal.invoke('local_retrieval', {'question': question, 'normalized': normalized,
                            'queries': queries, 'parts': parts, 'dynamic': dynamic, 'sid': sid}, live)
                        meta.update(value['metadata'])
                        return value['hits']
                    qa.web_supplement.fetch, qa._collect_local_hits = fetch, local
                    try:
                        with app.test_client() as client:
                            session = client.get('/api/assistant/session', base_url='http://127.0.0.1')
                            body = session.get_json() or {}
                            if session.status_code != 200 or not body.get('token'):
                                raise RuntimeError('p1_p6_session_initialization_failed')
                            attempted += 1
                            record['http_transaction_attempted'] = True
                            response = client.post('/api/assistant/ask', json={'question': case['question']},
                                headers={'X-Assistant-Token': body['token']}, base_url='http://127.0.0.1')
                            record.update(http_status=response.status_code, reply=response.get_json())
                    except Exception as exc:
                        record['transport_error_type'] = type(exc).__name__
                        record['http_status'] = None
                    finally:
                        record.update(completed_at=utcnow(), elapsed_seconds=round(time.monotonic() - started_tick, 3))
                        reply = record.get('reply') or {}
                        record['answer_sha256'] = digest(str(reply.get('answer') or '').encode())
                        write_json(destination / (case['case_id'] + '.result.json'), record)
                results.append(record)
                print(json.dumps({'case': case['case_id'], 'completed': index, 'total': 24,
                    'http_status': record.get('http_status'), 'elapsed_seconds': record['elapsed_seconds']}, ensure_ascii=False), flush=True)
        finally:
            if qa is not None and original_caller is not None:
                qa.model_caller, qa.web_supplement.fetch = original_caller, original_fetch
                qa._collect_local_hits = original_local
            after = capture_sources(destination, 'after', root)
            stability = compare_sources(before or {}, after)
            write_json(destination / 'source_stability.json', stability)
            write_json(destination / 'run_completed.json', {'completed_at': utcnow(),
                'qa_transactions_authorized': 24, 'qa_transactions_attempted': attempted,
                'cases_with_results': len(results), 'automatic_transaction_retries': 0,
                'all_cases_attempted': attempted == 24, 'source_stability': stability,
                'expert_accepted': False})
    if not stability or not stability['stable']:
        raise RuntimeError('p1_p6_source_changed_during_recording')
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--p0-run-plan', type=Path, default=DEFAULT_PLAN)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--record-private', action='store_true')
    parser.add_argument('--authorized-qa', type=int, default=0)
    args = parser.parse_args(argv)
    cases, plan_hash = load_case_plan(args.p0_run_plan)
    if not args.run:
        print(json.dumps({'mode': 'dry_run', 'qa_transactions': 24, 'case_ids': [r['case_id'] for r in cases],
                          'p0_run_plan_sha256': plan_hash, 'flags': ENVIRONMENT,
                          'paid_model_calls': 0, 'private_output_created': False}, ensure_ascii=False, indent=2))
        return 0
    if not args.record_private or args.authorized_qa != 24:
        parser.error('--run requires --record-private --authorized-qa=24; authorization counts complete QA transactions, not model calls')
    destination = record_run(cases, args.p0_run_plan, plan_hash)
    print(json.dumps({'private_output': str(destination), 'qa_transactions_authorized': 24}, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
