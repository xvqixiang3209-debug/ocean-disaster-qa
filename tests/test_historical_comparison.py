import json
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from forecast_runtime.historical_comparison import bulletin_events,comparison_question,query_comparison,comparison_facts,comparison_summary
from forecast_runtime.storage import BEIJING
from forecast_runtime.answer_facts import delivery_table,delivery_facts,fact_text
from marine_kb.business_validation import validate_business_delivery
from typhoon.web_reader import WebPageReader


def db(tmp_path):
    p=tmp_path/'marine_kb/knowledge.sqlite3';p.parent.mkdir()
    with sqlite3.connect(p) as c:
        c.execute('create table sources(source_id text,filename text,status text,sha256 text)')
        c.execute('create table chunks(chunk_id integer,source_id text,locator text,text text,metadata text)')
        c.execute("insert into sources values('s','2019年浙江省海洋灾害公报.doc','indexed','hash')")
        rows=['2019年全省风暴潮经济损失87.25亿元。','1.1909“利奇马”台风风暴潮灾害',
              '“利奇马”最大风暴增水为312厘米，发生在海门站。','表3（基面：85高程）',
              '站名 | 最高潮位（厘米） | 最高潮位出现时间\n海门 | 480 | 8月10日02:15',
              '受风暴潮和近岸浪共同影响，全省直接经济损失约76.22亿元，未造成人员死亡（含失踪）。',
              '2.1918“米娜”台风风暴潮灾害','最大风暴增水为152厘米，发生在澉浦站。',
              '浙江省直接经济损失约11.04亿元，未造成人员死亡（含失踪）。',
              '三、海浪灾害','其他灾害直接经济损失900万元。']
        c.executemany('insert into chunks values(?,?,?,?,?)',[(i,'s','paragraph:'+str(i),text,'{}') for i,text in enumerate(rows)])
    return p


def runtime(tmp_path):
    db(tmp_path)
    class Runtime:
        def __init__(self):
            self.store=SimpleNamespace(root=tmp_path/'forecast_runtime',grids=lambda:[
                {'product':'SWL','batch':'2026-10-17T20:00:00+08:00','valid_from':'2026-10-17T21:00:00+08:00','valid_to':'2026-10-20T20:00:00+08:00'},
                {'product':'SWL','batch':'2026-10-18T08:00:00+08:00','valid_from':'2026-10-18T09:00:00+08:00','valid_to':'2026-10-21T08:00:00+08:00'}])
            self._local=SimpleNamespace(request_cutoff=datetime(2026,10,17,23,59,59,tzinfo=BEIJING))
        def base_result(self,start,end):return {'start':start.isoformat(),'end':end.isoformat(),'as_of':'2026-10-18T18:00:00+08:00'}
        def config(self):return {'swl_kind':'surge'}
        def coastal(self,start,end,region):
            return {'selected_batch':'2026-10-17T20:00:00+08:00','requested_count':2,'usable_count':1,'missing_count':1,'partial_count':0,
                    'results':[{'state':'ready','name':'海岸甲','series':[{'swl_cm':150,'total_cm':500,'time':'2026-10-19T12:00:00+08:00'}]}]}
    return Runtime()


def test_event_extraction_never_uses_annual_or_next_chapter_totals(tmp_path):
    events=bulletin_events(db(tmp_path));a,b=events
    assert a['metrics']['direct_loss_yi']['value']==76.22
    assert b['metrics']['direct_loss_yi']['value']==11.04
    assert a['metrics']['surge_peak_cm']['value']==312
    assert a['metrics']['water_peak_cm']['value']==480
    assert a['metrics']['water_peak_cm']['datum']=='1985'
    assert b['metrics']['deaths_missing']['value']==0
    assert all(m['quote'] for e in events for m in e['metrics'].values())


@pytest.mark.parametrize('question',[
    '跟历史台风2019年“利奇马”相比，台风“蔷琵”截至2026年10月17日预报的浙江风暴潮影响是偏重还是偏轻？',
    '2019年利奇马与2019年米娜的风暴潮增水比较',
    '把2019年利奇马的最高潮位与这次台风作对照。',
])
def test_family_routes_to_comparison(question):assert comparison_question(question)


@pytest.mark.parametrize('question',[
    '按全国死亡人数和损失比较历史上最严重的风暴潮是哪一次？',
    '历史台风的相似度算法怎么比较？','台风预警与风暴潮警报有什么区别？',
])
def test_noncomparison_control(question):assert not comparison_question(question)


