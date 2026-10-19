import json
import pytest
from marine_kb.qa import MarineQuestionService, current_context


@pytest.mark.parametrize('original, proposed', [
    ('假设舟山海域发布海浪红色警报，居民现在必须做什么？',
     '舟山海域发布海浪红色警报，居民现在必须做什么？'),
    ('情景设定：海浪黄色警报升级为橙色，游客现在该如何避险？',
     '海浪黄色警报升级为橙色，游客现在该如何避险？'),
    ('舟山现在是否发布海浪红色警报？',
     '假设舟山发布海浪红色警报，这是什么意思？'),
])
def test_model_cannot_remove_or_invent_a_scenario(original, proposed):
    def caller(*args, **kwargs):
        return json.dumps({'normalized_question': proposed, 'retrieval_queries': [],
                           'subquestions': [proposed]}, ensure_ascii=False)
    service = MarineQuestionService(None, external_enabled=True, model_caller=caller)
    normalized, method, _, _ = service._normalize(original, 'test')
    assert normalized == original
    assert method == 'local_rules_fallback'
    assert current_context(normalized)[1] == current_context(original)[1]


def test_retained_scenario_can_still_be_reworded():
    original = '假设发布海浪红色警报，居民如何避险？'
    proposed = '在假设发布海浪红色警报的情景下，居民如何避险？'
    def caller(*args, **kwargs):
        return json.dumps({'normalized_question': proposed, 'retrieval_queries': [],
                           'subquestions': [proposed]}, ensure_ascii=False)
    service = MarineQuestionService(None, external_enabled=True, model_caller=caller)
    normalized, method, _, _ = service._normalize(original, 'test')
    assert normalized == proposed and method == 'llm'
    assert current_context(normalized)[1] is False


def test_followup_can_inherit_an_explicit_scenario_from_context():
    proposed = '假设发布海浪红色警报，居民该如何避险？'
    def caller(*args, **kwargs):
        return json.dumps({'normalized_question': proposed, 'retrieval_queries': [],
                           'subquestions': [proposed]}, ensure_ascii=False)
    service = MarineQuestionService(None, external_enabled=True, model_caller=caller)
    normalized, method, _, _ = service._normalize('那居民该如何避险？', 'test',
        context=[{'question': '假设发布海浪红色警报，这是什么意思？'}])
    assert normalized == proposed and method == 'llm'


def test_hypothetical_this_typhoon_does_not_reenable_current_data_gate(monkeypatch):
    question = '假设这次台风发布红色警报，居民现在该如何避险？'
    service = MarineQuestionService(None, external_enabled=True, model_caller=lambda *a, **k: '',
                                   structured_answers=True, search_policy='always')
    monkeypatch.setattr(service, '_hybrid_answer', lambda *args: {'answer_meta': args[5]})
    result = service._answer(question, allow_web=True, question_plan={
        'normalized_question': question, 'normalizer': 'test',
        'retrieval_queries': [], 'subquestions': [{'id': 1, 'question': question}]})
    assert result['answer_meta']['dynamic_question'] is False
