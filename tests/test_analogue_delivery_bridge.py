"""Real program analogue rows survive model recovery; no paid/network calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from forecast_runtime.analogue_delivery import (
    answer_analogue, analogue_data_card, requested_top_k, validate_analogue_delivery,
)


QUESTION = '用蔷琵（接口标识202630）的中国机构预报路径，查询3个最相似的历史台风，列出名称、年份和路径相似度。'


def result():
    context = {'track_kind': 'forecast', 'agency': '中国',
               'issued_at': '2026-10-20T17:00:00+08:00',
               'track_start': '2026-10-20T17:00:00+08:00',
               'track_end': '2026-10-25T17:00:00+08:00',
               'observed_point_count': 1, 'forecast_point_count': 8}
    return {'kind': 'analogue', 'state': 'ready', 'analogue_count': 3,
            'as_of': '2026-10-20T20:09:15+08:00',
            'current': {'sid': '202630', 'name': '蔷琵', 'source': 'ZJOCEAN:中国',
                        'track_count': 9, 'track_context': deepcopy(context)},
            'track_context': context, 'data_status': {'retrieved_at': '2026-10-20T20:09:15+08:00'},
            'method': {'matching_stage': 'path_only', 'input_track': deepcopy(context),
                       'track_components': {'dtw': 0.7, 'direction': 0.2, 'speed': 0.1},
                       'scored_candidate_count': 648},
            'analogues': [
                {'rank': 1, 'name': 'KIM', 'season': 1986, 'sid': '1986330N05173',
                 'similarity_score': 76.91, 'track_dtw_km': 220.12,
                 'historical_report_state': 'missing', 'storm_surge_data_state': 'missing',
                 'missing_information': ['缺历史观测']},
                {'rank': 2, 'name': 'MALAKAS', 'season': 2016, 'sid': '2016256N12146',
                 'similarity_score': 73.44, 'track_dtw_km': 270.76,
                 'historical_report': {'source_file': '2016马勒卡总结.doc',
                                       'max_surge_m': 1.58, 'max_surge_station': '温州',
                                       'max_water_level_m': 5.36, 'max_water_level_station': '澉浦'},
                 'storm_surge': {'is_estimate': False, 'record_count': 34,
                                 'max_surge_m': 4.97, 'max_surge_station': 'SGTOU',
                                 'max_water_level_m': 9.94, 'max_water_level_station': '定海',
                                 'sources': ['原始验潮记录.xls']}},
                {'rank': 3, 'name': 'KEN', 'season': 1982, 'sid': '1982257N17140',
                 'similarity_score': 71.33, 'track_dtw_km': 294.06,
                 'historical_report_state': 'missing', 'storm_surge_data_state': 'missing'},
            ]}


class Store:
    def __init__(self): self.values = {}
    def state(self, key, value): self.values[key] = deepcopy(value)


class QA:
    external_enabled = True
    model_caller = object()
    model_name = 'offline-fake'
    crag_enabled = True

    def __init__(self, generated=None):
        self.generated = generated or {'answer': '缺少名称年份，只能参考。',
                                      'answer_meta': {'answerability': 'partial',
                                                      'missing_information': ['缺少名称年份']}}
        self.called = []

    def _public_sources(self, question, hits):
        return [{'citation': '资料1', 'source_id': h['source_id'], 'url': h['url'],
                 'metadata': deepcopy(h['metadata'])} for h in hits]

    def joint_evidence(self, question, meta, allow_web, pinned=None):
        self.called.append((question, deepcopy(meta), deepcopy(pinned)))
        return pinned

    def _organized_answer(self, question, model, hits, meta):
        self.called.append((question, deepcopy(meta), deepcopy(hits)))
        return deepcopy(self.generated)


def answer(question=QUESTION, data=None, qa=None):
    runtime = SimpleNamespace(store=Store())
    response = answer_analogue(runtime, question, data or result(), qa or QA(),
                               {'as_of': '2026-10-20T20:10:00+08:00',
                                'normalized_question': QUESTION, 'subquestions': [{'question': '年份是什么'}]}, True)
    return response, runtime


def test_model_cannot_erase_or_contradict_three_source_bound_tuples():
    response, runtime = answer()
    text, meta = response['answer'], response['answer_meta']
    for token in ['KIM', '1986', '76.91', '220.12', 'MALAKAS', '2016', '73.44', 'KEN', '1982', '71.33']:
        assert token in text
    assert '缺少名称年份' not in text
    assert '试算' not in text and 'SWL' not in text
    assert meta['answerability'] == 'full' and meta['analogue_delivery_verified'] is True
    assert meta['analogue_explanation_validation'] == 'partial'
    assert meta['business_trial'] is False and meta['missing_information'] == []
    saved = next(iter(runtime.store.values.values()))
    digest = hashlib.sha256(json.dumps(result(), ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':'), allow_nan=True).encode()).hexdigest()
    assert saved['analogue_delivery']['result_sha256'] == digest
    assert meta['analogue_result_sha256'] == digest
    assert response['rag_sources'][0]['metadata']['program_result_sha256'] == digest


def test_model_explanation_has_separate_scope_and_cannot_overwrite_delivery_contract():
    qa = QA({'answer': '路径评分采用DTW、方向与移动速度，不能代表灾损。',
             'answer_meta': {'answerability': 'full', 'used_mode': 'llm'}})
    response, _runtime = answer(qa=qa)
    assert response['answer'].startswith('**历史台风路径相似查询结果**')
    assert '方法解读' in response['answer']
    assert response['answer_meta']['answerability'] == 'full'
    assert all(item[1]['normalized_question'] != QUESTION for item in qa.called)
    assert all(item[1]['subquestions'][0]['question'] != '年份是什么' for item in qa.called)


@pytest.mark.parametrize('mutation', [
    lambda r: r['analogues'][0].pop('similarity_score'),
    lambda r: r['analogues'][0].update(similarity_score=float('nan')),
    lambda r: r['analogues'][0].update(similarity_score=float('inf')),
    lambda r: r['analogues'][0].update(similarity_score=True),
    lambda r: r['analogues'][0].update(season=None),
    lambda r: r['analogues'][0].update(name=''),
    lambda r: r['analogues'][0].update(rank=2),
    lambda r: r['analogues'][1].update(sid='1986330N05173'),
    lambda r: r['analogues'][1].update(similarity_score=99),
    lambda r: r.update(analogue_count=2),
])
def test_missing_or_inconsistent_core_tuple_cannot_become_full(mutation):
    data = result(); mutation(data)
    response, _runtime = answer(data=data)
    assert response['answer_meta']['answerability'] == 'partial'
    assert not response['answer_meta']['analogue_delivery_verified']
    assert response['answer_meta']['missing_information']


@pytest.mark.parametrize('mutation', [
    lambda r: r['current'].update(sid='202631'),
    lambda r: r['track_context'].update(agency='日本'),
    lambda r: r['track_context'].update(track_kind='observed'),
    lambda r: r['track_context'].update(issued_at=None),
    lambda r: r['track_context'].update(track_start='2026-10-20 17:00'),
    lambda r: r['current'].update(track_count=1),
])
def test_wrong_storm_agency_path_or_unverifiable_time_is_not_the_requested_ranking(mutation):
    data = result(); mutation(data)
    response, _runtime = answer(data=data)
    assert response['answer_meta']['answerability'] == 'partial'
    assert response['answer_meta']['analogue_delivery']['delivered_count'] == 0
    assert '| KIM |' not in response['answer']


def test_pure_ranking_does_not_demand_history_observations_or_event_time():
    data = result()
    for row in data['analogues']:
        row.pop('historical_report', None); row.pop('storm_surge', None)
        row['missing_information'] = ['没有历史报告', '没有观测时刻']
    response, _runtime = answer(data=data)
    assert response['answer_meta']['missing_information'] == []
    assert response['answer_meta']['answerability'] == 'full'


def test_requested_water_level_is_partial_for_missing_cases_with_separate_report_and_station_scopes():
    response, _runtime = answer(QUESTION + '再列出它们的最高潮位。')
    text, meta = response['answer'], response['answer_meta']
    assert meta['analogue_delivery_verified'] is True and meta['answerability'] == 'partial'
    assert '5.36 m' in text and '澉浦' in text and '2016马勒卡总结.doc' in text
    assert '9.94 m' in text and '定海' in text and '原始验潮记录.xls' in text
    assert len(meta['missing_information']) == 2
    assert all('最高潮位' in gap for gap in meta['missing_information'])
    assert all('增水' not in gap for gap in meta['missing_information'])


def test_available_historical_values_do_not_imply_current_zhejiang_impact():
    response, _runtime = answer(QUESTION + '本次浙江影响是否严重？')
    assert response['answer_meta']['answerability'] == 'partial'
    assert any('本次浙江影响' in gap for gap in response['answer_meta']['missing_information'])
    assert '本次为严重' not in response['answer']


def test_insufficient_candidates_do_not_pad_or_claim_complete_requested_topk():
    data = result(); data['analogues'].pop(); data['analogue_count'] = 2
    response, _runtime = answer(data=data)
    assert response['answer_meta']['answerability'] == 'partial'
    assert '请求3个相似台风，程序实际返回2个' in response['answer']
    assert 'KEN' not in response['answer']


def test_requested_topk_is_not_hardcoded_to_three():
    assert requested_top_k('查询5个最相似台风') == 5
    assert requested_top_k('前7个历史台风') == 7
    data = result()
    question = QUESTION.replace('查询3个', '查询2个')
    assert not validate_analogue_delivery(question, data)['verified']


def test_model_unavailable_preserves_card():
    qa = QA(); qa.external_enabled = False
    response, _runtime = answer(qa=qa)
    assert 'KIM' in response['answer'] and not qa.called
    assert response['answer_meta']['answerability'] == 'full'


def test_original_real_recording_rows_are_delivered_offline_if_private_record_exists():
    source = Path(__file__).resolve().parents[1] / 'tmp/replay/objective_20261020_200819_263a4f37/ANALOGUE.result.json'
    if not source.exists():
        pytest.skip('private recording is excluded from public source snapshot')
    recorded = json.loads(source.read_text(encoding='utf-8'))
    response, _runtime = answer(recorded['question'], recorded['tool_result'])
    assert response['answer_meta']['answerability'] == 'full'
    for row in recorded['tool_result']['analogues']:
        assert row['name'] in response['answer'] and str(row['season']) in response['answer']
        assert str(row['similarity_score']) in response['answer']
    assert response['answer_meta']['missing_information'] == []


def test_current_stale_cache_is_not_latest_but_fixed_path_query_can_still_rank():
    data = result(); data['cache_stale'] = True
    assert not validate_analogue_delivery(QUESTION + '使用最新路径。', data)['verified']
    assert validate_analogue_delivery(QUESTION, data)['verified']


def test_unknown_extra_question_is_not_forgotten_when_ranking_ready():
    response, _runtime = answer(QUESTION + '它们还有什么共同特点？')
    assert response['answer_meta']['answerability'] == 'partial'
    assert any('额外问项' in gap for gap in response['answer_meta']['missing_information'])


def test_even_full_model_prose_cannot_reintroduce_different_rank_or_fake_missing():
    qa = QA({'answer': '未取得名称年份。', 'answer_meta': {'answerability': 'full', 'used_mode': 'llm'}})
    response, _runtime = answer(qa=qa)
    assert '未取得名称年份' not in response['answer']
    qa = QA({'answer': 'KIM的路径相似度为99。', 'answer_meta': {'answerability': 'full', 'used_mode': 'llm'}})
    response, _runtime = answer(qa=qa)
    assert '相似度为99' not in response['answer']


def test_source_reordering_cannot_rebind_card_citation_to_a_different_document():
    qa = QA({'answer': '路径方向参与评分。', 'rag_sources': [{'source_id': 'wrong'}],
             'answer_meta': {'answerability': 'full', 'used_mode': 'llm'}})
    response, _runtime = answer(qa=qa)
    assert '方法解读' not in response['answer']
    assert response['rag_sources'][0]['source_id'].startswith('forecast:')


def test_mixed_or_unknown_scoring_method_is_not_claimed_as_path_similarity():
    data = result(); data['method']['matching_stage'] = 'track_and_intensity'
    response, _runtime = answer(data=data)
    assert response['answer_meta']['answerability'] == 'partial'
    assert '纯路径评分' in ' '.join(response['answer_meta']['missing_information'])


def test_recorded_partial_answer_without_names_is_replaced_by_actual_program_card_offline():
    source = Path(__file__).resolve().parents[1] / 'tmp/replay/objective_20261020_200819_263a4f37/ANALOGUE.result.json'
    if not source.exists():
        pytest.skip('private recording is excluded from public source snapshot')
    recorded = json.loads(source.read_text(encoding='utf-8'))
    old = recorded['reply']
    qa = QA({'answer': old['answer'], 'answer_meta': {'answerability': old['answerability'],
             'missing_information': old.get('missing_information') or []}})
    response, _runtime = answer(recorded['question'], recorded['tool_result'], qa)
    assert response['answer_meta']['answerability'] == 'full'
    assert response['answer_meta']['analogue_explanation_validation'] == 'partial'
    for row in recorded['tool_result']['analogues']:
        assert row['name'] in response['answer'] and str(row['season']) in response['answer']
        assert str(row['similarity_score']) in response['answer']
    assert '待补齐' not in response['answer']


@pytest.mark.parametrize('requested', [1, 5, 10])
def test_query_parser_forwards_exact_requested_topk(requested):
    from datetime import datetime
    from forecast_runtime.analogue import query_analogue
    from forecast_runtime.storage import BEIJING
    from test_analogue_product_contract import snapshot
    calls = []
    rt = SimpleNamespace(base_result=lambda start, end: {'start': start.isoformat(), 'end': end.isoformat()},
          analogue_service=SimpleNamespace(find_similar=lambda payload, top_k: calls.append(top_k) or {'analogues': []}),
          sync=SimpleNamespace(typhoons=lambda: snapshot()), _local=SimpleNamespace(request_cutoff=None))
    now = datetime(2026, 10, 20, tzinfo=BEIJING)
    query_analogue(rt, f'用台风“测试甲”的中国机构预报路径，查询{requested}个最相似历史台风。', now, now)
    assert calls == [requested]


@pytest.mark.parametrize('requested', [0, 11, 100])
def test_query_invalid_topk_is_a_clarification_not_silent_clamp(requested):
    from datetime import datetime
    from forecast_runtime.analogue import query_analogue
    from forecast_runtime.storage import BEIJING
    from test_analogue_product_contract import snapshot
    rt = SimpleNamespace(base_result=lambda start, end: {},
          analogue_service=SimpleNamespace(find_similar=lambda *args, **kwargs: pytest.fail('no clamping')),
          sync=SimpleNamespace(typhoons=lambda: snapshot()), _local=SimpleNamespace(request_cutoff=None))
    now = datetime(2026, 10, 20, tzinfo=BEIJING)
    output = query_analogue(rt, f'用台风“测试甲”的中国机构预报路径，列出{requested}个最相似历史台风。', now, now)
    assert output['state'] == 'needs_clarification'


@pytest.mark.parametrize('requested', [1, 5, 10])
def test_actual_saved_path_algorithm_and_output_validator_accept_exact_topk(requested):
    import sqlite3
    from unittest.mock import patch
    from forecast_runtime.sync import select_typhoon_payload
    from typhoon.service import TyphoonAnalogueService, format_analogue_report
    from typhoon.validation import validate_analysis_result
    root = Path(__file__).resolve().parents[1]
    cache = root / 'data/forecast_runtime/forecast.sqlite3'
    history = root / 'typhoon/data/typhoon_history.sqlite3'
    if not cache.exists() or not history.exists():
        pytest.skip('private local source databases are not distributed')
    with sqlite3.connect('file:' + cache.as_posix() + '?mode=ro', uri=True) as db:
        raw = db.execute("select payload from state where key='typhoons'").fetchone()
    if not raw:
        pytest.skip('no saved live snapshot')
    data = json.loads(raw[0])
    payload = select_typhoon_payload(data, storm_id=data['list'][0]['typhoonID'])
    original = sqlite3.connect
    def readonly(path, *args, **kwargs):
        return original('file:' + Path(path).as_posix() + '?mode=ro', uri=True)
    with patch('sqlite3.connect', readonly):
        output = TyphoonAnalogueService().find_similar(payload, top_k=requested)
    assert len(output['analogues']) == requested
    output['report'] = format_analogue_report(output)
    validate_analysis_result(output)


@pytest.mark.parametrize('missing', ['retrieved_at', 'issued_at', 'both'])
def test_table_is_one_block_when_optional_observed_track_timestamps_are_absent(missing):
    data = result()
    data['current']['source'] = 'ZJOCEAN'
    data['track_context'].update(track_kind='observed', agency=None,
                                 observed_point_count=42, forecast_point_count=0)
    data['current']['track_count'] = 42
    if missing in {'issued_at', 'both'}:
        data['track_context'].pop('issued_at')
    if missing in {'retrieved_at', 'both'}:
        data['data_status'].pop('retrieved_at')
    data['current']['track_context'] = deepcopy(data['track_context'])
    data['method']['input_track'] = deepcopy(data['track_context'])
    question = QUESTION.replace('中国机构预报路径', '已发生路径')
    response, _runtime = answer(question, data)
    assert response['answer_meta']['answerability'] == 'full'
    table = response['answer'].split('| 排名 |', 1)[1].split('本次为纯路径', 1)[0]
    assert '|\n|---' in table and '|\n\n|' not in table
    assert table.count('[资料1]') == 3


def test_candidate_pool_limits_are_delivered_from_actual_method_note():
    data = result()
    data['method']['note'] = '浙江影响且具正式名称、最大风速和最低气压的候选参与评分；强度不参与路径排名。'
    response, _runtime = answer(data=data)
    assert data['method']['note'] in response['answer']
    assert '候选池与方法说明' in response['answer']