def test_forecast_cutoff_horizon_and_scope(tmp_path):
    rt=runtime(tmp_path);start=datetime(2026,10,17,tzinfo=BEIJING)
    r=query_comparison(rt,'截至2026年10月17日，这次台风与2019年利奇马相比风暴潮偏轻吗？',start,start)
    assert r['start']=='2026-10-18T00:00:00+08:00'
    assert r['end']=='2026-10-20T20:00:00+08:00'
    assert not r['cyclone_attribution_verified'] and not r['overall_disaster_severity_computed']
    assert r['forecast']['missing_count']==1
    assert next(c for c in r['comparisons'] if c['metric']=='surge_peak_cm')['difference']==-162
    assert len(r['historical'])==1


def test_historical_pairs_keep_separate_metric_directions(tmp_path):
    rt=runtime(tmp_path);d=datetime(2026,10,17,tzinfo=BEIJING)
    r=query_comparison(rt,'比较2019年利奇马与2019年米娜的风暴潮影响',d,d)
    assert r['comparison_mode']=='historical_events' and 'forecast' not in r
    assert next(v for v in r['comparisons'] if v['metric']=='surge_peak_cm')['difference']==160
    assert next(v for v in r['comparisons'] if v['metric']=='direct_loss_yi')['difference']==65.18


def test_fallback_retains_history_forecast_delta_and_limitations(tmp_path):
    rt=runtime(tmp_path);d=datetime(2026,10,17,tzinfo=BEIJING)
    r=query_comparison(rt,'截至2026年10月17日，与2019年利奇马相比台风风暴潮偏轻吗？',d,d)
    facts=delivery_facts(r);text=delivery_table(r,facts)
    validate_business_delivery(text,{'required_business_facts':facts},{1})
    assert '312.0 cm' in text and '150 cm' in text and '低162.0 cm' in text
    assert '台风场景归属尚未核定' in text
    bad=text.replace('低162.0 cm','高162.0 cm')
    with pytest.raises(ValueError,match='comparison_delta'):validate_business_delivery(bad,{'required_business_facts':facts},{1})
    with pytest.raises(ValueError,match='overall_disaster'):
        validate_business_delivery(text+'\n所以此次台风总体灾损偏轻。',{'required_business_facts':facts},{1})


def test_typhoon_fallback_uses_requested_cutoff_not_processing_instant():
    r={'kind':'typhoons','state':'ready','as_of':'2026-10-18T18:00:00+08:00','start':'2026-10-18T18:00:00+08:00',
       'end':'2026-10-18T18:00:00+08:00','requested_as_of':'2026-10-17T23:59:59+08:00','list':[]}
    answer=delivery_table(r,[])
    assert '资料截至时刻：2026-10-17T23:59:59' in answer
    assert '查询时段：2026-10-18' not in answer


def test_jcms_attachment_is_only_a_trusted_fixed_filename_endpoint():
    reader=WebPageReader(allow_official_documents=True)
    url='https://zrzyt.zj.gov.cn/module/download/downfile.jsp?classid=0&filename=d250422690d94459b0861f334749a579.pdf'
    assert reader._document_url_allowed(url)
    assert not reader._document_url_allowed(url.replace('zrzyt.zj.gov.cn','evil.example'))
    assert not reader._document_url_allowed(url.replace('filename=d250422690d94459b0861f334749a579.pdf','filename=../../private.pdf'))
    assert not reader._document_url_allowed(url+'&filename=other.pdf')
    assert not WebPageReader(allow_official_documents=False)._document_url_allowed(url)


def replace_rows(path,rows,filename='2019年浙江省海洋灾害公报.doc'):
    with sqlite3.connect(path) as c:
        c.execute('delete from chunks');c.execute('update sources set filename=?',(filename,))
        c.executemany('insert into chunks values(?,?,?,?,?)',[(i,'s','paragraph:'+str(i),text,'{}') for i,text in enumerate(rows)])


def test_another_numbered_event_terminates_current_event(tmp_path):
    path=db(tmp_path)
    replace_rows(path,['1、1909“利奇马”台风风暴潮灾害','最大风暴增水为312厘米，发生在海门站。',
        '全省直接经济损失76.22亿元，其中渔船损失272万元。','2、191016温带风暴潮灾害',
        '最大风暴增水87厘米，发生在镇海站。','直接经济损失1299万元。'])
    event=bulletin_events(path)[0]
    assert event['metrics']['surge_peak_cm']['value']==312
    assert event['metrics']['direct_loss_yi']['value']==76.22
    assert not any('1299' in row['text'] for row in event['evidence'])


