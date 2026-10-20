import hashlib
import json
from pathlib import Path

import pytest

from marine_kb.evidence_units import (
    build_doc_card, build_passages, document_identity_supports, evidence_view,doc_card_sha256,
)
from marine_kb.web_supplement import focused_public_spans


def source(body):
    return {'source_id':'web:fixture','title':'国务院办公厅关于印发《国家自然灾害救助应急预案》的通知',
            'url':'https://www.gov.cn/fixture','source_text':body,'text':body,
            'metadata':{'publication_date':'2024-02-04','retrieved_at':'2026-10-20T00:00:00+08:00'}}


def test_doc_identity_derivation_never_infers_currentness_or_body_facts():
    row = source('国办发〔2024〕9号\n\n本预案自2024年2月4日起施行。\n\n财政部会同应急管理部下达救助资金。')
    card = build_doc_card(row)
    assert card.issuer == '国务院办公厅'
    assert card.doc_no == '国办发〔2024〕9号'
    assert card.effective_from == '2024-02-04'
    assert card.version_status == 'version_unverified'
    assert card.jurisdiction == 'national'
    assert card.derivation['effective_from'].startswith('body_line:')
    assert document_identity_supports('依据《国家自然灾害救助应急预案》。',card)
    assert not document_identity_supports('依据《浙江省海洋灾害应急预案》。',card)
    for statement in ('《国家自然灾害救助应急预案》规定每人每天20元。',
                      '《国家自然灾害救助应急预案》规定财政部发放救助资金。',
                      '《国家自然灾害救助应急预案》由县政府批准申请。'):
        assert not document_identity_supports(statement,card)


def test_old_reviewed_capture_flags_recheck_without_asserting_expiry():
    row=source('本方案自2024年2月4日起施行。')
    row['metadata'].update(read_from_reviewed_archive=True,
                           retrieved_at='2020-01-01T00:00:00+00:00',
                           extraction_method='reviewed_official_pdf_excerpt')
    card=build_doc_card(row)
    assert card.reference_recheck_required
    assert card.version_status == 'version_unverified'
    assert card.acquisition_method == 'reviewed_official_pdf_excerpt'
    assert '超过180天' in card.render()


def test_body_offsets_section_paths_and_hashes_are_exact():
    body = '6 灾后救助\n\n6.2 过渡期生活救助\n\n省级部门申请过渡期生活救助资金。\n\n6.3 冬春救助\n\n6.3.1 地方应急管理部门组织摸排。\n\n按照户报、村评、乡审、县定程序确定对象。'
    passages = build_passages(source(body))
    winter = next(p for p in passages if '按照户报' in p.text)
    assert '6.3.1 地方应急管理部门组织摸排。' in winter.section_path
    assert any('6.3 冬春救助' == h for h in winter.section_path)
    for row in passages:
        assert body[row.start:row.end] == row.text
        assert row.sha256 == hashlib.sha256(row.text.encode()).hexdigest()
    assert list(p.passage_id for p in passages) == list(range(1,len(passages)+1))


def test_focus_is_original_order_no_frontloaded_effective_date_and_tail_preserved():
    body = '一、工作范围\n\n' + ('无关背景说明。\n\n'*300) + '6.3 冬春救助\n\n6.3.1 冬春救助对象核定\n\n通过户报、村评、乡审、县定程序确定救助对象。\n\n本方案自2024年4月30日起施行。'
    rows = focused_public_spans(body,'冬春救助依据什么办理？',['冬春救助程序是什么？'],limit=1800)
    assert any('户报、村评、乡审、县定' in p['text'] for p in rows)
    assert [p['start'] for p in rows] == sorted(p['start'] for p in rows)
    assert all(body[p['start']:p['end']] == p['text'] for p in rows)
    text = '\n\n'.join(p['text'] for p in rows)
    assert not text.startswith('本方案自')


