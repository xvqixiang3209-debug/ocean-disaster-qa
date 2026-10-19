import pytest
from marine_kb.query_intents import focused_queries
from marine_kb.web_supplement import public_search_queries


def test_where_to_receive_alerts_is_not_the_criteria_for_issuing_them():
 q='作为宁波市鄞州区居民，我在哪里能第一时间收到官方预警？'
 assert any('渠道' in query for query in focused_queries(q))
 queries=public_search_queries(q)
 assert any('渠道' in query and '鄞州' in query for query in queries)
 assert not any('发布条件' in query for query in queries)


def test_asof_standard_catalog_uses_registry_and_version_not_exact_day_weather_search():
 q='截至2026年10月17日，海洋观测预报与防灾减灾领域现行标准有哪些？'
 queries=public_search_queries(q,dynamic=True)
 assert any('site:std.samr.gov.cn' in query for query in queries)
 assert not any('2026年10月17日' in query or 'site:weather.com.cn' in query for query in queries)
 assert any('国家标准' in query for query in focused_queries(q))


def test_actual_dated_warning_question_still_preserves_valid_date():
 q='2026年10月17日舟山发布了哪些海洋灾害预警？'
 assert any('2026年10月17日' in query for query in public_search_queries(q,dynamic=True))


def test_catalog_facet_does_not_remove_operational_date_from_other_facet():
 parts=['海洋观测预报领域现行标准有哪些？','2026年10月17日舟山发布了哪些海洋灾害预警？']
 queries=public_search_queries('另外，'.join(parts),dynamic=True,subquestions=parts)
 assert any('site:std.samr.gov.cn' in query and '2026年10月17日' not in query for query in queries)
 assert any('2026年10月17日' in query and '预警' in query and 'site:std.samr.gov.cn' not in query for query in queries)


@pytest.mark.parametrize('q',['如何订阅舟山海浪预警？','通过哪些平台能收到宁波的官方预警？','在哪里查询官方海洋预警的接收渠道？'])
def test_channel_family_handles_unseen_word_order(q):
 assert any('渠道' in query for query in public_search_queries(q))


@pytest.mark.parametrize('q',['海洋业务有哪些国家标准？','有哪些海洋防灾减灾行业标准？','海洋观测规范目录在哪里查？'])
def test_standard_catalog_handles_unseen_word_order(q):
 assert any('site:std.samr.gov.cn' in query for query in public_search_queries(q))


def test_verified_research_repository_pdf_is_explicitly_enabled_and_host_bounded():
 from typhoon.web_reader import WebPageReader
 from marine_kb.web_supplement import trusted_url
 reader=WebPageReader(allow_official_documents=True)
 u='https://nora.nerc.ac.uk/id/eprint/529769/1/paper.pdf'
 assert reader._document_url_allowed(u) and trusted_url(u,True)
 assert not WebPageReader()._document_url_allowed(u)
 assert not trusted_url(u,False)
 assert not reader._document_url_allowed('https://nora.nerc.ac.uk.evil.example/paper.pdf')


def test_assimilation_routes_to_method_papers_not_only_defence_reports():
 from marine_kb.public_sources import primary_routes
 urls=[r['url'] for r in primary_routes('有哪些数据同化方案？对增水预报改进效果如何？')]
 assert any('529769' in url for url in urls) and any('2404.05758' in url for url in urls)
