"""Recovered threshold tables must keep row identities, units and boundaries."""
import json
import pytest

from marine_kb.claim_grounding import apply_attribution, draft_claims
from marine_kb.qa import _decode_model_answer
from marine_kb.quantities import validate_recovered_table_row


CYCLONE_SOURCE='''表 1 西北太平洋和南海热带气旋等级划分
热带气旋等级
底层中心附近最大平均风速(2 min平均风)
m/s kt km/h
底层中心附近
最大风力(级)
热带低压 10.8~17.1 22~33 39~62 6~7
热带风暴 17.2~24.4 34~47 63~88 8~9
强热带风暴 24.5~32.6 48~63 89~117 10~11
台风 32.7~41.4 64~80 118~149 12~13
强台风 41.5~50.9 81~99 150~183 14~15
超强台风 ≥51.0 ≥100 ≥184 16及以上'''


def recovered(table,sources):
    raw=json.dumps({'answerability':'partial','answer':table},ensure_ascii=False)
    evidence=[{'text':s,'passages':[{'id':1,'text':s}]} for s in sources]
    _,claims=draft_claims(raw)
    review={'claims':[{'id':c['id'],'status':'supported','refs':[{'citation':i+1,'passage_id':1} for i in range(len(sources))]} for c in claims]}
    def decode(value,evidence):return _decode_model_answer(json.dumps(value,ensure_ascii=False),evidence)
    return apply_attribution(raw,json.dumps(review,ensure_ascii=False),evidence,decode)


def test_five_named_cyclone_grades_restore_values_and_force_of_actual_pdf_columns():
    table='| 等级 | 最大平均风速（m/s，2 min平均风） | 最大风力（级） |\n|---|---|---|\n'
    table+='\n'.join('| '+label+' | '+speed+' | '+grade+' |' for label,speed,grade in [
        ('热带风暴','17.2～24.4','8～9'),('强热带风暴','24.5～32.6','10～11'),
        ('台风','32.7～41.4','12～13'),('强台风','41.5～50.9','14～15'),('超强台风','≥51.0','16及以上')])
    result,audit=recovered(table,[CYCLONE_SOURCE])
    assert audit['retained_claims']==5
    assert result[0].count('| 等级 |')==1
    for value in ('17.2～24.4','24.5～32.6','32.7～41.4','41.5～50.9','≥51.0','16及以上'):assert value in result[0]


@pytest.mark.parametrize('label,value',[
    ('台风','41.5～50.9'),             # another row's values exist in the same quote
    ('台风','64～80'),                 # another column, knots, not m/s
    ('超强台风','>51.0'),              # inclusive lower boundary changed
    ('超强台风','≤51.0'),              # direction inverted
    ('台风','32.7～50.9'),             # endpoints borrowed from two rows
    ('台风','32.7～44.1'),             # alternative basin threshold
    ('热带风暴','17.2～24.5'),          # rounded adjacent boundary
    ('台风','-32.7～-41.4'),           # sign lost
])
def test_known_numbers_cannot_license_wrong_row_column_or_bounds(label,value):
    table='| 等级 | 最大平均风速（m/s） |\n|---|---|\n| '+label+' | '+value+' |'
    assert recovered(table,[CYCLONE_SOURCE]) is None


def test_same_row_under_a_different_mean_period_is_not_support():
    source=CYCLONE_SOURCE+'\n表 2 另一海域\n最大平均风速(10 min平均风)\nm/s\n台风 32.7~44.1'
    table='| 等级 | 最大平均风速（m/s，2 min平均风） |\n|---|---|\n| 台风 | 32.7～44.1 |'
    assert recovered(table,[source]) is None
    assert recovered(table.replace('2 min','10 min'),[source]) is not None


def test_wrong_row_is_dropped_while_other_numeric_rows_survive():
    table='| 等级 | 风速（m/s） |\n|---|---|\n| 台风 | 41.5～50.9 |\n| 超强台风 | ≥51.0 |'
    result,audit=recovered(table,[CYCLONE_SOURCE])
    assert audit['retained_claims']==1 and '≥51.0' in result[0]
    assert '| 台风 |' not in result[0]
    assert audit['decisions'][0]['status']=='rejected_by_deterministic_check'


