"""P0 records existing attribution decisions without repairing their behavior.

The 11-claim fixture reproduces recorded G3 claim texts and verdicts from the
a6113ee handoff. It is a structural replay, not the missing original model call
or a new assessment of whether those verdicts are correct.
"""
import hashlib
import json

import pytest

from marine_kb.claim_grounding import apply_attribution, draft_claims
from marine_kb.qa import _decode_model_answer


G3_CLAIMS = [
    '过渡期生活救助和冬春救助属于政府灾害救助制度，巨灾保险赔付属于保险理赔，三者依据、办理主体和程序不同。',
    '1. 是否一回事：不是。',
    '过渡期生活救助资金由财政部会同应急管理部按相关政策规定下达，应急管理部指导人员核定、资金发放等工作[资料3]；冬春救助是受灾群众安全温暖过冬的制度性安排，由应急管理部会同财政部印发工作通知并组织申报[资料1]；巨灾保险赔付则由承保机构按保险合同查勘定损、公示后支付理赔款[资料2]。',
    '2. 过渡期生活救助依据：依据《国家自然灾害救助应急预案》办理。',
    '该预案规定，根据省级财政、应急管理部门的资金申请及需救助人员规模，财政部会同应急管理部按相关政策规定下达过渡期生活救助资金；应急管理部指导做好人员核定、资金发放等工作[资料3]。',
    '该资料为2024年修订版，但当前是否仍现行有效尚未核实[资料3]。',
    '3. 冬春救助依据：依据应急管理部会同财政部印发的冬春救助工作通知办理，通过国家自然灾害救助资金管理系统申报审批，程序为“户报、村评、乡审、县定”[资料1]。',
    '4. 巨灾保险赔付依据：依据保险合同及地方公共巨灾保险实施方案办理。',
    '以宁波市为例，依据《宁波市公共巨灾保险工作实施方案（2024—2026年）》，受灾居民向村（社区）报损，由联络员登记汇总并向承保机构报案，承保机构查勘定损、公示后按合同支付理赔款[资料2][资料4]。',
    '该方案自2024年4月30日起施行[资料4]。',
    '其他地区的具体依据，现有资料未覆盖。',
]
G3_STATUSES = [
    'unsupported', 'unsupported', 'unsupported', 'unsupported', 'unsupported',
    'limitation', 'unsupported', 'unsupported', 'unsupported', 'supported',
    'limitation',
]


def decode(candidate, evidence):
    return _decode_model_answer(json.dumps(candidate, ensure_ascii=False), evidence)


def g3_fixture():
    raw = json.dumps({'answer': '\n\n'.join(G3_CLAIMS)}, ensure_ascii=False)
    passage = '本方案自2024年4月30日起施行。'
    evidence = [{'text': '', 'passages': []} for _ in range(3)] + [
        {'text': passage, 'passages': [{'id': 1, 'text': passage}]},
    ]
    review = {'claims': [
        {'id': index, 'status': status,
         'refs': [{'citation': 4, 'passage_id': 1}] if status == 'supported' else []}
        for index, status in enumerate(G3_STATUSES, 1)
    ]}
    return raw, review, evidence


def test_recorded_g3_retention_audit_keeps_answer_bytes_unchanged():
    raw, review, evidence = g3_fixture()
    legacy, legacy_audit = apply_attribution(raw, json.dumps(review, ensure_ascii=False), evidence, decode)
    events = []
    traced, audit = apply_attribution(raw, json.dumps(review, ensure_ascii=False), evidence, decode,
                                     audit=True, audit_sink=events.append)
    assert legacy == traced
    assert traced[0] == ('该方案自2024年4月30日起施行[资料4]。\n\n'
                          '部分子项的依据尚未核对完整，以上仅保留本次来源能够支持的内容。')
    assert audit['claim_count'] == 11 and audit['retained_claims'] == 1
    assert len(audit['decisions']) == 11
    assert [row['id'] for row in audit['decisions'] if row['retained']] == [10]
    assert 'retained' not in legacy_audit['decisions'][0]
    assert 'drop_rule' not in legacy_audit['decisions'][0]
    assert audit['decisions'][5]['status'] == 'limitation'
    assert audit['decisions'][5]['retained'] is False
    assert audit['decisions'][5]['drop_rule'] == 'limitation_has_citation'
    assert audit['decisions'][10]['status'] == 'limitation'
    assert audit['decisions'][10]['retained'] is False
    assert audit['decisions'][10]['drop_rule'] == 'limitation_vocab_miss'
    assert audit['decisions'][9]['drop_rule'] is None
    assert audit['decisions'][0]['drop_rule'] == 'model_unsupported'
    assert len(events) == 1 and events[0]['retained_claims'] == 1
    assert events[0]['decisions'][9]['claim_sha256'] == hashlib.sha256(
        G3_CLAIMS[9].encode('utf-8')).hexdigest()
    assert set(events[0]['decisions'][0]) == {
        'id', 'status', 'retained', 'drop_rule', 'claim_sha256',
    }
    assert 'claim_text' not in json.dumps(events)
    assert '过渡期' not in json.dumps(events, ensure_ascii=False)


