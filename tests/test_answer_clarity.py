import io
import json
import unittest
import zipfile
import gzip
import time
from unittest.mock import patch

from marine_kb.answer_design import answer_design, readability_issues, is_policy_lookup, response_parts
from marine_kb.governance import reference_scope_allowed
from marine_kb.qa import MarineQuestionService, _question_parts
from marine_kb.retrieval import evidence_focus
from marine_kb.web_supplement import OfficialWebSupplement, focused_public_text
from typhoon.web_reader import WebPageReader, _ArticleParser, _Download, _DeadlineGuard
from typhoon.web_search import SearchOutcome
from test_marine_qa import FakeStore, source


QUESTION = '什么时候可以降级或终止响应？要走什么程序？'


class AnswerClarityTests(unittest.TestCase):
    def test_response_conditions_are_independent_when_planner_merges_them(self):
        parts = _question_parts(QUESTION, ['什么时候可以降级或终止响应？', '要走什么程序？'])
        self.assertEqual(len(parts), 3)
        self.assertIn('降级', parts[0]['question'])
        self.assertIn('终止', parts[1]['question'])
        self.assertIn('程序', parts[2]['question'])

    def test_startup_only_evidence_does_not_answer_termination(self):
        self.assertFalse(evidence_focus(QUESTION, '总指挥批准启动响应，办公室在一小时内报告并通知成员单位。'))
        self.assertTrue(evidence_focus(QUESTION, '经评估后，可以终止应急响应，由本级指挥机构决定。'))

    def test_web_success_does_not_force_data_question_into_definition_format(self):
        design = answer_design('2019年利奇马造成多少损失？', [])
        self.assertEqual(design['kind'], 'data_lookup')
        self.assertIn('时间与基准', design['guidance'])
        self.assertEqual(answer_design(QUESTION, [])['kind'], 'policy_procedure')

    def test_scope_and_repeal_are_kept_alongside_operative_clauses(self):
        scope = '本预案适用于主管部门组织开展的海洋灾害观测预警和调查评估工作。'
        repeal = '此前发布的旧版海洋灾害应急预案同时废止，适用新版本要求。'
        ending = '应急响应终止：警报解除后，由主管领导签发应急响应终止通知并发送有关单位。'
        body = scope + '\n\n' + ('介绍观测预报工作。' * 300) + '\n\n' + repeal + '\n\n' + ending
        text = focused_public_text(body, QUESTION, [p['question'] for p in _question_parts(QUESTION)])
        for expected in (scope, repeal, ending):
            self.assertIn(expected, text)

    def test_numbered_conditions_stay_with_termination_heading(self):
        body = ('1.1 本预案适用于本市海洋灾害应急处置工作。\n\n'
                + '5.6.1 应急终止的条件\n\n符合以下情形之一的，经评估后可以终止应急行动：\n\n'
                + '（1）灾害事件得到有效控制，引发灾害的条件已经消除；\n\n'
                + '（2）现场各种应急处置行动已无继续的必要；\n\n'
                + '5.6.2 应急终止的程序\n\n根据灾害处置情况，由有关指挥机构适时终止响应。')
        text = focused_public_text(body, '上海应急响应什么时候终止，由谁决定？',
                                   ['什么时候可以终止应急响应？','由谁决定终止应急响应？'])
        self.assertIn('引发灾害的条件已经消除', text)
        self.assertIn('现场各种应急处置行动已无继续的必要', text)
        self.assertIn('指挥机构适时终止响应', text)

    def test_readability_repair_retains_grounded_draft_if_rewrite_fails(self):
        payloads = []
        draft = '现有证据只涉及部分规定。经评估后可以终止应急响应。[资料1]'
        def caller(model, system, prompt, **kwargs):
            payload = json.loads(prompt)
            payloads.append(payload)
            if 'normalized_question' in system:
                return json.dumps({'normalized_question': payload['question']})
            if 'editorial_feedback' in payload:
                raise TimeoutError('transient')
            return json.dumps({'answerability':'partial','answer':draft,
                               'evidence_quotes':[{'citation':1,'passage_id':1}]})
        hit = source('经评估后可以终止应急响应。', metadata={'external_allowed':True})
        qa = MarineQuestionService(FakeStore([hit]), model_caller=caller, external_enabled=True, structured_answers=True)
        result = qa.answer('什么时候可以终止响应？')
        self.assertEqual(result['answer'], draft)
        self.assertEqual(result['answer_meta']['used_mode'], 'llm')
        self.assertEqual(result['answer_meta']['editorial_repairs'], 1)
        self.assertIn('self_check', payloads[-1]['answer_design'])

    def test_editorial_checker_does_not_reject_legitimate_insufficiency(self):
        self.assertEqual(readability_issues('未提供该站单位，无法计算。', 'insufficient'), [])
        self.assertEqual(readability_issues('可以按以下条件判断。[资料1]\n尚缺具体审批主体。', 'partial'), [])

    def test_unasked_gap_edit_cannot_erase_already_grounded_public_guidance(self):
        draft='清除花盆托盘积水，使用纱窗蚊帐并穿长袖长裤[资料1]。'
        def caller(model,system,prompt,**kwargs):
            payload=json.loads(prompt)
            if 'normalized_question' in system:
                return json.dumps({'normalized_question':payload['question']})
            if '证据覆盖复核器' in system:
                return json.dumps({'verdict':'revise','issues':[{'kind':'unasked_gap','target_id':'m1','reason':'通用防蚊未要求地方标准'}]})
            result={'answerability':'partial','answer':draft,'missing_information':['缺少地方专用防蚊标准']}
            if 'coverage_review_feedback' not in payload:
                result['evidence_quotes']=[{'citation':1,'passage_id':1}]
            return json.dumps(result)
        hit=source('蚊虫防护：清除花盆托盘积水，使用纱窗蚊帐并穿长袖长裤。',metadata={'external_allowed':True})
        qa=MarineQuestionService(FakeStore([hit]),model_caller=caller,external_enabled=True,structured_answers=True)
        qa.coverage_review_enabled=True
        result=qa._organized_answer('怎么防蚊？','deepseek-chat',[hit],
            {'as_of':'2026-10-19','subquestions':[{'id':1,'question':'怎么防蚊？'}]})
        self.assertEqual(result['answer'],draft)
        self.assertEqual(result['answer_meta']['used_mode'],'llm')
        self.assertTrue(result['rag_sources'])

    def test_versioned_explanation_does_not_certify_current_procedure(self):
        hit = source(metadata={'review_scope':'versioned_reference'})
        question = '浙江海洋灾害应急响应什么时候终止？'
        self.assertFalse(reference_scope_allowed(question, hit))
        self.assertTrue(reference_scope_allowed(question, hit, allow_versioned_explanation=True))
        self.assertFalse(reference_scope_allowed('浙江现行海洋灾害应急响应什么时候终止？', hit, allow_versioned_explanation=True))

    def test_current_response_status_keeps_live_freshness_and_question_intent(self):
        self.assertFalse(is_policy_lookup('当前浙江应急响应是否已经终止？'))
        self.assertEqual(response_parts('当前浙江应急响应是否已经终止？'), [])
        self.assertTrue(is_policy_lookup('自然资源部现行预案中终止响应的程序是什么？'))

    def test_old_response_announcement_cannot_answer_current_status(self):
        class Search:
            def search_with_status(self, query, **kwargs):
                return SearchOutcome(state='success', attempts=[{'state':'success'}], results=[
                    {'title':'海洋灾害应急响应终止通知','url':'https://www.example.gov.cn/old.html'}])
        class Reader:
            def enrich(self, rows):
                return [dict(row, final_url=row['url'], read_status='success', published_at='2020-10-15',
                             content='主管部门决定终止海洋灾害应急响应，请有关单位继续做好灾后防范和监测工作。') for row in rows]
        hits, meta = OfficialWebSupplement(Search(), Reader(), enabled=True, reference_pages={}).fetch(
            '当前浙江海洋灾害应急响应是否已经终止？', dynamic=True)
        self.assertEqual(hits, [])
        self.assertEqual(meta['web_stale_pages_excluded'], 1)


