"""Recovery tests use tiny OOXML fixtures; no workbook or business DB is edited."""
from pathlib import Path
from xml.sax.saxutils import escape
import json
import sqlite3
import zipfile

import pytest

from marine_kb.numeric_archive import NumericArchive, digest, number, time_value, geometry_metadata


def xlsx(path, rows, sheet='确定性预报surge'):
    def column(index):
        result=''
        while index:
            index,remainder=divmod(index-1,26);result=chr(65+remainder)+result
        return result
    xml=[]
    for r,row in enumerate(rows,1):
        cells=[]
        for c,value in enumerate(row,1):
            addr=column(c)+str(r)
            if isinstance(value,tuple):
                formula,cache=value
                cells.append(f'<c r="{addr}"><f>{escape(formula)}</f>'+('' if cache is None else f'<v>{cache}</v>')+'</c>')
            elif value is None:
                cells.append(f'<c r="{addr}"/>')
            elif isinstance(value,(int,float)):
                cells.append(f'<c r="{addr}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{addr}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
        xml.append(f'<row r="{r}">'+''.join(cells)+'</row>')
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('[Content_Types].xml','''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>''')
        z.writestr('_rels/.rels','''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''')
        z.writestr('xl/workbook.xml',f'''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{escape(sheet)}" sheetId="1" r:id="rId1"/></sheets></workbook>''')
        z.writestr('xl/_rels/workbook.xml.rels','''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>''')
        z.writestr('xl/worksheets/sheet1.xml','''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'''+''.join(xml)+'</sheetData></worksheet>')
    return path


def sql(path, table, columns, rows):
    def token(value):
        return 'NULL' if value is None else "'"+str(value).replace("'","''")+"'" if isinstance(value,str) else str(value)
    text='CREATE TABLE `'+table+'` (\n'+',\n'.join(' `'+c+'` text' for c in columns)+'\n) ENGINE=InnoDB;\n'
    text+='\n'.join('INSERT INTO `'+table+'` VALUES ('+','.join(map(token,r))+');' for r in rows)
    path.write_text(text,encoding='utf-8');return path


def registry(tmp_path, archive):
    site=sql(tmp_path/'sites.sql','m_fcst_site',['StationID','StationName','DataFile','Base85'],[
        ['A','健跳','J1',10],['B','健跳','J2',20],['C','舟山','SHENJIAMEN',30],['D','甲','JIA',40]])
    names=tmp_path/'names.txt';names.write_text('SHENJIAMEN 沈家门/朱家尖/舟山国家站\n',encoding='utf-8')
    archive.load_station_registry(site,names)


def test_actual_cells_keep_zero_null_sentinel_coordinates_and_hash(tmp_path):
    path=xlsx(tmp_path/'table.xlsx',[['时间','甲站','健跳','沈家门'],['05/14:00',0,-99999,None],['05/15:00',-0.2,2.345,'文字']])
    original=digest(path);archive=NumericArchive(tmp_path/'stage.sqlite3');registry(tmp_path,archive)
    out=archive.recover_workbook(path,source_id='source-1',filename='Surge_detem_TY0216_2026041310.xlsx',expected_sha256=original)
    assert out['cells']==6 and out['valid']==3 and out['invalid_sentinel']==1 and out['missing']==1
    rows={r['cell']:r for r in archive.query(file='source-1')['items']}
    assert rows['B2']['value']==0 and rows['B2']['quality']=='valid'
    assert rows['C2']['value'] is None and rows['C2']['raw']==-99999 and rows['C2']['quality']=='invalid_sentinel'
    assert rows['D2']['value'] is None and rows['D2']['quality']=='missing'
    assert rows['B3']['value']==-0.2 and rows['C3']['value']==2.345
    assert rows['D3']['quality']=='non_numeric'
    assert all(r['sha256']==original and r['sheet']=='确定性预报surge' for r in rows.values())
    assert all(r['unit']=='unknown' and r['vertical_datum']=='unknown' for r in rows.values())
    assert all(r['valid_time'] is None and r['time_status']=='day_clock_only' for r in rows.values())
    assert digest(path)==original


def test_partial_day_clock_never_borrows_year_month_from_filename(tmp_path):
    archive=NumericArchive(tmp_path/'stage.sqlite3')
    path=xlsx(tmp_path/'Surge_detem_TY0216_2026041310.xlsx',[['时间','甲'],['05/14:00',1.5]])
    archive.recover_workbook(path)
    raw=archive.query(time_token='05/14:00')
    assert raw['total']==1 and raw['items'][0]['valid_time'] is None
    assert archive.query(start='2026-04-05T00:00:00+08:00',end='2026-05-06T00:00:00+08:00')['total']==0


def test_explicit_dates_can_be_queried_but_timezone_unknown_is_not_invented(tmp_path):
    archive=NumericArchive(tmp_path/'stage.sqlite3')
    path=xlsx(tmp_path/'date.xlsx',[['时间','甲'],['2026-10-21T12:00:00+08:00',1],['2026-10-21 13:00:00',2]])
    archive.recover_workbook(path)
    rows=archive.query(start='2026-10-21T00:00:00+08:00',end='2026-10-22T00:00:00+08:00')['items']
    assert len(rows)==1 and rows[0]['value']==1 and rows[0]['time_status']=='explicit'
    assert archive.query(time_token='2026-10-21 13:00:00')['items'][0]['time_status']=='date_known_timezone_unknown'
    with pytest.raises(ValueError):archive.query(start='2026-10-21T00:00:00')


def test_temporary_profile_is_marked_assumed_and_requires_query_opt_in(tmp_path):
    archive=NumericArchive(tmp_path/'stage.sqlite3')
    path=xlsx(tmp_path/'partial.xlsx',[['时间','甲'],['05/14:00',1]])
    profile={'unit':'cm','vertical_datum':'1985_assumed','time_map':{'05/14:00':'2026-05-05T14:00:00+08:00'}}
    archive.recover_workbook(path,profile=profile)
    assert archive.query(start='2026-05-05T00:00:00+08:00')['total']==0
    row=archive.query(start='2026-05-05T00:00:00+08:00',allow_assumed=True)['items'][0]
    assert row['time_status']==row['unit_status']==row['datum_status']=='assumed'
    assert row['value']==1 and row['unit']=='cm'


def test_station_mapping_preserves_ambiguity_and_explicit_name_aliases(tmp_path):
    archive=NumericArchive(tmp_path/'stage.sqlite3');registry(tmp_path,archive)
    path=xlsx(tmp_path/'map.xlsx',[['时间','健跳','沈家门','未登记'],['05/14:00',1,2,3]])
    archive.recover_workbook(path)
    rows={r['station']:r for r in archive.query()['items']}
    assert rows['健跳']['station_id'] is None and rows['健跳']['candidates']==['A','B']
    assert rows['沈家门']['station_id']=='C'
    assert rows['未登记']['mapping_status']=='unmapped'
    # Query works after restarting the archive using only persisted source aliases.
    reopened=NumericArchive(tmp_path/'stage.sqlite3')
    assert reopened.query(station='朱家尖')['items'][0]['station']=='沈家门'


def test_formula_cache_kept_and_missing_cache_does_not_become_zero(tmp_path):
    archive=NumericArchive(tmp_path/'stage.sqlite3')
    path=xlsx(tmp_path/'formula.xlsx',[['时间','缓存','无缓存'],['05/14:00',('1+1',2),('2+2',None)]])
    archive.recover_workbook(path)
    rows={r['cell']:r for r in archive.query()['items']}
    assert rows['B2']['quality']=='formula_cached' and rows['B2']['value']==2 and rows['B2']['formula']=='=1+1'
    assert rows['C2']['quality']=='formula_unresolved' and rows['C2']['value'] is None


def test_hash_mismatch_and_unrecognized_sheet_do_not_commit_partial_recovery(tmp_path):
    archive=NumericArchive(tmp_path/'stage.sqlite3')
    good=xlsx(tmp_path/'good.xlsx',[['时间','甲'],['05/14:00',1]])
    with pytest.raises(ValueError,match='SHA256'):archive.recover_workbook(good,expected_sha256='0'*64)
    bad=xlsx(tmp_path/'bad.xlsx',[['not time','甲'],['05/14:00',1]])
    with pytest.raises(ValueError):archive.recover_workbook(bad)
    assert archive.summary()['files']==archive.summary()['cells']==0


def test_repeat_recovery_is_idempotent_and_assumption_change_rebuilds(tmp_path):
    archive=NumericArchive(tmp_path/'stage.sqlite3');path=xlsx(tmp_path/'table.xlsx',[['时间','甲'],['05/14:00',1]])
    archive.recover_workbook(path)
    assert archive.recover_workbook(path)['state']=='existing'
    assert archive.summary()['cells']==1
    archive.recover_workbook(path,profile={'unit':'cm'})
    assert archive.summary()['cells']==1 and archive.query()['items'][0]['unit_status']=='assumed'


def coast_inputs(tmp_path):
    features=[]
    for coast_id,blue in [('1',100),('2',110)]:
        features.append({'type':'Feature','geometry':{'type':'LineString','coordinates':[[121,28],[122,29]]},
          'properties':{'cid':coast_id,'TOWN':'岸段'+coast_id,'CWZ':'甲站','BLUE':blue,'YELLOW':None if coast_id=='2' else 120,
                        'ORANGE':None if coast_id=='2' else 130,'RED':140}})
    geo=tmp_path/'coasts.json';geo.write_text(json.dumps({'type':'FeatureCollection','features':features}),encoding='utf-8')
    sqlpath=sql(tmp_path/'coasts.sql','m_warn_tide_info_town',
        ['Id','name','StationName','BlueLevel','YellowLevel','OrangeLevel','RedLevel','Lat','Lon'],
        [[1,'岸段1','甲站',105,120,130,140,121,28],[2,'岸段2','甲站',110,-99999,-99999,140,121,28]])
    return geo,sqlpath


def test_coasts_keep_both_versions_invalid_thresholds_and_conflict_types(tmp_path):
    archive=NumericArchive(tmp_path/'stage.sqlite3');registry(tmp_path,archive)
    geo,sqlpath=coast_inputs(tmp_path)
    output=archive.recover_coasts(geo,sqlpath)
    assert output['geojson_features']==output['sql_rows']==2 and not output['issues']
    pair=archive.coasts('2')['items'];assert len(pair)==2
    s=next(r for r in pair if r['version']=='sql')
    levels={r['colour']:r for r in s['thresholds']}
    assert levels['yellow']['value'] is None and levels['yellow']['quality']=='invalid_sentinel'
    assert levels['orange']['value'] is None and levels['blue']['value']==110
    assert all(r['unit']=='unknown' for r in s['thresholds'])
    assert s['metadata']['coordinate_status']=='swapped_fields'
    summary=archive.summary()
    assert summary['threshold_value_conflict_fields']==1 and summary['threshold_value_conflict_coasts']==1
    assert summary['missing_encoding_differences']==2
    g=next(r for r in pair if r['version']=='geojson')
    assert g['metadata']['crs_status']=='unknown' and g['metadata']['coordinate_pair_count']==2


def test_nonincreasing_threshold_order_and_duplicate_ids_are_not_silently_accepted(tmp_path):
    archive=NumericArchive(tmp_path/'stage.sqlite3');geo,sqlpath=coast_inputs(tmp_path)
    data=json.loads(geo.read_text());data['features'][0]['properties']['BLUE']=150
    geo.write_text(json.dumps(data),encoding='utf-8')
    output=archive.recover_coasts(geo,sqlpath)
    assert any(r['kind']=='non_increasing_thresholds' for r in output['issues'])
    data['features'].append(data['features'][0]);geo.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError,match='重复ID'):archive.recover_coasts(geo,sqlpath)
    assert archive.summary()['coast_versions']=={'geojson':2,'sql':2}