def test_empty_attribution_still_emits_decisions_without_publishing():
    raw, review, evidence = g3_fixture()
    review['claims'][9].update(status='unsupported', refs=[])
    events = []
    assert apply_attribution(raw, json.dumps(review, ensure_ascii=False), evidence, decode, audit=True,
                             audit_sink=events.append) is None
    assert events[0]['claim_count'] == 11
    assert events[0]['retained_claims'] == 0
    assert events[0]['has_supported_quotes'] is False
    assert not any(row['retained'] for row in events[0]['decisions'])


def test_failed_audit_sink_cannot_change_answer():
    raw, review, evidence = g3_fixture()
    expected = apply_attribution(raw, json.dumps(review, ensure_ascii=False), evidence, decode)

    def failed_sink(fields):
        raise RuntimeError('telemetry unavailable')

    actual = apply_attribution(raw, json.dumps(review, ensure_ascii=False), evidence, decode, audit_sink=failed_sink)
    assert actual[0] == expected[0]
    assert [{k: v for k, v in row.items() if k not in {'retained', 'drop_rule'}}
            for row in actual[1]['decisions']] == expected[1]['decisions']


@pytest.mark.parametrize('text,rule', [
    ('本次资料缺少该站24小时预报值。', 'limitation_has_quantities'),
    ('该问题的业务输入尚未提供。', 'limitation_scope_miss'),
    ('现有资料所涉及地区与本题不同。', 'limitation_vocab_miss'),
    ('本次资料不足，不能据此判定安全。', None),
])
def test_limitation_diagnostics_observe_existing_filter(text, rule):
    raw, review, evidence = g3_fixture()
    raw = json.dumps({'answer': G3_CLAIMS[9] + '\n\n' + text}, ensure_ascii=False)
    _, claims = draft_claims(raw)
    assert len(claims) == 2
    review = {'claims': [
        {'id': 1, 'status': 'supported', 'refs': [{'citation': 4, 'passage_id': 1}]},
        {'id': 2, 'status': 'limitation', 'refs': []},
    ]}
    legacy = apply_attribution(raw, json.dumps(review, ensure_ascii=False), evidence, decode)
    traced, audit = apply_attribution(raw, json.dumps(review, ensure_ascii=False), evidence, decode, audit=True)
    assert traced == legacy[0]
    assert audit['decisions'][1]['retained'] is (rule is None)
    assert audit['decisions'][1]['drop_rule'] == rule


@pytest.mark.parametrize('bad_ref,rule', [
    ({'citation': 4, 'passage_id': 999}, 'reference_check_failed'),
    ({'citation': 4, 'passage_id': 1}, 'deterministic_check_failed'),
])
def test_failed_references_and_quantity_checks_are_distinguished(bad_ref, rule):
    raw, review, evidence = g3_fixture()
    raw = json.dumps({'answer': G3_CLAIMS[9] + '\n\n该方案要求24小时内完成办理[资料4]。'},
                     ensure_ascii=False)
    review = {'claims': [
        {'id': 1, 'status': 'supported', 'refs': [{'citation': 4, 'passage_id': 1}]},
        {'id': 2, 'status': 'supported', 'refs': [bad_ref]},
    ]}
    legacy = apply_attribution(raw, json.dumps(review, ensure_ascii=False), evidence, decode)
    traced, audit = apply_attribution(raw, json.dumps(review, ensure_ascii=False), evidence, decode, audit=True)
    assert traced == legacy[0]
    assert audit['decisions'][1]['retained'] is False
    assert audit['decisions'][1]['drop_rule'] == rule
