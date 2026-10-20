"""Regression of real protocol defects; no model/network calls."""
import copy
import json

import pytest

from marine_kb.answer_pipeline import repair_payload,review_scope,withhold_disputed_atoms
from marine_kb.evidence_units import evidence_view
from marine_kb.qa import _decode_model_answer


def navigation_view():
    return evidence_view([{'citation':1,'title':'浙江天气网',
        'url':'https://www.zj.gov.cn/weather.html',
        'text':'天气预警\n\n台风发布\n\n浙江省气象灾害预警发布平台'}])


def navigation_answer():
    return {'answerability':'partial',
            'answer':'在浙江天气网的“天气预警”栏目选择“台风发布”入口[资料1]。',
            'evidence_quotes':[{'citation':1,'passage_id':1},{'citation':1,'passage_id':2}]}


def test_short_exact_navigation_units_keep_real_channels():
    text,status,refs=_decode_model_answer(json.dumps(navigation_answer(),ensure_ascii=False),navigation_view())
    assert '天气预警' in text and '台风发布' in text and refs=={1}


@pytest.mark.parametrize('damage',['offset','end_overflow','passage_hash','body_hash','legacy_view','free_quote'])
def test_short_navigation_exception_never_accepts_unlocated_or_mutated_quote(damage):
    evidence=navigation_view();value=navigation_answer()
    if damage=='offset':evidence[0]['passages'][1]['start']+=1
    elif damage=='end_overflow':evidence[0]['passages'][1]['end']=len(evidence[0]['text'])+1000
    elif damage=='passage_hash':evidence[0]['passages'][1]['sha256']='0'*64
    elif damage=='body_hash':evidence[0]['body_sha256']='0'*64
    elif damage=='legacy_view':evidence[0]['evidence_view_version']=1
    else:value['evidence_quotes']=[{'citation':1,'quote':'天气预警'}]
    with pytest.raises(ValueError):_decode_model_answer(json.dumps(value,ensure_ascii=False),evidence)


def test_short_fact_does_not_become_navigation_evidence_by_using_short_label():
    evidence=navigation_view();value=navigation_answer()
    value['answer']='天气预警已经解除，可以出海[资料1]。'
    with pytest.raises(ValueError):_decode_model_answer(json.dumps(value,ensure_ascii=False),evidence)


def test_exact_navigation_label_adjacent_to_locator_does_not_require_quotes():
    value=navigation_answer()
    value['answer']='进入浙江天气网天气预警页面，查看“台风发布”入口[资料1]。'
    text,_,refs=_decode_model_answer(json.dumps(value,ensure_ascii=False),navigation_view())
    assert '天气预警页面' in text and refs=={1}


FACETS=[{'fid':'f1','id':1,'question':'浙江气象部门台风预警从哪里查？','kind':'channel','objects':['台风预警']},
        {'fid':'f2','id':2,'question':'浙江海洋部门风暴潮警报从哪里查？','kind':'channel','objects':['风暴潮警报']}]


def test_repair_payload_retains_identity_but_prunes_parent_topics_and_layout():
    parent={'question':'气象预警和海洋警报分别从哪里查？','subquestions':FACETS,
            'facet_contract':FACETS,'answer_design':{'subquestions':FACETS},
            'corrective_retrieval_plan':{'whole_question':'both'},'evidence_coverage':[{'id':1}]}
    before=copy.deepcopy(parent)
    repaired=repair_payload(parent,[FACETS[1]],FACETS,navigation_view(),
        [{'aid':'a1','text':'不允许把这段已答气象渠道再次作为生成草稿','facet_ids':['f1']}])
    assert parent==before
    assert repaired['facet_contract']==FACETS and repaired['target_fids']==['f2']
    assert repaired['read_only_fids']==['f1']
    assert repaired['subquestions']==[FACETS[1]]
    assert repaired['answer_design']['subquestion_layout'][0]['id']==2
    assert repaired['question']==FACETS[1]['question']
    assert 'corrective_retrieval_plan' not in repaired and 'evidence_coverage' not in repaired
    assert '不允许把这段' not in json.dumps(repaired,ensure_ascii=False)


