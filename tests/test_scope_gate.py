import json
from pathlib import Path

import pytest

from marine_kb.facet_contract import build_contract
from marine_kb.scope_gate import gate, gate_answer
from marine_kb.evidence_applicability import assess_page, document_kind

RECORDS = json.loads((Path(__file__).parent / 'fixtures/p0_scope_cases_24.json').read_text('utf8'))['cases']


@pytest.mark.parametrize('case', RECORDS, ids=[row['case_id'] for row in RECORDS])
def test_recorded_review_spans_do_not_upgrade_full(case):
    contract = build_contract(case['question'])
    result = gate(case['missing_information'], contract, case['review_issues'],
                  subquestion_coverage=case['subquestion_coverage'])
    assert result['answerability_upgrade_allowed'] is False
    assert len(result['kept']) + len(result['removed']) == len(case['missing_information'])
    assert all(row['reviewed_exact_span'] and not row['protected'] for row in result['removed'])


def test_recorded_unasked_technical_local_rule_is_removed():
    case = next(row for row in RECORDS if row['case_id'] == 'A2')
    result = gate(case['missing_information'], build_contract(case['question']), case['review_issues'])
    assert result['kept'] == []
    assert result['removed'][0]['gap_types'] == ['jurisdiction_rule_not_found']


@pytest.mark.parametrize('question,gap', [
    ('宁波巨灾保险今年赔了多少？', '缺少宁波今年实际赔款总额数据'),
    ('今天舟山南沙浴场开放吗？', '缺少南沙浴场当日开放公告及有效时段'),
    ('宁波家中受灾能申请什么救助？', '个人家庭收入、受损和救助资格尚未核定'),
    ('宁波家中受灾能申请什么救助？', '当地补助标准与申请窗口尚未取得'),
    ('如何计算舟山增水叠加潮位？', '缺少高程基准与SWL定义核定'),
    ('温州当地现行网箱加固标准怎么执行？', '缺少温州当地现行网箱加固标准'),
    ('宁波今年救助金额是多少？', '尚未取得该项完整结果'),
    ('今天舟山南沙浴场开放吗？', '尚未取得该项完整结果'),
])
def test_critic_cannot_remove_real_required_gap(question, gap):
    result = gate([gap], build_contract(question), [{'kind': 'unasked_gap', 'text': gap}])
    assert result['kept'] == [gap]
    assert result['decisions'][0]['protected']


def test_no_fuzzy_unreviewed_gap_removal_and_split_preserves_real_child():
    q = '舟山网箱锚泊怎么检查？'
    gap = '缺少舟山本地网箱专用规范'
    assert gate([gap], build_contract(q), [])['kept'] == [gap]
    assert gate([gap], build_contract(q), [{'kind': 'unasked_gap', 'text': '舟山的地方标准'}])['kept'] == [gap]
    mixed = gap + '；保单是否赔付尚未核定'
    result = gate([mixed], build_contract(q), [{'kind': 'unasked_gap', 'text': gap}])
    assert result['kept'] == ['保单是否赔付尚未核定']
    assert result['removed'][0]['text'] == gap
    assert any(row['protected'] for row in result['decisions'] if row['action'] == 'keep')


def test_answer_mutation_never_removes_sibling_facts_or_clause_substring():
    fact = '检查锚链连接并修复松动处[资料1]。'
    gap = '缺少舟山本地网箱专用规范。'
    review = [{'kind': 'unasked_gap', 'text': gap}]
    result = gate_answer(fact + '\n\n' + gap, [gap], build_contract('舟山网箱怎么检查？'), review)
    assert result['answer'] == fact
    compound = fact[:-1] + '，但' + gap
    assert gate_answer(compound, [gap], build_contract('舟山网箱怎么检查？'), review)['answer'] == compound
    whole_review = [{'kind': 'unasked_gap', 'text': compound}]
    assert gate_answer(compound, [compound], build_contract('舟山网箱怎么检查？'), whole_review)['answer'] == compound


def test_typing_corrections_default_off_and_explicitly_enabled(monkeypatch):
    monkeypatch.delenv('MARINE_SCOPE_GATE', raising=False)
    assert document_kind('台风的预报与防御', '台风科普') == 'forecast'
    monkeypatch.setenv('MARINE_SCOPE_GATE', 'true')
    assert document_kind('台风的预报与防御', '台风科普') == 'knowledge'
    assert document_kind('浙江省海浪预报（10月18日）', '浪高2米') == 'forecast'
    assert document_kind('舟山海浪与风暴潮预报（10月18日）', '浪高2米') == 'forecast'


def test_general_guidance_no_local_adoption_gap(monkeypatch):
    monkeypatch.setenv('MARINE_SCOPE_GATE', 'true')
    result = assess_page('温州网箱台风前怎么检查加固？', {
        'title': '海水鱼养殖防台风技术手册', 'content': '检查网箱缆绳和框架，修复网衣。'})
    assert result['use'] == 'direct'
    assert '来源未明确覆盖所问地点' not in result['reasons']


def test_unverified_edition_support_does_not_certify_current_law(monkeypatch):
    monkeypatch.setenv('MARINE_SCOPE_GATE', 'true')
    page = {'title': '国务院办公厅关于印发国家自然灾害救助应急预案的通知',
            'content': '本预案适用于全国。财政部会同应急管理部下达资金。', 'published_at': '2024-01-01'}
    basis = assess_page('过渡期生活救助依据什么办理？', page)
    assert basis['use'] == 'direct' and basis['temporal'] == 'version_unverified'
    assert basis['support_scope'] == 'cited_edition_explanation_only'
    current = assess_page('现行过渡期生活救助依据什么办理？', page)
    assert current['use'] == 'reference'
    status = assess_page('今天舟山南沙浴场开放吗？', page)
    assert status['use'] == 'reference'