def test_canonical_view_same_ids_hashes_separates_identity_and_selected_body():
    row = source('6.3 冬春救助\n\n按照户报、村评、乡审、县定程序确定对象。')
    row['metadata']['crag_passage_ids'] = [2]
    first = evidence_view([row],version=2)
    second = evidence_view([row],version=2)
    assert first == second
    assert [p['id'] for p in first[0]['passages']] == [0,2]
    assert first[0]['passages'][0]['kind'] == 'document_identity'
    assert first[0]['passages'][1]['kind'] == 'body'
    assert first[0]['passages'][0]['start'] is None
    assert first[0]['view_sha256'] == second[0]['view_sha256']
    assert first[0]['doc_card_sha256'] == doc_card_sha256(first[0]['doc_card'])


def test_doc_card_digest_covers_issuer_dates_and_url_with_body_unchanged():
    base={'title':'某工作方案','text':'开展救助工作。','metadata':{'issuer':'甲机构','publication_date':'2024-02-04'},
          'url':'https://www.mem.gov.cn/a.html'}
    first=evidence_view([base])[0]
    changes=[{**base,'metadata':{**base['metadata'],'issuer':'乙机构'}},
             {**base,'metadata':{**base['metadata'],'publication_date':'2025-02-04'}},
             {**base,'url':'https://www.mem.gov.cn/b.html'}]
    for changed in changes:
        other=evidence_view([changed])[0]
        assert other['body_sha256']==first['body_sha256']
        assert other['doc_card_sha256']!=first['doc_card_sha256']
        assert other['view_sha256']!=first['view_sha256']


def test_attachment_inherits_notice_identity_with_explicit_provenance_only():
    parent = build_doc_card(source('国办发〔2024〕9号\n\n正文。')).to_dict()
    row = {'title':'附件：救助工作指南','text':'办理材料包含户主申请和公示材料。',
           'url':'https://www.gov.cn/file.pdf',
           'metadata':{'parent_notice_doc_card':parent}}
    card = build_doc_card(row)
    assert card.issuer == '国务院办公厅'
    assert card.doc_no == parent['doc_no']
    assert card.derivation['issuer'].startswith('parent_notice:')
    assert card.title == '附件：救助工作指南'
    assert card.version_status == 'version_unverified'
    view = evidence_view([row])[0]
    assert view['doc_card']['parent_notice'] == parent
    assert all('国办发' not in p['text'] for p in view['passages'] if p['id'] != 0)


def test_version_one_is_exact_legacy_view():
    from marine_kb.qa import _evidence_passages
    row = source('台风定义及来源原文。'*400)
    row['metadata']['crag_passage_ids'] = [1,3]
    view = evidence_view([row],version=1)[0]
    assert view['passages'] == [p for p in _evidence_passages(row['text'][:3000]) if p['id'] in {1,3}]
    assert 'doc_card' not in view


@pytest.mark.parametrize('version',[1,2])
def test_empty_crag_selection_keeps_pinned_business_body(version):
    row = source('程序查询结果：2026年10月18日最大增水为152厘米。')
    row['metadata']['crag_passage_ids'] = []
    view = evidence_view([row],version=version)[0]
    assert any('152厘米' in p['text'] for p in view['passages'] if p['id'] != 0)
    if version == 2:
        all_rows = evidence_view([row],version=2)[0]['passages']
        chosen = evidence_view([row],{1:[1]},version=2)[0]['passages']
        assert chosen == [p for p in all_rows if p['id'] in {0,1}]


def test_recorded_public_retrieval_rebuilds_exact_units_without_any_network():
    # The raw P0 cassette remains private under tmp, never copied into fixtures.
    path = Path(__file__).resolve().parents[1]/'tmp/replay/p0_20261020_005001_b167cdca/G3.cassette.jsonl'
    if not path.exists(): pytest.skip('private local cassette unavailable')
    from marine_kb.replay import _unwire
    found = 0
    for record in map(json.loads,path.read_text(encoding='utf-8').splitlines()):
        if record.get('kind') != 'public_retrieval' or record.get('outcome') != 'return': continue
        response = _unwire(record['response'])
        for hit in response[0]:
            body = hit.get('text','')
            for row in build_passages(hit):
                assert body[row.start:row.end] == row.text
            found += 1
    assert found > 0
