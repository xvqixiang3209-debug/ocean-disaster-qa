"""Opt-in orchestration tests with synthetic dependencies, no model/network IO."""
import copy
import hashlib
import json

from marine_kb import phase2
from marine_kb.answer_pipeline import organized_v2
from marine_kb.evidence_units import evidence_view
from marine_kb.qa import MarineQuestionService
from marine_kb.typed_grounding import PROMPT as GROUNDING_PROMPT


FACETS = [
    {'id':1,'fid':'f1','question':'甲制度怎么办理？','kind':'explanation','objects':['甲制度'],
     'required_outputs':['explanation'],'allowed_gaps':['evidence_not_found'],'places':[],'place_role':'background'},
    {'id':2,'fid':'f2','question':'乙制度怎么办理？','kind':'explanation','objects':['乙制度'],
     'required_outputs':['explanation'],'allowed_gaps':['evidence_not_found'],'places':[],'place_role':'background'},
]


def hit(subject, body, number):
    return {'source_id':'web:source'+str(number),'chunk_id':'chunk'+str(number),
            'title':subject+'办理规则','filename':subject+'办理规则','text':body,
            'source_text':body,'url':'https://www.mem.gov.cn/article'+str(number)+'.html',
            'status':'public_web','locator':'正文','quality_status':'parsed','score':1,
            'retrieval_details':{'direct_evidence':True},
            'metadata':{'source_type':'public_web','external_allowed':True,'primary_scope':'national_guidance'}}


class StubQA:
    coverage_review_enabled = False
    _public_sources = staticmethod(MarineQuestionService._public_sources)

    def __init__(self, public):
        self.public = public
        self.retrieval_calls = []
        self.model_calls = []

    def _call_model(self, model, system, prompt):
        phase2.current_budget().take()
        payload=json.loads(prompt)
        self.model_calls.append(payload)
        if system == GROUNDING_PROMPT:
            return json.dumps({'atoms':[
                {'aid':atom['aid'],'text':atom['text'],'status':'supported','type':'direct_fact',
                 'facet_ids':atom['facet_ids'],
                 'refs':[{'citation':2 if '乙制度' in atom['text'] else 1,'passage_id':1}]}
                for atom in payload['atoms']]},ensure_ascii=False)
        repairing = bool(payload.get('repair_instruction'))
        return json.dumps({'answerability':'partial','answer':(
            '乙制度由乡镇受理后公示[资料2]。' if repairing else
            '甲制度由县级审核后发放[资料1]。'),
            'evidence_quotes':[{'citation':2 if repairing else 1,'passage_id':1}],
            'missing_information':[]},ensure_ascii=False)

    def _collect_local_hits(self,*args):
        self.retrieval_calls.append(('local',args))
        return []

    def _fetch_public(self,question,facets,dynamic,allow_web):
        self.retrieval_calls.append(('public',allow_web))
        return (copy.deepcopy(self.public) if allow_web else []),{}

    def _can_send(self, row):
        return row['metadata'].get('external_allowed') is True


def run_pipeline(monkeypatch, *, allow_web=True, extra_meta=None, max_calls=10, public_available=True):
    monkeypatch.setattr('marine_kb.facet_contract.build_contract',lambda *a,**kw:copy.deepcopy(FACETS))
    monkeypatch.setenv('MARINE_RELATION_GUARD','off')
    initial=hit('甲制度','甲制度由县级审核后发放。',1)
    extra=hit('乙制度','乙制度由乡镇受理后公示。',2)
    qa=StubQA([extra] if public_available else [])
    evidence=evidence_view([initial],version=2)
    sources=qa._public_sources('甲制度和乙制度怎么办理？',[initial])
    for source in sources: source['metadata']['external_effective_allowed']=True
    token=phase2._BUDGET.set(phase2.AnswerBudget(max_calls=max_calls))
    try:
        result=organized_v2(qa,'甲制度和乙制度怎么办理？','stub',[initial],sources,evidence,
            {'subquestions':copy.deepcopy(FACETS),'evidence':evidence,'evidence_coverage':[]},
            {'as_of':'2026-10-20','web_allowed':allow_web,'dynamic_question':False,**(extra_meta or {})})
    finally:
        phase2._BUDGET.reset(token)
    return qa,result


