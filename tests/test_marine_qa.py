"""Offline tests: no API key, network or original source changes required."""
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime

from marine_kb.qa import MarineQuestionService, current_context
from marine_kb.retrieval import rank_candidates, search
from marine_kb.semantic import LocalSemanticIndex
from marine_kb.storage import KnowledgeStore


def source(text="麦莎台风防御措施：组织渔船回港避风，转移危险区域人员。", **overrides):
    value = {"source_id": "s1", "chunk_id": 1, "title": "2005麦莎防台工作总结", "filename": "2005麦莎.docx", "text": text,
             "locator": "第3页", "category": "历史报告", "status": "indexed", "metadata": {"event_year": 2005}, "sids": [], "score": 1}
    value.update(overrides)
    return value


class FakeStore:
    def __init__(self, sources=()):
        self.sources = list(sources)
        self.queries = []

    def search_candidates(self, query, **kwargs):
        self.queries.append((query, kwargs))
        return self.sources


class FakeCaller:
    def __init__(self, answer="组织渔船回港避风。[资料1]", normalized=None, fail=False):
        self.answer = answer
        self.normalized = normalized
        self.fail = fail
        self.calls = []

    def __call__(self, model, system, prompt, **kwargs):
        self.calls.append((system, prompt))
        if self.fail:
            raise TimeoutError("private-token-must-not-leak")
        if "normalized_question" in system:
            return json.dumps({"normalized_question": self.normalized or json.loads(prompt)["question"]}, ensure_ascii=False)
        return self.answer


class FakeWeb:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {"answer": "公开网页记载的结果。[网页1]", "web_sources": [{"title": "公开来源", "url": "https://example.org/a"}], "answer_meta": {"used_mode": "llm"}}

    def answer(self, question, **kwargs):
        self.calls.append(question)
        return self.result


