"""Deliver server-owned path analogue tuples independently of language recovery.

A ranking is a calculation, not an event-time observation.  The original
program result is retained and hashed; generated prose cannot replace its rows.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
import re


_AGENCIES = {'CMA': '中国', '中国气象局': '中国', '中国': '中国',
             'JMA': '日本', '日本气象厅': '日本', '日本': '日本',
             'JTWC': '美国', '美国': '美国', 'HKO': '香港', '香港': '香港'}
_METRICS = {
    'surge': (r'增水', 'max_surge_m', '最大风暴增水', 'm'),
    'water_level': (r'潮位|水位', 'max_water_level_m', '最高潮位', 'm'),
    'wave': (r'浪高|波高', 'max_significant_wave_m', '最大有效波高', 'm'),
    'wind': (r'风速|风力', 'max_wind_ms', '最大风速', 'm/s'),
    'pressure': (r'气压', 'min_pressure_hpa', '最低气压', 'hPa'),
    'loss': (r'经济损失|灾损|损失', 'direct_economic_loss_cny', '直接经济损失', '元'),
    'deaths': (r'死亡|伤亡', 'deaths', '死亡人数', '人'),
    'population': (r'受灾人口|受灾人数', 'affected_population', '受灾人数', '人'),
}


def _finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _number(value):
    return format(value, '.15g')


def _cell(value):
    return str(value).replace('|', '／').replace('\n', ' ').strip()


def _timestamp(value):
    try:
        return bool(value) and datetime.fromisoformat(str(value).replace('Z', '+00:00')).tzinfo is not None
    except (TypeError, ValueError):
        return False


def requested_top_k(question, default=3):
    match = re.search(r'前\s*(\d+)(?!\d)|(?:查(?:询|找)?|找|检索|比较|列出|给出)\s*(\d+)\s*(?:个|场)', str(question))
    return int(next(g for g in match.groups() if g)) if match else default


def validate_analogue_delivery(question, result):
    """Validate real tuple fields and requested selection; never infer missing zeros."""
    requested = requested_top_k(question)
    errors = []
    context_errors = []
    if result.get('kind') != 'analogue' or result.get('state') != 'ready':
        errors.append(result.get('reason') or '路径相似检索没有返回可用结果。')
    current = result.get('current') or {}
    context = result.get('track_context') or current.get('track_context') or {}
    method = result.get('method') or {}
    if not current.get('sid') or not current.get('name'):
        context_errors.append('缺少本次输入台风的名称或接口标识。')
    ids = re.findall(r'(?:标识|编号|ID)\s*[:：]?\s*(\d{4,8})', str(question), re.I)
    if ids and str(current.get('sid')) not in ids:
        context_errors.append('实际输入台风标识与用户指定对象不一致。')
    expected_agencies = [value for key, value in _AGENCIES.items()
                         if re.search(re.escape(key) + r'.{0,16}(?:预报|预测)路径', str(question), re.I)]
    expects_forecast = bool(expected_agencies or re.search(r'预报路径|预测路径|未来.{0,8}路径', str(question)))
    expects_observed = bool(re.search(r'观测路径|实况路径|已发生路径|历史已发生', str(question)))
    kind = context.get('track_kind')
    if kind not in {'forecast', 'observed'}:
        context_errors.append('未明确本次使用预报路径还是已发生路径。')
    if expects_forecast and kind != 'forecast' or expects_observed and kind != 'observed':
        context_errors.append('实际路径类型与用户指定的路径类型不一致。')
    if expected_agencies and context.get('agency') not in expected_agencies:
        context_errors.append('实际预报机构与用户指定机构不一致。')
    if kind == 'forecast' and (not context.get('agency') or not _timestamp(context.get('issued_at'))
                               or not isinstance(context.get('forecast_point_count'), int)
                               or context['forecast_point_count'] <= 0):
        context_errors.append('机构预报路径缺少可核对的机构、起报时刻或预报点。')
    if not _timestamp(context.get('track_start')) or not _timestamp(context.get('track_end')):
        context_errors.append('输入路径的起止时刻缺失或没有时区。')
    if not current.get('source') or not isinstance(current.get('track_count'), int) or isinstance(current.get('track_count'), bool) or current['track_count'] < 2:
        context_errors.append('输入路径来源或有效路径点数不足。')
    if method.get('matching_stage') != 'path_only':
        errors.append('程序未确认本次分数采用纯路径评分，不能将混合分数当路径相似度。')
    method_context = method.get('input_track') or {}
    for field in ('track_kind', 'agency', 'issued_at', 'track_start', 'track_end'):
        if field in method_context and method_context[field] != context.get(field):
            context_errors.append('评分方法记录与输入路径的' + field + '不一致。')
    if not 1 <= requested <= 10:
        errors.append('本次请求的排名数量超出1至10个的交付范围。')
    source_rows = result.get('analogues')
    if not isinstance(source_rows, list):
        source_rows = []
        errors.append('程序没有返回相似台风列表。')
    if result.get('analogue_count') != len(source_rows):
        errors.append('程序声明的相似个数与实际列表长度不一致。')
    rows = []
    seen = set()
    previous_score = float('inf')
    for index, row in enumerate(source_rows, 1):
        if not isinstance(row, dict):
            errors.append('第' + str(index) + '行不是有效排名记录。')
            continue
        score, year = row.get('similarity_score'), row.get('season')
        valid = (isinstance(row.get('rank'), int) and row.get('rank') == index and not isinstance(row.get('rank'), bool)
                 and bool(row.get('sid')) and bool(str(row.get('name') or '').strip())
                 and isinstance(year, int) and not isinstance(year, bool) and 1800 <= year <= 2200
                 and _finite(score) and 0 <= score <= 100 and score <= previous_score
                 and str(row['sid']) not in seen)
        if not valid:
            errors.append('第' + str(index) + '行的排名、身份、年份或路径相似度不能核对。')
            continue
        seen.add(str(row['sid']))
        previous_score = score
        rows.append(deepcopy(row))
    if len(source_rows) != requested:
        errors.append('请求' + str(requested) + '个相似台风，程序实际返回' + str(len(source_rows)) + '个。')
    if (result.get('cache_stale') or (result.get('data_status') or {}).get('cache_stale')) and re.search(r'最新|当前|现在|今天|今日', str(question)):
        errors.append('已取得的是滞后的路径缓存，不能据此交付所问最新路径的排名。')
    errors.extend(context_errors)
    # An incorrectly selected path must not be presented as the requested ranking.
    if context_errors:
        rows = []
    return {'verified': not errors, 'requested_count': requested,
            'returned_count': len(source_rows), 'delivered_count': len(rows),
            'rows': rows, 'context': deepcopy(context), 'errors': list(dict.fromkeys(errors))}


def _requested_metrics(question):
    q = str(question)
    metrics = [key for key, (pattern, *_rest) in _METRICS.items() if re.search(pattern, q)]
    if re.search(r'风暴潮', q) and not {'surge', 'water_level'} & set(metrics):
        metrics.extend(['surge', 'water_level'])
    return metrics


def _historical_extras(question, rows):
    """Keep only asked historical quantities; report and station scopes stay separate."""
    lines, gaps = [], []
    metrics = _requested_metrics(question)
    for row in rows:
        label = str(row['season']) + '年' + str(row['name'])
        report = row.get('historical_report') or {}
        surge = row.get('storm_surge') or {}
        for metric in metrics:
            _pattern, field, title, unit = _METRICS[metric]
            values = []
            if _finite(report.get(field)) and (report.get('source_file') or report.get('source_sha256')):
                station = report.get(field.replace('_m', '_station')) if unit == 'm' else None
                values.append(title + '：' + _number(report[field]) + ' ' + unit
                              + ('，站点：' + str(station) if station else '')
                              + '；历史报告来源：' + str(report.get('source_file') or report['source_sha256']))
            if (field in {'max_surge_m', 'max_water_level_m'} and _finite(surge.get(field))
                    and surge.get('is_estimate') is False and surge.get('record_count', 0) > 0
                    and surge.get('sources')):
                station = surge.get(field.replace('_m', '_station'))
                values.append(title + '：' + _number(surge[field]) + ' ' + unit
                              + ('，站点：' + str(station) if station else '')
                              + '；关联验潮站记录来源：' + '、'.join(map(str, surge['sources']))
                              + '；沿用该记录原始高程基准，与报告口径分列')
            if values:
                lines.extend('- ' + label + '，' + value + '[资料1]。' for value in values)
            else:
                gaps.append(label + '未取得带来源的' + title + '，不以零替代。')
    if re.search(r'(?:历史|各|这些|它们).{0,10}(?:影响|灾害报告|防御报告|灾情)', str(question)) and not metrics:
        for row in rows:
            report = row.get('historical_report') or {}
            source = report.get('source_file')
            summary = report.get('disaster_summary') or report.get('forecast_summary')
            if source and summary:
                lines.append('- ' + str(row['season']) + '年' + str(row['name']) + '历史报告记载：'
                             + str(summary) + '；来源：' + str(source) + '[资料1]。')
            else:
                gaps.append(str(row['season']) + '年' + str(row['name']) + '缺少关联历史灾害报告。')
    # Similarity cannot answer current Zhejiang effects, even when history is available.
    if re.search(r'(?:本次|这次|当前).{0,24}(?:影响|灾损|损失|严重|风险)|(?:浙江|当地).{0,8}(?:将|会|是否).{0,12}(?:影响|受灾|损失|严重)', str(question)):
        gaps.append('本次浙江影响需匹配本次客观风浪预报、适用阈值或正式发布信息，路径相似排名不能代替这些数据。')
    for pattern, label in ((r'登陆(?:时间|地点)|何时登陆|哪里登陆', '历史个例的登陆时间或地点'),
                           (r'降雨|降水|雨量', '匹配个例与时空范围的降水数据'),
                           (r'转移|停航|停课|撤离', '适用预案及正式措施发布记录')):
        if re.search(pattern, str(question)):
            gaps.append('本次路径检索未交付' + label + '。')
    known_extra = r'增水|潮位|水位|浪高|波高|风速|风力|气压|经济损失|灾损|损失|死亡|伤亡|受灾|风暴潮|影响|灾害报告|灾情|算法|原理|依据|评分|相似度|路径|登陆|降雨|降水|雨量|转移|停航|停课|撤离'
    # Do not silently call an arbitrary mixed request complete.  Unrecognised
    # extra interrogatives are retained as a real scope gap, never sent to the
    # method-only model and then forgotten.
    for clause in re.split(r'[。；;？?]', str(question)):
        if (re.search(r'怎么|如何|为何|能否|是否|什么|哪些|多少|何时|哪里', clause)
                and not re.search(known_extra + r'|名称|名字|年份|哪年|几个|几场', clause)):
            gaps.append('本次路径检索尚未交付这一额外问项：' + clause.strip())
    return lines, list(dict.fromkeys(gaps))


def analogue_data_card(question, result, contract=None):
    contract = contract or validate_analogue_delivery(question, result)
    current, context = result.get('current') or {}, contract['context']
    rows = contract['rows']
    lines = ['**历史台风路径相似查询结果**']
    if rows:
        kind = '机构预报路径' if context.get('track_kind') == 'forecast' else '已发生路径'
        lines.append('输入台风：' + _cell(current.get('name', '')) + '（接口标识'
                     + _cell(current.get('sid', '')) + '）；路径类型：' + kind
                     + ('；机构：' + _cell(context['agency']) if context.get('agency') else '') + '[资料1]。')
        if context.get('issued_at'):
            lines.append(('起报时刻：' if context.get('track_kind') == 'forecast' else '最新路径记录时刻：')
                         + str(context['issued_at']) + '[资料1]。')
        lines.append('输入路径时段：' + str(context.get('track_start')) + ' 至 '
                     + str(context.get('track_end')) + '；路径点数：' + str(current.get('track_count'))
                     + '；路径来源：' + _cell(current.get('source')) + '[资料1]。')
        retrieved = (result.get('data_status') or current.get('data_status') or {}).get('retrieved_at')
        if retrieved:
            lines.append('接口资料取得时刻：' + str(retrieved) + '[资料1]。')
        lines.append('按上述输入路径，程序请求' + str(contract['requested_count']) + '个，返回'
                     + str(contract['returned_count']) + '个，可核对交付' + str(len(rows)) + '个[资料1]。')
        lines.extend(['| 排名 | 历史台风 | 年份 | 路径相似度（无量纲） | 路径DTW距离 |',
                      '|---|---|---|---|---|'])
        for row in rows:
            distance = _number(row['track_dtw_km']) + ' km' if _finite(row.get('track_dtw_km')) else '未提供'
            lines.append('| ' + ' | '.join([str(row['rank']), _cell(row['name']), str(row['season']),
                         _number(row['similarity_score']), distance + '[资料1]']) + ' |')
        method = result.get('method') or {}
        if method.get('matching_stage') == 'path_only':
            lines.append('本次为纯路径排名，台风强度不参与评分；相似度是本算法的无量纲评分，不能解释为发生概率或灾损比例[资料1]。')
        else:
            lines.append('评分方法标识：' + _cell(method.get('matching_stage') or '未提供') + '；分数不解释为发生概率[资料1]。')
        if method.get('note'):
            lines.append('候选池与方法说明：' + _cell(method['note']) + '[资料1]。')
        components = method.get('track_components') or {}
        if components and all(_finite(value) for value in components.values()):
            lines.append('路径评分因素与权重：' + '，'.join(_cell(key) + '=' + _number(value)
                         for key, value in components.items()) + '[资料1]。')
        if _finite(method.get('scored_candidate_count')):
            lines.append('本次参与评分的候选数：' + _number(method['scored_candidate_count']) + '[资料1]。')
        lines.append('这些结果用于历史路径类比，不据此判定本次浙江灾害轻重[资料1]。')
    extra_lines, extra_gaps = _historical_extras(question, rows)
    if extra_lines:
        lines.extend(['**所问历史资料**', *extra_lines])
    gaps = list(dict.fromkeys(contract['errors'] + extra_gaps))
    if gaps:
        lines.extend(['**尚未交付的部分**', *['- ' + gap for gap in gaps]])
    # Optional timestamps change the paragraph count.  Keep tables as one
    # Markdown block rather than assuming that the header is always line six.
    blocks, table_lines = [], []
    for line in lines:
        if line.startswith('|'):
            table_lines.append(line)
        else:
            if table_lines:
                blocks.append('\n'.join(table_lines))
                table_lines = []
            blocks.append(line)
    if table_lines:
        blocks.append('\n'.join(table_lines))
    return '\n\n'.join(blocks), gaps


def answer_analogue(runtime, question, result, qa, meta, allow_web):
    """Server data card plus separately scoped, evidence-checked explanation.

    The original question's numerical delivery is *not* entrusted to the model's
    sentence recovery.  Explanation failure cannot erase valid source tuples.
    """
    contract = validate_analogue_delivery(question, result)
    card, gaps = analogue_data_card(question, result, contract)
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=True)
    result_hash = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
    identity = result_hash[:24]
    evidence = card + '\n\n原始程序结果（JSON）：\n' + encoded
    stored = {**deepcopy(result), 'summary': card, 'analogue_delivery': {
        'result_sha256': result_hash, 'contract': contract, 'card': card, 'missing_information': gaps}}
    runtime.store.state('evidence:' + identity, stored)
    hit = {'source_id': 'forecast:' + identity, 'chunk_id': identity,
           'title': '历史台风路径相似程序结果', 'filename': '路径相似检索结果',
           'category': '台风路径类比', 'status': 'approved', 'text': evidence,
           'locator': str(contract['context'].get('issued_at') or result.get('as_of') or ''),
           'url': '/api/forecast/evidence/' + identity, 'retrieval': 'analogue_program_calculation',
           'metadata': {'source_type': 'program_calculation', 'external_allowed': True,
                        'trial': False, 'analogue_program_result': True, 'program_result_sha256': result_hash,
                        'retrieved_at': (result.get('data_status') or {}).get('retrieved_at'),
                        'calculation': {'kind': 'path_analogue', 'result_sha256': result_hash,
                                        'requested_count': contract['requested_count'],
                                        'returned_count': contract['returned_count']}}}
    current = {**meta, 'route': 'business_forecast', 'business_query': 'analogue',
               'business_state': result.get('state'), 'business_trial': False,
               'business_evidence_id': identity, 'tool_data_view': True,
               'answerability': 'full' if contract['verified'] and not gaps else 'partial',
               'missing_information': gaps, 'required_business_facts': [],
               'analogue_delivery_verified': contract['verified'],
               'analogue_delivery': {key: value for key, value in contract.items() if key != 'rows'},
               'analogue_result_sha256': result_hash,
               'generation_used': False, 'used_mode': 'business_data'}
    sources = qa._public_sources(question, [hit])
    # Check the exact rendered tuples as well as the input contract.  This is a
    # deterministic output check, not the language model's self-assessment.
    rendered_rows = ['| ' + ' | '.join([str(row['rank']), _cell(row['name']), str(row['season']),
                     _number(row['similarity_score'])]) + ' |' for row in contract['rows']]
    render_verified = contract['verified'] and all(line in card for line in rendered_rows)
    current['analogue_delivery_verified'] = render_verified
    current['analogue_delivery']['rendered_rows_verified'] = render_verified
    if contract['verified'] and not render_verified:
        current['answerability'] = 'partial'
        current['missing_information'] = gaps + ['程序排名表未完整渲染。']
    answer = {'answer': card, 'rag_sources': sources, 'web_sources': [], 'answer_meta': current}
    if getattr(qa, 'external_enabled', False) and getattr(qa, 'model_caller', None) and render_verified:
        # The model sees one precise explanatory task, not the old rank/year
        # facets which wrongly demanded event_time and deleted the whole table.
        explanation_question = '说明本次历史台风路径相似检索使用哪些路径因素，以及这种排名能够说明和不能说明什么。不要重列个例名单、年份和分数。'
        explanation_meta = {'as_of': meta.get('as_of') or result.get('as_of'),
                            'route': 'analogue_method_explanation', 'business_query': 'analogue',
                            'normalized_question': explanation_question,
                            'current_turn_question': explanation_question,
                            'subquestions': [{'question': explanation_question}],
                            'business_instruction': '只解释资料1中method的实际方法及其边界。程序数据卡已经完整展示排名；不增添个例时刻、实测或现行预警缺项。历史观测缺失不能否定已计算的路径排名。',
                            'business_trial': False, 'tool_data_view': True,
                            'analogue_program_result': True}
        try:
            hits = qa.joint_evidence(explanation_question, explanation_meta, allow_web, pinned=[hit]) if getattr(qa, 'crag_enabled', False) else [hit]
            organized = qa._organized_answer(explanation_question, qa.model_name, hits, explanation_meta)
            generated_meta = organized.get('answer_meta') or {}
            # Keep the model's *scoped* validation, never use it as proof of a
            # program tuple.  Unresolved explanation remains out of the answer.
            explanation_text = str(organized.get('answer') or '').strip()
            explanation_sources = organized.get('rag_sources') or sources
            source_binding = bool(explanation_sources and explanation_sources[0].get('source_id') == hit['source_id'])
            core_repeated = (any(str(row['name']).lower() in explanation_text.lower() for row in contract['rows'])
                             or bool(re.search(r'缺少.{0,18}(?:名称|年份|名单|相似度)|未(?:取得|提供|找到).{0,18}(?:名称|年份|名单|相似度)', explanation_text)))
            if (source_binding and not core_repeated and generated_meta.get('answerability') == 'full'
                    and generated_meta.get('used_mode') != 'model_error'
                    and not generated_meta.get('missing_information')
                    and not generated_meta.get('business_validation_error')
                    and explanation_text):
                answer['answer'] += '\n\n**方法解读**\n\n' + organized['answer']
                answer['rag_sources'] = explanation_sources
                answer['web_sources'] = organized.get('web_sources') or []
                current.update(generation_used=True, used_mode='llm_with_verified_analogue_card',
                               analogue_language_state='validated',
                               analogue_explanation_validation='full')
            else:
                current.update(analogue_language_state='unresolved_explanation_data_preserved',
                               analogue_explanation_validation=generated_meta.get('answerability', 'unknown'))
        except Exception as exc:
            current.update(analogue_language_state='unavailable_explanation_data_preserved',
                           analogue_explanation_error=type(exc).__name__)
    return answer