@pytest.mark.parametrize('value,accepted',[
    ('4≤Hs<6',True),('4≤Hs≤6',False),('4<Hs<6',False),('6≤Hs<9',False),('4～6',False)
])
def test_wave_interval_operators_are_part_of_the_fact(value,accepted):
    evidence=[{'text':'| 等级 | 有效波高（米） |\n|---|---|\n| 巨浪 | 4≤Hs<6 |\n| 狂浪 | 6≤Hs<9 |'}]
    if accepted:validate_recovered_table_row('| 等级 | 有效波高（米） |','| 巨浪 | '+value+' |',evidence)
    else:
        with pytest.raises(ValueError,match='table_row_or_boundary_not_supported'):
            validate_recovered_table_row('| 等级 | 有效波高（米） |','| 巨浪 | '+value+' |',evidence)


def test_same_dimension_conversion_still_needs_the_same_station():
    source='| 站点 | 水位（厘米） |\n|---|---|\n| A站 | 231 |\n| B站 | 300 |'
    validate_recovered_table_row('| 站点 | 水位（米） |','| A站 | 2.31 |',[{'text':source}])
    with pytest.raises(ValueError):validate_recovered_table_row('| 站点 | 水位（米） |','| B站 | 2.31 |',[{'text':source}])


def test_prose_cannot_lend_another_named_row_its_number_even_in_one_sentence():
    source='台风最大平均风速为32.7至41.4 m/s，强台风为41.5至50.9 m/s。'
    table='| 等级 | 风速（m/s） |\n|---|---|\n| 台风 | 41.5～50.9 |'
    assert recovered(table,[source]) is None


def test_unit_header_and_row_cannot_be_joined_across_cited_sources():
    table='| 等级 | 风速（m/s） |\n|---|---|\n| 台风 | 32.7～41.4 |'
    assert recovered(table,['m/s\n其他等级 32.7~41.4','台风 32.7~41.4']) is None


def test_only_selected_passage_can_support_the_recovered_row():
    # A full source may contain the answer; a repair selecting another paragraph
    # still has to be rejected until the correct paragraph is actually selected.
    whole=CYCLONE_SOURCE
    selected='台风属于热带气旋。这里只说明概念，不给出数值等级。'
    evidence=[{'text':whole,'passages':[{'id':1,'text':selected}]}]
    raw=json.dumps({'answer':'| 等级 | 风速（m/s） |\n|---|---|\n| 台风 | 32.7～41.4 |'})
    review={'claims':[{'id':1,'status':'supported','refs':[{'citation':1,'passage_id':1}]}]}
    assert apply_attribution(raw,json.dumps(review),evidence,lambda value,e:_decode_model_answer(json.dumps(value),e)) is None


def test_single_pdf_unit_column_preserves_row_and_half_open_boundary():
    source='有效波高（米）\n巨浪 4≤Hs<6\n狂浪 6≤Hs<9\nHs为有效波高。'
    validate_recovered_table_row('| 等级 | 有效波高（米） |','| 巨浪 | 4≤Hs<6 |',[{'text':source}])


@pytest.mark.parametrize('gap,overlap,accepted',[('',0,True),('',8,True),('其他未选中的表头\n',0,False)])
def test_selected_same_source_passage_boundaries_preserve_header_without_filling_gaps(gap,overlap,accepted):
    header='最大平均风速(2 min平均风)\nm/s kt km/h\n最大风力(级)\n'
    row='台风 32.7~41.4 64~80 118~149 12~13'
    source=header+gap+row
    evidence=[{'text':source,'passages':[{'id':1,'text':header},{'id':2,'text':source[len(header)+len(gap)-overlap:]}]}]
    raw=json.dumps({'answer':'| 等级 | 风速（m/s，2 min平均风） |\n|---|---|\n| 台风 | 32.7～41.4 |'})
    review={'claims':[{'id':1,'status':'supported','refs':[{'citation':1,'passage_id':1},{'citation':1,'passage_id':2}]}]}
    result=apply_attribution(raw,json.dumps(review),evidence,lambda value,e:_decode_model_answer(json.dumps(value),e))
    assert (result is not None)==accepted


