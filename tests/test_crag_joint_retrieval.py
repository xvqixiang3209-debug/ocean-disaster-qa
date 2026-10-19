"""CRAG contracts: actual dual scheduling, facet gaps and protected tool values."""
import json
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

from marine_kb.crag import CorrectiveRetrieval,PROMPT,finalize
from marine_kb.qa import MarineQuestionService
from marine_kb.evidence_applicability import coverage_matrix

QUESTION='风暴潮是什么？台风是什么？'
PARTS=[{'id':1,'question':'风暴潮是什么？'},{'id':2,'question':'台风是什么？'}]

def hit(ident,topic,web=False,private=False,**extra):
    return {'source_id':ident,'chunk_id':ident,'title':topic+'定义','filename':topic+'.txt',
            'text':topic+'是指一种海洋或天气现象，本文提供定义、特点和形成机制。',
            'status':'public_web' if web else 'approved','locator':'原文段落',
            'url':'https://www.cma.gov.cn/'+ident if web else None,
            'metadata':{'source_type':'public_web' if web else 'official_public_knowledge','external_allowed':not private},
            'retrieval_details':{'direct_evidence':True},**extra}

class QA:
    model_name='configured-model'
    def __init__(self,local=(),web=(),extra=()):
        self.local=list(local);self.web=list(web);self.extra=list(extra)
        self.local_calls=[];self.web_calls=[];self.model_calls=[]
        self.web_supplement=SimpleNamespace(enabled=True,fetch=self.retry)
    def _can_send(self,row):return row.get('metadata',{}).get('external_allowed') is True
    def _collect_local_hits(self,q,normalized,queries,parts,meta,dynamic,sid):
        self.local_calls.append({'question':q,'queries':queries,'dynamic':dynamic});return self.local
    def _fetch_public(self,q,parts,dynamic,allow):
        self.web_calls.append({'question':q,'parts':parts,'allow':allow})
        return (self.web,{'web_search_executed':True,'web_search_states':['success']}) if allow else ([],{'web_search_executed':False,'web_supplement_state':'not_requested'})
    def retry(self,q,**kw):
        self.web_calls.append({'question':q,**kw})
        return self.extra,{'web_search_executed':True,'web_search_states':['success'],'web_search_queries':['台风 定义 官方']}
    def _call_model(self,model,system,prompt):
        assert system==PROMPT;data=json.loads(prompt);self.model_calls.append(data)
        rows=[]
        for part in data['subquestions']:
            refs=[]
            topic='风暴潮' if part['id']==1 else '台风'
            for d in data['documents']:
                if topic in d['title']:refs.append({'source':d['source'],'passages':[1]})
            rows.append({'id':part['id'],'correct_sources':refs,'reference_sources':[],'reason':'原文提供对应定义'})
        return json.dumps({'evaluations':rows},ensure_ascii=False)

def retrieve(qa,*,allow=True,pinned=None,parts=None,question=QUESTION):
    meta={'subquestions':parts or PARTS,'dynamic_question':False,'route':'business_forecast' if pinned else 'local_rag'}
    selected=CorrectiveRetrieval(qa).retrieve(question,meta,allow,pinned=pinned)
    return selected,meta

def test_full_local_still_executes_web_and_keeps_both_channels():
    qa=QA([hit('local','风暴潮'),hit('local2','台风')],[hit('web','台风',web=True)])
    selected,meta=retrieve(qa)
    assert qa.local_calls and qa.web_calls and meta['web_search_executed']
    assert meta['crag_audit']['local']['selected']>0 and meta['crag_audit']['web']['selected']>0
    assert {r['metadata']['crag_channel'] for r in selected}=={'local','web'}

def test_different_siblings_use_different_evidence():
    qa=QA([hit('local','风暴潮')],[hit('web','台风',web=True)])
    selected,meta=retrieve(qa)
    assert all(p['state']=='supported' for p in meta['crag_evidence_plan'])
    matrix=coverage_matrix(QUESTION,PARTS,selected)
    assert matrix[0]['candidate_citations']==[1] and matrix[1]['candidate_citations']==[2]

def test_gap_drives_bounded_web_and_local_correction():
    qa=QA([hit('local','风暴潮')],[],[hit('web','台风',web=True)])
    selected,meta=retrieve(qa)
    assert len(qa.local_calls)==2 and len(qa.web_calls)==2
    assert qa.web_calls[-1]['subquestions']==['台风是什么？']
    assert qa.web_calls[-1]['corrective'] is True
    assert len(selected)==2 and meta['crag_audit']['corrective_rounds']==1
    assert all(p['state']=='supported' for p in meta['crag_evidence_plan'])