def test_numeric_table_uses_header_not_column_position(tmp_path):
    path=db(tmp_path)
    replace_rows(path,['1、1909“利奇马”台风风暴潮灾害','表3（基面：85高程）',
       '站名 | 最大增水 / （厘米） | 最大增水出现时间 | 最高潮位 / （厘米） | 最高潮位出现时间 | 预警级别\n海门 | 312 | 8月10日01:00 | 480 | 8月10日02:15 | 橙色\n镇海 | 88 | 8月10日01:00 | 230 | 8月10日02:15 | 蓝色'])
    event=bulletin_events(path)[0]
    assert event['metrics']['water_peak_cm']['value']==480
    assert event['surge_levels'][0]['value']==312
    assert event['water_levels'][0]['observed_time_text']=='8月10日02:15'


def test_pdf_headings_and_loss_thousands_spacing(tmp_path):
    path=db(tmp_path)
    text='年度总损失99亿元。\n1．1909“利奇马”台风风暴潮灾害\n最大增水出现在海门站（312厘米）。\n全省直接经济损失76 220万元。\n2．1918“米娜”台风风暴潮灾害\n最大增水出现在澉浦站（152厘米）。\n全省直接经济损失11 040万元。\n三、海浪灾害\n直接经济损失900万元。'
    replace_rows(path,[text]);events=bulletin_events(path)
    assert len(events)==2
    assert [event['metrics']['direct_loss_yi']['value'] for event in events]==[7.622,1.104]
    assert all(row['quote'] in text for event in events for row in event['metrics'].values())


def test_city_never_inherits_provincial_event_peak(tmp_path):
    rt=runtime(tmp_path);rt.coasts=lambda:[{'Prefecture':'宁波市','County':'象山县'}]
    requested=[];original=rt.coastal
    rt.coastal=lambda start,end,region:(requested.append(region) or original(start,end,region))
    d=datetime(2026,10,17,tzinfo=BEIJING)
    result=query_comparison(rt,'截至2026年10月17日宁波市这次预报与2019年利奇马风暴潮相比如何？',d,d)
    assert requested==['宁波市']
    assert result['historical'][0]['metrics']=={}
    assert not result['comparisons'] and result['state']=='partial'


def test_unmatched_past_event_does_not_become_current_forecast(tmp_path):
    rt=runtime(tmp_path);d=datetime(2026,10,17,tzinfo=BEIJING)
    result=query_comparison(rt,'比较2019年利奇马与2006年桑美的浙江风暴潮增水',d,d)
    assert result['state']=='partial' and 'forecast' not in result
    assert '未以当前预报替代' in result['reason']


def test_requested_year_after_name_is_respected(tmp_path):
    rt=runtime(tmp_path);d=datetime(2026,10,17,tzinfo=BEIJING)
    result=query_comparison(rt,'比较利奇马（2015年）与2019年米娜的风暴潮增水',d,d)
    assert [event['name'] for event in result['historical']]==['米娜']
    assert 'forecast' not in result


def test_province_adapter_does_not_relabel_national_data(tmp_path):
    path=db(tmp_path)
    with sqlite3.connect(path) as c:c.execute("update sources set filename='2019年中国海洋灾害公报.doc'")
    assert bulletin_events(path)==[]


def test_forecast_result_from_future_batch_is_rejected(tmp_path):
    rt=runtime(tmp_path);d=datetime(2026,10,17,tzinfo=BEIJING)
    original=rt.coastal
    rt.coastal=lambda *args:original(*args)|{'selected_batch':'2026-10-18T08:00:00+08:00'}
    result=query_comparison(rt,'截至2026年10月17日，这次台风与2019年利奇马相比风暴潮偏轻吗？',d,d)
    assert result['state']=='partial' and 'forecast' not in result


def test_historical_loss_and_deaths_are_required_in_delivery(tmp_path):
    rt=runtime(tmp_path);d=datetime(2026,10,17,tzinfo=BEIJING)
    result=query_comparison(rt,'比较2019年利奇马与2019年米娜的风暴潮增水、经济损失和死亡失踪人数',d,d)
    facts=delivery_facts(result);answer=delivery_table(result,facts)
    validate_business_delivery(answer,{'required_business_facts':facts},{1})
    assert '76.22 亿元' in answer and '65.18 亿元' in answer and '死亡含失踪人数' in answer
    damaged='\n'.join(line for line in answer.splitlines() if '死亡含失踪人数' not in line)
    with pytest.raises(ValueError,match='historical_metric'):
        validate_business_delivery(damaged,{'required_business_facts':facts},{1})


