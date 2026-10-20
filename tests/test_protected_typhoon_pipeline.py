"""Real pipeline with synthetic dependency outputs, never a paid-model result."""
import copy
import json
import re

import pytest

from forecast_runtime.answer_facts import delivery_facts,fact_text
from marine_kb import phase2
from marine_kb.answer_pipeline import organized_v2,withhold_disputed_atoms
from marine_kb.evidence_units import evidence_view
from marine_kb.facet_contract import build_contract
from marine_kb.facet_delivery import evaluate_delivery
from marine_kb.protected_tool_delivery import verified_tool_atoms,merge_tool_atoms,resolve_tool_gaps,tool_display_atoms
from marine_kb.qa import MarineQuestionService
from marine_kb.typed_grounding import PROMPT as GROUNDING_PROMPT

QUESTION='截至2026年10月20日，现在有几个台风在活动？最新的位置、强度和移动方向是什么？'
EVIDENCE_ID='a'*24


def setup_data(question=QUESTION,count=1):
    point={'time':'2026-10-20 17:00:00','lon':133.8,'lat':16.7,
           'maxSpeed':62,'pressure':915,'moveDirection':'WNW','moveSpeed':24,
           'windScale':17,'intensity':'SuperTY'}
    result={'kind':'typhoons','state':'ready' if count else 'no_active_typhoons',
            'request_question':question,'list':[{'nameCN':'测试台风','typhoonID':202630+i,'points':[point]} for i in range(count)],
            'as_of':'2026-10-20T18:40:00+08:00','requested_as_of':'2026-10-20T18:40:00+08:00',
            'retrieved_at':'2026-10-20T18:39:00+08:00','query_time_mode':'current',
            'snapshot_scope':'complete_api_response','selection_scope':'all_active',
            'activity_count_verified':True,'activity_list_count_verified':True,
            'activity_list_count':count,'cache_stale':False}
    facts=delivery_facts(result)
    hit={'source_id':'forecast:'+EVIDENCE_ID,'chunk_id':EVIDENCE_ID,
         'title':'台风接口查询结果','text':fact_text(facts),'status':'indexed',
         'metadata':{'source_type':'business_forecast','external_allowed':True,'trial':False,
                     'official_typhoon_records':True}}
    meta={'business_evidence_id':EVIDENCE_ID,'required_business_facts':facts,'web_allowed':False}
    return hit,meta,build_contract(question)


def test_exact_source_records_cover_count_and_all_requested_dynamic_fields():
    hit,meta,facets=setup_data()
    atoms,view,audit=verified_tool_atoms(meta,evidence_view([hit]),facets)
    assert atoms and not audit['rejected']
    assert all(r['status']=='full' for r in evaluate_delivery(facets,atoms))
    assert all(a['origin']=='verified_tool_record' for a in atoms)
    assert all(any(p['id']==a['evidence_quotes'][0]['passage_id'] for p in view[0]['passages']) for a in atoms)


@pytest.mark.parametrize('damage',['body_hash','source_id','type','fact_value'])
def test_unbound_metadata_or_modified_fact_cannot_mint_program_truth(damage):
    hit,meta,facets=setup_data();view=evidence_view([hit])
    if damage=='body_hash':view[0]['body_sha256']='0'*64
    if damage=='source_id':view[0]['doc_card']['source_id']='web:fake'
    if damage=='type':view[0]['metadata']['source_type']='public_web'
    if damage=='fact_value':
        for f in meta['required_business_facts']:
            if f['kind']=='typhoon_count':f['value']=999
    atoms,_,audit=verified_tool_atoms(meta,view,facets)
    assert not any(a.get('tool_fact',{}).get('value')==999 for a in atoms)
    if damage!='fact_value':assert not atoms


def test_retrieval_selection_cannot_erase_count_passage():
    hit,meta,facets=setup_data();view=evidence_view([hit]);view[0]['passages']=view[0]['passages'][:1]
    atoms,extended,_=verified_tool_atoms(meta,view,facets)
    assert atoms and all(a['evidence_quotes'][0]['passage_id']>0 for a in atoms)
    assert len(extended[0]['passages'])>1 and len(view[0]['passages'])==1