def test_correcting_missing_sibling_preserves_unchanged_verified_passage():
    qa=QA([hit('local','风暴潮')],[],[hit('web','台风',web=True)])
    normal=qa._call_model
    def grader(model,system,prompt):
        result=json.loads(normal(model,system,prompt))
        if len(qa.model_calls)>1:
            result['evaluations'][0]['correct_sources']=[]
        return json.dumps(result,ensure_ascii=False)
    qa._call_model=grader
    selected,meta=retrieve(qa)
    assert all(p['state']=='supported' for p in meta['crag_evidence_plan'])
    assert any(h['source_id']=='local' for h in selected)

def test_irrelevant_shared_keyword_and_holiday_are_not_evidence():
    noise=hit('noise','台风',web=True,title='国务院办公厅节假日安排',text='台风期间的节假日安排日历。')
    qa=QA([hit('local','风暴潮')],[noise])
    selected,meta=retrieve(qa)
    assert all(s['source_id']!='noise' for s in selected)
    assert any(r['reason']=='unrelated_notice' for r in meta['crag_audit']['excluded'])

def test_private_text_never_enters_grader_or_search():
    private=hit('private','风暴潮',private=True,text='PRIVATE_SECRET_CORPUS')
    qa=QA([private],[hit('web','台风',web=True)])
    _,meta=retrieve(qa)
    assert 'PRIVATE_SECRET_CORPUS' not in str(qa.model_calls)+str(qa.web_calls)
    assert meta['blocked_evidence_count']==1

def test_search_correction_contains_user_facets_not_source_private_literals():
    qa=QA([hit('local','风暴潮',text='风暴潮是指专业现象。内部文件独特名称ABC-XYZ。')])
    retrieve(qa)
    assert 'ABC-XYZ' not in str(qa.web_calls)

def test_invalid_passage_id_cannot_certify_correct_evidence():
    qa=QA([hit('local','风暴潮')])
    qa._call_model=lambda *a:json.dumps({'evaluations':[{'id':1,'correct_sources':[{'source':1,'passages':[999]}]},{'id':2,'correct_sources':[]}]})
    selected,meta=retrieve(qa)
    assert selected==[] and all(p['state']=='missing' for p in meta['crag_evidence_plan'])

def test_evaluator_failure_keeps_reference_without_claiming_correctness():
    qa=QA([hit('local','风暴潮')])
    qa._call_model=Mock(side_effect=TimeoutError)
    selected,meta=retrieve(qa)
    assert selected and meta['crag_audit']['evaluator']=='evaluator_unavailable_conservative_reference'
    assert not any(p['state']=='supported' for p in meta['crag_evidence_plan'])

def test_explicit_web_disable_is_preserved():
    qa=QA([hit('local','风暴潮')])
    _,meta=retrieve(qa,allow=False)
    assert len(qa.web_calls)==1 and not qa.web_calls[0]['allow']
    assert not meta['crag_audit']['web']['executed'] and not meta['crag_audit']['web']['attempted']

def test_pinned_numeric_record_never_dropped_or_renumbered():
    tool=hit('forecast:abc','计算结果',text='箬横镇最高总潮位685.86 cm。',metadata={'source_type':'business_forecast','external_allowed':True})
    qa=QA([hit('local','风暴潮')],[hit('web','台风',web=True)])
    selected,meta=retrieve(qa,pinned=[tool])
    assert selected[0]['source_id']=='forecast:abc' and selected[0]['text']==tool['text']
    assert meta['crag_audit']['selected_sources'][0]['citation']==1

def test_parent_live_date_does_not_exclude_stable_sibling_definition():
    qa=QA([hit('local','风暴潮')],[hit('web','台风',web=True)])
    meta={'subquestions':PARTS,'dynamic_question':True}
    selected=CorrectiveRetrieval(qa).retrieve('今天舟山能游泳吗？另问风暴潮是什么、台风是什么？',meta,True)
    assert any(s['source_id']=='local' for s in selected)

def test_local_and_web_retrieval_are_concurrent():
    qa=QA([hit('local','风暴潮')],[hit('web','台风',web=True)]);left,right=Event(),Event()
    original_local,original_web=qa._collect_local_hits,qa._fetch_public
    def local(*a):left.set();assert right.wait(2);return original_local(*a)
    def web(*a):right.set();assert left.wait(2);return original_web(*a)
    qa._collect_local_hits=local;qa._fetch_public=web
    selected,_=retrieve(qa);assert len(selected)==2

def test_output_usage_counts_do_not_invent_web_citations():
    result={'rag_sources':[hit('l','风暴潮')],'web_sources':[],
            'answer_meta':{'crag_audit':{'local':{'executed':True},'web':{'executed':True,'selected':2}}}}
    finalized=finalize(result)
    assert finalized['answer_meta']['crag_audit']['used_web']==0
    assert not finalized['answer_meta']['web_evidence_used']

