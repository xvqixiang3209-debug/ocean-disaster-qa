"""A selectable purchase stamp must not hide a scanned PDF body from OCR."""
from marine_kb.parsers import _quality_content, _text_quality


STAMP = '订单号：0120260101010091  防伪编号：2026-0101-1050-4217-9094  购买单位: 示例机构\n示例机构 专用\n'


def test_long_order_stamp_alone_is_not_usable_body():
    assert _text_quality(STAMP) == 'sparse'
    assert not _quality_content(STAMP).strip()


def test_stamp_does_not_hide_legitimate_prose_on_same_page():
    body = '海洋站的自动观测系统应符合本标准规定。观测设备安装位置、技术性能和资料处理方法均应记录，并保存原始观测资料。'
    assert _text_quality(STAMP + body) == 'usable'
    assert body in _quality_content(STAMP + body)


def test_ordinary_usage_of_order_number_is_not_an_order_stamp():
    body = '申请单位提交的资料应注明订单编号，并说明海洋观测仪器的测量范围、分辨率、安装方式、传输方式和使用环境。'
    assert _text_quality(body) == 'usable'
    assert body in _quality_content(body)


def test_unrelated_special_use_line_is_not_stripped_by_guessing_company_names():
    body = '海洋观测业务专用\n本章规定观测站的设备布设要求、运行管理要求和维护程序，应保留全部有效观测记录。'
    assert _text_quality(body) == 'usable'
    assert '海洋观测业务专用' in _quality_content(body)
