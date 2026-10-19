import io
from unittest.mock import patch
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from typhoon.web_reader import WebPageReader, _ReadFailure
from marine_kb.web_supplement import public_search_queries, focused_public_text


def test_blank_scanned_pages_are_not_document_evidence():
    writer = PdfWriter()
    for _ in range(30):
        writer.add_blank_page(width=100, height=100)
    output = io.BytesIO()
    writer.write(output)
    with pytest.raises(_ReadFailure, match='OCR'):
        WebPageReader()._document_text(output.getvalue(), True)


def test_page_labels_preserve_original_nonempty_page_positions():
    body = '灾害统计包含经核定的直接经济损失和人员伤亡等指标，原始数据保留统计范围及单位。'
    document = SimpleNamespace(is_encrypted=False, pages=[
        SimpleNamespace(extract_text=lambda: ''),
        SimpleNamespace(extract_text=lambda: body)])
    with patch('pypdf.PdfReader', return_value=document):
        assert WebPageReader()._document_text(b'', True) == '第2页\n' + body


def test_explicit_national_history_query_does_not_become_zhejiang():
    queries = public_search_queries('我国历史上哪一次风暴潮造成的淹没最严重？')
    assert any('全国' in q for q in queries)
    assert not any('浙江' in q for q in queries)


def test_local_history_query_keeps_place():
    assert any('温州' in q for q in public_search_queries('温州历史上哪一年风暴潮淹没最严重？'))


def test_long_pdf_page_delivers_definitions_and_reporting_procedure():
    body = ('第36页\n' + '其他统计说明及直接经济损失统计指标。' * 90 + '\n'
            '5．因灾死亡人口：因灾受重伤7日内救治无效死亡的人员数量。\n'
            '6．因灾失踪人口：下落不明且暂时无法确认死亡的人员数量。\n\n'
            '第8页\n' + '关于受灾人员报送范围的其他说明。' * 90 + '\n'
            '（3）核报。\n灾情稳定后县级7日内核报，市级接报后2日内报送。')
    assert len(body) > 2400
    text = focused_public_text(body, '死亡和失踪人员怎么认定与核报？',
                               ['死亡和失踪人员怎么认定？', '死亡和失踪人员怎么核报？'])
    assert '5．因灾死亡人口：' in text and '6．因灾失踪人口：' in text
    assert '县级7日内核报' in text


def test_short_original_excerpt_keeps_all_threshold_rows_within_budget():
    body = '原文核验段落。\n\n' + '\n\n'.join(f'第{i}项：赔付金额{i*500}元及对应条件。' for i in range(1,8))
    assert focused_public_text(body,'保险怎么赔？') == body


def test_standard_grade_inputs_are_not_a_local_forecast_request():
    from forecast_runtime.routing import numeric_request
    assert not numeric_request('按中国热带气旋等级标准，2分钟平均风速32.7米每秒应分在哪个等级？')
    assert numeric_request('2026年10月17日舟山预报风速多少？请同时解释热带气旋等级。')
