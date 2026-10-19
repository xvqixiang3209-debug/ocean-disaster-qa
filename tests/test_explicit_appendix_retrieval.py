from copy import deepcopy
import hashlib

import pytest

from marine_kb.retrieval import explicit_appendix_candidates


class Store:
    def __init__(self):
        self.calls = []
        source = {'source_id': 'manual', 'filename': '沿岸预警技术规范.pdf',
                  'category': '标准规范', 'status': 'approved',
                  'metadata': {'review_scope': 'versioned_reference',
                               'edition_current_verified': False, 'external_allowed': False}}
        self.document = {'source': source, 'chunks': [
            {'source_id': 'manual', 'chunk_id': 2, 'locator': 'page:9', 'metadata': {},
             'text': '附 录 B\n（资料性）\n代表站推荐表\n表 B.1 代表站\n甲站、乙站'},
            {'source_id': 'manual', 'chunk_id': 3, 'locator': 'page:10', 'metadata': {},
             'text': '表 B.1 代表站（续）\n丙站、丁站。括号内为替代站。'},
            {'source_id': 'manual', 'chunk_id': 4, 'locator': 'page:11', 'metadata': {},
             'text': '附录C（资料性）产品格式示例。'},
        ]}

    def detail(self, source_id, limit):
        self.calls.append(source_id)
        assert source_id == 'manual'  # No global appendix search in other books.
        return deepcopy(self.document)


def parent():
    return {'source_id': 'manual', 'chunk_id': 1, 'locator': 'page:6',
            'title': '沿岸预警技术规范.pdf', 'category': '标准规范', 'status': 'approved',
            'text': '产品格式示例见附录C。代表性验潮站选择见附录 B。',
            'url': '/knowledge.html?source=manual',
            'metadata': {'review_scope': 'versioned_reference', 'edition_current_verified': False}}


def test_explicit_same_document_appendix_and_same_table_continuation_keep_exact_text():
    store = Store()
    rows = explicit_appendix_candidates(store, '代表站怎么选？', [parent()])
    assert [r['chunk_id'] for r in rows] == [2, 3]
    assert store.calls == ['manual']
    for row, original in zip(rows, store.document['chunks']):
        assert row['text'] == original['text']
        assert row['locator'] == original['locator']
        assert row['_evidence_digest'] == hashlib.sha256(original['text'].encode()).hexdigest()
        assert row['metadata']['edition_current_verified'] is False
        assert row['metadata']['external_allowed'] is False
        assert row['metadata']['explicit_reference']['from_chunk_id'] == 1
        assert row['metadata']['explicit_reference']['appendix'] == 'B'


def test_current_rule_request_cannot_follow_unknown_version():
    store = Store()
    assert explicit_appendix_candidates(store, '现行警戒潮位标准中代表站怎么选？', [parent()]) == []
    assert store.calls == []


@pytest.mark.parametrize('failure', ['pending_parent', 'draft_parent', 'withdrawn_document', 'pending_target'])
def test_review_and_version_gates_apply_to_both_parent_and_fetched_document(failure):
    store, item = Store(), parent()
    if failure == 'pending_parent':
        item['status'] = 'pending_review'
    elif failure == 'draft_parent':
        item['metadata']['is_draft'] = True
    elif failure == 'withdrawn_document':
        store.document['source']['metadata']['edition_status'] = 'withdrawn'
    else:
        store.document['source']['status'] = 'pending_review'
    assert explicit_appendix_candidates(store, '代表站怎么选？', [item]) == []


@pytest.mark.parametrize('failure', ['foreign_document', 'foreign_chunk', 'external_reference'])
def test_does_not_resolve_a_different_documents_appendix(failure):
    store, item = Store(), parent()
    if failure == 'foreign_document':
        store.document['source']['source_id'] = 'another_manual'
    elif failure == 'foreign_chunk':
        for chunk in store.document['chunks']:
            chunk['source_id'] = 'another_manual'
    else:
        item['text'] = '代表性验潮站选择见另一份规范的附录B。'
    assert explicit_appendix_candidates(store, '代表站怎么选？', [item]) == []


def test_wrong_table_or_unmarked_adjacent_page_is_not_attached():
    store = Store()
    store.document['chunks'][1]['text'] = '表 B.2 其他产品表（续）'
    item = parent()
    item['text'] = '代表性验潮站选择见附录B。'
    assert [r['chunk_id'] for r in explicit_appendix_candidates(store, '代表站怎么选？', [item])] == [2]


def test_already_selected_page_is_not_duplicated_and_zero_limit_does_nothing():
    store, item = Store(), parent()
    existing = {**store.document['source'], **store.document['chunks'][0]}
    assert [r['chunk_id'] for r in explicit_appendix_candidates(store, '代表站怎么选？', [item, existing])] == [3]
    assert explicit_appendix_candidates(store, '代表站怎么选？', [item], limit=0) == []
