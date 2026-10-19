"""Local acceptance only; creates transient conversation sessions, never reviews data."""
from pathlib import Path
from datetime import datetime
import argparse
import json
import hashlib
import time
import urllib.request

APP = Path(__file__).resolve().parents[1]


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('--origin', default='http://127.0.0.1:8767')
    args = cli.parse_args()
    if args.origin not in {'http://127.0.0.1:8767', 'http://localhost:8767'}:
        raise ValueError('This acceptance tool is restricted to the local phase-one port.')
    def call(path, payload=None, token=None, raw=False):
        headers = {'Content-Type':'application/json'}
        if token:
            headers['X-Knowledge-Token'] = token
        req = urllib.request.Request(args.origin + path, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        with urllib.request.urlopen(req, timeout=90) as response:
            content = response.read()
            return content.decode() if raw else json.loads(content)
    started = time.perf_counter()
    checks = []
    status = call('/api/knowledge/status')
    token = status.pop('csrf_token')
    status.pop('latest_jobs', None)
    assert status['source_count'] >= 2794
    checks.append('governance_sources_present')
    source = min(call('/api/knowledge/sources?status=indexed')['items'], key=lambda row: row['bytes'])
    with urllib.request.urlopen(args.origin + '/api/knowledge/sources/' + source['source_id'] + '/file', timeout=30) as response:
        assert 'attachment' in response.headers['Content-Disposition']
        assert hashlib.sha256(response.read()).hexdigest() == source['sha256']
    checks.append('original_file_download_hash_matches')
    health = call('/api/typhoon/status')
    assert health['knowledge_base']['available']
    assert health['rag']['available']
    checks.append('legacy_history_and_rag_available')
    payload = json.loads((APP/'typhoon/examples/validation/current_lekima_full_track_summary_link_test.json').read_text(encoding='utf-8-sig'))
    payload['report_mode'] = 'template'
    payload['request_id'] = 'phase1-local-acceptance'
    result = call('/api/typhoon/similar', payload)
    assert len(result['analogues']) == 10
    assert result['report_meta']['used_mode'] == 'template'
    selected = result['analogues'][0]
    checks.append('path_similarity_returns_ten')
    identity = {'request_id':result['request_id'],'sid':selected['sid']}
    chosen = call('/api/typhoon/conversation/select', identity)
    assert chosen['selected_sid'] == selected['sid'] and chosen['answer']
    checks.append('select_historical_typhoon')
    answer = call('/api/typhoon/conversation/ask', {**identity, 'question':'有哪些应对措施'})
    assert answer['answer'] and answer['selected_sid'] == selected['sid']
    checks.append('selected_followup_answer')
    current = call('/api/typhoon/conversation/ask', {**identity, 'question':'今年浙江登陆几个台风'})
    assert current['answer_meta']['used_mode'] == 'no_evidence'
    assert not current.get('rag_sources')
    checks.append('selected_current_query_not_certified_by_history')
    rule = call('/api/typhoon/conversation/ask', {**identity,'question':'现行风暴潮预警标准是什么'})
    assert rule['answer_meta']['used_mode'] == 'no_evidence'
    checks.append('selected_rule_cannot_bypass_review')
    stream = call('/api/chat', {'message':'2019年利奇马台风最大增水是多少'}, raw=True)
    events = [json.loads(line[6:]) for line in stream.splitlines() if line.startswith('data: ')]
    assert any(e.get('type') == 'done' for e in events)
    assert not any(e.get('type') == 'error' for e in events)
    assert any(e.get('type') == 'rag_sources' and e.get('items') for e in events)
    checks.append('general_chat_rag_after_exit_selected_mode')
    standard = call('/api/knowledge/ask', {'question':'海浪警报标准中的24小时是什么意思'}, token)
    assert standard['answer_meta']['used_mode'] == 'no_evidence'
    checks.append('unreviewed_standard_excluded')
    report = {'timestamp':datetime.now().astimezone().isoformat(), 'checks':checks,
              'passed':len(checks),'status':status,'first_analogue':{'sid':selected['sid'],'name':selected['name']},
              'selected_route':answer.get('answer_meta',{}),'elapsed_seconds':round(time.perf_counter()-started,2),
              'limits':'Local behavior verification, not expert answer-accuracy measurement. No external model or web called.'}
    (APP/'docs/首期服务联调验收.json').write_text(json.dumps(report,ensure_ascii=False,indent=2), encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
