from marine_kb.evidence_selection import select_evidence


def test_identity_paragraph_does_not_cover_deadline_siblings():
    prefix = '根据《海洋观测资料管理办法》（2017年公布版本），'
    parts = [{'question': prefix + '何时汇交上季度资料'}, {'question': prefix + '申请使用资料后多久答复'},
             {'question': prefix + '申请使用资料后多久交付'}]
    def hit(n, text):
        return {'source_id': 'same', 'chunk_id': n, 'title': '海洋观测资料管理办法.doc',
                'named_document_match': True, 'text': text,
                'metadata': {'applicability': {'use': 'direct'}}}
    rows = [hit(1, '《海洋观测资料管理办法》2017年公布版本，已经审议通过，自公布之日起施行。'),
            hit(2, '第九条 从事海洋观测的其他单位每季度第一个月二十日前汇交上季度资料。'),
            hit(3, '第十八条 海洋观测资料管理机构在受理申请后的十五个工作日内答复，在查验结束后的十个工作日内完成资料交付。')]
    picked = select_evidence('；'.join(p['question'] for p in parts), rows, parts, limit=2)
    assert {x['chunk_id'] for x in picked} == {2, 3}
