"""One targeted late-review adjudication with synthetic judge responses only."""
import copy
import json

import pytest

from marine_kb.answer_pipeline import withhold_disputed_atoms
from marine_kb.evidence_units import evidence_view


def fixture():
    evidence=evidence_view([{'title':'甲制度办理办法','text':'甲制度由县级部门审核申请。\n\n这一段不用于本原子。'},
                            {'title':'乙制度办理办法','text':'乙制度由乡镇部门受理申请。'}])
    facets=[{'fid':'f1','question':'甲制度由谁审核？'},{'fid':'f2','question':'乙制度由谁受理？'}]
    atoms=[{'aid':'c1a1','type':'direct_fact','text':'甲制度由县级部门审核申请[资料1]。','facet_ids':['f1'],
            'evidence_quotes':[{'citation':1,'passage_id':1,'quote':'甲制度由县级部门审核申请。'}]},
           {'aid':'c2a1','type':'direct_fact','text':'乙制度由乡镇部门受理申请[资料2]。','facet_ids':['f2'],
            'evidence_quotes':[{'citation':2,'passage_id':1,'quote':'乙制度由乡镇部门受理申请。'}]},
           {'aid':'c3a1','type':'synthesis','text':'两者办理主体不同。','facet_ids':['f1','f2'],
            'premises':['c1a1','c2a1'],'inference':{'operator':'different'},'evidence_quotes':[]}]
    result={'accepted_atoms':atoms,'decisions':[{'aid':'c1a1','retained':True,'status':'supported'}]}
    issues=[{'kind':'unsupported_claim','text':atoms[0]['text'],
             'target_id':'a1','target_text':atoms[0]['text'],'reason':'覆盖审查对审核机关存在异议'}]
    return result,issues,evidence,facets


def supported(check):
    atom=check['atoms'][0]
    return {'atoms':[{'aid':atom['aid'],'text':atom['text'],'status':'supported',
                     'type':atom['type'],'facet_ids':atom['facet_ids'],
                     'refs':copy.deepcopy(atom['evidence_quotes']),'reason':'原段再次支持原关系'}]}


def run(result,issues,evidence,facets,caller=supported,budget=lambda stage:True):
    return withhold_disputed_atoms(result,issues,recheck=caller,evidence=evidence,facets=facets,budget=budget)


def test_second_support_keeps_exact_original_and_rechecks_one_atom_only():
    result,issues,evidence,facets=fixture();before=copy.deepcopy(result);calls=[];stages=[]
    def caller(check):
        calls.append(copy.deepcopy(check));return supported(check)
    actual=run(result,issues,evidence,facets,caller,lambda stage:stages.append(stage) or True)
    assert result==before and actual['accepted_atoms']==before['accepted_atoms']
    assert stages==['judge_recheck'] and len(calls)==1
    assert len(calls[0]['atoms'])==1 and calls[0]['atoms'][0]['aid']=='c1a1'
    assert calls[0]['coverage_review_disagreement']==issues
    assert len(calls[0]['evidence'])==1 and len(calls[0]['evidence'][0]['passages'])==1
    assert 'text' not in calls[0]['evidence'][0]
    audit=actual['disputed_atom_withholding']
    assert audit['resolved_issues']==issues and not audit['unresolved_issues'] and not audit['withheld_atom_ids']
    assert audit['rechecks'][0]['state']=='supported_again'
    assert audit['rechecks'][0]['attempted'] and audit['rechecks'][0]['completed']


def test_disagreement_withholds_only_atom_and_dependent_synthesis():
    result,issues,evidence,facets=fixture()
    def disagree(check):
        value=supported(check);value['atoms'][0].update(status='unsupported',refs=[]);return value
    actual=run(result,issues,evidence,facets,disagree)
    assert [a['aid'] for a in actual['accepted_atoms']]==['c2a1']
    audit=actual['disputed_atom_withholding']
    assert audit['withheld_atom_ids']==['c1a1','c3a1'] and audit['unresolved_issues']==issues
    assert audit['rechecks'][0]['completed'] and audit['rechecks'][0]['state']=='disagreed'
    assert all(d.get('drop_rule')!='final_review_disputed_pending_recheck' for d in actual['decisions'])


