"""Public numeric answer routing, with independent workbook source identities."""
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3

import pytest

from marine_kb.numeric_archive import NumericArchive, digest
from marine_kb.numeric_access import answer, matches, open_archive
from test_numeric_archive import xlsx

F1='Surge_detem_TY0216_2026041310.xlsx'
F2='Waterlevel_detem_TY0216_2026041310.xlsx'
ABSENT='Surge_ensem_TY9999_2026041310.xlsx'


@pytest.fixture
def data(tmp_path):
    store=SimpleNamespace(database=tmp_path/'knowledge.sqlite3')
    archive=NumericArchive(tmp_path/'numeric_archive.sqlite3')
    first=xlsx(tmp_path/'first.xlsx',[['时间','大戢山','坎门'],['05/15:00',1.25,2],['06/16:00',3,4]])
    second=xlsx(tmp_path/'second.xlsx',[['时间','大戢山','坎门'],['05/15:00',5.5,6],['06/16:00',7,8]],sheet='确定性预报wl')
    archive.recover_workbook(first,filename=F1,source_id='marine-source-one')
    archive.recover_workbook(second,filename=F2,source_id='marine-source-two')
    return store,archive


def test_two_files_same_location_and_raw_time_have_separate_values_and_citations(data):
    store,_=data
    result=answer(store,f'请对照{F1}和{F2}的大戢山，05/15:00的原值分别是多少？')
    assert result['answer_meta']['numeric_archive_rows']==2
    assert F1 in result['answer'] and F2 in result['answer']
    assert '1.25' in result['answer'] and '5.5' in result['answer']
    sources=result['rag_sources']
    assert [s['source_id'] for s in sources]==['marine-source-one','marine-source-two']
    assert sources[0]['metadata']['source_sha256']!=sources[1]['metadata']['source_sha256']
    assert [s['citation_id'] for s in sources]==[1,2]
    assert all('[资料'+str(s['citation_id'])+']' in result['answer'] for s in sources)
    assert all('B2' in s['locator'] for s in sources)


def test_individual_file_place_time_clauses_preserve_the_requested_pairs(data):
    store,_=data
    result=answer(store,f'{F1}的大戢山05/15:00；{F2}的坎门06/16:00分别是多少？')
    requests=result['answer_meta']['numeric_archive_requests']
    assert [(r['file'],r['station'],r['time_token'])for r in requests]==[(F1,'大戢山','05/15:00'),(F2,'坎门','06/16:00')]
    assert '1.25' in result['answer'] and '| 8.0 |' in result['answer']
    assert result['answer_meta']['numeric_archive_rows']==2


@pytest.mark.parametrize('tail',['大戢山、坎门，05/15:00、06/16:00','分别查大戢山05/15:00、坎门06/16:00'])
def test_ambiguous_multiple_file_place_time_pairing_requests_clarification(data,tail):
    store,_=data
    result=answer(store,f'{F1}和{F2}，{tail}')
    assert result['answer_meta']['used_mode']=='needs_clarification'
    assert result['answer_meta']['numeric_archive_rows']==0
    assert '对应关系不明确' in result['answer']
    assert result['rag_sources']==[]


def test_explicit_all_files_same_pairs_can_be_delivered_without_cross_binding(data):
    store,_=data
    result=answer(store,f'{F1}和{F2}，两份表都查大戢山05/15:00、坎门06/16:00')
    assert result['answer_meta']['numeric_archive_rows']==4
    assert len(result['rag_sources'])==2
    assert len(result['answer_meta']['numeric_archive_requests'])==4


def test_missing_second_file_does_not_erase_first_file_numeric_delivery(data):
    store,_=data
    result=answer(store,f'{F1}和{ABSENT}大戢山05/15:00分别是多少？')
    assert result['answer_meta']['numeric_archive_rows']==1
    assert '1.25' in result['answer']
    assert '未登记文件：'+ABSENT in result['answer']
    assert result['rag_sources'][0]['source_id']=='marine-source-one'
    assert result['answer_meta']['answerability']=='partial'


def test_missing_second_time_keeps_first_answer_and_names_exact_gap(data):
    store,_=data
    result=answer(store,f'{F1}大戢山05/15:00；{F2}坎门09/12:00')
    assert result['answer_meta']['numeric_archive_rows']==1
    assert '1.25' in result['answer'] and '09/12:00' in result['answer']
    assert F2 in '\n'.join(result['answer_meta']['missing_information'])
    assert len(result['rag_sources'])==1


def test_unknown_units_and_years_do_not_create_quantified_difference(data):
    store,_=data
    result=answer(store,f'比较{F1}和{F2}大戢山05/15:00，相差多少？')
    assert result['answer_meta']['numeric_archive_rows']==2
    assert 'unknown' in result['answer'] and '完整年月' in result['answer']
    assert '未计算带业务含义的差值' in result['answer']
    assert '4.25' not in result['answer']
    assert '厘米' not in result['answer'].split('不能')[0]
    assert not any(s['metadata'].get('time_status')=='explicit' for s in result['rag_sources'])


@pytest.mark.parametrize('extra',['同时显示今天NC海浪二维图','另外查询今天浙江发布的预警清单和覆盖范围'])
def test_additional_nc_or_live_warning_questions_are_explicitly_unprocessed(data,extra):
    store,_=data
    result=answer(store,f'查询{F1}大戢山05/15:00，{extra}')
    assert result['answer_meta']['numeric_archive_rows']==1
    assert result['answer_meta']['unprocessed_subquestions']
    assert '尚未处理' in result['answer'] and '不能视为整题已完整交付' in result['answer']
    assert result['answer_meta']['answerability']!='full'


def test_no_result_is_not_zero_and_does_not_invent_sources(data):
    store,_=data
    result=answer(store,f'{F1}大戢山09/12:00是多少？')
    assert result['answer_meta']['numeric_archive_rows']==0
    assert result['answer_meta']['business_state']=='no_data'
    assert '没有记录或缺测不等于0' in result['answer']
    assert result['rag_sources']==[] and result['answer_meta']['answerability']=='insufficient'


def test_exact_filename_matching_is_case_insensitive_without_changing_source_name(data):
    store,_=data
    result=answer(store,f'{F1.lower()}大戢山05/15:00')
    assert result['answer_meta']['numeric_archive_rows']==1
    assert result['rag_sources'][0]['title']==F1


def test_read_only_query_keeps_archive_and_excludes_disk_paths(data):
    store,archive=data;before=digest(archive.database)
    opened=open_archive(store)
    with opened.connect() as db:
        assert db.execute('PRAGMA query_only').fetchone()[0]==1
        with pytest.raises(sqlite3.OperationalError):db.execute('DELETE FROM numeric_files')
    result=answer(store,f'{F1}大戢山05/15:00')
    text=json.dumps(result,ensure_ascii=False)
    assert 'raw_path' not in text and str(archive.database.parent) not in text
    assert digest(archive.database)==before


def test_missing_archive_names_every_requested_file_and_other_route_is_not_claimed(tmp_path):
    store=SimpleNamespace(database=tmp_path/'knowledge.sqlite3')
    result=answer(store,f'{F1}和{F2}大戢山05/15:00')
    assert F1 in result['answer'] and F2 in result['answer']
    assert result['answer_meta']['answerability']=='insufficient'
    assert matches('显示今天浙江NC海浪图') is False
    assert answer(store,'显示今天浙江NC海浪图') is None
