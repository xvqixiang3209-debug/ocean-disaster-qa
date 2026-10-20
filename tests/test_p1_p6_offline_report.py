import json
from pathlib import Path

import pytest

from tools.build_p1_p6_offline_report import DEFAULT_RECORDS, build_report, render_html
from marine_kb.replay import _wire


def test_report_empty_inputs_fail_without_live_fallback(tmp_path):
    with pytest.raises(ValueError, match='no_private_recorded_results'):
        build_report(tmp_path)


def test_synthetic_report_fixture_does_not_masquerade_as_new_model_answer(tmp_path):
    # This fixture is test data, not an actual model response or 24-case run.
    result = {'case': {'case_id': 'FIXTURE', 'question': '如何加固网箱？'}, 'http_status': 200,
              'reply': {'answer': '检查缆绳并加固网箱[资料1]。', 'answerability': 'full'}}
    (tmp_path/'FIXTURE.result.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    request = {'args': ('mock', 'structural fixture', json.dumps({'question': result['case']['question'],
               'subquestions': [{'id':1,'question':'如何加固网箱？'}],
               'evidence': [{'citation':1,'text':'检查缆绳并加固网箱。','title':'技术手册','passages':[{'id':1,'text':'检查缆绳并加固网箱。'}]}]}, ensure_ascii=False)), 'kwargs': {}}
    interaction = {'record_type':'interaction','kind':'model','seq':0,'outcome':'return',
                   'request':_wire(request),'response':_wire((json.dumps({'answer':result['reply']['answer']},ensure_ascii=False), {}))}
    (tmp_path/'FIXTURE.cassette.jsonl').write_text(json.dumps(interaction,ensure_ascii=False)+'\n', encoding='utf-8')
    report = build_report(tmp_path)
    assert report['summary']['new_model_calls'] == report['summary']['new_typed_model_answers'] == 0
    assert report['cases'][0]['new_v2_model_answer'] is None
    assert report['cases'][0]['old_actual_answer'] == result['reply']['answer']
    rendered = render_html(report)
    assert '无新模型生成' in rendered and '不是新版实际回答' in rendered


def test_report_html_escapes_untrusted_source_and_answer_text():
    report = {'title':'<script>x</script>','scope':'<img src=x>','limits':[], 'summary':{},
              'cases':[{'case_id':'<case>','question':'<question>','old_http_status':200,'old_system_label':'full',
                        'old_actual_answer':'<script>alert(1)</script>','deterministic_scope_gate':[],
                        'recorded_draft_structures':[],'relation_guard_on_recorded_drafts':[],
                        'recorded_passage_identity_checks':[],'new_evidence_view_repeatability':{}, 'legacy_claim_reviews':[]}]}
    rendered = render_html(report)
    assert '<script>' not in rendered and '&lt;script&gt;' in rendered


@pytest.mark.skipif(not (DEFAULT_RECORDS/'G3.result.json').exists(), reason='private P0 recordings are not included in a code snapshot')
def test_actual_24_report_keeps_failures_and_real_old_answers_without_new_outputs():
    report = build_report(DEFAULT_RECORDS)
    summary = report['summary']
    assert summary['recorded_complete_questions'] == 24
    assert summary['recorded_http_status'] == {'200':23, '503':1}
    assert summary['recorded_model_interactions'] == 177
    assert summary['actual_generated_drafts_parsed'] == summary['drafts_with_all_exact_atoms']
    assert summary['new_model_calls'] == summary['new_web_calls'] == summary['new_typed_model_answers'] == 0
    failure = next(c for c in report['cases'] if c['case_id']=='G3_extra_3')
    assert failure['recorded_draft_structures'] == [] and failure['new_v2_model_answer'] is None
    assert all(c['new_v2_model_answer'] is None for c in report['cases'])
