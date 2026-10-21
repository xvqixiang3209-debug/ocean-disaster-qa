import pytest

from marine_kb.storage import KnowledgeStore
from marine_kb.retrieval import search, evidence_kind


def add(store, title, text, status='approved', **metadata):
    identity, _ = store.register(filename=title, relative_path=title, sha256=title, size=1,
                                 raw_path='unused', category='标准规范', metadata={})
    store.finish(identity, status=status, quality_status='parsed', warnings=[],
                 metadata=metadata, chunks=[{'locator': '原文', 'text': text}])
    return identity


@pytest.fixture
def store(tmp_path):
    return KnowledgeStore(tmp_path / 'test.sqlite3')


def test_explicit_document_not_lost_among_shared_years(store):
    wanted = add(store, '海洋观测资料管理办法.doc', '第九条 其他单位每季度第一个月的二十日前汇交上季度资料。', event_year=2017)
    for number in range(100):
        add(store, f'2017浙江海洋观测公报{number}.txt', '2017年海洋观测资料管理工作，资料资料。')
    rows = search(store, '根据《海洋观测资料管理办法》2017年版本，其他单位何时汇交上季度资料？')
    assert rows[0]['source_id'] == wanted


def test_numeric_standard_identity_handles_typography(store):
    wanted = add(store, '海洋预报和警报发布.GBT+19721.2-2017.pdf', '海浪警报时效为24h。',
                 standard_id='GB/T 19721.2-2017', review_scope='versioned_reference')
    assert search(store, '按GB∕T 19721.2—2017，海浪警报时效是多少？')[0]['source_id'] == wanted


def test_named_document_does_not_bypass_pending_or_draft(store):
    add(store, '海洋资料管理办法.doc', '每季度汇交资料。', status='pending_review')
    add(store, '海洋资料管理办法草案.doc', '每季度汇交资料。', is_draft=True)
    assert search(store, '《海洋资料管理办法》规定何时汇交？') == []


def test_named_identity_does_not_bypass_version_scope(store):
    add(store, '海浪警报发布标准19721.2-2017.pdf', '海浪警报时效为24h。', review_scope='versioned_reference')
    assert search(store, '当前海浪警报标准的时效是多少？') == []


def test_standard_title_is_not_a_historical_forecast():
    assert evidence_kind({'title': '海浪预报和警报发布标准', 'text': '预计未来24小时'}) == '法规标准条文（所列版本）'


def test_named_versioned_txt_is_eligible_for_its_requested_edition(store):
    wanted = add(store, '海洋观测资料管理办法（2017年正式发布文本）.txt',
                 '其他单位于每季度第一个月的二十日前汇交上季度资料。',
                 review_scope='versioned_reference', publication_date='2017-06-07')
    assert search(store, '根据《海洋观测资料管理办法》（2017年公布版本）何时汇交上季度资料？请引用条款。')[0]['source_id'] == wanted


def test_amended_text_does_not_impersonate_original_edition(store):
    add(store, '海洋观测站点管理办法（2019年修正）.txt', '2017年公布，旧站至少一年的对比观测。',
        publication_date='2019-07-24')
    rows = search(store, '根据《海洋观测站点管理办法》（2017年公布版本），旧站对比观测多久？')
    assert not any(x['retrieval_details']['direct_evidence'] for x in rows)


def test_exact_identity_card_does_not_select_sibling_part(store):
    wanted = add(store, 'GB_T 14914.6-2021_官方身份卡.txt', '标准号：GB/T 14914.6-2021；标准名称：海洋观测规范 第6部分：数据处理与质量控制。',
                 standard_id='GB/T 14914.6-2021')
    add(store, 'GB_T 14914.3-2021_官方身份卡.txt', '标准号：GB/T 14914.3-2021；标准名称：海洋观测规范 第3部分：浮标潜标观测。',
        standard_id='GB/T 14914.3-2021')
    rows = search(store, 'GB/T14914.6-2021的全称和版本是什么？')
    assert rows[0]['source_id'] == wanted
    assert rows[0]['retrieval_details']['direct_evidence'] is True