@pytest.mark.parametrize('filename',['knowledge.sqlite3','forecast.sqlite3','typhoon_rag.sqlite3','typhoon_history.sqlite3'])
def test_archive_refuses_production_database_names(tmp_path,filename):
    with pytest.raises(ValueError,match='独立数据库'):NumericArchive(tmp_path/filename)


def test_missing_values_and_geometry_bounds_are_explicit():
    assert number(0)==(0,'valid') and number(-99999)==(None,'invalid_sentinel')
    assert number(None)==(None,'missing') and number(float('nan'))==(None,'nonfinite')
    assert time_value('28/08:00')[1:] == (None,'day_clock_only')
    result=geometry_metadata({'type':'LineString','coordinates':[[121,28],[999,29]]})
    assert result['coordinate_pair_count']==1 and result['coordinate_issues']==['invalid_coordinate_pair']


def test_read_only_open_does_not_ddl_and_cannot_write(tmp_path):
    path=tmp_path/'stage.sqlite3';archive=NumericArchive(path)
    book=xlsx(tmp_path/'table.xlsx',[['时间','甲'],['05/14:00',1]])
    archive.recover_workbook(book)
    before=digest(path);readonly=NumericArchive(path,read_only=True)
    assert readonly.query()['items'][0]['value']==1 and readonly.summary()['files']==1
    with readonly.connect() as db:
        assert db.execute('PRAGMA query_only').fetchone()[0]==1
        with pytest.raises(sqlite3.OperationalError):db.execute('DELETE FROM numeric_files')
    with pytest.raises(ValueError,match='只读'):readonly.recover_workbook(book)
    assert digest(path)==before