def test_high_word_in_tide_metric_cannot_hide_wrong_delta_direction():
    fact={'kind':'comparison','metric':'最高潮位','left':'2019年利奇马','right':'2019年米娜',
          'left_value':480,'right_value':450,'difference':30,'unit':'cm','citation':1}
    with pytest.raises(ValueError,match='comparison_delta'):
        validate_business_delivery('最高潮位：2019年利奇马480 cm，2019年米娜450 cm；前者比后者低30 cm[资料1]。',{'required_business_facts':[fact]},{1})
    validate_business_delivery('最高潮位：2019年利奇马480 cm，2019年米娜450 cm；前者比后者高30 cm[资料1]。',{'required_business_facts':[fact]},{1})


def test_death_only_does_not_assert_missing_people_count(tmp_path):
    path=db(tmp_path);replace_rows(path,['1.1909“利奇马”台风风暴潮灾害','直接经济损失1.25亿元，无人员死亡。'])
    event=bulletin_events(path)[0]
    assert event['metrics']['deaths']['value']==0 and 'deaths_missing' not in event['metrics']


def test_uniform_datum_and_repeated_pdf_chapter_header(tmp_path):
    path=db(tmp_path)
    replace_rows(path,['1.1909“利奇马”台风风暴潮灾害\n站名 最高潮位（厘米） 最高潮位出现时间 达到的预警级别\n海门站 480 8月10日 02:15 橙色',
        '二、风暴潮灾害\n注：本公报中所有潮位数据采用的基面均为1985 国家高程基准一期。\n受“利奇马”风暴潮和近岸浪共同影响，直接经济损失76.22亿元。\n三、海浪灾害\n直接经济损失900万元。'])
    event=bulletin_events(path)[0]
    assert event['datum']=='1985' and event['metrics']['water_peak_cm']['value']==480
    assert event['metrics']['direct_loss_yi']['value']==76.22


def test_same_name_without_year_requests_clarification(tmp_path):
    rt=runtime(tmp_path);path=tmp_path/'marine_kb/knowledge.sqlite3'
    with sqlite3.connect(path) as c:
        c.execute("insert into sources values('s2','2013年浙江省海洋灾害公报.doc','indexed','hash2')")
        c.executemany('insert into chunks values(?,?,?,?,?)',[(100,'s2','paragraph:1','1.1328“利奇马”台风风暴潮灾害','{}'),(101,'s2','paragraph:2','最大风暴增水为75厘米，发生在海门站。','{}')])
    d=datetime(2026,10,17,tzinfo=BEIJING)
    r=query_comparison(rt,'这次台风的风暴潮和历史利奇马相比如何？',d,d)
    assert r['state']=='needs_clarification' and not r['historical'] and 'forecast' not in r


def test_asof_phrase_does_not_turn_missing_old_event_into_forecast(tmp_path):
    rt=runtime(tmp_path);d=datetime(2026,10,17,tzinfo=BEIJING)
    r=query_comparison(rt,'截至2026年10月17日资料，比较2019年利奇马与2006年桑美的风暴潮。',d,d)
    assert r['state']=='partial' and 'forecast' not in r


def test_unshared_stations_do_not_claim_complete_tide_comparison(tmp_path):
    rt=runtime(tmp_path);path=tmp_path/'marine_kb/knowledge.sqlite3'
    replace_rows(path,['1.1909“利奇马”台风风暴潮灾害','表3（基面：85高程）',
        '站名 | 最高潮位（厘米） | 最高潮位出现时间\n海门 | 480 | 8月10日02:15',
        '2.1918“米娜”台风风暴潮灾害','表4（基面：85高程）',
        '站名 | 最高潮位（厘米） | 最高潮位出现时间\n澉浦 | 583 | 10月1日02:15'])
    d=datetime(2026,10,17,tzinfo=BEIJING)
    r=query_comparison(rt,'比较2019年利奇马与2019年米娜的最高潮位。',d,d)
    assert r['state']=='partial' and not r['comparisons']
    assert any('同站' in message for message in r['missing_information'])


@pytest.mark.parametrize('question',[
    '比较2013年菲特与2019年利奇马的最大增水和损失。',
    '比较2022年梅花与2023年卡努在宁波的最高潮位和增水。',
    '比较2019年利奇马和2019年米娜海门站的最高潮位。',
])
def test_metric_comparisons_route_even_when_typhoon_word_omitted(question):
    assert comparison_question(question)