@pytest.mark.parametrize('failure',['exception','missing_atom','rewrite','wider_facets','new_ref','broader_quote','other_type'])
def test_failure_cannot_replace_existing_proof_or_claim_completed_support(failure):
    result,issues,evidence,facets=fixture()
    def caller(check):
        if failure=='exception':raise RuntimeError('offline failure')
        if failure=='missing_atom':return {'atoms':[]}
        value=supported(check);row=value['atoms'][0]
        if failure=='rewrite':row['text']='甲制度直接发款[资料1]。'
        elif failure=='wider_facets':row['facet_ids']=['f1','f2']
        elif failure=='new_ref':row['refs'][0]['passage_id']=2
        elif failure=='broader_quote':row['refs'][0]['quote']+='额外事实'
        elif failure=='other_type':row['type']='calculation'
        return value
    actual=run(result,issues,evidence,facets,caller)
    assert [a['aid'] for a in actual['accepted_atoms']]==['c2a1']
    record=actual['disputed_atom_withholding']['rechecks'][0]
    assert record['attempted'] and not record['confirmed_support']
    if failure in {'exception','missing_atom'}:assert not record['completed'] and record['state']=='error'
    else:assert record['completed'] and record['state']=='disagreed'


def test_budget_denial_does_not_call_judge_or_falsely_say_rechecked():
    result,issues,evidence,facets=fixture()
    def forbidden(check):pytest.fail('budget denial must not call a model')
    actual=run(result,issues,evidence,facets,forbidden,lambda stage:False)
    record=actual['disputed_atom_withholding']['rechecks'][0]
    assert record['state']=='budget_exhausted' and not record['attempted'] and not record['completed']
    assert record['confirmed_support'] is False


def test_same_conflict_is_reused_once_across_initial_and_final_review():
    result,issues,evidence,facets=fixture();calls=[]
    def caller(check):calls.append(check);return supported(check)
    initial=run(result,issues,evidence,facets,caller)
    final=run(initial,issues,evidence,facets,caller)
    assert len(calls)==1 and final['accepted_atoms']==result['accepted_atoms']
    assert final['disputed_atom_withholding']['rechecks'][0]['cache_hit']
    assert len(final['disputed_atom_rechecks'])==1


def test_cache_identity_ignores_local_aid_but_includes_facet_and_actual_passage():
    result,issues,evidence,facets=fixture();calls=[]
    def caller(check):calls.append(check);return supported(check)
    initial=run(result,issues,evidence,facets,caller)
    renamed=copy.deepcopy(initial);renamed['accepted_atoms'][0]['aid']='local1_1_c1a1'
    same=run(renamed,issues,evidence,facets,caller)
    assert len(calls)==1 and same['accepted_atoms'][0]['aid']=='local1_1_c1a1'
    assert same['disputed_atom_withholding']['rechecks'][0]['cache_hit']
    changed=copy.deepcopy(evidence);passage=next(p for p in changed[0]['passages'] if p['id']==1);passage['text']='补充背景。'+passage['text']
    run(initial,issues,changed,facets,caller)
    assert len(calls)==2,'changing an actual original passage cannot reuse an earlier verdict'
    reassigned=copy.deepcopy(initial);reassigned['accepted_atoms'][0]['facet_ids']=['f2']
    run(reassigned,issues,evidence,facets,caller)
    assert len(calls)==3,'same words with different facet ownership are a distinct conflict'


def test_failed_attempt_is_not_repeated_under_same_conflict_key():
    result,issues,evidence,facets=fixture();calls=[]
    def broken(check):calls.append(check);raise RuntimeError('offline')
    actual=run(result,issues,evidence,facets,broken)
    # Simulate a retained snapshot passed again by a later coverage review.
    actual['accepted_atoms']=copy.deepcopy(result['accepted_atoms'])
    final=run(actual,issues,evidence,facets,broken)
    assert len(calls)==1 and final['disputed_atom_withholding']['rechecks'][0]['cache_hit']