def test_repair_rejects_target_id_outside_immutable_registry():
    with pytest.raises(ValueError,match='invalid_repair_target_fids'):
        repair_payload({},[{'fid':'f3','question':'其他问题'}],FACETS,[],[])


def test_scope_replacement_updates_child_missing_without_dropping_hard_remainder(monkeypatch):
    import marine_kb.scope_gate
    monkeypatch.setenv('MARINE_SCOPE_GATE','true')
    monkeypatch.setattr(marine_kb.scope_gate,'gate_answer',lambda *a,**kw:{
        'answer':a[0],'kept':['个人保单资格尚未核定'],
        'removed':[{'text':'可选保费补贴信息尚未提供'}],
        'replacements':[{'original':'个人保单资格尚未核定；可选保费补贴信息尚未提供',
                         'replacement':'个人保单资格尚未核定'}]})
    class QA:coverage_review_enabled=False
    raw={'answer':'已保留有据办理流程[资料1]。',
         'missing_information':['个人保单资格尚未核定；可选保费补贴信息尚未提供'],
         'subquestion_coverage':[{'id':1,'missing_information':['个人保单资格尚未核定；可选保费补贴信息尚未提供']}]}
    changed,_,_=review_scope(QA(),'offline',json.dumps(raw,ensure_ascii=False),[],FACETS,'怎么申请？')
    value=json.loads(changed)
    assert value['missing_information']==['个人保单资格尚未核定']
    assert value['subquestion_coverage'][0]['missing_information']==['个人保单资格尚未核定']


def test_late_disagreement_withholds_only_matching_fact_and_dependent_inference():
    original={'accepted_atoms':[
        {'aid':'a','type':'direct_fact','text':'甲制度由县级审核[资料1]。','facet_ids':['f1']},
        {'aid':'b','type':'direct_fact','text':'乙制度由乡镇受理[资料2]。','facet_ids':['f2']},
        {'aid':'c','type':'synthesis','text':'两者审批主体不同。','premises':['a','b']}], 'decisions':[]}
    before=copy.deepcopy(original)
    changed=withhold_disputed_atoms(original,[{'kind':'unsupported_claim','text':'甲制度由县级审核[资料1]。'}])
    assert original==before
    assert [a['aid'] for a in changed['accepted_atoms']]==['b']
    assert changed['accepted_atoms'][0]==before['accepted_atoms'][1]
    assert changed['disputed_atom_withholding']['withheld_atom_ids']==['a','c']


def test_unasked_gap_never_withholds_substantive_fact():
    original={'accepted_atoms':[{'aid':'a','type':'direct_fact','text':'甲制度由县级审核[资料1]。'}]}
    changed=withhold_disputed_atoms(original,[{'kind':'unasked_gap','text':'甲制度由县级审核[资料1]。'}])
    assert changed['accepted_atoms']==original['accepted_atoms']


def test_short_critic_phrase_is_bound_to_its_original_paragraph():
    original={'accepted_atoms':[
        {'aid':'a','type':'direct_fact','text':'甲制度由县级审核[资料1]。','facet_ids':['f1']},
        {'aid':'b','type':'direct_fact','text':'乙制度由县级审核[资料2]。','facet_ids':['f2']} ]}
    changed=withhold_disputed_atoms(original,[{'kind':'unsupported_claim','text':'由县级审核',
        'target_id':'a1','target_text':'甲制度由县级审核[资料1]。'}])
    assert [a['aid'] for a in changed['accepted_atoms']]==['b']


def test_unresolved_critic_target_does_not_sweep_unrelated_siblings():
    original={'accepted_atoms':[
        {'aid':'a','type':'direct_fact','text':'甲制度由县级审核[资料1]。'},
        {'aid':'b','type':'direct_fact','text':'乙制度由县级审核[资料2]。'}]}
    changed=withhold_disputed_atoms(original,[{'kind':'unsupported_claim','text':'由县级审核','target_id':'a1'}])
    assert changed['accepted_atoms']==original['accepted_atoms']
    assert changed['disputed_atom_withholding']['ambiguous']
