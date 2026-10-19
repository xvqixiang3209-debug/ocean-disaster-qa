from copy import deepcopy

import pytest

from marine_kb.query_intents import focused_queries, intents, support_score
from marine_kb.retrieval import rank_candidates, search


def source(text, *, chunk=1, source_id='procedure', status='approved', formal=True):
    return {'source_id': source_id, 'chunk_id': chunk, 'text': text,
            'title': '沿岸监测与警报作业细则.pdf' if formal else '历史风暴潮警报.docx',
            'category': '标准规范' if formal else '历史预报产品', 'status': status,
            'metadata': {'review_scope': 'versioned_reference' if formal else 'historical_reference',
                         'edition_current_verified': False, 'edition_status': 'unverified'},
            'locator': 'page:2', 'score': 20}


STATIONS = '承担以甲港、乙港等潮位站为代表站点的沿海核定岸段风暴潮预警指导产品制作与发布。'
PRODUCT = '风暴潮警报的技术判据：代表站、高潮位、风暴潮增水、警戒潮位、警报级别；发布风暴潮警报。'


@pytest.mark.parametrize('question', [
    '风暴潮警报的技术判据是什么？代表站怎么选？',
    '用于沿岸预警的代表站点有哪些，分别负责哪些岸段？',
    '沿海代表性验潮站都包括哪些站？',
    '验潮站的代表性和选取原则怎么理解？',
    '潮位站如何选取？',
])
def test_station_family_includes_unseen_wording_without_source_ids(question):
    assert 'representative_stations' in intents(question)
    assert any('代表站点' in query for query in focused_queries(question))


def test_station_assignment_section_beats_generic_product_columns():
    question = '风暴潮警报的技术判据是什么？代表站怎么选？'
    assert support_score(question, STATIONS) > support_score(question, PRODUCT)
    stations = source(STATIONS, chunk=10)
    # Boilerplate pages from the same document used to exhaust its three-page
    # allowance before the actual station assignment section was considered.
    catalogue = [source('海洋灾害联合会商与信息发布实施细则，规定海洋灾害警报发布工作。', chunk=i)
                 for i in range(1, 5)]
    products = [source(PRODUCT, chunk=100+i, source_id=f'forecast_{i}', formal=False)
                for i in range(30)]
    result = rank_candidates(question, products + catalogue + [stations], limit=8)
    assert result[0]['chunk_id'] == 10
    assert result[0]['metadata']['edition_current_verified'] is False
    assert result[0]['text'] == STATIONS  # Retrieval adds no inferred selection rules.


def test_focused_station_search_recovers_section_outside_original_candidate_window():
    station = source(STATIONS, chunk=12)
    products = [source(PRODUCT, chunk=100+i, source_id=f'forecast_{i}', formal=False)
                for i in range(80)]

    class Store:
        semantic_index = None

        def search_candidates(self, query, **options):
            if '代表站点 潮位站 核定岸段' in query:
                return [deepcopy(station)]
            return deepcopy(products)

    result = search(Store(), '代表站怎么选，风暴潮警报有哪些技术判据？', limit=8)
    assert result[0]['chunk_id'] == 12
    assert result[0]['retrieval_details']['direct_evidence'] is True


def test_station_preference_keeps_pending_draft_and_currency_gates():
    approved = source(STATIONS)
    pending = source(STATIONS, source_id='pending', status='pending_review')
    draft = source(STATIONS, source_id='draft')
    draft['metadata']['is_draft'] = True
    assert rank_candidates('代表站怎么选？', [pending, draft]) == []
    assert rank_candidates('现行警戒潮位标准中，代表站如何选取？', [approved]) == []
    assert rank_candidates('代表站怎么选？', [approved])[0]['metadata']['review_scope'] == 'versioned_reference'


def test_station_boost_never_satisfies_an_unmatched_event_identity():
    assert rank_candidates('2019年利奇马的风暴潮代表站实际潮位是多少？', [source(STATIONS)]) == []
