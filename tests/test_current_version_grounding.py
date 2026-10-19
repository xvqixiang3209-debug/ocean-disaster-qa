import json
import pytest
from marine_kb.version_claims import validate_current_version_claims
from marine_kb.qa import _decode_model_answer


def evidence(verified=False):
 text='GB/T 13972—2010 海洋水文仪器通用技术条件。本标准代替GB/T 13972—1992。'
 return [{'text':text,'passages':[{'id':1,'text':text}],
          'metadata':{'edition_current_verified':verified,'applicability':{'temporal':'version_unverified'}}}]


@pytest.mark.parametrize('answer',[
 'GB/T 13972—2010为现行版本，其代替旧版[资料1]。',
 '可确认GB/T 13972—2010是现行有效标准[资料1]。',
 '根据现行标准GB/T 13972—2010解释[资料1]。',
 '现行GB/T 13972—2010代替旧版[资料1]。',
 'GB/T 13972—2010为现行版本，但其现行有效性尚未核实[资料1]。',
])
def test_replacement_official_source_or_later_caveat_never_proves_current_status(answer):
 with pytest.raises(ValueError,match='current_version_not_verified'):
  validate_current_version_claims(answer,evidence())


@pytest.mark.parametrize('answer',[
 'GB/T 13972—2010代替GB/T 13972—1992，仅按所列版本参考[资料1]。',
 '无法确认GB/T 13972—2010为现行版本[资料1]。',
 'GB/T 13972—2010的现行有效性尚未核实[资料1]。',
 '不能根据现行标准清单作出结论，因为本次没有完整目录[资料1]。',
 '上述为技术手册归纳，不是温州地方现行标准[资料1]。',
 '本次资料不是现行规范清单，只说明技术方法[资料1]。',
])
def test_archive_explanation_and_explicit_uncertainty_remain_available(answer):
 validate_current_version_claims(answer,evidence())


def test_explicit_verification_is_required_and_invalid_time_cannot_be_overridden():
 validate_current_version_claims('该标准为现行版本[资料1]。',evidence(True))
 rows=evidence(True);rows[0]['metadata']['applicability']['temporal']='after_cutoff'
 with pytest.raises(ValueError):validate_current_version_claims('该标准为现行版本[资料1]。',rows)


def test_main_decoder_applies_version_gate_to_recovered_candidates_too():
 rows=evidence();raw={'answerability':'partial','answer':'GB/T 13972—2010为现行版本[资料1]。','evidence_quotes':[{'citation':1,'passage_id':1}]}
 with pytest.raises(ValueError,match='current_version_not_verified'):
  _decode_model_answer(json.dumps(raw,ensure_ascii=False),rows)