def test_model_count_is_replaced_and_unrelated_sibling_is_preserved():
    hit,meta,facets=setup_data();atoms,_,_=verified_tool_atoms(meta,evidence_view([hit]),facets)
    rows=[{'aid':'model_wrong','text':'活动列表有2个台风[资料1]','type':'direct_fact','facet_ids':['f1']},
          {'aid':'other','text':'朱家尖浪高1.2米[资料2]','type':'direct_fact','facet_ids':['f3']}]
    merged=merge_tool_atoms({'accepted_atoms':rows},atoms,facets)
    assert 'model_wrong' not in {a['aid'] for a in merged['accepted_atoms']}
    assert 'other' in {a['aid'] for a in merged['accepted_atoms']}
    disputed=withhold_disputed_atoms(merged,[{'kind':'unsupported_claim','text':atoms[0]['text']}])
    assert atoms[0]['aid'] in {a['aid'] for a in disputed['accepted_atoms']}


def test_ready_data_resolve_only_same_day_same_field_availability_gaps():
    hit,meta,facets=setup_data();atoms,_,_=verified_tool_atoms(meta,evidence_view([hit]),facets)
    gaps=['缺少2026年10月20日活动台风数量','缺少2026年10月19日活动台风数量',
          '缺少全球完整活动台风数量','缺少SWL单位核定资料','缺少个人获赔资格']
    resolved=resolve_tool_gaps(gaps,facets,atoms)
    assert gaps[0] not in resolved['kept']
    assert resolved['kept']==gaps[1:]


class OmittingModel:
    coverage_review_enabled=False
    _public_sources=staticmethod(MarineQuestionService._public_sources)
    def _call_model(self,model,system,prompt):
        phase2.current_budget().take()
        p=json.loads(prompt)
        if system==GROUNDING_PROMPT:
            return json.dumps({'atoms':[{'aid':a['aid'],'text':a['text'],'status':'unsupported',
                'type':'direct_fact','facet_ids':a['facet_ids'],'refs':[]} for a in p['atoms']]})
        return json.dumps({'answer':'该数量是接口活动列表的数量[资料1]。','answerability':'partial',
            'evidence_quotes':[{'citation':1,'passage_id':1}],
            'missing_information':['缺少2026年10月20日活动台风数量']},ensure_ascii=False)
    def _collect_local_hits(self,*args):return []
    def _fetch_public(self,*args):return [],{}
    def _can_send(self,hit):return True


@pytest.mark.parametrize('count',[0,1,4])
def test_end_to_end_mock_omission_does_not_lose_real_tool_fields(monkeypatch,count):
    for name in ['MARINE_FACET_CONTRACT','MARINE_EVIDENCE_UNITS','MARINE_SCOPE_GATE']:
        monkeypatch.setenv(name,'true')
    monkeypatch.setenv('MARINE_RELATION_GUARD','off')
    hit,meta,facets=setup_data(count=count)
    qa=OmittingModel();sources=qa._public_sources(QUESTION,[hit]);view=evidence_view([hit])
    token=phase2._BUDGET.set(phase2.AnswerBudget(max_calls=10))
    try:
        result=organized_v2(qa,QUESTION,'synthetic',[hit],sources,view,{'subquestions':facets},meta)
    finally:phase2._BUDGET.reset(token)
    assert f'接口完整活动列表有{count}个台风' in result['answer']
    assert result['answer_meta']['phase2']['business_validation_error'] is None
    assert result['answer_meta']['answerability']=='full'
    assert '缺少2026年10月20日活动台风数量' not in result['answer_meta']['missing_information']
    if count:
        assert re.search(r'东经133\.8(?:度|°)',result['answer']) and re.search(r'北纬16\.7(?:度|°)',result['answer'])
        assert '62 m/s' in result['answer'] and '915 hPa' in result['answer'] and 'WNW' in result['answer']
    assert result['rag_sources'][0]['review_label'].startswith('官方台风接口记录')


