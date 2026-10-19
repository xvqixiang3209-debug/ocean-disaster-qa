import json
import pytest


@pytest.mark.parametrize('question',[
    '我们一家七口，台风前按照官方家庭应急基础清单最低要备多少饮用水？这个量是每天的吗？',
    '家庭防灾准备，四口之家最低备水总量怎么查？',
    '每人台风应急饮用水备用量按哪份清单？',
    '家里4口人，应急饮用水按全国基础清单总共几升？',
])
def test_household_paraphrases_discover_the_actual_national_list(question):
    from marine_kb.public_sources import primary_routes
    from marine_kb.web_supplement import public_search_queries
    assert any('mem.gov.cn' in row['url'] for row in primary_routes(question))
    assert any('家庭' in q and '饮用水' in q for q in public_search_queries(question))


@pytest.mark.parametrize('question',[
    '有效波高恰好6米时应该叫巨浪还是狂浪？有效波高是单个最高浪吗？',
    '海浪等级的分界：巨浪上限和狂浪下限属于哪个？',
    '狂浪这个名称怎么分，和最大浪有啥关系？',
])
def test_wave_boundary_phrasing_seeks_grade_table_not_example_forecast(question):
    from marine_kb.query_intents import intents,focused_queries
    assert 'wave_grading' in intents(question)
    assert any('级别划分' in q for q in focused_queries(question))


def test_grade_table_beats_historical_range_wording():
    from marine_kb.evidence_selection import select_evidence
    q='有效波高恰好6米时应该叫巨浪还是狂浪？有效波高是单个最高浪吗？'
    table={'title':'海洋灾害公报附表','text':'海浪级别划分 有效波高（米） 巨浪4.0≤Hs＜6.0 狂浪6.0≤Hs＜9.0。Hs为有效波高。','chunk_id':'table'}
    historic={'title':'历史台风总结','text':'实况有效波高6.0米，预计5到7米巨到狂浪。','chunk_id':'case'}
    assert select_evidence(q,[historic,table],limit=1)[0]['chunk_id']=='table'


@pytest.mark.parametrize('question',[
    '截至2026年10月17日，国外用神经网络预报海平面的研究有哪些进展和局限？',
    '风暴潮机器学习预测有哪些论文可参考？',
    '国内外海洋灾害预警技术最近有何研究成果？',
])
def test_marine_research_paraphrases_keep_academic_discovery(question):
    from marine_kb.public_sources import research_question,primary_routes
    from marine_kb.web_supplement import public_search_queries
    assert research_question(question)
    assert any('copernicus.org' in row['url'] for row in primary_routes(question))
    assert any('site:gmd.copernicus.org' in q for q in public_search_queries(question))


def test_rag_method_is_not_misrouted_into_sea_level_model_papers():
    from marine_kb.web_supplement import public_search_queries
    from marine_kb.public_sources import primary_routes
    q='怎样让海洋专业问答逐句附证据，搜错资料时自动改检索？请依据研究论文说明方法。'
    assert not any('2177/2026' in row['url'] for row in primary_routes(q))
    assert not any('sea level' in q for q in public_search_queries(q))


def test_quote_only_repair_preserves_original_text_and_does_not_bypass_units():
    from marine_kb.citation_repair import apply_quote_refs
    from marine_kb.qa import _decode_model_answer
    text='县级在48小时内报送，市级汇总后在24小时内报送。'
    evidence=[{'text':text,'passages':[{'id':1,'text':text}]}]
    value={'answerability':'partial','answer':'县级在48小时内报送[资料1]。','evidence_quotes':[]}
    choice=json.dumps({'evidence_quotes':[{'citation':1,'passage_id':1}],'unsupported_citations':[]})
    repaired=apply_quote_refs(json.dumps(value,ensure_ascii=False),choice,evidence,_decode_model_answer)
    assert json.loads(repaired)['answer']==value['answer']
    value['answer']='县级在49小时内报送[资料1]。'
    with pytest.raises(ValueError):apply_quote_refs(json.dumps(value,ensure_ascii=False),choice,evidence,_decode_model_answer)
    with pytest.raises(ValueError):apply_quote_refs(json.dumps(value),json.dumps({'evidence_quotes':[{'citation':99,'passage_id':1}]}),evidence,_decode_model_answer)


def test_source_card_keeps_actual_selected_passage_not_just_preview():
    from marine_kb.qa import _sources_with_selected_passages
    text='第一段只介绍背景。第二段记载县级48小时内报送。'
    evidence=[{'text':text,'passages':[{'id':1,'text':'第一段只介绍背景。'},{'id':2,'text':'第二段记载县级48小时内报送。'}]}]
    source={'citation_id':1,'excerpt':'第一段只介绍背景。'}
    output=_sources_with_selected_passages([source],evidence,{'evidence_quotes':[{'citation':1,'passage_id':2}]})[0]
    assert output['excerpt']==source['excerpt']
    assert output['supporting_passages'][0]['quote']=='第二段记载县级48小时内报送。'
    assert len(output['supporting_passages'][0]['sha256'])==64


def test_explicit_family_count_paraphrase_uses_program_math():
    from marine_kb.grounded_calculations import add_public_calculations
    hit={'source_id':'official','title':'基础版清单','text':'饮用水：保障每人3天基本饮水需求，至少3升/人。','metadata':{'source_type':'public_web'}}
    rows=add_public_calculations('家里4口人，应急饮用水按全国基础清单总共几升？',[hit])
    assert rows[-1]['metadata']['calculation']['family_total_litres']=='12'
    assert rows[-1]['metadata']['calculation']['per_person_total_litres']=='3'
    rows=add_public_calculations('家里只剩四个人，按家庭应急清单饮用水总量是多少？',[hit])
    assert rows[-1]['metadata']['calculation']['family_total_litres']=='12'
    assert len(add_public_calculations('我们一家四口，隔壁六口，合计应急饮用水总量多少？',[hit]))==1
    assert len(add_public_calculations('我们一家七口应急饮用水多少；那家里只剩四个人，按同一清单饮用水总量是多少？',[hit]))==1


def test_system_inventory_question_routes_to_runtime_snapshot_despite_long_clause():
    from marine_kb.guidance import system_question
    assert system_question('截至2026年10月16日，这个系统已经接入了多少网格文件、每隔多久更新？如果没有当日记录，请区分现在的配置。')
    assert not system_question('海洋动力系统如何导致风暴潮？')


def test_cleanup_has_a_direct_national_disease_control_source():
    from marine_kb.public_sources import primary_routes
    assert any('ndcpa.gov.cn' in r['url'] for r in primary_routes('海水泡过的室内地面怎么清理，需要消毒吗？'))


def test_same_rule_and_changed_parameter_are_followups_but_same_time_is_not():
    from marine_kb.conversation import followup_question
    assert followup_question('那如果家里只剩四个人，按同一清单饮用水的总量是多少？')
    assert followup_question('按同一口径，换成明天呢？')
    assert not followup_question('舟山和宁波在同一时刻的浪高分别多少？')