def test_production_flag_routes_legacy_local_first_through_dual_scheduler():
    qa=MarineQuestionService(SimpleNamespace(),crag_enabled=True,search_policy='local_first',external_enabled=True,model_caller=Mock(),structured_answers=True)
    qa.prepare_question=Mock(return_value={'normalized_question':QUESTION,'normalizer':'test','retrieval_queries':[], 'subquestions':PARTS,'sid':None})
    qa.joint_evidence=Mock(return_value=[])
    qa._organized_answer=Mock(return_value={'answer':'缺少相关正文','rag_sources':[],'web_sources':[],'answer_meta':{'used_mode':'no_evidence'}})
    qa.answer(QUESTION,allow_web=True)
    qa.joint_evidence.assert_called_once()


def test_map_case_keeps_station_facts_and_uses_joint_retriever():
    from marine_kb.crag import answer_selected_case
    qa=SimpleNamespace(model_name='configured-model')
    qa.joint_evidence=Mock(side_effect=lambda q,m,a,**kw:kw['pinned'])
    qa._organized_answer=Mock(return_value={'answer':'海门站最大增水3.12 m[资料1]。',
        'rag_sources':[],'web_sources':[],'answer_meta':{}})
    case={'sid':'2019case','season':2019,'name':'LEKIMA',
          'storm_surge':{'max_surge_m':3.12,'max_surge_station':'海门'},
          'historical_report':{'storm_surge_station_details':[{'station':'海门','max_surge_m':3.12,'warning_level':'红色'}]}}
    plan={'normalized_question':'2019年利奇马各站超警多少？','subquestions':[{'id':1,'question':'各站超警多少？'}]}
    answer_selected_case(qa,plan,case,{})
    args,kw=qa.joint_evidence.call_args
    assert args[2] is True and kw['sid']=='2019case'
    assert '3.12' in kw['pinned'][0]['text'] and '海门' in kw['pinned'][0]['text']
    assert kw['pinned'][0]['metadata']['source_type']=='historical_case_tool'


def test_map_api_crag_route_does_not_call_local_sufficiency_legacy_generator():
    from flask import Flask
    from marine_kb.chat_api import create_chat_blueprint
    from unittest.mock import patch
    plan={'normalized_question':'2019年利奇马各站超警多少？','sid':'case','context_used':True,'history_messages_used':0}
    qa=SimpleNamespace(crag_enabled=True,prepare_question=Mock(return_value=plan))
    sessions=SimpleNamespace(select=Mock(return_value={'sid':'case'}),get=Mock(return_value={'result':{'current':{}}}))
    conversations=SimpleNamespace(sessions=sessions,ask=Mock(side_effect=AssertionError('legacy bypass')))
    components=SimpleNamespace(conversations=conversations,repositories=SimpleNamespace(historical=SimpleNamespace(
        get_storm=lambda *a,**kw:{'sid':'case','name':'LEKIMA','season':2019})))
    app=Flask('crag-map');app.register_blueprint(create_chat_blueprint(qa,components));client=app.test_client()
    token=client.get('/api/assistant/session').json['token']
    with patch('marine_kb.crag.answer_selected_case',return_value={'answer':'有据回答','answer_meta':{'used_mode':'llm'}}) as joint:
        result=client.post('/api/assistant/ask',json={'question':'各站超警多少？','selected_sid':'case','match_request_id':'valid-session'},headers={'X-Assistant-Token':token})
    assert result.status_code==200 and joint.call_count==1 and conversations.ask.call_count==0


def test_warning_release_with_definition_sibling_keeps_official_route():
    from forecast_runtime.official_warnings import warning_question
    assert warning_question('截至2026年10月17日12时，国家海洋预报台对秦皇岛发布了哪些风暴潮警报？黄色警报是什么意思？')
    assert not warning_question('风暴潮黄色警报是什么意思？')


def test_policy_forecast_trigger_does_not_set_document_expiry():
    from datetime import datetime,timezone,timedelta
    from marine_kb.evidence_applicability import assess_page
    result=assess_page('浙江海洋灾害应急响应何时终止？',{'title':'海洋灾害应急预案',
        'content':'本预案适用于海洋灾害。预计未来24小时风暴潮达到标准时启动响应。灾害消除后终止响应。',
        'published_at':'2026-06-30T08:00:00+08:00'},now=datetime(2026,10,19,tzinfo=timezone(timedelta(hours=8))))
    assert result['valid_to'] is None and result['temporal']=='version_unverified' and result['use']!='excluded'


def test_republished_national_plan_keeps_national_scope():
    from marine_kb.evidence_applicability import assess_page
    result=assess_page('浙江海洋灾害响应怎样终止？',{'title':'海洋灾害应急预案',
        'content':'本预案为国家层面部门应急预案，适用于自然资源部组织开展的我国管辖海域范围内风暴潮灾害。自然资源部海区局承担工作。'})
    assert result['geography']=='national_framework' and result['use']=='reference'