def test_unparseable_table_rows_are_visible_in_recovery_audit():
    table='| 等级 | 风速（m/s） | 未知列 |\n|---|---|---|\n| 台风 | 32.7～41.4 | — |\n| 台风 | 32.7～41.4 | 999 |'
    result,audit=recovered(table,[CYCLONE_SOURCE])
    assert audit['table_rows_seen']==2 and audit['table_rows_considered']==1 and audit['table_rows_skipped']==1
    assert '999' not in result[0]


@pytest.mark.parametrize('value,accepted',[('3',True),('1',False)])
def test_same_unit_columns_keep_maximum_and_minimum_identity(value,accepted):
    source='| 站点 | 最高水位（米） | 最低水位（米） |\n|---|---|---|\n| A站 | 3 | 1 |'
    table='| 站点 | 最高水位（米） |\n|---|---|\n| A站 | '+value+' |'
    assert (recovered(table,[source]) is not None)==accepted


def test_same_dimension_water_level_is_not_wave_height():
    source='| 站点 | 水位（米） | 浪高（米） |\n|---|---|---|\n| A站 | 3 | 1 |'
    table='| 站点 | 浪高（米） |\n|---|---|\n| A站 | 3 |'
    assert recovered(table,[source]) is None


def test_repeated_pdf_units_without_column_names_are_ambiguous():
    source='m m\nA站 3 1'
    table='| 站点 | 最高水位（米） |\n|---|---|\n| A站 | 3 |'
    assert recovered(table,[source]) is None


def test_matching_prose_column_is_required_when_a_source_names_the_property():
    source='A站水位为3米。'
    assert recovered('| 站点 | 水位（米） |\n|---|---|\n| A站 | 3 |',[source]) is not None
    assert recovered('| 站点 | 浪高（米） |\n|---|---|\n| A站 | 3 |',[source]) is None


def test_unparsed_numeric_operator_cannot_evade_strict_boundary_validation():
    source=CYCLONE_SOURCE
    assert recovered('| 等级 | 风速（m/s） |\n|---|---|\n| 台风 | ≥32.7且<41.5 |',[source]) is None


def test_json_source_type_does_not_exempt_wrong_object_model_table():
    source=json.dumps({'rows':[{'station':'A','value_cm':231},{'station':'B','value_cm':300}]})
    evidence=[{'text':source,'metadata':{'source_type':'business_forecast'},'passages':[{'id':1,'text':source}]}]
    raw=json.dumps({'answer':'| 站点 | 水位（厘米） |\n|---|---|\n| B | 231 |'})
    review={'claims':[{'id':1,'status':'supported','refs':[{'citation':1,'passage_id':1}]}]}
    assert apply_attribution(raw,json.dumps(review),evidence,lambda value,e:_decode_model_answer(json.dumps(value),e)) is None

@pytest.mark.parametrize('inline_grade',[False,True])
def test_wind_range_header_qualifier_and_explicit_inline_grade_units(inline_grade):
    header='| 等级 | 风速范围（m/s） | '+('风力等级' if inline_grade else '对应风力（级）')+' |'
    rows=[('热带风暴','17.2～24.4','8～9'),('强热带风暴','24.5～32.6','10～11'),
          ('台风','32.7～41.4','12～13'),('强台风','41.5～50.9','14～15'),('超强台风','≥51.0','16及以上')]
    def grade(value):return value.replace('及以上','级及以上') if '及以上' in value else value+'级'
    table=header+'\n|---|---|---|\n'+'\n'.join('| '+label+' | '+speed+' | '+(grade(g) if inline_grade else g)+' |' for label,speed,g in rows)
    result,audit=recovered(table,[CYCLONE_SOURCE])
    assert audit['retained_claims']==5
    assert '≥51.0' in result[0]


@pytest.mark.parametrize('label,speed,grade',[
    ('台风','41.5～50.9','12～13级'),('台风','64～80','12～13级'),
    ('超强台风','>51.0','16级及以上'),('台风','32.7～50.9','12～13级'),
    ('台风','32.7～41.4','14～15级'),('超强台风','≥51.0','>16级')])