def test_legacy_report_subset_is_never_used_as_region_peak(tmp_path,monkeypatch):
    rt=runtime(tmp_path);d=datetime(2026,10,17,tzinfo=BEIJING)
    record={'name':'桑美','season':2006,'region':'浙江','source_title':'2006台风报告','source_sha256':'x',
            'coverage':'parsed_report_station_subset','metrics':{'surge_peak_cm':{'value':119,'unit':'cm','station':'乍浦',
            'coverage':'parsed_report_station_subset'}},'evidence':[],'water_levels':[]}
    monkeypatch.setattr('forecast_runtime.historical_comparison._report_events',lambda runtime,question:[record])
    r=query_comparison(rt,'比较2006年桑美台风与2019年利奇马的浙江最大增水。',d,d)
    assert r['state']=='partial' and not r['comparisons']
    assert any('已解析测站子集' in item for item in r['missing_information'])


def test_legacy_subset_alternative_not_given_to_model_as_event_maximum():
    r={'historical':[{'name':'菲特','metrics':{'surge_peak_cm':{'value':383}},'alternative_reports':[
        {'source_title':'旧报告','source_sha256':'hash','metrics':{'surge_peak_cm':{'value':119}},
         'evidence':[{'record':{'max_surge_m':1.19}}]}]}]}
    text=comparison_summary(r)
    assert '383' in text and '119' not in text and '1.19' not in text
    assert 'excluded_from_comparison' in text


def test_same_delta_at_another_station_cannot_satisfy_required_station():
    fact={'kind':'comparison','metric':'北仑最高潮位','station':'北仑','left':'2022年梅花','right':'2023年卡努',
          'left_value':228,'right_value':261,'difference':-33,'unit':'cm','citation':1}
    with pytest.raises(ValueError,match='comparison_delta'):
        validate_business_delivery('乌沙山最高潮位：2022年梅花373 cm，2023年卡努406 cm；前者比后者低33 cm[资料1]。',{'required_business_facts':[fact]},{1})


def loss_scope_runtime(tmp_path):
    rt=runtime(tmp_path)
    replace_rows(tmp_path/'marine_kb/knowledge.sqlite3',[
        '1.1810“安比”台风风暴潮灾害',
        '受“安比”台风风暴潮和近岸浪的共同影响，舟山市水产养殖受灾面积460公顷，直接经济损失8 905万元，未造成人员死亡（含失踪）。',
        '2.1814“摩羯”台风风暴潮灾害',
        '受“摩羯”台风风暴潮和近岸浪的共同影响，台州市水产养殖受灾面积81.33公顷，直接经济损失1 657.45万元，未造成人员死亡（含失踪）。',
        '3.1818“温比亚”台风风暴潮灾害',
        '受“温比亚”台风风暴潮和近岸浪的共同影响，舟山市水产养殖受灾面积333.33公顷，直接经济损失2 312万元，未造成人员死亡（含失踪）。',
        '4.1808“玛莉亚”台风风暴潮灾害',
        '受“玛莉亚”台风风暴潮和近岸浪的共同影响，全省水产养殖受灾面积3 178.20公顷，直接经济损失43 544.10万元，未造成人员死亡（含失踪）。',
    ],filename='2018年浙江省海洋灾害公报.doc')
    return rt


def test_city_aggregate_never_promoted_to_province_total(tmp_path):
    rt=loss_scope_runtime(tmp_path);d=datetime(2026,10,17,tzinfo=BEIJING)
    r=query_comparison(rt,'比较2018年安比与2018年玛莉亚的浙江风暴潮直接经济损失。',d,d)
    assert r['state']=='partial' and not r['comparisons']
    local=r['historical'][0]['metrics']['direct_loss_yi']
    assert local['value']==0.8905 and local['jurisdiction']['name']=='舟山市'
    assert any('舟山市' in item and '浙江完整' in item for item in r['missing_information'])
    facts=comparison_facts(r)
    assert next(f for f in facts if f.get('event_name')=='安比' and f.get('unit')=='亿元')['region']=='舟山市'
    assert '舟山市' in delivery_table(r,facts)


def test_two_local_aggregates_at_different_cities_do_not_compare(tmp_path):
    rt=loss_scope_runtime(tmp_path);d=datetime(2026,10,17,tzinfo=BEIJING)
    r=query_comparison(rt,'比较2018年安比与2018年摩羯的浙江风暴潮损失。',d,d)
    assert r['state']=='partial' and not r['comparisons']
    assert {e['metrics']['direct_loss_yi']['region'] for e in r['historical']}=={'舟山市','台州市'}


