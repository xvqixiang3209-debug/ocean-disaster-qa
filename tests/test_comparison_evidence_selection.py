from marine_kb.evidence_selection import select_evidence


def source(name, year, text):
    return {'source_id':name, 'chunk_id':name, 'title':str(year)+'年'+name+'风暴潮过程报告',
            'text':text, 'metadata':{}, 'score':1}


def test_distinct_event_reports_can_supply_both_requested_comparison_sides():
    left=source('利奇马',2019,'2019年利奇马风暴潮最大增水312厘米，发生在海门站。')
    right=source('米娜',2019,'2019年米娜风暴潮最大增水152厘米，发生在澉浦站。')
    alien=source('桑美',2006,'2006年桑美风暴潮最大增水100厘米。')
    question='比较2019年利奇马和2019年米娜在浙江的风暴潮最大增水。'
    selected=select_evidence(question,[left,right,alien],limit=4)
    assert {r['source_id'] for r in selected}=={'利奇马','米娜'}


def test_single_named_event_still_excludes_another_storm_report():
    left=source('利奇马',2019,'2019年利奇马风暴潮最大增水312厘米。')
    right=source('米娜',2019,'2019年米娜风暴潮最大增水152厘米。')
    selected=select_evidence('2019年利奇马的风暴潮最大增水是多少？',[right,left],limit=4)
    assert [r['source_id'] for r in selected]==['利奇马']