class MarineQATest(unittest.TestCase):
    def test_location_phrase_does_not_trigger_realtime_gate(self):
        stamp = datetime(2026, 10, 15)
        for question in ("2019年利奇马最大增水出现在什么站？", "最大增水出现在哪个地方？"):
            self.assertFalse(current_context(question, stamp)[1])
        for question in ("现在海门站潮位多少？", "2019年利奇马与现在的台风相比呢？", "当前最大增水出现在什么站？"):
            self.assertTrue(current_context(question, stamp)[1])

    def test_model_probe_has_no_documents_or_question_history(self):
        caller = FakeCaller(answer="连接正常")
        service = MarineQuestionService(FakeStore([source()]), model_caller=caller,
            external_enabled=True, model_configured=True, model_provider="deepseek")
        self.assertEqual(service.model_status()["state"], "ready_to_test")
        self.assertEqual(service.test_model_connection()["state"], "connected")
        self.assertTrue(service.model_status()["tested"])
        self.assertEqual(len(caller.calls), 1)
        self.assertNotIn("麦莎", str(caller.calls))
        self.assertNotIn("回港", str(caller.calls))
        self.assertEqual(service.store.queries, [])

    def test_local_historical_answer_has_traceable_citation(self):
        result = MarineQuestionService(FakeStore([source()])).answer("麦莎台风采取了哪些应对措施")
        self.assertIn("回港避风", result["answer"])
        self.assertIn("[资料1]", result["answer"])
        self.assertIn("未审定", result["answer"])
        self.assertEqual(result["rag_sources"][0]["locator"], "第3页")
        self.assertEqual(result["answer_meta"]["used_mode"], "local_extractive")
        self.assertFalse(result["answer_meta"]["embedding_enabled"])

    def test_semantic_expansion_links_response_synonyms(self):
        store = FakeStore([source("组织渔船回港避风并转移人员。")])
        answer = MarineQuestionService(store).answer("当时怎么应对")
        self.assertTrue(answer["rag_sources"])
        self.assertTrue(any("撤离" in query for query, _ in store.queries))

    def test_typo_normalization_without_external_model(self):
        result = MarineQuestionService(FakeStore([source("最大风速42 m/s，最低气压950 hPa。")])).answer("台风强度汝河")
        self.assertEqual(result["answer_meta"]["normalized_question"], "台风强度如何")
        self.assertIn("42 m/s", result["answer"])

    def test_llm_question_normalization_and_approved_summary(self):
        caller = FakeCaller(normalized="麦莎台风应对措施")
        result = MarineQuestionService(FakeStore([source(status="approved", metadata={"external_allowed": True})]), model_caller=caller, external_enabled=True).answer("麦莎防御怎么办")
        self.assertEqual(result["answer_meta"]["normalizer"], "llm")
        self.assertEqual(result["answer_meta"]["used_mode"], "llm")
        self.assertEqual(len(caller.calls), 2)
        self.assertEqual(set(json.loads(caller.calls[0][1])), {"question"})

    def test_default_never_calls_model_or_web(self):
        caller, web = FakeCaller(), FakeWeb()
        MarineQuestionService(FakeStore([source()]), model_caller=caller, web_service=web).answer("今年有几个台风", allow_web=True)
        self.assertEqual(caller.calls, [])
        self.assertEqual(web.calls, [])

    def test_no_external_document_permission_blocks_body_transmission(self):
        secret = "INTERNAL_SECRET_X91"
        caller = FakeCaller()
        result = MarineQuestionService(FakeStore([source("麦莎台风防御措施：回港避风。" + secret)]), model_caller=caller, external_enabled=True).answer("麦莎台风应对措施")
        self.assertEqual(len(caller.calls), 1)  # question normalization only
        self.assertNotIn(secret, str(caller.calls))
        self.assertEqual(result["answer_meta"]["used_mode"], "local_extractive")

    def test_pending_rejected_and_question_bank_excluded(self):
        sources = [source(status="pending_review"), source(chunk_id=2, status="rejected"), source(chunk_id=3, filename="台风海洋灾害智能体基础问题库.xlsx")]
        result = MarineQuestionService(FakeStore(sources)).answer("麦莎台风应对措施")
        self.assertEqual(result["rag_sources"], [])

    def test_pending_visible_only_explicit_management_search(self):
        store = FakeStore([source(status="pending_review")])
        self.assertEqual(search(store, "应对措施"), [])
        self.assertEqual(len(search(store, "应对措施", include_pending=True)), 1)

    def test_rule_requires_review_and_draft_never_official(self):
        sources = [source(category="标准", filename="海浪标准.pdf"), source(chunk_id=2, status="approved", filename="风暴潮标准报批稿.pdf")]
        self.assertEqual(search(FakeStore(sources), "防御措施"), [])

    def test_wrong_year_is_not_direct_evidence(self):
        result = MarineQuestionService(FakeStore([source()])).answer("2019年麦莎台风应对措施")
        self.assertEqual(result["rag_sources"], [])

    def test_wrong_typhoon_is_not_direct_evidence(self):
        result = MarineQuestionService(FakeStore([source()])).answer("利奇马台风应对措施")
        self.assertEqual(result["rag_sources"], [])

    def test_selected_sid_does_not_use_other_sid(self):
        result = MarineQuestionService(FakeStore([source(sids=["sid-other"])] )).answer("应对措施", sid="sid-selected")
        self.assertEqual(result["rag_sources"], [])

    def test_dedup_and_source_diversification(self):
        sources = [source(chunk_id=index) for index in range(6)]
        self.assertEqual(len(rank_candidates("应对措施", sources)), 1)

    def test_dynamic_cannot_answer_from_archival_docs(self):
        result = MarineQuestionService(FakeStore([source("今年麦莎台风防御措施：回港避风。")])).answer("现在有哪些防御措施")
        self.assertEqual(result["rag_sources"], [])
        self.assertIn("历史档案不能替代", result["answer"])

    def test_year_is_resolved_from_current_date(self):
        question, dynamic = current_context("今年登陆几个台风", datetime(2031, 1, 3))
        self.assertEqual(question, "2031年登陆几个台风")
        self.assertTrue(dynamic)

    def test_local_rag_hit_never_triggers_web(self):
        web = FakeWeb()
        result = MarineQuestionService(FakeStore([source()]), web_service=web, external_enabled=True).answer("麦莎台风应对措施", allow_web=True)
        self.assertTrue(result["rag_sources"])
        self.assertEqual(web.calls, [])

    def test_web_fallback_only_question_no_documents(self):
        secret = "INTERNAL_SECRET_X91"
        web = FakeWeb()
        result = MarineQuestionService(FakeStore([source(secret)]), web_service=web, external_enabled=True).answer("今年浙江登陆几个台风", allow_web=True)
        self.assertEqual(result["answer_meta"]["route"], "web_fallback")
        self.assertEqual(result["rag_sources"], [])
        self.assertNotIn(secret, str(web.calls))

    def test_no_source_web_answer_cannot_be_used(self):
        web = FakeWeb({"answer": "随意编造10个", "web_sources": []})
        result = MarineQuestionService(FakeStore(), web_service=web, external_enabled=True).answer("今年浙江登陆几个台风", allow_web=True)
        self.assertNotIn("10个", result["answer"])
        self.assertEqual(result["answer_meta"]["used_mode"], "no_evidence")

    def test_failed_llm_truthful_extract_and_redacted_error(self):
        caller = FakeCaller(fail=True)
        result = MarineQuestionService(FakeStore([source(metadata={"external_allowed": True})]), model_caller=caller, external_enabled=True).answer("麦莎台风应对措施")
        self.assertEqual(result["answer_meta"]["used_mode"], "local_extractive")
        self.assertEqual(result["answer_meta"]["fallback_reason"], "TimeoutError")
        self.assertNotIn("private-token", str(result))

    def test_unknown_citation_rejected(self):
        caller = FakeCaller(answer="一百万人转移。[资料99]")
        result = MarineQuestionService(FakeStore([source(metadata={"external_allowed": True})]), model_caller=caller, external_enabled=True).answer("麦莎台风应对措施")
        self.assertEqual(result["answer_meta"]["used_mode"], "local_extractive")
        self.assertNotIn("一百万人", result["answer"])

    def test_fabricated_numeric_value_with_valid_citation_rejected(self):
        caller = FakeCaller(answer="转移12345人。[资料1]")
        result = MarineQuestionService(FakeStore([source(metadata={"external_allowed": True})]), model_caller=caller, external_enabled=True).answer("麦莎台风应对措施")
        self.assertEqual(result["answer_meta"]["used_mode"], "local_extractive")
        self.assertNotIn("12345", result["answer"])

    def test_storm_chinese_english_aliases_share_scope(self):
        hit = source("MATSA最大风速42 m/s。", title="2005 MATSA实况", filename="MATSA.txt")
        result = MarineQuestionService(FakeStore([hit])).answer("2005年麦莎台风强度如何")
        self.assertTrue(result["rag_sources"])

    def test_chinese_storm_identity_not_changed_by_model(self):
        caller = FakeCaller(normalized="利奇马台风应对措施")
        result = MarineQuestionService(FakeStore(), model_caller=caller, external_enabled=True).answer("麦莎台风应对措施")
        self.assertEqual(result["answer_meta"]["normalizer"], "local_rules_fallback")

    def test_case_boundary_does_not_attach_next_storm_to_lekima(self):
        crossing = source("图3.76 1909号台风“利奇马”造成网箱受损\n15）1918号台风风暴潮灾害（橙色）\n1918号台风“米娜”（Mitag）于2019年10月1日登陆。\n受其影响，澉浦站增水最大为1.52 m。", title="浙江台风风暴潮灾害", filename="历史书.pdf", metadata={"event_year": 2019})
        good = source("1909号台风“利奇马”（LEKIMA）于2019年8月登陆。\n海门站增水最大，为3.12 m。", chunk_id=2, title="浙江台风风暴潮灾害", filename="历史书.pdf", metadata={"event_year": 2019})
        result = MarineQuestionService(FakeStore([crossing, good])).answer("2019年利奇马台风最大增水是多少")
        self.assertNotIn("1.52", result["answer"])
        self.assertIn("3.12", result["answer"])
        self.assertEqual(len(result["rag_sources"]), 1)

    def test_year_equipment_count_is_not_calendar_date(self):
        hit = source("储备设备2013台，减少经济损失。", metadata={"event_year": 2026})
        result = MarineQuestionService(FakeStore([hit])).answer("2013年浙江有哪些经济损失")
        self.assertEqual(result["rag_sources"], [])

    def test_warning_notice_not_used_as_standard(self):
        hit = source("海浪红色警报：预计24小时波高6米。", category="警报单")
        result = MarineQuestionService(FakeStore([hit])).answer("海浪红色警报标准是什么")
        self.assertEqual(result["rag_sources"], [])

    def test_damage_query_not_answered_by_surge_only(self):
        hit = source("2013年天兔 USAGI风暴潮最大增水0.5米。", title="2013天兔", filename="1319.xls")
        result = MarineQuestionService(FakeStore([hit])).answer("2013年天兔造成哪些损失")
        self.assertEqual(result["rag_sources"], [])

    def test_forecast_not_used_as_observed_maximum(self):
        hit = source("2019年利奇马预计明天海门站最大增水3米。", title="利奇马风暴潮警报", filename="2019警报.docx", metadata={"event_year": 2019})
        result = MarineQuestionService(FakeStore([hit])).answer("2019年利奇马最大增水是多少")
        self.assertEqual(result["rag_sources"], [])

    def test_explicit_bulletin_request_not_substituted_by_book(self):
        hit = source("2013年天兔台风造成经济损失1亿元。", title="历史风暴潮书籍", filename="历史书.pdf", metadata={"event_year": 2013})
        result = MarineQuestionService(FakeStore([hit])).answer("2013年公报记录的天兔损失")
        self.assertEqual(result["rag_sources"], [])

    def test_annual_list_not_individual_typhoon_damage(self):
        hit = source("2013年海洋灾害造成经济损失281636万元。\n今年影响浙江的台风包括1319“天兔”、1323“菲特”和1312“潭美”。", title="2013年浙江公报", filename="2013公报.pdf", metadata={"event_year": 2013})
        result = MarineQuestionService(FakeStore([hit])).answer("2013年天兔造成哪些损失")
        self.assertEqual(result["rag_sources"], [])

    def test_inline_individual_damage_does_not_inherit_annual_total(self):
        hit = source("2013年潭美和菲特引发灾害，各类海洋灾害造成损失282267万元。\n其中1323“菲特”台风造成直接经济损失233759万元；全年死亡24人。", title="2013年浙江公报", filename="2013公报.pdf", metadata={"event_year": 2013})
        result = MarineQuestionService(FakeStore([hit])).answer("2013年菲特造成哪些损失")
        self.assertIn("233759", result["answer"])
        self.assertNotIn("282267", result["answer"])
        self.assertNotIn("死亡24", result["answer"])

    def test_bulletin_case_heading_dot_quote_preserves_boundary(self):
        hit = source("潭美损失44118万元。\n3．1323“菲特”台风风暴潮灾害\n菲特造成直接经济损失233759万元。", title="2013公报", filename="2013公报.pdf", metadata={"event_year": 2013})
        result = MarineQuestionService(FakeStore([hit])).answer("2013年菲特造成哪些损失")
        self.assertIn("233759", result["answer"])
        self.assertNotIn("44118", result["answer"])

    def test_bulletin_filename_year_overrides_comparison_year_in_text(self):
        hit = source("2023年损失3775万元，与2013年相比下降。", title="2023浙江省海洋灾害公报.pdf", filename="2023公报.pdf", metadata={})
        result = MarineQuestionService(FakeStore([hit])).answer("2013年公报灾害损失")
        self.assertEqual(result["rag_sources"], [])

    def test_figure_name_alone_not_case_identity(self):
        hit = source("最大增水1.52米。\n图3.76 1909号台风“利奇马”造成网箱受损", title="2019公报", filename="2019公报.pdf", metadata={"event_year": 2019})
        result = MarineQuestionService(FakeStore([hit])).answer("2019年利奇马最大增水")
        self.assertEqual(result["rag_sources"], [])

    def test_incomplete_document_visible_quality_warning_no_raw_errors(self):
        for metadata in ({"requires_human_review": True}, {"quarantined_pages": [{"page": 2, "reason": "private-path-secret"}]}, {"embedded_media_count": 3}, {"conversion": "local_word_sha256_content_cache"}, {"complete_text_read": False}):
            with self.subTest(metadata=metadata):
                result = MarineQuestionService(FakeStore([source(metadata=metadata)])).answer("麦莎台风应对措施")
                self.assertIn("非全文结论", result["answer"])
                self.assertIn("未核验", result["rag_sources"][0]["quality_warning"])
                self.assertNotIn("private-path-secret", str(result))

    def test_model_summary_keeps_fixed_quality_warning(self):
        caller = FakeCaller()
        result = MarineQuestionService(FakeStore([source(metadata={"requires_human_review": True, "external_allowed": True, "warnings": "private-file-location"})]), model_caller=caller, external_enabled=True).answer("麦莎台风应对措施")
        self.assertEqual(result["answer_meta"]["used_mode"], "llm")
        self.assertIn("非全文结论", result["answer"])
        self.assertNotIn("private-file-location", str(caller.calls))

    def test_identity_not_rewritten_by_llm(self):
        caller = FakeCaller(normalized="2019年LEKIMA应对措施")
        result = MarineQuestionService(FakeStore(), model_caller=caller, external_enabled=True).answer("2005年MATSA应对措施")
        self.assertEqual(result["answer_meta"]["normalizer"], "local_rules_fallback")
        self.assertEqual(result["answer_meta"]["normalized_question"], "2005年MATSA应对措施")

    def test_legacy_requires_selected_sid_and_never_transmits_body(self):
        class Legacy:
            def search(self, query, **kwargs):
                return [{"content": "麦莎台风防御措施：渔船避风。", "document_id": "d1", "chunk_id": 1, "filename": "2005麦莎.txt", "linked_sid": "matsa", "metadata": {}}]
        caller = FakeCaller()
        service = MarineQuestionService(FakeStore(), legacy_rag=Legacy(), model_caller=caller, external_enabled=True)
        result = service.answer("麦莎台风应对措施", sid="matsa")
        self.assertEqual(len(result["rag_sources"]), 1)
        self.assertEqual(len(caller.calls), 1)
        self.assertEqual(service.answer("麦莎台风应对措施", sid="other")["rag_sources"], [])