def test_range_header_keeps_wrong_row_unit_boundary_and_inline_grade_rejections(label,speed,grade):
    table='| 等级 | 风速范围（m/s） | 风力等级 |\n|---|---|---|\n| '+label+' | '+speed+' | '+grade+' |'
    assert recovered(table,[CYCLONE_SOURCE]) is None


def test_range_qualifier_does_not_discard_extreme_or_physical_quantity_identity():
    source='| 站名 | 最小风速（m/s） | 波高（米） |\n|---|---|---|\n| A站 | 3 | 4 |'
    for header,row in [('| 站名 | 最大风速范围（m/s） |','| A站 | 3 |'),
                       ('| 站名 | 水位范围（米） |','| A站 | 4 |')]:
        with pytest.raises(ValueError,match='table_row_or_boundary_not_supported'):
            validate_recovered_table_row(header,row,[{'text':source}])


def test_inline_input_unit_with_unitless_heading_still_requires_exact_classification_row():
    source='| 输入风速（m/s） | 等级 |\n|---|---|\n| 32.6 | 强热带风暴 |\n| 32.7 | 台风 |\n| 41.4 | 台风 |\n| 41.5 | 强台风 |\n| 50.9 | 强台风 |\n| 51.0 | 超强台风 |'
    good='| 输入风速 | 等级 |\n|---|---|\n| 32.6 m/s | 强热带风暴 |\n| 32.7 m/s | 台风 |\n| 41.4 m/s | 台风 |\n| 41.5 m/s | 强台风 |\n| 50.9 m/s | 强台风 |\n| 51.0 m/s | 超强台风 |'
    result,audit=recovered(good,[source])
    assert audit['retained_claims']==6 and audit['table_rows_skipped']==0
    for wrong in ('| 32.6 m/s | 台风 |','| 32.7 kt | 台风 |','| 51.0 m/s | 强台风 |'):
        assert recovered('| 输入风速 | 等级 |\n|---|---|\n'+wrong,[source]) is None
    with pytest.raises(ValueError,match='grounding_claim_limit'):
        draft_claims(json.dumps({'answer':good.replace(' m/s','')}))


def test_classification_mean_period_and_generic_interval_bind_one_physical_column():
    classified='| 输入风速（m/s，2分钟平均） | 等级 |\n|---|---|\n| 32.6 | 强热带风暴 |\n| 32.7 | 台风 |\n| 41.4 | 台风 |\n| 41.5 | 强台风 |\n| 50.9 | 强台风 |\n| 51.0 | 超强台风 |'
    table='| 2分钟平均风速（m/s） | 等级 | 依据区间（m/s） |\n|---|---|---|\n'
    table+='\n'.join('| '+v+' | '+grade+' | '+interval+' |' for v,grade,interval in [
        ('32.6','强热带风暴','24.5～32.6'),('32.7','台风','32.7～41.4'),('41.4','台风','32.7～41.4'),
        ('41.5','强台风','41.5～50.9'),('50.9','强台风','41.5～50.9'),('51.0','超强台风','≥51.0')])
    result,audit=recovered(table,[CYCLONE_SOURCE,classified])
    assert audit['retained_claims']==6 and audit['table_rows_skipped']==0
    for wrong in [table.replace('2分钟平均','10分钟平均'),table.replace('≥51.0','>51.0'),
                  table.replace('| 32.6 | 强热带风暴 |','| 32.6 | 台风 |')]:
        result=recovered(wrong,[CYCLONE_SOURCE,classified])
        assert result is None or result[1]['retained_claims']<6


def test_generic_interval_cannot_choose_between_multiple_same_dimension_fields():
    source='| 地点 | 水位（米） | 波高（米） |\n|---|---|---|\n| A | 3 | 4～6 |'
    with pytest.raises(ValueError,match='table_row_or_boundary_not_supported'):
        validate_recovered_table_row('| 地点 | 水位（米） | 波高（米） | 依据区间（米） |',
                                     '| A | 3 | 4 | 4～6 |',[{'text':source}])


