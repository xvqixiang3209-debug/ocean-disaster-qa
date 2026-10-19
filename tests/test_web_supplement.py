import json
import unittest

from marine_kb.qa import MarineQuestionService
from marine_kb.web_supplement import OfficialWebSupplement, public_search_queries, terminology_queries, trusted_url
from typhoon.web_search import SearchOutcome
from typhoon.web_reader import WebPageReader
from test_marine_qa import FakeStore, source


class Search:
    def __init__(self):
        self.queries = []

    def search_with_status(self, query, **kwargs):
        self.queries.append(query)
        return SearchOutcome(state='success', attempts=[{'state': 'success'}], results=[
            {'title': '台风定义', 'url': 'https://www.cma.gov.cn/definition.html'},
            {'title': '伪造来源', 'url': 'https://cma.gov.cn.attacker.example/definition.html'}])


class Reader:
    def enrich(self, rows):
        return [dict(row, final_url=row['url'], read_status='success',
                     content='台风是热带气旋的一种。热带气旋是发生在热带或副热带洋面上的低压涡旋。',
                     read_at='2026-10-15T13:00:00+00:00') for row in rows]


class WebSupplementTests(unittest.TestCase):
    def test_public_term_query_contains_no_private_question_context(self):
        question = '内部秘密项目X91：台风是什么？联系人和原文不要传播。'
        self.assertEqual(terminology_queries(question), ['台风 定义 site:cma.gov.cn'])
        self.assertEqual(terminology_queries('2019年利奇马最大增水多少？'), [])
        self.assertEqual(terminology_queries('用模式表计算哪些站超警？'), [])
        self.assertEqual(terminology_queries('什么是风暴增水？它和天文大潮碰在一起会怎么样？')[0],
                         '风暴增水 天文潮 叠加 site:weather.com.cn')

    def test_official_url_boundary(self):
        for url in ('https://www.cma.gov.cn/a', 'https://www.nmefc.cn/a'):
            self.assertTrue(trusted_url(url))
        for url in ('https://cma.gov.cn.evil.example/a', 'http://127.0.0.1/a',
                    'https://user:password@cma.gov.cn/a', 'javascript:alert(1)',
                    'https://cma.gov.cn:8080/a'):
            self.assertFalse(trusted_url(url))

    def test_regional_business_domain_boundaries_and_read_priority(self):
        for domain in ('zjocean.org.cn', 'hxzs.com.cn'):
            for host in (domain, 'www.' + domain, 'service.' + domain):
                with self.subTest(host=host):
                    url = 'https://' + host + '/notice'
                    self.assertTrue(trusted_url(url))
                    self.assertTrue(WebPageReader._official({'url': url}))
            for host in (domain + '.attacker.example', 'fake' + domain):
                with self.subTest(host=host):
                    url = 'https://' + host + '/notice'
                    self.assertFalse(trusted_url(url))
                    self.assertFalse(WebPageReader._official({'url': url}))
            for url in ('https://user:password@' + domain + '/notice',
                        'https://' + domain + ':8080/notice',
                        'https://' + domain + '@attacker.example/notice'):
                with self.subTest(url=url):
                    self.assertFalse(trusted_url(url))

    def test_regional_business_queries_reach_primary_sites(self):
        self.assertIn('site:hxzs.com.cn', ' '.join(public_search_queries('今天舟山客运是否停航？', dynamic=True)))
        self.assertIn('site:zjocean.org.cn', ' '.join(public_search_queries('当前浙江海浪预警是什么？', dynamic=True)))
        self.assertNotIn('site:hxzs.com.cn', ' '.join(public_search_queries('今天福建客运是否停航？', dynamic=True)))
        self.assertNotIn('site:zjocean.org.cn', ' '.join(public_search_queries('当前广东海浪预警是什么？', dynamic=True)))

    def test_regional_business_bodies_used_but_spoofed_redirect_rejected(self):
        class BusinessSearch:
            def search_with_status(self, query, **kwargs):
                return SearchOutcome(state='success', results=[
                    {'title': '舟山客运停复航公告', 'url': 'https://hxzs.com.cn/notice'},
                    {'title': '舟山海浪预警', 'url': 'https://zjocean.org.cn/notice'},
                    {'title': '舟山客运伪造公告', 'url': 'https://hxzs.com.cn.attacker.example/notice'},
                    {'title': '舟山客运重定向公告', 'url': 'https://www.hxzs.com.cn/redirect'}])

        class BusinessReader:
            def enrich(self, rows):
                return [dict(row, read_status='success',
                             final_url='https://hxzs.com.cn.attacker.example/notice' if row['url'].endswith('/redirect') else row['url'],
                             content=('舟山海浪预警查询：预报机构公布海浪预警的发布时间、浪高、警报级别及适用海域，客运安排另查运营方公告。'
                                      if 'zjocean.org.cn' in row['url'] else
                                      '舟山客运停复航公告查询：运营方按具体航线和航次公布停航与复航安排，船票旅客查询对应客运时刻。'))
                        for row in rows]

        for question, expected_url in (
                ('舟山客运停复航公告怎么查询？', 'https://hxzs.com.cn/notice'),
                ('舟山海浪预警怎么查询？', 'https://zjocean.org.cn/notice')):
            with self.subTest(question=question):
                hits, _ = OfficialWebSupplement(BusinessSearch(), BusinessReader(), enabled=True,
                                                reference_pages={}).fetch(question)
                urls = {hit['url'] for hit in hits}
                self.assertIn(expected_url, urls)
                self.assertLessEqual(urls, {'https://hxzs.com.cn/notice', 'https://zjocean.org.cn/notice'})

    def test_fetched_bodies_only_and_cache(self):
        search = Search()
        service = OfficialWebSupplement(search, Reader(), enabled=True, reference_pages={})
        hits, meta = service.fetch('什么是台风？')
        self.assertEqual(len(hits), 1)
        self.assertEqual(meta['web_supplement_state'], 'success')
        self.assertEqual(hits[0]['metadata']['source_type'], 'public_web')
        self.assertTrue(hits[0]['metadata']['external_allowed'])
        _, cached = service.fetch('什么是台风？')
        self.assertTrue(cached['web_supplement_cache_hit'])
        self.assertEqual(len(search.queries), 1)

    def test_redirect_outside_official_domain_or_failed_read_not_evidence(self):
        class RedirectReader:
            def enrich(self, rows):
                return [dict(row, final_url='https://attacker.example/a', read_status='success', content='台风是' * 30) for row in rows]
        class FailedReader:
            def enrich(self, rows):
                return [dict(row, read_status='failed', snippet='伪造定义' * 20, content='') for row in rows]
        for reader in (RedirectReader(), FailedReader()):
            hits, meta = OfficialWebSupplement(Search(), reader, enabled=True, reference_pages={}).fetch('什么是台风？')
            self.assertEqual(hits, [])
            self.assertEqual(meta['web_supplement_state'], 'unavailable')

    def test_interaction_query_retains_relation_without_unasked_numeric_examples(self):
        class InteractionReader:
            def enrich(self, rows):
                return [dict(row, final_url=row['url'], read_status='success', content=
                    '旧文泛指海面异常升降，中心气压每降低1hPa，海面上升1cm。'
                    '风暴增水与天文潮高潮叠加时，会引起沿海水位暴涨和海水倒灌。'
                    '如果增水很高，即使未遇天文高潮，也可能造成严重潮灾。') for row in rows]
        hits, _ = OfficialWebSupplement(Search(), InteractionReader(), enabled=True,
                                        reference_pages={}).fetch('风暴增水与天文大潮叠加是什么意思？')
        self.assertEqual(len(hits), 1)
        self.assertIn('叠加', hits[0]['text'])
        self.assertNotIn('1hPa', hits[0]['text'])
        self.assertNotIn('升降', hits[0]['text'])

    def test_web_definitions_join_local_evidence_and_keep_provenance(self):
        search = Search()
        supplement = OfficialWebSupplement(search, Reader(), enabled=True, reference_pages={})
        payloads = []
        def caller(model, system, prompt, **kwargs):
            payload = json.loads(prompt)
            payloads.append(payload)
            if 'normalized_question' in system:
                return json.dumps({'normalized_question': payload['question']})
            self.assertEqual(payload['boundary_reason'], '')
            self.assertIn('public_web', str(payload['evidence']))
            return json.dumps({'answerability': 'full', 'answer': '台风是热带气旋的一种。[资料1]',
                               'evidence_quotes': [{'citation': 1, 'passage_id': 1}]})
        qa = MarineQuestionService(FakeStore([source('台风资料：PRIVATE_SOURCE')]), model_caller=caller,
                                   external_enabled=True, structured_answers=True, web_supplement=supplement, search_policy='local_first')
        result = qa.answer('什么是台风？', allow_web=True)
        self.assertEqual(result['answer_meta']['used_mode'], 'llm')
        self.assertEqual(result['rag_sources'], [])
        self.assertEqual(len(result['web_sources']), 1)
        self.assertIn('cma.gov.cn', result['web_sources'][0]['url'])
        self.assertNotIn('PRIVATE_SOURCE', str(payloads))
        self.assertNotIn('PRIVATE_SOURCE', str(search.queries))

    def test_live_search_rejects_undated_definitions_and_explicit_disable_skips_search(self):
        search = Search()
        supplement = OfficialWebSupplement(search, Reader(), enabled=True, reference_pages={})
        def caller(model, system, prompt, **kwargs):
            payload = json.loads(prompt)
            if 'normalized_question' in system:
                return json.dumps({'normalized_question': payload['question']})
            return json.dumps({'answerability': 'insufficient', 'answer': '缺少当前实况，无法判断。'})
        qa = MarineQuestionService(FakeStore(), model_caller=caller, external_enabled=True,
                                   structured_answers=True, web_supplement=supplement, search_policy='local_first')
        live = qa.answer('当前台风是什么情况？', allow_web=True)
        self.assertTrue(search.queries)
        self.assertEqual(live['answer_meta']['web_stale_pages_excluded'], 1)
        self.assertEqual(live['web_sources'], [])
        previous_count = len(search.queries)
        qa.answer('什么是台风？', allow_web=False)
        self.assertEqual(len(search.queries), previous_count)