def test_read_only_missing_database_creates_no_directory(tmp_path):
    parent=tmp_path/'not-created'
    with pytest.raises(FileNotFoundError):NumericArchive(parent/'stage.sqlite3',read_only=True)
    assert not parent.exists()


@pytest.mark.parametrize('coasts_first',[True,False])
def test_coastal_headers_match_explicit_county_name_without_losing_station_annotation(tmp_path,coasts_first):
    archive=NumericArchive(tmp_path/'stage.sqlite3');geo,sqlpath=coast_inputs(tmp_path)
    data=json.loads(geo.read_text())
    for feature in data['features']:feature['properties']['COUNTY']='县A'
    geo.write_text(json.dumps(data),encoding='utf-8')
    if coasts_first:archive.recover_coasts(geo,sqlpath)
    path=xlsx(tmp_path/'coastal.xlsx',[['时间','县A-岸段1(甲站)*'],['05/14:00',123.4]])
    archive.recover_workbook(path)
    if not coasts_first:archive.recover_coasts(geo,sqlpath)
    row=archive.query(coast='1')['items'][0]
    assert row['coast_id']=='1' and row['location_kind']=='coast'
    assert row['coast_mapping_status']=='unique_source_coast_name'
    assert row['station']=='县A-岸段1(甲站)*' and row['value']==123.4 and row['cell']=='B2'
    assert archive.query(station='县A-岸段1')['items'][0]['coast_id']=='1'


def test_full_coastal_admc_identity_is_not_made_ambiguous_by_shared_town_field(tmp_path):
    archive=NumericArchive(tmp_path/'stage.sqlite3');geo,sqlpath=coast_inputs(tmp_path)
    data=json.loads(geo.read_text())
    for i,feature in enumerate(data['features']):
        feature['properties'].update(COUNTY='县A',TOWN='共享',ADMC='共享岸段' if i else '另一岸段')
    geo.write_text(json.dumps(data),encoding='utf-8');archive.recover_coasts(geo,sqlpath)
    path=xlsx(tmp_path/'specific.xlsx',[['时间','县A-共享岸段(甲站)'],['05/14:00',5]])
    archive.recover_workbook(path)
    assert archive.query(coast='2')['items'][0]['coast_id']=='2'
    assert archive.query(coast='1')['items']==[]