class OfficialAttachmentTests(unittest.TestCase):
    def compressed_download(self, text, limit):
        body=gzip.compress(text.encode())
        class Response:
            status=200
            def __init__(self): self.body=body
            def getheaders(self): return [('content-encoding','gzip'),('content-length',str(len(body)))]
            def read1(self, size):
                result,self.body=self.body[:size],self.body[size:]
                return result
            def close(self): pass
        class Connection:
            sock=None
            def __init__(self,*args): pass
            def request(self,*args,**kwargs): pass
            def getresponse(self): return Response()
            def close(self): pass
        deadline=time.monotonic()+3
        guard=_DeadlineGuard(deadline)
        try:
            with patch('typhoon.web_reader._resolve',return_value='93.184.216.34'), patch('typhoon.web_reader._PinnedConnection',Connection):
                return WebPageReader()._request('https://www.example.gov.cn/a',deadline,guard,limit)
        finally:
            guard.finish()

    def test_normal_compressed_official_body_is_readable(self):
        result=self.compressed_download('海冰是海水冻结形成的冰。',1000)
        self.assertEqual(result.body.decode(),'海冰是海水冻结形成的冰。')
        self.assertFalse(result.truncated)

    def test_compressed_body_cannot_exceed_decompressed_size_limit(self):
        result=self.compressed_download('a'*100000,1000)
        self.assertEqual(len(result.body),1000)
        self.assertTrue(result.truncated)

    def docx(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>应急响应终止：经评估后由主管领导签发通知，发送有关单位并开展后续监测。</w:t></w:r></w:p></w:body></w:document>')
        return buf.getvalue()

    def test_documents_are_opt_in_and_restricted_to_official_urls(self):
        reader = WebPageReader(allow_official_documents=True)
        self.assertTrue(reader._document_url_allowed('https://www.example.gov.cn/plan.docx'))
        self.assertFalse(reader._document_url_allowed('https://www.example.gov.cn.evil.example/plan.docx'))
        self.assertFalse(reader._document_url_allowed('https://www.example.gov.cn/plan.zip'))
        self.assertFalse(WebPageReader()._document_url_allowed('https://www.example.gov.cn/plan.docx'))

    def test_docx_is_read_as_text_not_executed_or_opened_in_office(self):
        reader = WebPageReader(allow_official_documents=True)
        url = 'https://www.example.gov.cn/plan.docx'
        download = _Download(url, 200, {'content-type':'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}, self.docx())
        with patch.object(reader, '_fetch', return_value=download):
            result = reader._read_page(url)
        self.assertEqual(result['read_status'], 'success')
        self.assertIn('主管领导签发通知', result['content'])
        self.assertEqual(result['extraction_method'], 'official_docx_text')

    def test_truncated_document_cannot_be_used(self):
        reader = WebPageReader(allow_official_documents=True)
        url = 'https://www.example.gov.cn/plan.docx'
        download = _Download(url, 200, {}, self.docx(), truncated=True)
        with patch.object(reader, '_fetch', return_value=download):
            self.assertEqual(reader._read_page(url)['read_status'], 'unsupported')

    def test_attachment_discovery_keeps_notice_context_and_uses_only_official_body(self):
        class Search:
            def search_with_status(self, query, **kwargs):
                return SearchOutcome(state='success', attempts=[{'state':'success'}], results=[{'title':'海洋灾害应急预案通知','url':'https://www.example.gov.cn/notice.html'}])
        class Reader:
            def enrich(self, rows):
                result=[]
                for row in rows:
                    parser = _ArticleParser()
                    parser.feed('<article>此前发布的旧版预案同时废止，请执行新的海洋灾害应急预案。<a href="https://www.example.gov.cn/plan.docx">海洋灾害应急预案</a></article>')
                    links = [{**link,'url':link['href']} for link in parser.extract()['document_links']]
                    body = '应急响应终止：警报解除后，由主管领导签发应急响应终止通知并发送有关单位。' if row['url'].endswith('docx') else '此前发布的旧版预案同时废止，请执行新的海洋灾害应急预案。'
                    result.append(dict(row, final_url=row['url'], read_status='success', content=body, document_links=links))
                return result
        hits, meta = OfficialWebSupplement(Search(), Reader(), enabled=True, reference_pages={}).fetch(QUESTION)
        self.assertTrue(hits[0]['url'].endswith('docx'))
        self.assertIn('同时废止', hits[0]['text'])
        self.assertIn('签发应急响应终止通知', hits[0]['text'])
        self.assertTrue(hits[0]['metadata']['parent_url'].endswith('notice.html'))
