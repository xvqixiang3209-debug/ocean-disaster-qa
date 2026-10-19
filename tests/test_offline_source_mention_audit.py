"""Offline audit distinguishes prose source mentions from formal citations."""
import pytest
from tools.audit_full160_computations import plain_source_mentions, audit_typed_facts


@pytest.mark.parametrize('text,expected', [
    ('结论[资料1]。资料4未提供正文。', [4]),
    ('资料2、3、4涉及历史资料；资料1至5另列。', [1, 2, 3, 4, 5]),
    ('资料2—5均为公报，资料7和资料8为标准。', [2, 3, 4, 5, 7, 8]),
    ('引用[资料4]及[资料12]，均不是纯正文提及。', []),
    ('资料2024年发布；本次收集资料12份。', []),
    ('资料4和资料4、5。', [4, 5]),
])
def test_plain_mentions_are_separate(text, expected):
    assert plain_source_mentions(text) == expected


def test_plain_mention_never_satisfies_required_fact_citation():
    fact = {'kind': 'computed', 'required': True, 'citation': 1,
            'metric': '有效波高最大', 'location': '朱家尖', 'value': 1.2, 'unit': 'm'}
    answer = '朱家尖有效波高最大1.2 m，见资料1。'
    assert plain_source_mentions(answer) == [1]
    result = audit_typed_facts(answer, [fact], set(), {1})
    assert result[0]['check'] == 'fail:required_fact_missing_returned_citation'
