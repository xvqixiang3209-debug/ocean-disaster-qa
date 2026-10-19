from marine_kb.qa import _evidence_passages


def test_short_typed_table_and_qualifications_keep_exact_original_passage():
    original = ('按原表与用户输入计算。' * 35 + '\n'
                '| 输入风速（m/s，2分钟平均） | 等级 |\n|---|---|\n'
                '| 32.6 | 强热带风暴 |\n| 32.7 | 台风 |\n'
                '| 41.4 | 台风 |\n| 41.5 | 强台风 |\n'
                '| 50.9 | 强台风 |\n| 51.0 | 超强台风 |\n'
                '仅适用于明确的2分钟平均风，不转换时距，不是实况。')
    assert 400 < len(original) < 1600
    assert _evidence_passages(original) == [{'id': 1, 'text': original}]


def test_untyped_or_long_table_does_not_remove_passage_size_bound():
    for original in [('上下文。' * 150 + '\n| 风速 | 类别 |\n|---|---|\n| 32.6 | A |'),
                     ('上下文。' * 500 + '\n| 风速（m/s） | 类别 |\n|---|---|\n| 32.6 | A |')]:
        passages = _evidence_passages(original)
        assert len(passages) > 1
        assert all(len(p['text']) <= 400 and p['text'] in original for p in passages)