def test_tool_records_and_unasked_gaps_never_trigger_dispute_recheck():
    result,issues,evidence,facets=fixture();result['accepted_atoms'][0]['origin']='verified_tool_record'
    def forbidden(check):pytest.fail('protected tool data must not be adjudicated by a model')
    actual=run(result,issues,evidence,facets,forbidden)
    assert actual['accepted_atoms']==result['accepted_atoms'] and not actual['disputed_atom_withholding']['rechecks']
    result,issues,evidence,facets=fixture();issues[0]['kind']='unasked_gap'
    actual=run(result,issues,evidence,facets,forbidden)
    assert actual['accepted_atoms']==result['accepted_atoms'] and not actual['disputed_atom_withholding']['rechecks']


def test_ambiguous_short_span_does_not_recheck_unrelated_sibling():
    result,issues,evidence,facets=fixture();result['accepted_atoms'][1]['text']='乙制度由县级部门审核申请[资料2]。'
    issues[0]={'kind':'unsupported_claim','text':'由县级部门审核申请'}
    actual=run(result,issues,evidence,facets,lambda check:pytest.fail('ambiguous conflict must remain unresolved'))
    assert actual['accepted_atoms']==result['accepted_atoms']
    assert actual['disputed_atom_withholding']['ambiguous']
    assert actual['disputed_atom_withholding']['unresolved_issues']==issues


def test_pipeline_late_support_is_not_still_labelled_unresolved(monkeypatch):
    from marine_kb import answer_pipeline,facet_contract,phase2
    from marine_kb.typed_grounding import PROMPT
    result,issues,evidence,facets=fixture()
    facet={**facets[0],'id':1,'kind':'explanation','required_outputs':['explanation'],
           'objects':['甲制度'],'allowed_gaps':['evidence_not_found']}
    monkeypatch.setattr(facet_contract,'build_contract',lambda *args:[facet])
    monkeypatch.setenv('MARINE_RELATION_GUARD','off')
    monkeypatch.setenv('MARINE_SCOPE_GATE','false')
    monkeypatch.setattr(answer_pipeline,'review_scope',lambda qa,model,raw,*args:(raw,{'verdict':'repair','issues':issues},None))
    class OfflineQA:
        coverage_review_enabled=False
        calls=[]
        def _call_model(self,model,system,prompt):
            phase2.current_budget().take();self.calls.append(json.loads(prompt))
            if system==PROMPT:
                check=json.loads(prompt)
                # Ordinary first grounding receives parsed atoms without refs.
                atom=check['atoms'][0]
                row={'aid':atom['aid'],'text':atom['text'],'status':'supported','type':'direct_fact',
                     'facet_ids':['f1'],'refs':[{'citation':1,'passage_id':1}]}
                return json.dumps({'atoms':[row]},ensure_ascii=False)
            return json.dumps({'answer':result['accepted_atoms'][0]['text'],'missing_information':[]},ensure_ascii=False)
        def _collect_local_hits(self,*args):pytest.fail('complete confirmed answer requires no recovery')
        def _fetch_public(self,*args):pytest.fail('complete confirmed answer requires no network')
    qa=OfflineQA();token=phase2._BUDGET.set(phase2.AnswerBudget(max_calls=3))
    try:
        answer=answer_pipeline.organized_v2(qa,'甲制度是什么？','offline',[],
            [{'citation_id':1,'citation':'资料1','source_type':'public_web','title':'甲制度办理办法','metadata':{}}],
            evidence,{'subquestions':[facet]},{'web_allowed':False})
    finally:phase2._BUDGET.reset(token)
    audit=answer['answer_meta']['phase2']
    assert answer['answer_meta']['answerability']=='full'
    assert audit['unresolved_final_review']==[]
    assert audit['disputed_atom_rechecks'][0]['state']=='supported_again'
    assert '暂缓发布争议' not in answer['answer']
    assert len(qa.calls)==3 and len(qa.calls[-1]['atoms'])==1
