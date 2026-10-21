"""Source column units and interval boundaries survive the numeric gate."""
import pytest

from marine_kb.quantities import (CitationQuantityError, declared_column_unit,
                                 table_quantities, validate_quantity_units,
                                 validate_recovered_table_row)


SOURCE = '''表A.1 十六流向方位与度数对照表
| 方位 | 符号 | 中心角度/° | 角度范围/° |
|---|---|---|---|
| 西西北 | WNW | 292.5 | (281.25,303.75] |
| 北西北 | NNW | 337.5 | (326.25,348.75] |'''
EVIDENCE = [{'text': SOURCE}]
HEADER = '| 方位 | 符号 | 中心角度/° | 角度范围/° |'


@pytest.mark.parametrize('label,unit', [
    ('中心角度/°', '°'), ('角度范围／°', '°'), ('风速/(m/s)', 'm/s'),
    ('水位/cm', 'cm'), ('m/s', 'm/s'), ('风速 m/s', None),
    ('角度/未知', None), ('角度/°和m', None),
])
def test_slash_column_unit_is_an_explicit_complete_unit(label, unit):
    assert declared_column_unit(label) == unit


def test_prose_center_angles_are_supported_by_same_source_explicit_table_units():
    assert len(table_quantities(SOURCE)) == 6
    validate_quantity_units('西西北中心角292.5°，北西北中心角337.5°[资料1]。', EVIDENCE)
    with pytest.raises(CitationQuantityError):
        validate_quantity_units('西西北中心角291.5°[资料1]。', EVIDENCE)
    # A source merely mentioning degrees elsewhere cannot type a bare table.
    unrelated = [{'text': SOURCE.replace('/°', '') + '\n其他说明：观测角度30°。'}]
    with pytest.raises(CitationQuantityError):
        validate_quantity_units('西西北中心角292.5°[资料1]。', unrelated)


@pytest.mark.parametrize('row', [
    '| 西西北 | WNW | 292.5 | (281.25,303.75] |',
    '| 北西北 | NNW | 337.5 | (326.25,348.75] |',
])
def test_complete_interval_preserves_its_row_column_and_endpoint_types(row):
    validate_recovered_table_row(HEADER, row, EVIDENCE)


@pytest.mark.parametrize('row', [
    '| 西西北 | WNW | 292.5 | [281.25,303.75] |',
    '| 西西北 | WNW | 292.5 | (281.25,303.75) |',
    '| 西西北 | WNW | 292.5 | (326.25,348.75] |',
    '| 西西北 | WNW | 337.5 | (281.25,303.75] |',
    '| 西西北 | WNW | 281.25 | (292.5,303.75] |',
])
def test_known_numbers_do_not_license_wrong_row_column_or_boundaries(row):
    with pytest.raises(ValueError, match='table_row_or_boundary_not_supported'):
        validate_recovered_table_row(HEADER, row, EVIDENCE)


def test_units_and_values_cannot_be_joined_across_documents():
    evidence = [{'text': HEADER + '\n|---|---|---|---|\n| 其他方向 | X | 1 | (0,2] |'},
                {'text': '| 方位 | 符号 | 中心角度 | 角度范围 |\n|---|---|---|---|\n| 西西北 | WNW | 292.5 | (281.25,303.75] |'}]
    with pytest.raises(CitationQuantityError):
        validate_quantity_units('西西北中心角292.5°[资料1][资料2]。', evidence)
