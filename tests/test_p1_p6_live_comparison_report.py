"""Report generation reads recordings only; all captures below are synthetic."""
import copy
import json
import shutil

import pytest

from test_p0_baseline_report import captured_run
from tools.build_p0_baseline_report import EXPECTED_IDS,ReportInvariantError
from tools.build_p1_p6_live_comparison import build_report,html_report,_analysis


@pytest.fixture
def paired(captured_run,tmp_path):
    old,_=captured_run
    new=tmp_path/'p1_p6_fixture'
    shutil.copytree(old,new)
    return old,new


def test_full_report_preserves_failed_transactions_and_does_not_touch_private_cassettes(paired,monkeypatch):
    import socket
    monkeypatch.setattr(socket,'create_connection',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('no network')))
    old,new=paired
    path=new/'G3.result.json'
    value=json.loads(path.read_text(encoding='utf-8'))
    value['http_status']=503;value['reply']['answer']='';value['reply']['error']='connection_failed'
    path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
    original={str(p):p.read_bytes() for run in (old,new) for p in run.glob('*.cassette.jsonl')}
    report=build_report(old,new)
    assert len(report['items'])==24
    row=next(r for r in report['items'] if r['id']=='G3')
    assert row['after']['http_failure'] and row['after']['answer']==''
    assert report['integrity']['report_model_calls']==report['integrity']['report_network_calls']==0
    assert report['summaries']['after']['http_status_counts']=={'200':23,'503':1}
    assert all(r['comparison']['quality_conclusion']=='unverified' for r in report['items'])
    exported=json.dumps(report,ensure_ascii=False)+html_report(report)
    for marker in ('model_private_sentinel','reader_private_sentinel','response_private_sentinel','must_not_export'):
        assert marker not in exported
    assert all(p.read_bytes()==original[str(p)] for run in (old,new) for p in run.glob('*.cassette.jsonl'))


def test_unknown_usage_stays_unknown_and_answer_html_is_escaped(paired):
    old,new=paired;path=new/'M1.result.json';value=json.loads(path.read_text(encoding='utf-8'))
    value['reply']['answer']='<script>window.bad=true</script>'
    value['reply']['trace']['model_calls'][0].update(prompt_tokens=None,completion_tokens=None,total_tokens=None)
    path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
    report=build_report(old,new)
    assert report['summaries']['after']['token_usage']['total_tokens'] is None
    assert report['summaries']['after']['token_usage']['unknown_usage_call_count']==1
    rendered=html_report(report)
    assert '<script>window.bad' not in rendered and '&lt;script&gt;window.bad' in rendered


def test_independent_review_join_requires_exact_case_question(paired,tmp_path):
    old,new=paired;review=tmp_path/'review.json'
    review.write_text(json.dumps({'method':'independent source readback','cases':[
        {'case_id':'M1','question':'问题M1','review_status':'reviewed','overall_outcome':'mixed'}]}),encoding='utf-8')
    report=build_report(old,new,review)
    assert report['items'][0]['independent_review']['overall_outcome']=='mixed'
    assert report['independent_review_provenance']['reviewed_cases']==1
    review.write_text(json.dumps({'cases':[{'case_id':'M1','question':'改了问题'}]}),encoding='utf-8')
    with pytest.raises(ReportInvariantError,match='question_mismatch'):build_report(old,new,review)


def test_incomplete_or_changed_question_run_is_not_full_comparison(paired):
    old,new=paired
    path=new/'run_plan.json';value=json.loads(path.read_text(encoding='utf-8'))
    value['cases'][0]['question']='changed'
    path.write_text(json.dumps(value),encoding='utf-8')
    with pytest.raises(ReportInvariantError,match='plan_mismatch'):build_report(old,new)
    (new/'G3.result.json').unlink()
    with pytest.raises(ReportInvariantError,match='missing_or_extra'):build_report(old,new)


def test_actual_program_composite_decisions_match_only_the_exact_draft_hash():
    atoms=[{'aid':'c1a1','claim_id':1,'text':'甲制度未取得依据；','facet_ids':['f1']},
           {'aid':'c1a2','claim_id':1,'text':'乙制度由乡镇受理。','facet_ids':['f2']}]
    models=[{'seq':1,'stage':'typed_grounding','payload':{'atoms':atoms,'draft_sha256':'exact'},'response':{}},
            {'seq':2,'stage':'claim_grounding','payload':{'claims':[{'id':1,'text':'旧句'}]},
             'response':{'claims':[{'id':1,'status':'unsupported','reason':'旧意见'}]}}]
    reply={'evidence_diagnostics':{'phase2':{'draft_sha256':'exact','decisions':[
        {'aid':'c1a1','retained':False,'drop_rule':'model_unsupported'},
        {'aid':'c1a2','retained':True,'drop_rule':None}]}}}
    result=_analysis(models,reply)
    assert result['composite_clause_outcomes'][0]['kept_atoms']==1
    assert result['composite_clause_outcomes'][0]['dropped_atoms']==1
    assert 'not_v2_judgment' in result['legacy_judge_opinions'][0]['provenance']
    reply['evidence_diagnostics']['phase2']['draft_sha256']='different'
    assert _analysis(models,reply)['composite_clause_outcomes']==[]
