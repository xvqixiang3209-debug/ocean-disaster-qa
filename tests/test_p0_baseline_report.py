"""Offline checks for complete, truthful and prompt-free P0 report export."""
import hashlib
import json

import pytest

from marine_kb.replay import ModelCassette
from tools.build_p0_baseline_report import (
    BASE_IDS, EXPECTED_IDS, ReportInvariantError, build_report, html_report, token_summary,
    error_code_label,
)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


@pytest.fixture
def captured_run(tmp_path):
    run = tmp_path / 'p0_fixture'
    run.mkdir()
    baseline = tmp_path / 'baseline.json'
    before = [{'id': key, 'question': '问题' + key, 'answer': '旧答' + key,
               'answer_sha256': hashlib.sha256(('旧答' + key).encode()).hexdigest(),
               'model_label': 'partial', 'http_status': 200, 'sources': []} for key in BASE_IDS]
    write_json(baseline, {'items': before})
    cases = []
    for key in EXPECTED_IDS:
        base = key.split('_extra_')[0]
        extra = '_extra_' in key
        cases.append({'case_id': key, 'question': '问题' + base,
                      'phase': 'additional_repeat' if extra else 'baseline',
                      'repeat': int(key.rsplit('_', 1)[1]) if extra else 1})
    write_json(run / 'run_plan.json', {
        'mode': 'run', 'qa_transactions': 24, 'case_ids': list(EXPECTED_IDS), 'cases': cases,
        'source_sha256': hashlib.sha256(baseline.read_bytes()).hexdigest(),
        'started_at': '2026-10-20T01:00:00+08:00',
    })
    write_json(run / 'run_completed.json', {'qa_transactions': 24,
        'automatic_transaction_retries': 0, 'completed_at': '2026-10-20T01:02:00+08:00'})
    for case in cases:
        key = case['case_id']
        write_json(run / (key + '.started.json'), {'case_id': key, 'attempt': 1,
            'automatic_transaction_retry': False, 'started_at': '2026-10-20T01:00:01+08:00'})
        trace = {'schema_version': 1, 'elapsed_ms': 1200, 'started_at': '2026-10-20T01:00:01+08:00',
            'trace_id': 'fixture', 'truncated': {'events': 0, 'model_calls': 0},
            'model_calls': [{'call_id': 1, 'stage': 'generate', 'prompt_tokens': 7,
                             'completion_tokens': 3, 'total_tokens': 10}],
            'events': [{'event': 'coverage_review', 'elapsed_ms': 100,
                        'fields': {'verdict': 'pass', 'issues': []}}]}
        write_json(run / (key + '.result.json'), {'case': case, 'http_status': 200,
            'completed_at': '2026-10-20T01:00:02+08:00', 'reply': {
                'answer': '真实最终答' + key, 'answerability': 'partial', 'trace': trace,
                'missing_information': [], 'sources': [{'title': '官方来源', 'url':
                    'https://example.gov.cn/body?token=must_not_export#private', 'excerpt': 'reader_private_sentinel'}],
                'evidence_diagnostics': {'local_retrieval_executed': True, 'web_search_executed': True,
                    'coverage_review': {'raw_prompt': 'critic_private_sentinel'}}}})
        with ModelCassette(run / (key + '.cassette.jsonl'), mode='record',
                           allow_sensitive_recording=True, private_root=tmp_path) as cassette:
            cassette.invoke('model', {'prompt': 'model_private_sentinel'}, lambda: ('response_private_sentinel',
                              {'prompt_tokens': 7, 'completion_tokens': 3, 'total_tokens': 10}))
    return run, baseline


def test_complete_report_reads_without_consuming_or_leaking(captured_run):
    run, baseline = captured_run
    old_bytes = {path.name: path.read_bytes() for path in run.glob('*.cassette.jsonl')}
    report = build_report(run, baseline)
    assert report['execution']['completed_transactions'] == 24
    assert report['execution']['model_calls'] == 24
    assert report['execution']['token_usage']['total_tokens'] == 240
    assert report['execution']['both_retrievals_executed'] == 24
    assert len(report['repeat_groups']) == 4
    assert all(row['transactions'] == 4 for row in report['repeat_groups'])
    serialized = json.dumps(report, ensure_ascii=False) + html_report(report)
    for secret in ('model_private_sentinel', 'response_private_sentinel', 'reader_private_sentinel',
                   'critic_private_sentinel', 'must_not_export'):
        assert secret not in serialized
    assert {path.name: path.read_bytes() for path in run.glob('*.cassette.jsonl')} == old_bytes
    assert report['invariants']['cassettes_consumed'] == 0
    assert report['items'][0]['answer'] == '真实最终答M1'


def test_missing_completion_is_not_reported_as_finished(captured_run):
    run, baseline = captured_run
    (run / 'run_completed.json').unlink()
    with pytest.raises(ReportInvariantError, match='run_completed.json'):
        build_report(run, baseline)


def test_truncated_cassette_is_refused(captured_run):
    run, baseline = captured_run
    path = run / 'G3.cassette.jsonl'
    path.write_text('\n'.join(path.read_text(encoding='utf-8').splitlines()[:-1]) + '\n', encoding='utf-8')
    from marine_kb.replay import CassetteIncomplete
    with pytest.raises(CassetteIncomplete, match='completion_footer'):
        build_report(run, baseline)


def test_extra_transaction_is_refused(captured_run):
    run, baseline = captured_run
    write_json(run / 'G3_extra_4.started.json', {})
    with pytest.raises(ReportInvariantError, match='extra_transaction_artifact'):
        build_report(run, baseline)


def test_question_change_is_refused(captured_run):
    run, baseline = captured_run
    path = run / 'run_plan.json'
    plan = json.loads(path.read_text(encoding='utf-8'))
    plan['cases'][12]['question'] = '换了问题'
    write_json(path, plan)
    with pytest.raises(ReportInvariantError, match='question_mismatch'):
        build_report(run, baseline)


def test_empty_http_success_is_not_fabricated(captured_run):
    run, baseline = captured_run
    path = run / 'M1.result.json'
    result = json.loads(path.read_text(encoding='utf-8'))
    result['reply']['answer'] = ''
    write_json(path, result)
    with pytest.raises(ReportInvariantError, match='empty_success_answer'):
        build_report(run, baseline)


def test_cassette_and_trace_model_counts_must_match(captured_run):
    run, baseline = captured_run
    path = run / 'M1.result.json'
    result = json.loads(path.read_text(encoding='utf-8'))
    result['reply']['trace']['model_calls'] = []
    write_json(path, result)
    with pytest.raises(ReportInvariantError, match='model_count_mismatch'):
        build_report(run, baseline)


def test_usage_missing_is_not_estimated():
    result = token_summary([{'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 3},
                            {'prompt_tokens': None, 'total_tokens': None, 'error_code': 'rate_limit_exceeded'}])
    assert result['prompt_tokens'] is None
    assert result['known_prompt_tokens'] == 1
    assert result['prompt_tokens_reported_calls'] == 1
    assert result['total_tokens'] is None
    assert result['known_total_tokens'] == 3
    assert result['unknown_usage_call_count'] == 1
    assert result['usage_reported_calls'] == 1
    assert result['failed_calls'] == 1
    assert result['failed_calls_without_usage'] == 1


def test_redacted_error_codes_have_readable_count_label():
    assert error_code_label({'redacted': True, 'sha256': 'a' * 64}) == 'redacted_error_code'
    assert error_code_label('semantic_evidence_support_failed') == 'semantic_evidence_support_failed'
    assert error_code_label(None) == 'error_code_unavailable'
