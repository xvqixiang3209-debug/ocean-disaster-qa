"""V2 integration with explicit synthetic model responses, never live calls."""
import json
from unittest.mock import patch

import pytest

from marine_kb.phase2 import AnswerBudget,AnswerBudgetExceeded,budget_context,current_budget
from marine_kb.qa import MarineQuestionService,STRUCTURED_ANSWER_PROMPT,_sources_with_selected_passages
from marine_kb.typed_grounding import PROMPT as TYPED_PROMPT
from marine_kb.coverage_review import PROMPT as REVIEW_PROMPT
from marine_kb.relation_guard import check,health_authority


@pytest.fixture
def v2(monkeypatch):
    for name in ('MARINE_FACET_CONTRACT','MARINE_EVIDENCE_UNITS','MARINE_SCOPE_GATE'):
        monkeypatch.setenv(name,'true')
    monkeypatch.setenv('MARINE_TYPED_GROUNDING','on')
    monkeypatch.setenv('MARINE_RELATION_GUARD','on')


def service(caller):
    qa=MarineQuestionService.__new__(MarineQuestionService)
    qa.external_enabled=True;qa.model_caller=caller;qa.coverage_review_enabled=True
    qa._public_sources=lambda q,h:[{'citation':i,'citation_id':i,'title':v['title'],'locator':'正文',
        'review_label':'indexed','quality_warning':'','metadata':v.get('metadata',{}),
        'source_type':'public_web','source_id':str(i)} for i,v in enumerate(h,1)]
    qa._collect_local_hits=lambda *a:[]
    qa._fetch_public=lambda *a:([],{})
    return qa


def test_normal_path_uses_typed_review_and_canonical_view(v2):
    calls=[]
    def caller(model,system,prompt,**options):
        value=json.loads(prompt);calls.append((system,value))
        if system == STRUCTURED_ANSWER_PROMPT:
            return json.dumps({'answer':'海浪是海面上的波动[资料1]。','answerability':'full',
                               'missing_information':[]},ensure_ascii=False)
        if system == REVIEW_PROMPT:return '{"verdict":"pass","issues":[]}'
        if system == TYPED_PROMPT:
            return json.dumps({'atoms':[dict(a,status='supported',type='direct_fact',
                refs=[{'citation':1,'passage_id':1}]) for a in value['atoms']]},ensure_ascii=False)
        raise AssertionError('unrecorded mock stage')
    qa=service(caller)
    hit={'title':'海浪科普','text':'海浪是海面上的波动。','metadata':{'url':'https://www.nmefc.cn/a'}}
    with budget_context():
        result=qa._organized_answer('什么是海浪？','fake',[hit],{'as_of':'2026-10-20','subquestions':[{'id':1,'question':'什么是海浪？'}]})
        assert current_budget().calls==3
    assert result['answer_meta']['answerability']=='full'
    assert result['answer_meta']['phase2']['publishable']
    assert result['answer_meta']['phase2']['delivery'][0]['status']=='full'
    assert '海浪是海面上的波动' in result['answer']
    gen=next(p for s,p in calls if s==STRUCTURED_ANSWER_PROMPT)
    judge=next(p for s,p in calls if s==TYPED_PROMPT)
    assert gen['evidence'][0]['passages']==judge['evidence'][0]['passages']


def test_budget_rejects_before_provider_and_nested_context_reuses(v2):
    provider=[]
    qa=service(lambda *a,**kw:provider.append(kw) or '{}')
    with budget_context() as budget:
        budget.max_calls=1
        with budget_context() as nested:assert nested is budget
        qa._call_model('fake',REVIEW_PROMPT,'{}')
        with pytest.raises(AnswerBudgetExceeded):qa._call_model('fake',REVIEW_PROMPT,'{}')
    assert len(provider)==1
    assert provider[0]['max_retries']==0 and provider[0]['timeout']<=25


def test_elapsed_budget_rejects_before_provider(v2):
    provider=[];qa=service(lambda *a,**kw:provider.append(kw) or '{}')
    with budget_context() as budget:
        budget.started-=61
        with pytest.raises(AnswerBudgetExceeded):qa._call_model('fake',REVIEW_PROMPT,'{}')
    assert not provider


