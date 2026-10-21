import json
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

from marine_kb import warning_knowledge as wk


QUESTION = "海浪预警和风暴潮预警分别是什么意思，预警等级与应急响应有什么区别？"


class WarningKnowledgeTests(unittest.TestCase):
    def test_concept_variants_and_background_location(self):
        for question in (
            QUESTION,
            "什么是海浪预警？",
            "浙江的风暴潮警报是什么意思？",
            "海浪和风暴潮预警有什么不同？",
            "海洋灾害预警的四色从低到高怎么排？",
            "预警等级是不是应急响应等级？",
            "海浪警报与风暴潮警报是一回事吗？",
            "浙江的海浪预警意味着什么？",
            "海洋灾害预警和应急响应有什么区别？",
        ):
            with self.subTest(question=question):
                self.assertTrue(wk.matches(question))

    def test_operational_and_mixed_questions_do_not_get_captured(self):
        for question in (
            "截至2026年10月20日浙江发布了什么海浪预警？",
            "今天海浪预警是什么等级？覆盖哪些海域？",
            "浙江海浪预警的有效时间是什么？",
            "风暴潮橙色预警的高潮位阈值是多少米？",
            "海浪蓝色警报有效波高几米？",
            "海浪预警是什么意思，明天舟山能不能出海？",
            "海洋灾害应急响应由谁启动，什么情况下终止？",
            "浙江海洋灾害预警解除的条件是什么？",
            "海浪预警是什么，应该采取哪些措施？",
            "海浪预警是什么意思，哪里查询最新预警记录？",
            "风暴潮预警是什么意思，最高潮位怎么算？",
            "海浪预警是什么意思，请同时制作浙江影响报告。",
            "海啸预警和风暴潮预警的颜色有什么区别？",
            "台风预警和应急响应有什么区别？",
            "暴雨预警和应急响应有什么区别？",
            "海浪预警为什么导致停航？",
            "什么是海浪？",
        ):
            with self.subTest(question=question):
                self.assertFalse(wk.matches(question))
                self.assertIsNone(wk.answer(question))

    def test_every_fact_has_original_evidence_and_public_provenance(self):
        data = json.loads(wk.DATA_PATH.read_text(encoding="utf-8"))
        originals = {f'{source["id"]}:{excerpt["id"]}'
                     for source in data["sources"] for excerpt in source["excerpts"]}
        for source in data["sources"]:
            self.assertTrue(source["url"].startswith("https://"))
            self.assertRegex(source["url"], r"\.gov\.cn/")
            self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
            self.assertIn("+08:00", source["fetched_at"])
            self.assertTrue(source["publisher"])
            self.assertTrue(source["published_at"])
            self.assertTrue(all(row["locator"] and row["text"] for row in source["excerpts"]))
        for fact in data["facts"]:
            self.assertTrue(fact["supports"])
            self.assertTrue(set(fact["supports"]) <= originals)
            self.assertIn(fact["kind"], {"direct_fact", "grounded_summary", "evidence_synthesis"})

    def test_all_four_requested_concepts_are_substantive_not_gaps(self):
        reply = wk.answer(QUESTION)
        self.assertEqual(reply["answer_meta"]["answerability"], "full")
        self.assertFalse(reply["answer_meta"]["generation_used"])
        self.assertFalse(reply["answer_meta"]["live_web_search_performed"])
        self.assertEqual(reply["answer_meta"]["missing_information"], [])
        parts = {row["id"]: row for row in wk.build(QUESTION)["sections"]}
        self.assertIn("有效波高", parts["wave_warning"]["facts"][0]["text"])
        self.assertIn("高潮位", parts["surge_warning"]["facts"][0]["text"])
        self.assertIn("气压", parts["surge_warning"]["facts"][0]["text"])
        self.assertIn("蓝、黄、橙、红", reply["answer"])
        self.assertIn("组织力量、调集资源", reply["answer"])
        self.assertIn("不能只凭颜色", reply["answer"])
        self.assertIn("研判将要发生", reply["answer"])
        self.assertNotRegex(reply["answer"], r"缺少|待补|资料不足|无法解释|无法定义|未提供.*阈值")
        self.assertTrue(all(row["status"] == "full" for row in reply["answer_meta"]["subquestion_coverage"]))
        ids = {row["citation_id"] for row in reply["web_sources"]}
        self.assertTrue(set(map(int, re.findall(r"\[资料(\d+)\]", reply["answer"]))) <= ids)

    def test_definition_only_keeps_its_owner_and_minimal_sources(self):
        for word, owner, needle in (("海浪", "wave_warning", "有效波高"),
                                    ("风暴潮", "surge_warning", "高潮位")):
            payload = wk.build(f"{word}预警是什么意思？")
            self.assertEqual(payload["topics"], [owner])
            self.assertEqual(len(payload["hits"]), 1)
            self.assertIn(needle, payload["sections"][0]["facts"][0]["text"])
            self.assertNotIn("海浪蓝色警报", payload["hits"][0]["text"] if word == "风暴潮" else "")
            self.assertNotIn("风暴潮蓝色警报", payload["hits"][0]["text"] if word == "海浪" else "")

    def test_wave_surge_comparison_is_grounded_synthesis(self):
        payload = wk.build("海浪预警和风暴潮预警是一回事吗？")
        self.assertIn("wave_surge_comparison", payload["topics"])
        reply = wk.answer(payload["question"])
        self.assertIn("不能把海浪预警和风暴潮预警当作同一种预警", reply["answer"])
        self.assertNotIn("warning_response_comparison", payload["topics"])

    def _model(self, mutate=None, raises=False):
        calls = []

        def call(model, system, prompt):
            calls.append((model, system, json.loads(prompt)))
            if raises:
                raise RuntimeError("fake model offline")
            sections = json.loads(prompt)["sections"]
            layout = {"sections": [{"id": row["id"], "heading": row["allowed_headings"][-1],
                                     "fact_ids": [fact["id"] for fact in reversed(row["facts"])]}
                                    for row in sections]}
            if mutate:
                mutate(layout)
            return json.dumps(layout, ensure_ascii=False)

        return SimpleNamespace(external_enabled=True, model_caller=True, model_name="fake-model", _call_model=call), calls

    def test_model_can_organize_but_cannot_rewrite_protected_facts(self):
        qa, calls = self._model()
        reply = wk.answer(QUESTION, qa=qa, meta={"as_of": "2026-10-20T20:30:00+08:00"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(reply["answer_meta"]["used_mode"], "llm")
        self.assertEqual(reply["answer_meta"]["generation_scope"], "verified_fact_layout_only")
        self.assertFalse(reply["answer_meta"]["fallback"])
        self.assertEqual(reply["answer_meta"]["as_of"], "2026-10-20T20:30:00+08:00")
        for section in wk.build(QUESTION)["sections"]:
            for fact in section["facts"]:
                self.assertIn(fact["text"], reply["answer"])

    def test_joint_local_and_web_retrieval_executes_without_corrupting_card_relations(self):
        qa, calls = self._model()
        retrieval_calls = []
        def retrieve(question, meta, allow_web, pinned):
            retrieval_calls.append((question, allow_web, pinned))
            meta.update(local_retrieval_executed=True, web_search_executed=True,
                        web_search_queries=['海浪 风暴潮 预警 官方'])
            return [*pinned, {'text':'不相关历史台风记录，不得挤掉概念事实'}]
        qa.crag_enabled = True
        qa.joint_evidence = retrieve
        reply = wk.answer(QUESTION, qa=qa, allow_web=True)
        self.assertEqual(len(retrieval_calls), 1)
        self.assertTrue(reply['answer_meta']['live_web_search_performed'])
        self.assertTrue(reply['answer_meta']['local_retrieval_executed'])
        self.assertNotIn('不相关历史台风记录', reply['answer'])
        self.assertEqual(reply['answer_meta']['missing_information'], [])
        self.assertIn('有效波高', reply['answer'])

    def test_model_drop_duplicate_wrong_subject_or_new_business_prose_falls_back(self):
        def drop_section(layout):
            layout["sections"].pop()

        def drop_fact(layout):
            layout["sections"][-1]["fact_ids"].pop()

        def swap_owner(layout):
            layout["sections"][0]["fact_ids"], layout["sections"][1]["fact_ids"] = (
                layout["sections"][1]["fact_ids"], layout["sections"][0]["fact_ids"])

        def add_prose(layout):
            layout["sections"][0]["text"] = "海浪预警关注海水涨到多高；缺少当地启动阈值。"

        def mislabel(layout):
            layout["sections"][0]["heading"] = "风暴潮预警是什么意思"

        def duplicate(layout):
            layout["sections"][-1]["fact_ids"] = [layout["sections"][-1]["fact_ids"][0]] * 2

        expected = wk.answer(QUESTION)["answer"]
        for mutation in (drop_section, drop_fact, swap_owner, add_prose, mislabel, duplicate):
            with self.subTest(mutation=mutation.__name__):
                qa, _ = self._model(mutation)
                reply = wk.answer(QUESTION, qa=qa)
                self.assertEqual(reply["answer"], expected)
                self.assertTrue(reply["answer_meta"]["fallback"])
                self.assertTrue(reply["answer_meta"]["concept_layout_error"])
                self.assertEqual(reply["answer_meta"]["answerability"], "full")

    def test_model_failure_does_not_publish_fragment_or_generic_missing_data(self):
        qa, _ = self._model(raises=True)
        reply = wk.answer(QUESTION, qa=qa)
        self.assertEqual(reply["answer"], wk.answer(QUESTION)["answer"])
        self.assertEqual(reply["answer_meta"]["concept_layout_error"], "RuntimeError")

    def test_build_is_immutable_and_renders_only_used_originals(self):
        first = wk.build(QUESTION)
        first["sections"][0]["facts"][0]["text"] = "incorrect"
        self.assertNotEqual(wk.build(QUESTION)["sections"][0]["facts"][0]["text"], "incorrect")
        levels = wk.build("海洋灾害预警的四色是什么意思？")
        self.assertEqual(levels["topics"], ["warning_levels"])
        self.assertNotIn("人员搜救", "".join(row["text"] for row in levels["hits"]))


if __name__ == "__main__":
    unittest.main()
