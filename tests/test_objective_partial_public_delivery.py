"""Exercise the public QA fallback boundary with offline program deliveries."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from marine_kb.qa import MarineQuestionService


CONTEXT = {
    'dataset_id': 'zjocean_l2_202610210800_b7fc3a3cbadb',
    'start': '2026-10-21T09:00:00+08:00',
    'end': '2026-10-24T09:00:00+08:00', 'hours': 72,
}
ACTION = {
    'type': 'objective_forecast', 'dataset_id': CONTEXT['dataset_id'],
    'field': 'wave', 'valid_time': CONTEXT['start'],
    'bounds': [120, 27, 125, 31], 'state': 'ready',
}
SOURCE = {
    'citation_id': 1, 'source_id': 'forecast:objective-test-evidence',
    'title': '本次NC原生网格程序查询',
    'metadata': {'source_type': 'business_forecast', 'objective_forecast': True},
}
GENERAL = {
    'scope': 'general',
    'explanation': '海浪是海面受到风等作用形成的波动。理解波高的含义有助于阅读预报，但当前地点的具体海况需要对应有效时刻的数据。',
    'missing_inputs': ['查询地点和有效时刻'],
}


def service_for(primary, *, forbid_model=True):
    """Keep the production public answer() wrapper; replace only inner inputs."""
    service = MarineQuestionService.__new__(MarineQuestionService)
    service.general_explanations = True
    service.external_enabled = True
    service.crag_enabled = False
    service.model_name = 'offline-dummy-model'
    service.model_caller = Mock(side_effect=AssertionError('No provider access allowed'))
    service._answer = Mock(return_value=primary)
    service._call_model = Mock(
        side_effect=AssertionError('Verified objective delivery must not call fallback')
        if forbid_model else None,
        return_value=json.dumps(GENERAL, ensure_ascii=False),
    )
    return service


def delivery(answer, *, state='partial', gaps=None):
    return {
        'answer': answer, 'rag_sources': [deepcopy(SOURCE)], 'web_sources': [],
        'answer_meta': {
            'used_mode': 'business_data', 'route': 'business_forecast',
            'answerability': 'partial' if state == 'partial' else 'insufficient',
            'business_state': state,
            'objective_delivery_verified': True,
            'business_evidence_id': 'objective-test-evidence',
            'tool_data_view': True,
            'objective_context': deepcopy(CONTEXT),
            'map_action': deepcopy(ACTION),
            'objective_dataset_id': CONTEXT['dataset_id'],
            'normalized_question': '查询浙江风浪与气压产品',
            'missing_information': list(gaps or []),
        },
    }


def assert_program_delivery_preserved(service, primary, question):
    original = deepcopy(primary)
    result = service.answer(question, allow_web=True)
    # This is the actual decorated production public entry, not a copied guard.
    service._answer.assert_called_once_with(question, allow_web=True)
    assert result is primary
    assert result['answer'] == original['answer']
    assert result['rag_sources'] == original['rag_sources']
    assert result['web_sources'] == original['web_sources']
    for key, value in original['answer_meta'].items():
        assert result['answer_meta'][key] == value
    assert result['answer_meta']['objective_context'] == CONTEXT
    assert result['answer_meta']['map_action'] == ACTION
    assert '通用解释与处理思路' not in result['answer']
    assert not result['answer_meta'].get('general_knowledge_used')
    service._call_model.assert_not_called()
    service.model_caller.assert_not_called()


def test_partial_verified_wind_wave_values_and_spatial_gap_survive_public_fallback():
    gap = 'L2范围未覆盖南部参考框，仅统计请求框与源网格的交集。'
    primary = delivery(
        '本次原生网格最大有效波高6.47 m；最大海面风速21.74 m/s（暂按阵风解释）[资料1]。\n' + gap,
        gaps=[gap],
    )
    assert_program_delivery_preserved(service_for(primary), primary, '浙江未来海浪和海面风有多大？')


def test_verified_no_pressure_is_not_replaced_by_generic_explanation():
    gap = '所选L2数据集未提供气压网格，未使用ECMWF或其他批次补齐。'
    primary = delivery(gap + '[资料1]', state='no_data', gaps=[gap])
    primary['answer_meta']['map_action']['field'] = 'pressure'
    primary['answer_meta']['map_action']['state'] = 'not_provided'
    expected_action = deepcopy(primary['answer_meta']['map_action'])
    service = service_for(primary)
    result = service.answer('同一批次气压最低多少？')
    assert result is primary and result['answer'] == gap + '[资料1]'
    assert result['rag_sources'] == [SOURCE]
    assert result['answer_meta']['business_state'] == 'no_data'
    assert result['answer_meta']['missing_information'] == [gap]
    assert result['answer_meta']['objective_context'] == CONTEXT
    assert result['answer_meta']['map_action'] == expected_action
    assert not result['answer_meta'].get('general_knowledge_used')
    service._call_model.assert_not_called()
    service.model_caller.assert_not_called()


def test_verified_outside_time_range_keeps_exact_requested_date_and_gap():
    gap = '请求2026年11月1日08时，当前批次有效期未覆盖该时刻；未改查今天或其他批次。'
    primary = delivery(gap + '[资料1]', state='no_data', gaps=[gap])
    primary['answer_meta']['objective_context'] = {
        **CONTEXT, 'start': '2026-11-01T08:00:00+08:00', 'end': '2026-11-04T08:00:00+08:00',
    }
    expected = deepcopy(primary)
    service = service_for(primary)
    result = service.answer('显示11月1日08时的海浪图')
    assert result is primary and result['answer'] == expected['answer']
    assert result['rag_sources'] == expected['rag_sources']
    assert result['answer_meta']['objective_context'] == expected['answer_meta']['objective_context']
    assert result['answer_meta']['map_action'] == expected['answer_meta']['map_action']
    assert result['answer_meta']['missing_information'] == [gap]
    service._call_model.assert_not_called()
    service.model_caller.assert_not_called()


@pytest.mark.parametrize('missing_key', ['objective_delivery_verified', 'business_evidence_id', 'tool_data_view'])
def test_incomplete_verification_does_not_disable_general_explanations(missing_key):
    primary = delivery('当前证据不足。')
    primary['answer_meta'].pop(missing_key)
    primary['answer_meta']['answerability'] = 'insufficient'
    service = service_for(primary, forbid_model=False)
    result = service.answer('什么是海浪？')
    service._call_model.assert_called_once()
    assert GENERAL['explanation'] in result['answer']
    assert '通用解释与处理思路' in result['answer']
    assert result['answer_meta']['general_knowledge_used'] is True
    service.model_caller.assert_not_called()


def test_ordinary_insufficient_answer_still_uses_offline_general_guidance():
    primary = {'answer': '未取得定义原文。', 'rag_sources': [], 'web_sources': [],
               'answer_meta': {'used_mode': 'local_extractive', 'answerability': 'insufficient',
                               'route': 'local_rag', 'missing_information': ['定义依据']}}
    service = service_for(primary, forbid_model=False)
    result = service.answer('海浪是什么？')
    service._call_model.assert_called_once()
    assert GENERAL['explanation'] in result['answer']
    assert result['answer_meta']['general_guidance_not_verified'] is True
    assert 'objective_delivery_verified' not in result['answer_meta']
    service.model_caller.assert_not_called()
