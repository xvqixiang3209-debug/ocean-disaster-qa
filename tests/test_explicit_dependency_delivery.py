from unittest.mock import patch

from marine_kb.qa import MarineQuestionService


def test_explicit_dependency_requires_its_own_external_permission():
    service = MarineQuestionService(None, external_enabled=True)
    parent = {'source_id': 'standard', 'chunk_id': 1, 'text': '选择见附录A。'}
    allowed = {'source_id': 'standard', 'chunk_id': 2,
               'text': '附录A原文', 'metadata': {'external_allowed': True}}
    blocked = {'source_id': 'standard', 'chunk_id': 3,
               'text': '附录A受限续表', 'metadata': {'external_allowed': False}}
    with patch('marine_kb.retrieval.explicit_appendix_candidates', return_value=[allowed, blocked]) as lookup:
        selected = service._with_explicit_dependencies('代表站如何选择？', [parent])
    lookup.assert_called_once_with(None, '代表站如何选择？', [parent], limit=2)
    assert selected == [parent, allowed]
    assert selected[1] is allowed