def test_requested_deadline_beats_repeated_identity_and_edition(store):
    identity, _ = store.register(filename='海洋观测资料管理办法.doc', relative_path='资料管理办法.doc',
                                 sha256='raw', size=1, raw_path='unused', category='标准规范')
    store.finish(identity, status='approved', quality_status='parsed', warnings=[],
                 metadata={'review_scope': 'versioned_reference', 'publication_date': '2017-06-07'},
                 chunks=[{'locator': 'identity', 'text': '《海洋观测资料管理办法》2017年公布版本，海洋观测资料管理办法。'},
                         {'locator': 'penalty', 'text': '第二十五条 违反第十八条关于资料使用申请查验和答复规定的，依法给予处分。'},
                         {'locator': 'deadline', 'text': '第十八条 资料管理机构应在受理申请后的十五个工作日内答复，查验结束后十个工作日内完成资料交付。'}])
    for action in ('答复', '交付'):
        rows = search(store, '根据《海洋观测资料管理办法》（2017年公布版本），申请使用资料后多久' + action + '？', limit=3)
        assert rows[0]['locator'] == 'deadline'


def test_notice_code_selects_exact_issuer_year_issue_among_shared_titles(store):
    wanted = add(store, '浙防指〔2024〕4 号，关于印发防汛应急预案的通知.pdf',
                 '风险提示采用提示单。风险管控形成指令单、督办单。')
    for title in ('浙防指〔2023〕4号防汛应急预案.pdf',
                  '浙防指〔2024〕14号防汛应急预案.pdf',
                  '浙防指办〔2024〕4号防汛应急预案.pdf'):
        add(store, title, '风险提示采用提示单。风险管控形成指令单、督办单。')
    named = store.named_document_candidates('根据浙防指[2024]4号，风险管控采用哪些单据？')
    assert {row['source_id'] for row in named} == {wanted}
    rows = search(store, '按浙防指〔2024〕4号，风险管控采用哪些单据？')
    assert rows[0]['source_id'] == wanted
    assert rows[0]['named_document_match'] is True


def test_notice_identity_does_not_bypass_review_or_cited_only_title(store):
    add(store, '浙防指〔2024〕4号防汛应急预案.pdf', '提示单、指令单、督办单。', status='pending_review')
    add(store, '某县应急预案.pdf', '根据浙防指〔2024〕4号，采用提示单。')
    assert store.named_document_candidates('根据浙防指〔2024〕4号，三张单是什么？') == []


def test_quoted_short_subject_beats_repeated_notice_title_and_survives_selection(store):
    from marine_kb.evidence_selection import select_evidence
    identity, _ = store.register(filename='甲防指〔2025〕8号防汛防台抗旱应急预案.pdf',
                                 relative_path='预案.pdf', sha256='raw-notice', size=1,
                                 raw_path='unused', category='标准规范')
    store.finish(identity, status='approved', quality_status='parsed', warnings=[], metadata={},
                 chunks=[{'locator': f'title-{n}', 'text': '甲防指〔2025〕8号防汛防台抗旱应急预案，防汛防台抗旱应急预案。'} for n in range(10)] +
                        [{'locator': 'operative', 'text': '建立巡检、整改、复核的“闭环链”机制。'}])
    question = '根据甲防指〔2025〕8号防汛防台抗旱应急预案，“闭环链”包括什么？'
    rows = search(store, question)
    assert rows[0]['locator'] == 'operative'
    assert select_evidence(question, rows, [{'question': question}], limit=1)[0]['locator'] == 'operative'


def test_quoted_term_in_title_alone_is_not_substantive_clause_priority(store):
    identity, _ = store.register(filename='乙防指〔2025〕9号“闭环链”管理办法.pdf',
                                 relative_path='办法.pdf', sha256='raw-other', size=1,
                                 raw_path='unused', category='标准规范')
    store.finish(identity, status='approved', quality_status='parsed', warnings=[], metadata={},
                 chunks=[{'locator': 'date', 'text': '本办法自公布之日起实施。'},
                         {'locator': 'substance', 'text': '“闭环链”包括登记、处置和复核三个环节。'}])
    assert search(store, '根据乙防指〔2025〕9号，“闭环链”包括什么？')[0]['locator'] == 'substance'