def test_targeted_new_source_recovery_keeps_original_citation_and_frozen_sibling(monkeypatch):
    qa,result=run_pipeline(monkeypatch)
    assert '甲制度由县级审核后发放[资料1]' in result['answer']
    assert '乙制度由乡镇受理后公示[资料2]' in result['answer']
    assert len(result['web_sources'])==2
    assert {row['citation_id'] for row in result['web_sources']}=={1,2}
    assert all(row['citation']==f'资料{row["citation_id"]}' for row in result['web_sources'])
    trace=result['answer_meta']['phase2']['recovery_trace']
    assert any(row.get('new_sources')==1 for row in trace)
    assert not any(row.get('error') for row in trace)
    rewrite=next(p for p in qa.model_calls if p.get('repair_instruction'))
    assert rewrite['facet_contract']==FACETS
    assert rewrite['subquestions']==[FACETS[1]]
    assert rewrite['target_fids']==['f2'] and rewrite['read_only_fids']==['f1']
    assert rewrite['question']=='乙制度怎么办理？'
    assert rewrite['answer_design']['subquestions']==[FACETS[1]]
    assert 'text' not in rewrite['frozen_siblings'][0]
    assert rewrite['evidence'][0]['doc_card']['citation']==1
    assert rewrite['evidence'][1]['doc_card']['citation']==2
    assert rewrite['evidence'][0]['body_sha256']==hashlib.sha256('甲制度由县级审核后发放。'.encode()).hexdigest()
    assert rewrite['evidence'][1]['body_sha256']==hashlib.sha256('乙制度由乡镇受理后公示。'.encode()).hexdigest()
    assert rewrite['frozen_siblings'][0]['facet_ids']==['f1']


def test_explicit_web_disabled_cannot_be_overridden_by_missing_facet_recovery(monkeypatch):
    qa,result=run_pipeline(monkeypatch,allow_web=False)
    assert not any(call==('public',True) for call in qa.retrieval_calls)
    assert not any(row['citation_id']==2 for row in result['web_sources'])


def test_targeted_retrieval_keeps_map_storm_identity_and_live_archive_filter(monkeypatch):
    qa,_=run_pipeline(monkeypatch,extra_meta={'sid':'selected-storm','dynamic_question':True})
    call=next(args for kind,args in qa.retrieval_calls if kind=='local')
    assert call[-2:] == (True,'selected-storm')


def test_missing_targeted_data_does_not_erase_grounded_sibling_or_invent_source(monkeypatch):
    _,result=run_pipeline(monkeypatch,public_available=False)
    assert '甲制度由县级审核后发放[资料1]' in result['answer']
    assert '乙制度由乡镇受理后公示[资料2]' not in result['answer']
    assert result['answer_meta']['answerability']=='partial'
    assert {row['citation_id'] for row in result['web_sources']} == {1}
    coverage=result['answer_meta']['subquestion_coverage']
    assert coverage[0]['status']=='full' and coverage[1]['status']!='full'


def test_zero_model_budget_does_not_call_model_or_retrieval(monkeypatch):
    qa,result=run_pipeline(monkeypatch,max_calls=0)
    assert qa.model_calls == [] and qa.retrieval_calls == []
    assert result['answer_meta']['generation_used'] is False
    assert result['answer_meta']['used_mode']=='model_error'


def test_merged_review_respects_exhausted_budget_and_keeps_valid_sibling(monkeypatch):
    from marine_kb.coverage_review import PROMPT as REVIEW_PROMPT
    original=StubQA._call_model
    def with_coverage(self,model,system,prompt):
        if system==REVIEW_PROMPT:
            phase2.current_budget().take()
            self.model_calls.append(json.loads(prompt))
            return json.dumps({'verdict':'pass','issues':[]})
        return original(self,model,system,prompt)
    monkeypatch.setattr(StubQA,'_call_model',with_coverage)
    monkeypatch.setattr(StubQA,'coverage_review_enabled',True)
    monkeypatch.setenv('MARINE_SCOPE_GATE','true')
    qa,result=run_pipeline(monkeypatch,max_calls=5)
    assert len(qa.model_calls)==5
    assert result['answer_meta']['phase2']['merged_scope_review']['error_code']=='coverage_budget_exhausted'
    assert '甲制度由县级审核后发放[资料1]' in result['answer']
    assert result['answer_meta']['answerability']=='partial'


def test_initial_critic_disagreement_cannot_publish_full_without_repair(monkeypatch):
    from marine_kb.coverage_review import PROMPT as REVIEW_PROMPT
    original=StubQA._call_model
    def conflicting_review(self,model,system,prompt):
        if system==REVIEW_PROMPT:
            phase2.current_budget().take();p=json.loads(prompt);self.model_calls.append(p)
            return json.dumps({'verdict':'revise','issues':[{'kind':'unsupported_claim',
                'target_id':'a1','text':'甲制度由县级审核后发放[资料1]。'}]},ensure_ascii=False)
        return original(self,model,system,prompt)
    monkeypatch.setattr(StubQA,'_call_model',conflicting_review)
    monkeypatch.setattr(StubQA,'coverage_review_enabled',True)
    qa,result=run_pipeline(monkeypatch,max_calls=3,public_available=False)
    assert result['answer_meta']['answerability']=='partial'
    assert '甲制度由县级审核后发放[资料1]' not in result['answer']
    assert result['answer_meta']['phase2']['initial_disputed_atom_withholding']['withheld_atom_ids']