class LocalSemanticIndexTest(unittest.TestCase):
    def setUp(self):
        try:
            import sklearn  # noqa: F401
        except ImportError:
            self.skipTest("Optional local LSA dependencies not installed")
        self.directory = tempfile.TemporaryDirectory(prefix="marine-lsa-test-")
        self.addCleanup(self.directory.cleanup)
        self.store = KnowledgeStore(Path(self.directory.name) / "test.sqlite")
        self.index = LocalSemanticIndex(self.store.database, Path(self.directory.name) / "index")
        self.store.semantic_index = self.index
        self.ids = []
        rows = [
            ("台风防御总结", "麦莎台风应对措施包括渔船回港避风，转移危险区域人员。"),
            ("海浪研究报告", "海浪波高和波浪周期需要结合观测资料分析。"),
            ("赤潮事件报告", "赤潮调查记录有害藻华和养殖生物死亡情况。"),
            ("风暴潮实况报告", "验潮站风暴潮最大增水2.41米，海门站超警。"),
        ]
        for index, (title, text) in enumerate(rows):
            identity, _ = self.store.register(filename=title, relative_path=title, sha256=str(index), size=len(text), raw_path=Path(self.directory.name) / title, category="历史资料")
            self.store.finish(identity, status="indexed", quality_status="parsed", metadata={}, warnings=[], chunks=[{"text": text, "locator": "第1页"}])
            self.ids.append(identity)

    def test_explicit_build_numeric_serialization_and_reload(self):
        built = self.index.rebuild(dimensions=3, max_features=1000)
        self.assertEqual(built["state"], "ready")
        self.assertEqual(built["indexed_chunks"], 4)
        reloaded = LocalSemanticIndex(self.store.database, self.index.index_dir)
        hits = reloaded.search("渔船回港避风防御措施")
        self.assertTrue(hits)
        self.assertIn("台风防御", hits[0]["title"])
        self.assertEqual(hits[0]["semantic_method"], "local_tfidf_char_ngram_lsa")
        self.assertFalse(list(self.index.index_dir.glob("*.pkl")))
        self.assertFalse(list(self.index.index_dir.glob("*.joblib")))

    def test_missing_lsa_falls_back_to_fts(self):
        hits = search(self.store, "台风应对措施")
        self.assertTrue(hits)
        self.assertEqual(self.index.last_status, "missing")

    def test_rejected_source_stales_index_then_fts_omits_it(self):
        self.index.rebuild(dimensions=3)
        self.store.review(self.ids[0], "rejected", "资料重复需撤回")
        self.assertEqual(self.index.search("台风应对措施"), [])
        self.assertEqual(self.index.last_status, "stale")
        self.assertFalse(any(hit["source_id"] == self.ids[0] for hit in search(self.store, "台风应对措施")))

    def test_tampered_numeric_index_is_not_loaded(self):
        self.index.rebuild(dimensions=3)
        with (self.index.index_dir / "vectors.npz").open("ab") as handle:
            handle.write(b"tampered")
        self.assertEqual(self.index.search("台风应对措施"), [])
        self.assertEqual(self.index.last_status, "integrity_failed")

    def test_hybrid_keeps_strict_evidence_checks(self):
        self.index.rebuild(dimensions=3)
        result = MarineQuestionService(self.store).answer("利奇马台风应对措施")
        self.assertEqual(result["rag_sources"], [])
        self.assertEqual(result["answer_meta"]["local_lsa_status"], "ready")

    def test_selected_sid_filters_vector_candidates(self):
        with self.store.connect() as connection:
            connection.execute("INSERT INTO links VALUES(?,?,?,?,?,?)", (1, "matsa", 1, "approved", "test", "麦莎"))
        self.index.rebuild(dimensions=3)
        self.assertEqual(self.index.search("台风应对措施", sid="other"), [])
        self.assertEqual(len(self.index.search("台风应对措施", sid="matsa")), 1)


if __name__ == "__main__":
    unittest.main()
