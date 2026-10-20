"""Metadata support is opt-in, role-bound and never a body-fact shortcut."""
import json

import pytest

from marine_kb.evidence_units import evidence_view
from marine_kb.qa import _decode_model_answer


def source():
    return evidence_view([{'title':'某市政府关于印发《某市灾害救助办法》的通知',
                           'text':'本办法自2025年4月30日起施行。县级审核后发放救助资金，每人20元。',
                           'metadata':{'url':'https://example.gov.cn/policy.html'}}])


def decode(answer,quotes,sources=None):
    return _decode_model_answer(json.dumps({'answerability':'full','answer':answer,'evidence_quotes':quotes},ensure_ascii=False),sources or source())


def test_card_only_title_supported_without_requiring_title_inside_body(monkeypatch):
    monkeypatch.setenv('MARINE_EVIDENCE_UNITS','true')
    answer='依据《某市灾害救助办法》办理[资料1]。'
    assert decode(answer,[{'citation':1,'passage_id':0}])[0] == answer


def test_card_and_body_clauses_use_separate_support(monkeypatch):
    monkeypatch.setenv('MARINE_EVIDENCE_UNITS','true')
    sources=source()
    answer='依据《某市灾害救助办法》办理[资料1]。\n县级审核后发放救助资金，每人20元[资料1]。'
    assert decode(answer,[{'citation':'资料1','passage_id':0},{'citation':1,'passage_id':1}],sources)[0] == answer


@pytest.mark.parametrize('answer',[
    '依据《某市灾害救助办法》办理，每人20元[资料1]。',
    '县级审核后发放救助资金[资料1]。',
    '所有家庭均可申请救助资金[资料1]。',
    '《某市灾害救助办法》现行有效，全国适用[资料1]。',
    '该文件规定县级部门必须批准申请[资料1]。',
])
def test_card_zero_cannot_support_money_procedure_or_eligibility(monkeypatch,answer):
    monkeypatch.setenv('MARINE_EVIDENCE_UNITS','true')
    with pytest.raises(ValueError):
        decode(answer,[{'citation':1,'passage_id':0}])


def test_metadata_name_does_not_relax_wrong_body_numeric_value_or_unit(monkeypatch):
    monkeypatch.setenv('MARINE_EVIDENCE_UNITS','true')
    for phrase in ('每人200元','每人20米'):
        answer='依据《某市灾害救助办法》办理[资料1]。\n县级审核后发放救助资金，'+phrase+'[资料1]。'
        with pytest.raises(ValueError):
            decode(answer,[{'citation':1,'passage_id':0},{'citation':1,'passage_id':1}])


def test_identity_date_role_is_not_exchanged_for_issued_date(monkeypatch):
    monkeypatch.setenv('MARINE_EVIDENCE_UNITS','true')
    assert decode('该办法自2025年4月30日起施行[资料1]。',[{'citation':1,'passage_id':0}])[2] == {1}
    with pytest.raises(ValueError):
        decode('该办法于2025年4月30日印发[资料1]。',[{'citation':1,'passage_id':0}])


def test_wrong_title_and_laundered_operative_prefix_are_rejected(monkeypatch):
    monkeypatch.setenv('MARINE_EVIDENCE_UNITS','true')
    for answer in ('依据《另一市办法》办理[资料1]。','《某市灾害救助办法》由县级审核申请[资料1]。'):
        with pytest.raises(ValueError):
            decode(answer,[{'citation':1,'passage_id':0}])


def test_operative_words_inside_exact_file_name_are_identity_not_procedure(monkeypatch):
    monkeypatch.setenv('MARINE_EVIDENCE_UNITS','true')
    sources=evidence_view([{'title':'关于印发《灾害救助资金申请审核发放办法》的通知','text':'本办法自2025年4月30日起施行。'}])
    answer='文件名称为《灾害救助资金申请审核发放办法》[资料1]。'
    assert decode(answer,[{'citation':1,'passage_id':0}],sources)[0] == answer
    with pytest.raises(ValueError):
        decode('依据《灾害救助资金申请审核发放办法》立即审核并发放资金[资料1]。',[{'citation':1,'passage_id':0}],sources)


def test_metadata_zero_requires_server_v2_card_not_fake_passage(monkeypatch):
    monkeypatch.setenv('MARINE_EVIDENCE_UNITS','true')
    for sources in ([{'text':'普通正文。','passages':[{'id':0,'text':'文件：某市灾害救助办法'}]}],
                    [{**source()[0],'evidence_view_version':1}]):
        with pytest.raises(ValueError,match='document_identity_requires_v2_card'):
            decode('依据《某市灾害救助办法》办理[资料1]。',[{'citation':1,'passage_id':0}],sources)


def test_flag_off_keeps_legacy_rejection_and_body_bytes(monkeypatch):
    monkeypatch.delenv('MARINE_EVIDENCE_UNITS',raising=False)
    with pytest.raises(ValueError,match='quote_not_in_source'):
        decode('依据《某市灾害救助办法》办理[资料1]。',[{'citation':1,'passage_id':0}])
    answer='县级审核后发放救助资金，每人20元[资料1]。'
    before=decode(answer,[{'citation':1,'passage_id':1}])
    monkeypatch.setenv('MARINE_EVIDENCE_UNITS','true')
    assert decode(answer,[{'citation':1,'passage_id':1}]) == before


@pytest.mark.parametrize('heading',['**补助20万元**','**所有家庭都能领取**','**该办法现行有效**'])
def test_unchecked_factual_heading_cannot_hide_behind_identity_atom(monkeypatch,heading):
    monkeypatch.setenv('MARINE_EVIDENCE_UNITS','true')
    with pytest.raises(ValueError):
        decode(heading+'\n依据《某市灾害救助办法》办理[资料1]。',[{'citation':1,'passage_id':0}])