def test_document_card_support_is_exposed_separately():
    ev=[{'text':'正文无标题。','passages':[{'id':0,'text':'文件：国家预案'}],
         'evidence_view_version':2,'doc_card':{'title':'国家预案'},'metadata':{}}]
    out=_sources_with_selected_passages([{'citation_id':1}],ev,{'evidence_quotes':[{'citation':1,'passage_id':0}]})
    assert out[0]['supporting_passages'][0]['kind']=='document_identity'
    assert out[0]['doc_card']['title']=='国家预案'


def test_mixed_subject_is_detected_even_when_rodent_word_not_deleted():
    issues=check('鼠类和蚊虫活动增加，可能传播登革热、出血热和钩体病[资料1]。',
        [{'question':'洪灾后怎么防蚊？'}],[{'metadata':{'url':'https://www.chinacdc.cn/a'}}])
    assert 'relation_mixed_vector_context' in {r['code'] for r in issues}


@pytest.mark.parametrize('text',['钩体病不是蚊媒病。','不能把钩体病当作蚊媒病。','蚊虫可传播登革出血热。'])
def test_relation_corrections_and_ambiguous_haemorrhagic_name(text):
    assert not check(text,[{'question':'如何防蚊？'}])


def test_health_host_boundary_and_official_repost_origin():
    assert health_authority({'metadata':{'url':'https://icdc.chinacdc.cn/a'}})
    assert not health_authority({'metadata':{'url':'https://chinacdc.cn.evil.example/a'}})
    assert not health_authority({'metadata':{'url':'https://www.zj.gov.cn/a'}})
    assert health_authority({'metadata':{'url':'https://www.zj.gov.cn/a','origin_issuer':'国家卫生健康委员会'}})
    assert health_authority({'metadata':{'url':'https://wsjkw.zj.gov.cn/a'}})


def test_relation_guard_does_not_supply_replacement_facts(v2):
    from marine_kb.answer_pipeline import enforce_relations
    atom={'aid':'a1','text':'蚊虫传播钩体病[资料1]。','display_text':'蚊虫传播钩体病[资料1]。',
          'type':'direct_fact','facet_ids':['f1'],'evidence_quotes':[{'citation':1,'quote':'错误正文'}]}
    f={'fid':'f1','id':1,'question':'蚊子传播什么疾病？','kind':'risk_relation','required_outputs':['relation'],
       'allowed_gaps':['evidence_not_found']}
    source={'text':'错误正文','metadata':{'url':'https://www.chinacdc.cn/a'}}
    result=enforce_relations({'accepted_atoms':[atom],'decisions':[]},[f],[source])
    assert not result['accepted_atoms']
    assert '蚊虫传播钩体病' not in result['answer']
    assert result['render_mode']=='with_gaps'


def test_generic_prevention_does_not_add_chemical_doses():
    fs=[{'question':'台风后如何防蚊？','kind':'method_steps'}]
    assert any(i['code']=='relation_unasked_chemical_dose' for i in check('使用双硫磷处理积水。',fs))
    assert not check('清除积水、安装纱窗、使用蚊帐。',fs)
    assert not check('按说明使用驱蚊用品。',fs)
    assert not check('用双硫磷处理积水。',[{'question':'双硫磷用药有什么要求？','kind':'method_steps'}])


def test_turn_context_is_request_local_and_child_workers_inherit(v2):
    from concurrent.futures import ThreadPoolExecutor
    from marine_kb.phase2 import turn_context,set_turn_key,current_turn_key
    from marine_kb.qa_observability import submit_observed
    with turn_context():
        set_turn_key('a'*64)
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert submit_observed(pool,current_turn_key).result()=='a'*64
        with turn_context():assert current_turn_key() is None
        assert current_turn_key()=='a'*64
    assert current_turn_key() is None


def test_relation_registry_rejects_unchecked_or_changed_excerpt(tmp_path):
    from marine_kb.relation_guard import load_lexicon,LEXICON
    value=json.loads(LEXICON.read_text(encoding='utf-8'))
    assert len(load_lexicon())==5
    value['relations'][0]['excerpt']='被篡改的关系'
    value['relations'][1]['checked_by']=None
    p=tmp_path/'registry.json';p.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
    assert len(load_lexicon(p))==3
