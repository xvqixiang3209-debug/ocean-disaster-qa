"""Regression contracts for public knowledge retrieval, not canned answers."""
from marine_kb.public_service_intents import public_queries, supports_subject
from marine_kb.web_supplement import public_search_queries, focused_public_text
from marine_kb.public_sources import primary_routes
from marine_kb.evidence_applicability import assess_page, facet_support


def test_mosquito_search_keeps_vector_and_risk_instead_of_water_disinfection():
    queries = public_search_queries('暴雨后院子蚊虫很多，可能传播什么病，如何防蚊？')
    assert any('蚊媒传染病' in q and 'nhc.gov.cn' in q for q in queries)
    assert any('孳生地' in q for q in queries)
    assert not any('饮水安全' in q for q in queries)


def test_private_terms_are_not_exported_by_new_public_lenses():
    queries = public_search_queries('内部项目Secret889联系人王某，灾后蚊子多怎么防？')
    assert queries and all('Secret889' not in q and '王某' not in q for q in queries)


def test_aquaculture_search_keeps_cage_and_pond_objects():
    queries = public_search_queries('浙江渔民如何在台风前加固鱼排、网箱和养殖池塘？')
    assert any('网箱' in q and '锚泊' in q for q in queries)
    assert any('池塘' in q and '进排水' in q for q in queries)
    assert any(q.startswith('台风 海水鱼') for q in queries)  # national technical source


def test_government_relief_is_not_an_insurance_only_route():
    question = '宁波家庭因台风受灾，可以申请什么政府救助？'
    assert all('保险' not in q for q in public_queries(question))
    routes = primary_routes(question)
    assert routes and all('保险' not in r['title'] for r in routes)
    assert not supports_subject(question, '宁波公共巨灾保险，住房进水按保单赔付，保险救助。')
    assert supports_subject(question, '国家自然灾害救助包括应急生活救助、过渡期生活救助和倒损住房恢复重建救助。')


def test_explicit_insurance_question_retains_its_own_evidence():
    assert supports_subject('政府救助和保险分别怎么申请？', '公共巨灾保险的申请与赔付程序。')
    queries=public_queries('过渡期生活救助、冬春救助和巨灾保险赔付一样吗？')
    assert any('应急生活' in q for q in queries)
    assert any('保险合同' in q for q in queries)


def test_specialist_body_required_before_facet_can_use_it():
    assert not facet_support('灾后怎么防蚊？', '灾后怎么防蚊？', {'text':'灾后做好饮水消毒，水淹住宅应清污。'})
    assert not supports_subject('网箱怎么加固？', '海塘和水库堤坝及时加固，人员撤离。')
    assert supports_subject('网箱怎么加固？', '检查网箱框架、锚泊缆绳及网衣，修补受损部位。')


def test_channel_queries_cover_meteorology_and_marine_separately():
    queries = public_search_queries('在浙江哪里查台风和海洋预警？')
    assert any('气象' in q and 'weather.com.cn' in q for q in queries)
    assert any('海洋预报网' in q for q in queries)


def test_channel_method_must_not_query_a_current_warning_archive():
    from forecast_runtime.official_warnings import warning_question
    q='浙江海洋预报网上的台风路径能替代气象台发布的台风预警吗？我应怎样查询两类信息？'
    assert not warning_question(q)
    assert any('weather.com.cn' in r['url'] for r in primary_routes(q))
    assert warning_question('截至2026年10月17日浙江已经发布了哪些海浪警报？')


def test_review_target_ids_are_exact_and_cannot_supply_new_claims():
    import json
    import pytest
    from marine_kb.coverage_review import review_targets,decode_review
    answer='按技术手册检查网箱缆绳。'
    missing=['未取得温州地方专用加固标准']
    targets=review_targets(answer,missing)
    raw={'verdict':'revise','issues':[{'kind':'unasked_gap','target_id':'m1','reason':'只问通用检查步骤'}]}
    result=decode_review(json.dumps(raw),answer,missing,targets)
    assert result['issues'][0]['text']==missing[0]
    raw['issues'][0]['text']='伪造的新内容'
    with pytest.raises(ValueError):decode_review(json.dumps(raw),answer,missing,targets)


def test_national_versioned_method_keeps_a_version_scoped_explanation():
    from marine_kb.qa import _version_only_reference_coverage
    question='申请倒损住房恢复重建救助的程序是什么？'
    app={'use':'reference','temporal':'version_unverified','object_match':'matched',
         'named_object_match':'matched','geography':'national_framework',
         'reasons':['尚未核实文件生效及修订废止关系']}
    contract={'mode':'method_or_knowledge'}
    part={'question':question,'citations':[1]}
    assert _version_only_reference_coverage(contract,part,{'reference_citations':[1]},[{'metadata':{'applicability':app}}])
    app['geography']='other_region'
    assert not _version_only_reference_coverage(contract,part,{'reference_citations':[1]},[{'metadata':{'applicability':app}}])


def test_general_health_method_can_use_other_province_science_not_its_local_status():
    page={'title':'上海健康科普：灾后防蚊知识','content':'清除花盆托盘积水，穿长袖长裤，使用纱窗蚊帐防止叮咬。'}
    app=assess_page('舟山家里蚊虫多，怎么清理积水和防叮咬？',page)
    assert app['kind']=='knowledge' and app['use']=='direct'
    local={'title':'上海市浴场恢复开放公告','content':'上海某浴场即日起恢复开放。'}
    assert assess_page('今天舟山浴场是否开放？',local,dynamic=True)['use']!='direct'


def test_circled_procedure_item_is_not_a_household_count():
    from decimal import Decimal
    from marine_kb.quantities import quantities,validate_quantity_units
    assert not quantities('①户主向村委会申请；②村委会评议；③县级审批。')
    assert [(v,u) for v,u,_,_ in quantities('受灾家庭12户。')]==[(Decimal(12),'户')]
    validate_quantity_units('具体为：①户主向村委会申请[资料1]。',[{'text':'户主向村委会提出书面申请。'}])


def test_service_directory_is_not_a_live_warning_list():
    page = {'title':'浙江省气象灾害预警发布平台', '_primary_scope':'service_directory',
            'content':'浙江省气象灾害预警发布平台提供台风、大风、暴雨等预警信号查询。'}
    method = assess_page('浙江在哪里查台风预警？', page)
    status = assess_page('当前浙江哪些港口已停航？', page, dynamic=True)
    assert method['kind'] == 'knowledge' and method['use'] != 'excluded'
    assert status['use'] == 'reference'


def test_national_ministry_rule_is_not_an_unrelated_local_example():
    page={'title':'应急管理部 财政部关于倒损住房恢复重建救助工作规范的通知',
          'content':'各省、自治区、直辖市应急管理厅。县级应急管理部门按照户报、村评、乡审、县定程序办理住房恢复重建救助。'}
    app=assess_page('宁波家庭受灾如何申请住房恢复重建救助？',page)
    assert app['geography']=='national_framework'
    assert app['temporal']=='version_unverified' and app['use']=='reference'


def test_excerpt_keeps_specialist_instructions_from_long_article():
    text = ('天气概况与其他事项。\n' * 100 +
            '清除花盆托盘积水，防止蚊虫孳生；外出穿长袖衣裤，使用蚊帐和驱蚊剂。\n' +
            '蚊虫可传播登革热等疾病，但不能以蚊虫增多认定当地正在流行。')
    excerpt = focused_public_text(text, '灾后蚊子多会传播疾病吗？怎么防？', limit=500)
    assert '孳生' in excerpt and '长袖' in excerpt and '登革热' in excerpt
