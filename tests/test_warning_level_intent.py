import pytest
from marine_kb.public_sources import primary_routes,research_question
from marine_kb.query_intents import focused_queries
from marine_kb.web_supplement import public_search_queries


@pytest.mark.parametrize('q',[
 '警戒潮位的蓝、黄、橙、红四级是怎么定的？到哪一级我才需要行动？',
 '蓝色警戒潮位是什么意思？现在就要准备吗？',
 '橙色警戒潮位是什么意思，与黄色有何不同？',
 '警戒潮位分哪四个级别？',
])
def test_level_meanings_do_not_become_research_calculations(q):
 assert not research_question(q)
 routes=primary_routes(q)
 assert any('宁波市海洋灾害公报' in r['title'] for r in routes)
 assert not any(r['_primary_scope']=='research_method' for r in routes)
 assert any('四色警戒潮位' in query for query in focused_queries(q))
 assert any('四色警戒潮位' in query for query in public_search_queries(q))


@pytest.mark.parametrize('q',[
 '四色警戒潮位核定的频率分析和重现期如何计算？',
 '警戒潮位怎么定？请给出定量方法、公式和研究论文。',
])
def test_explicit_technical_requests_keep_research_evidence(q):
 assert research_question(q)
 assert any(r['_primary_scope']=='research_method' for r in primary_routes(q))