def test_multi_field_and_time_questions_survive_complete_model_omission(monkeypatch):
    question='今天活动台风有几个、分别叫什么？各台风最新中心最大风速、最低气压、移动方向和移动速度分别是多少？请同时写明查询时间与记录时间。'
    for name in ['MARINE_FACET_CONTRACT','MARINE_EVIDENCE_UNITS','MARINE_SCOPE_GATE']:
        monkeypatch.setenv(name,'true')
    monkeypatch.setenv('MARINE_RELATION_GUARD','off')
    hit,meta,_=setup_data(question=question)
    parts=['今天活动台风有几个、分别叫什么？','各台风最新中心最大风速是多少？',
           '各台风最低气压是多少？','各台风移动方向是什么？','各台风移动速度是多少？',
           '查询时间与记录时间分别是什么？']
    facets=build_contract(question,[{'id':i,'question':q} for i,q in enumerate(parts,1)])
    qa=OmittingModel();token=phase2._BUDGET.set(phase2.AnswerBudget())
    try:
        result=organized_v2(qa,question,'synthetic',[hit],qa._public_sources(question,[hit]),
                           evidence_view([hit]),{'subquestions':facets},meta)
    finally:phase2._BUDGET.reset(token)
    assert result['answer_meta']['phase2']['business_validation_error'] is None
    assert '完整活动列表有1个台风' in result['answer']
    assert '62 m/s' in result['answer'] and '915 hPa' in result['answer']
    assert 'WNW' in result['answer'] and '24 km/h' in result['answer']
    assert '2026-10-20 17:00:00' in result['answer'] and '2026-10-20 18:40:00' in result['answer']


@pytest.mark.parametrize('damage',[None,'other_time','other_value','other_source','other_metric','ambiguous_storm'])
def test_display_deduplicates_only_exact_same_source_table_row(damage):
    hit,meta,facets=setup_data(count=2 if damage=='ambiguous_storm' else 1)
    atoms,_,_=verified_tool_atoms(meta,evidence_view([hit]),facets)
    row={'aid':'inherited_wind','type':'direct_fact','facet_ids':['f1','f2'],
         'table_header':'| 项目 | 数值 | 记录时刻 | 来源 |',
         'text':'| 中心最大风速 | 62 m/s（单位按现有接口解释，尚未核定） | 2026-10-20 17:00:00 | [资料1] |'}
    if damage=='other_time':row['text']=row['text'].replace('17:00','16:00')
    if damage=='other_value':row['text']=row['text'].replace('62 m/s','63 m/s')
    if damage=='other_source':row['text']=row['text'].replace('资料1','资料2')
    if damage=='other_metric':row['text']=row['text'].replace('中心最大风速','岸段波高')
    original=copy.deepcopy(row)
    displayed,audit=tool_display_atoms(atoms+[row],facets)
    assert row==original
    assert any(a['aid']==row['aid'] for a in displayed)==(damage is not None)
    if damage is None:assert audit[0]['rule']=='exact_tool_row_already_in_data_card'


def test_display_keeps_other_domain_and_shows_shared_explanation_once():
    hit,meta,facets=setup_data();atoms,_,_=verified_tool_atoms(meta,evidence_view([hit]),facets)
    facets=facets+[{'fid':'f3','domain':'marine_forecast'}]
    row={'aid':'row','type':'direct_fact','facet_ids':['f1','f2','f3'],
         'table_header':'| 项目 | 数值 | 记录时刻 | 来源 |',
         'text':'| 中心最大风速 | 62 m/s（单位按现有接口解释，尚未核定） | 2026-10-20 17:00:00 | [资料1] |'}
    note={'aid':'note','type':'limitation','facet_ids':['f1','f2'],'text':'所列单位尚未核定[资料1]。'}
    displayed,_=tool_display_atoms(atoms+[row,note],facets)
    assert next(a for a in displayed if a['aid']=='row')['facet_ids']==['f3']
    assert next(a for a in displayed if a['aid']=='note')['facet_ids']==['f1']
    assert note['facet_ids']==['f1','f2']