def test_requested_city_keeps_only_proven_same_city_aggregates(tmp_path):
    rt=loss_scope_runtime(tmp_path);d=datetime(2026,10,17,tzinfo=BEIJING)
    r=query_comparison(rt,'比较2018年安比与2018年温比亚在舟山的风暴潮直接经济损失。',d,d)
    assert r['requested_scope']['kind']=='city' and r['state']=='ready'
    comparison=next(c for c in r['comparisons'] if c['metric']=='direct_loss_yi')
    assert comparison['left_value']==0.8905 and comparison['right_value']==0.2312
    assert comparison['difference']==0.6593
    wrong=query_comparison(rt,'比较2018年安比与2018年摩羯在舟山的风暴潮直接经济损失。',d,d)
    assert wrong['historical'][1]['metrics']=={} and not wrong['comparisons']


def test_joint_cities_total_cannot_be_split_or_become_province(tmp_path):
    rt=runtime(tmp_path);p=tmp_path/'marine_kb/knowledge.sqlite3'
    replace_rows(p,['1.1909“利奇马”台风风暴潮灾害',
        '台风风暴潮造成浙江省温州、台州市直接经济损失5812万元，无人员伤亡。',
        '2.1918“米娜”台风风暴潮灾害','全省直接经济损失11.04亿元，无人员伤亡。'])
    event=bulletin_events(p)[0]
    metric=event['metrics']['direct_loss_yi']
    assert metric['jurisdiction']=={'kind':'city_group','name':'温州市、台州市','members':['温州市','台州市']}
    d=datetime(2026,10,17,tzinfo=BEIJING)
    province=query_comparison(rt,'比较2019年利奇马与2019年米娜的浙江风暴潮损失。',d,d)
    assert not province['comparisons']
    city=query_comparison(rt,'比较2019年利奇马与2019年米娜的温州风暴潮损失。',d,d)
    assert not city['historical'][0]['metrics']


def test_unknown_jurisdiction_and_earlier_city_caption_do_not_leak(tmp_path):
    p=db(tmp_path)
    replace_rows(p,['1.1909“利奇马”台风风暴潮灾害',
        '图1 舟山普陀区受灾。受台风风暴潮影响，全省水产养殖受灾，直接经济损失76.22亿元，无人员伤亡。',
        '2.1918“米娜”台风风暴潮灾害','直接经济损失11.04亿元，无人员伤亡。'])
    a,b=bulletin_events(p)
    assert a['metrics']['direct_loss_yi']['jurisdiction']['kind']=='province'
    assert b['metrics']['direct_loss_yi']['jurisdiction']['kind']=='unknown'
    assert b['metrics']['deaths']['jurisdiction']['kind']=='unknown'


def test_historical_local_value_must_deliver_its_true_jurisdiction(tmp_path):
    rt=loss_scope_runtime(tmp_path);d=datetime(2026,10,17,tzinfo=BEIJING)
    r=query_comparison(rt,'比较2018年安比和2018年玛莉亚的浙江风暴潮直接经济损失。',d,d)
    facts=comparison_facts(r);text=delivery_table(r,facts)
    validate_business_delivery(text,{'required_business_facts':facts},{1})
    wrong=text.replace('舟山市','浙江')
    with pytest.raises(ValueError):validate_business_delivery(wrong,{'required_business_facts':facts},{1})
    # A correct data table appended afterwards must not erase the earlier
    # unsupported claim that a city subtotal is the entire province total.
    wrong='2018年安比造成浙江直接经济损失0.8905亿元[资料1]。\n'+text
    with pytest.raises(ValueError):validate_business_delivery(wrong,{'required_business_facts':facts},{1})


def test_unknown_statistical_scope_must_be_stated_in_answer(tmp_path):
    path=db(tmp_path)
    replace_rows(path,['1.1909“利奇马”台风风暴潮灾害','直接经济损失76.22亿元。'])
    event=bulletin_events(path)[0]
    facts=comparison_facts({'historical':[event]})
    answer='2019年利奇马，原文未明确地域范围，直接经济损失76.22亿元[资料1]。'
    validate_business_delivery(answer,{'required_business_facts':facts},{1})
    with pytest.raises(ValueError):validate_business_delivery(answer.replace('原文未明确地域范围，',''),{'required_business_facts':facts},{1})
