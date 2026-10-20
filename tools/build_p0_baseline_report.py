"""Read a completed private 24-transaction P0 run; never call a dependency.

The report exports actual final answers and public trace metadata only. Private
model prompts, model response bodies, reader bodies, headers, and credentials in
the cassettes are integrity-checked locally but are never copied to the report.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
from html import escape
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from marine_kb.replay import ModelCassette

DEFAULT_BASELINE = ROOT / 'exports' / '公开服务问答检索修复_原因与12题实际对照_20261019.json'
BASE_IDS = ('M1', 'M2', 'M3', 'C1', 'C2', 'C3', 'A1', 'A2', 'A3', 'G1', 'G2', 'G3')
REPEATED_IDS = ('G3', 'A2', 'C3', 'M1')
EXPECTED_IDS = BASE_IDS + tuple(f'{case}_extra_{n}' for case in REPEATED_IDS for n in range(1, 4))
CN_ZONE = timezone(timedelta(hours=8))
_SECRET = re.compile(r'(?i)(?:sk-[a-z0-9_-]{16,}|gh[pousr]_[a-z0-9_]{16,}|'
                     r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|Bearer\s+\S+)')


class ReportInvariantError(ValueError):
    """An incomplete or inconsistent capture cannot be presented as a full run."""


def require(condition, code):
    if not condition:
        raise ReportInvariantError(code)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise ReportInvariantError('unreadable_required_json:' + Path(path).name) from exc


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def text_sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def iso(value):
    require(isinstance(value, str), 'missing_timestamp')
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ReportInvariantError('invalid_timestamp') from exc
    require(result.tzinfo is not None, 'timestamp_requires_timezone')
    return result


def safe_url(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
            return None
        # Do not export URL credentials, query tokens, or fragment data.
        return urlunsplit((parsed.scheme, parsed.hostname + (f':{parsed.port}' if parsed.port else ''), parsed.path, '', ''))
    except ValueError:
        return None


def source_cards(rows):
    return [{
        'citation_id': row.get('citation_id'),
        'title': row.get('title') or row.get('filename'),
        'source_type': row.get('source_type'),
        'url': safe_url(row.get('url')),
    } for row in (rows or []) if isinstance(row, dict)]


def token_summary(calls):
    def available(row):
        return all(type(row.get(key)) is int and row[key] >= 0
                   for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'))
    result = {'calls': len(calls),
              'usage_reported_calls': sum(available(row) for row in calls),
              'unknown_usage_call_count': sum(not available(row) for row in calls),
              'failed_calls': sum(bool(row.get('error_code')) for row in calls),
              'failed_calls_without_usage': sum(bool(row.get('error_code')) and not available(row) for row in calls)}
    for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        values = [row.get(key) for row in calls]
        known = [value for value in values if type(value) is int and value >= 0]
        result[key] = sum(known) if len(known) == len(values) else None
        result['known_' + key] = sum(known)
        result[key + '_reported_calls'] = len(known)
    return result


def error_code_label(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and value.get('redacted'):
        return 'redacted_error_code'
    return 'error_code_unavailable'


def offline_replay_summary(run_dir):
    path = run_dir / 'offline_replay_batch.json'
    if not path.is_file():
        return None
    report = read_json(path)
    rows = report.get('items') or []
    require(len(rows) == 24 and tuple(row.get('id') for row in rows) == EXPECTED_IDS,
            'offline_replay_does_not_match_full_authorized_plan')
    return {
        'report_sha256': file_sha(path), 'transactions': len(rows),
        'matched_final_http_answer_or_error': sum(row.get('response_projection_matches') is True for row in rows),
        'matched_final_answer_sha256': sum(row.get('answer_hash_matches') is True for row in rows),
        'strict_replay_passed': sum(row.get('success') is True for row in rows),
        'strict_replay_failed': sum(row.get('success') is not True for row in rows),
        'fully_consumed_cassettes': sum(not row.get('unconsumed_by_kind') for row in rows),
        'unknown_request_mismatches': sum(len(row.get('request_mismatches') or []) for row in rows),
        'network_attempts_blocked': sum(row.get('network_attempts_blocked') or 0 for row in rows),
        'extra_live_model_calls': 0,
        'sdk_exception_code_differences': sum((row.get('trace_error_code_comparison') or {}).get('matches') is False for row in rows),
        'verified_source_files': sorted({row['source_snapshot']['verified_files'] for row in rows if row.get('source_snapshot')}),
        'scope_note': '离线复现包括失败事务。成功表示精确依赖指纹、最终HTTP状态及回答/错误匹配，不表示回答正确、问题已修复或专家验收。没有新增真实API调用。',
        'limitations': ['HTTP认证与网页会话没有重放；SDK未知异常由受控RecordedCallError恢复，公开异常码差异单独统计。',
            '时间及v1字典排序丢失的键序从原始模型请求中受限恢复；没有改变值或放宽请求指纹。'],
    }


def public_trace(trace):
    require(isinstance(trace, dict) and trace.get('schema_version') == 1, 'missing_p0_trace')
    calls, events = trace.get('model_calls'), trace.get('events')
    require(isinstance(calls, list) and isinstance(events, list), 'invalid_public_trace')
    require(all(isinstance(row, dict) for row in calls + events), 'invalid_public_trace_rows')
    # The upstream trace contains only allowlisted codes and content hashes.
    # Only export its explicit public schema, not arbitrary fields from reply.
    return {key: trace.get(key) for key in (
        'schema_version', 'trace_id', 'started_at', 'elapsed_ms', 'model_calls', 'events', 'truncated')}


def before_record(row):
    answer = row.get('answer')
    require(isinstance(answer, str), 'baseline_answer_not_string')
    old_hash = row.get('answer_sha256')
    require(old_hash == text_sha(answer), 'baseline_answer_hash_mismatch:' + str(row.get('id')))
    return {
        'answer': answer, 'answer_sha256': old_hash, 'model_label': row.get('model_label'),
        'http_status': row.get('http_status'),
        'missing_information': row.get('missing_information', []),
        'validation_error': (row.get('validation') or {}).get('error'),
        'sources': source_cards(row.get('sources')),
    }


def inspect_case(run_dir, case, before):
    case_id = case['case_id']
    started_path = run_dir / f'{case_id}.started.json'
    result_path = run_dir / f'{case_id}.result.json'
    cassette_path = run_dir / f'{case_id}.cassette.jsonl'
    started, result = read_json(started_path), read_json(result_path)
    require(started.get('case_id') == case_id and started.get('attempt') == 1
            and started.get('automatic_transaction_retry') is False,
            'transaction_attempt_not_exactly_once:' + case_id)
    require(result.get('case') == case, 'result_plan_mismatch:' + case_id)
    status = result.get('http_status')
    require(type(status) is int and 100 <= status <= 599, 'missing_http_status:' + case_id)
    reply = result.get('reply')
    require(isinstance(reply, dict), 'missing_http_json_response:' + case_id)
    answer = reply.get('answer') or ''
    require(isinstance(answer, str), 'answer_not_string:' + case_id)
    require(bool(answer.strip()) or status >= 400, 'empty_success_answer:' + case_id)
    start_time, end_time = iso(started.get('started_at')), iso(result.get('completed_at'))
    require(end_time >= start_time, 'negative_transaction_time:' + case_id)

    # Construction verifies header, complete footer, every request and response
    # hash, sequence, and journal hash. No invoke()/reader/model is ever called.
    cassette = ModelCassette(cassette_path, mode='replay')
    try:
        cassette_summary = cassette.summary()
        records = cassette._records  # read-only inspection; do not consume.
        by_kind = dict(Counter(row['kind'] for row in records))
        cassette_errors = [{'seq': row['seq'], 'kind': row['kind'], **row['error']}
                           for row in records if row.get('outcome') == 'error']
        require(cassette_summary['consumed'] == 0, 'report_consumed_cassette:' + case_id)
    finally:
        cassette.close(complete=False)
    trace = public_trace(reply.get('trace'))
    calls = trace['model_calls']
    truncated = trace.get('truncated') or {}
    require(not truncated.get('model_calls'), 'trace_model_call_truncated:' + case_id)
    require(len(calls) == by_kind.get('model', 0), 'trace_cassette_model_count_mismatch:' + case_id)
    call_ids = [row.get('call_id') for row in calls]
    require(call_ids == list(range(1, len(calls) + 1)), 'trace_model_ids_incomplete:' + case_id)
    # P0 defines each call at the model-caller boundary; an SDK's internal HTTP
    # retry policy is outside this transaction count and is not inferred here.
    diagnostic = reply.get('evidence_diagnostics') or {}
    stages = dict(Counter(str(row.get('stage', 'unknown')) for row in calls))
    stage_errors = [row for row in trace['events'] if row.get('event') == 'stage_failure']
    coverage = [row for row in trace['events'] if row.get('event') == 'coverage_review']
    retention = [row for row in trace['events'] if row.get('event') == 'claim_retention']
    validation = reply.get('validation') or {}
    return {
        'id': case_id, 'base_id': case_id.split('_extra_')[0],
        'question': case['question'], 'phase': case['phase'], 'repeat': case['repeat'],
        'started_at': started['started_at'], 'completed_at': result['completed_at'],
        'wall_time_seconds': round((end_time - start_time).total_seconds(), 3),
        'http_status': status, 'http_failure_recorded': status >= 400,
        'http_error_code': reply.get('error') if status >= 400 else None,
        'answer': answer, 'answer_sha256': text_sha(answer),
        'answer_characters': len(answer),
        'model_label': reply.get('answerability'),
        'answer_kind': reply.get('answer_kind'),
        'missing_information': reply.get('missing_information', []),
        'subquestion_coverage': reply.get('subquestion_coverage', []),
        'validation_error': validation.get('error'),
        'sources': source_cards(reply.get('sources')),
        'retrieval': {key: diagnostic.get(key) for key in (
            'local_retrieval_executed', 'web_search_executed', 'local_evidence_count',
            'web_body_candidate_count', 'evidence_sent_count', 'corrective_retrieval_attempted')},
        'model_calls': len(calls), 'calls_by_stage': stages,
        'token_usage': token_summary(calls),
        'model_call_errors': [row for row in calls if row.get('error_code')],
        'stage_errors': stage_errors,
        'coverage_review_public': coverage,
        'claim_retention_public': retention,
        'trace': trace,
        'cassette': {'sha256': file_sha(cassette_path), 'interactions': len(records),
                     'calls_by_kind': by_kind, 'dependency_errors': cassette_errors,
                     'integrity_verified': True, 'complete_footer_verified': True,
                     'consumed': 0, 'raw_content_exported': False},
        'capture_sha256': {'started': file_sha(started_path), 'result': file_sha(result_path)},
        'before': before,
    }


def build_report(run_dir, baseline=DEFAULT_BASELINE):
    run_dir, baseline = Path(run_dir).resolve(), Path(baseline).resolve()
    plan = read_json(run_dir / 'run_plan.json')
    completed = read_json(run_dir / 'run_completed.json')
    require(plan.get('mode') == 'run' and plan.get('qa_transactions') == 24, 'requires_24_transaction_run')
    require(completed.get('qa_transactions') == 24
            and completed.get('automatic_transaction_retries') == 0, 'run_not_completed_exactly_once')
    cases = plan.get('cases')
    require(isinstance(cases, list) and len(cases) == 24, 'incomplete_case_plan')
    require(tuple(row.get('case_id') for row in cases) == EXPECTED_IDS
            and tuple(plan.get('case_ids', [])) == EXPECTED_IDS, 'unexpected_case_ids_or_order')
    expected = set(EXPECTED_IDS)
    for suffix in ('.started.json', '.result.json', '.cassette.jsonl'):
        require({path.name[:-len(suffix)] for path in run_dir.glob('*' + suffix)} == expected,
                'missing_or_extra_transaction_artifact:' + suffix)
    old = read_json(baseline)
    old_rows = old.get('items', [])
    require(len(old_rows) == 12 and {row.get('id') for row in old_rows} == set(BASE_IDS), 'unexpected_baseline_cases')
    require(plan.get('source_sha256') == file_sha(baseline), 'run_baseline_source_hash_mismatch')
    old_by_id = {row['id']: row for row in old_rows}
    for row in cases:
        base_id = row['case_id'].split('_extra_')[0]
        require(row.get('question') == old_by_id[base_id].get('question'), 'question_mismatch:' + row['case_id'])
        is_extra = '_extra_' in row['case_id']
        require(row.get('phase') == ('additional_repeat' if is_extra else 'baseline'), 'invalid_case_phase')
        require(row.get('repeat') == (int(row['case_id'].rsplit('_', 1)[1]) if is_extra else 1), 'invalid_case_repeat')
    items = [inspect_case(run_dir, row, before_record(old_by_id[row['case_id'].split('_extra_')[0]])) for row in cases]
    start_time, end_time = iso(plan.get('started_at')), iso(completed.get('completed_at'))
    require(end_time >= start_time, 'negative_run_time')
    require(all(start_time <= iso(row['started_at']) <= iso(row['completed_at']) <= end_time for row in items),
            'case_time_outside_run')
    model_calls = [call for row in items for call in row['trace']['model_calls']]
    repeat_groups = []
    for base_id in REPEATED_IDS:
        rows = [row for row in items if row['base_id'] == base_id]
        require(len(rows) == 4, 'repeat_group_incomplete:' + base_id)
        repeat_groups.append({'base_id': base_id, 'transactions': 4,
            'case_ids': [row['id'] for row in rows],
            'model_labels': dict(Counter(str(row['model_label']) for row in rows)),
            'distinct_answer_sha256': len({row['answer_sha256'] for row in rows}),
            'answer_characters': {row['id']: row['answer_characters'] for row in rows},
            'validation_errors': {row['id']: row['validation_error'] for row in rows},
            'note': '重复中的差异包含模型输出与检索/缓存变化；不能只凭差异归因模型随机性。'})
    snapshot = run_dir / 'run_source_snapshot.json'
    source_verification = None
    if snapshot.is_file():
        hashes = read_json(snapshot).get('source_sha256')
        require(isinstance(hashes, dict) and bool(hashes), 'source_snapshot_hashes_missing')
        for name, expected_sha in hashes.items():
            path = (ROOT / name).resolve()
            require(path.is_relative_to(ROOT) and path.is_file() and file_sha(path) == expected_sha,
                    'source_changed_after_recording:' + str(name))
        source_verification = {'verified_files': len(hashes), 'all_unchanged': True}
    report = {
        'schema_version': 1,
        'generated_at': datetime.now(CN_ZONE).isoformat(timespec='seconds'),
        'run_id': run_dir.name,
        'scope': '用户授权的24次完整问答：12题各1次，G3、A2、C3、M1各追加3次。没有追加收费事务，也没有重跑160题。',
        'notes': [
            '本轮是P0可观测性录制基线，尚未执行P1–P6回答规则修复。',
            '系统full/partial/insufficient标签不是事实正确率、专家验收结论或与豆包的胜率。',
            'before为2026年10月19日专项12题实际回答；P0未改变回答决策。新旧差异不能直接解释为代码质量提升。',
            '每个完整问答可包含多次模型调用；这里的次数按模型调用边界记录，不推测SDK内部HTTP重试次数。',
            'token只统计提供方实际返回usage；缺项保留null并另列已知小计，不估算费用。',
            '原始请求、生成草稿、网页正文只留本机tmp/replay私有cassette。本报告只含最终回答、来源卡及公开trace代码和哈希。',
        ],
        'expert_accepted': False,
        'execution': {
            'started_at': plan['started_at'], 'completed_at': completed['completed_at'],
            'started_at_beijing': start_time.astimezone(CN_ZONE).isoformat(timespec='seconds'),
            'completed_at_beijing': end_time.astimezone(CN_ZONE).isoformat(timespec='seconds'),
            'wall_time_seconds': round((end_time - start_time).total_seconds(), 3),
            'authorized_transactions': 24, 'completed_transactions': 24,
            'transaction_attempts': 24, 'automatic_transaction_retries': 0,
            'http_status_counts': dict(Counter(str(row['http_status']) for row in items)),
            'answers_nonempty': sum(bool(row['answer'].strip()) for row in items),
            'both_retrievals_executed': sum(row['retrieval']['local_retrieval_executed'] is True
                                          and row['retrieval']['web_search_executed'] is True for row in items),
            'model_calls': len(model_calls),
            'calls_by_stage': dict(Counter(str(row.get('stage', 'unknown')) for row in model_calls)),
            'token_usage': token_summary(model_calls),
            'validation_errors': dict(Counter(row['validation_error'] for row in items if row['validation_error'])),
            'stage_error_codes': dict(Counter(error_code_label(event.get('fields', {}).get('error_code'))
                                             for row in items for event in row['stage_errors'])),
        },
        'model_label_counts': {
            'all_24': dict(Counter(str(row['model_label']) for row in items)),
            'base_12_only': dict(Counter(str(row['model_label']) for row in items[:12])),
            'historical_12': dict(Counter(str(row['model_label']) for row in old_rows)),
        },
        'invariants': {'expected_unique_transactions': 24, 'attempted_once': True,
            'all_completion_footers_verified': True, 'all_cassette_hashes_verified': True,
            'all_model_call_counts_match_cassette': True, 'all_before_answer_hashes_verified': True,
            'all_questions_match_authorized_plan': True, 'report_model_calls': 0,
            'report_network_calls': 0, 'cassettes_consumed': 0},
        'provenance': {'baseline_file': baseline.name, 'baseline_sha256': file_sha(baseline),
            'run_plan_sha256': file_sha(run_dir / 'run_plan.json'),
            'run_completed_sha256': file_sha(run_dir / 'run_completed.json'),
            'run_source_snapshot_sha256': file_sha(snapshot) if snapshot.is_file() else None,
            'current_source_verification': source_verification},
        'offline_replay': offline_replay_summary(run_dir),
        'repeat_groups': repeat_groups, 'items': items,
    }
    require(not _SECRET.search(json.dumps(report, ensure_ascii=False)), 'credential_pattern_in_report')
    return report


def html_report(report):
    def pretty(value):
        return '<pre>' + escape(json.dumps(value, ensure_ascii=False, indent=2)) + '</pre>'
    def actual(text):
        return '<div class="answer">' + escape(text) + '</div>'
    out = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
        '<title>P0录制基线：24次实际问答</title><style>'
        'body{max-width:1160px;margin:32px auto;padding:0 22px;font:16px/1.75 system-ui;color:#153645}'
        'h1,h2,h3{color:#087e8b;line-height:1.4}table{border-collapse:collapse;width:100%;margin:18px 0}'
        'td,th{border:1px solid #b9d4da;padding:9px;text-align:left;vertical-align:top}'
        '.answer,pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f0f7f8;padding:16px}'
        '.notice{background:#fff6e7;padding:18px}details{margin:18px 0}summary{cursor:pointer;font-weight:600}'
        'a{color:#087e8b}.muted{color:#536b73;font-size:14px}</style>',
        '<h1>P0录制基线：24次实际问答与诊断</h1>',
        '<p class="notice">' + escape(report['scope']) + '</p>',
        '<ul>' + ''.join('<li>' + escape(text) + '</li>' for text in report['notes']) + '</ul>',
        '<h2>1. 执行与真实token统计</h2>', pretty(report['execution']),
        '<h2>2. 系统标签与重复波动</h2><p>下面是系统自评标签计数，不是正确率。追加重复题不计入独立题目覆盖率。</p>',
        pretty(report['model_label_counts']), pretty(report['repeat_groups']),
        '<h2>3. 完整性与来源核对</h2>', pretty(report['invariants']), pretty(report['provenance']),
        '<h2>3.1 离线精确复现</h2><p>离线复现成功包含原有失败事务，不作为回答质量验收。</p>',
        pretty(report.get('offline_replay')),
        '<h2>4. 24次问题与实际最终回答</h2>',
        '<table><tr><th>事务</th><th>系统标签</th><th>模型调用</th><th>回答字符</th><th>验证错误</th></tr>']
    for row in report['items']:
        cells = ('<a href="#' + row['id'] + '">' + row['id'] + '</a>', escape(str(row['model_label'])),
                 str(row['model_calls']), str(row['answer_characters']), escape(str(row['validation_error'])))
        out.append('<tr>' + ''.join('<td>' + cell + '</td>' for cell in cells) + '</tr>')
    out.append('</table>')
    for row in report['items']:
        out.extend(['<section id="' + row['id'] + '"><h3>' + escape(row['id'] + ' ' + row['question']) + '</h3>',
            '<p class="muted">HTTP ' + str(row['http_status']) + '；系统标签 ' + escape(str(row['model_label'])) +
            '；模型调用 ' + str(row['model_calls']) + '；回答SHA256 ' + row['answer_sha256'] + '</p>',
            actual(row['answer']) if row['answer'] else '<p class="notice">本次HTTP失败，没有最终回答。</p>',
            '<p>系统保留的缺项：</p>', pretty(row['missing_information']),
            '<details><summary>2026年10月19日同题实际回答（原样）</summary>', actual(row['before']['answer']),
            '<p>旧系统标签：' + escape(str(row['before']['model_label'])) + '</p></details>',
            '<details><summary>本次来源卡（不含正文）</summary>', pretty(row['sources']), '</details>',
            '<details><summary>调用、token、验证与覆盖审查（公开代码及哈希）</summary>',
            pretty({key: row[key] for key in ('token_usage', 'calls_by_stage', 'model_call_errors',
                'stage_errors', 'coverage_review_public', 'claim_retention_public', 'subquestion_coverage', 'cassette')}),
            '</details><details><summary>完整公开trace（没有提示词与资料原文）</summary>',
            pretty(row['trace']), '</details></section>'])
    out.append('<p class="muted">报告生成于 ' + escape(report['generated_at']) + '，原始cassette未公开上传。</p></html>')
    return '\n'.join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, default=DEFAULT_BASELINE)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'exports')
    args = parser.parse_args(argv)
    run_dir = args.run_dir.resolve()
    require(run_dir.is_relative_to((ROOT / 'tmp' / 'replay').resolve())
            and re.fullmatch(r'p0_[A-Za-z0-9_]+', run_dir.name), 'run_dir_must_be_private_p0_recording')
    report = build_report(run_dir, args.baseline)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = args.output_dir / ('P0录制基线_24次实际回答与诊断_' + run_dir.name)
    stem.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    stem.with_suffix('.html').write_text(html_report(report), encoding='utf-8')
    print(json.dumps({'json': str(stem.with_suffix('.json')), 'html': str(stem.with_suffix('.html')),
                      'transactions': 24, 'model_calls': report['execution']['model_calls'],
                      'token_usage': report['execution']['token_usage']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
