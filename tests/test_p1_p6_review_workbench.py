"""Read-only audit tooling, not synthetic model-quality measurements."""
import json

import pytest

from marine_kb.replay import _wire
from tools.review_p1_p6_answers import build_workbench, main, model_steps, RULES


def test_workbench_never_labels_synthetic_fixture_manually_reviewed(tmp_path):
    result={'case':{'case_id':'G3','question':'三项制度是不是一回事？'},'http_status':200,
            'reply':{'answer':'结构测试稿，不是实测回答。','answerability':'full'}}
    (tmp_path/'G3.result.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    recorded={'record_type':'interaction','kind':'model','seq':0,'outcome':'return',
              'request':_wire({'args':('fixture','fixture',json.dumps({'atoms':[{'aid':'a','text':'测试'}]})), 'kwargs':{}}),
              'response':_wire((json.dumps({'atoms':[{'aid':'a','status':'supported'}]}),{}))}
    (tmp_path/'G3.cassette.jsonl').write_text(json.dumps(recorded,ensure_ascii=False)+'\n',encoding='utf-8')
    workbench=build_workbench(tmp_path)
    case=workbench['cases'][0]
    assert case['human_review_status']=='unverifiable' and case['human_review_not_yet_done']
    assert case['steps'][0]['stage']=='typed_judge'
    assert workbench['new_model_calls']==workbench['new_network_calls']==0
    assert workbench['contains_private_raw_data'] is True


def test_incomplete_cassette_is_visible_not_fabricated(tmp_path):
    p=tmp_path/'case.cassette.jsonl';p.write_text('{"unfinished":',encoding='utf-8')
    rows,errors=model_steps(p)
    assert rows==[] and errors[0]['error']=='incomplete_or_invalid_jsonl'


def test_raw_workbench_cannot_be_written_outside_private_tmp(tmp_path):
    with pytest.raises(ValueError,match='remain_in_app_tmp'):
        main(['--run-dir',str(tmp_path),'--output',str(tmp_path/'public.json')])


def test_rules_cover_all_twelve_topics_without_quality_score():
    assert len(RULES['family_deliverables'])==12
    assert '不能替代条款' in ''.join(RULES['retention'])
    assert '模型full标签' in ''.join(RULES['forbidden_shortcuts'])
