"""Read-only workbench for a human audit of recorded v2 answer delivery.

No SDK, network client or production service is imported. This tool collects
actual drafts, selected source bodies and decisions for reading; it does not
manufacture judgments or treat model labels as a quality score. Raw workbench
output belongs in app/tmp, alongside the private cassettes, not on GitHub.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

APP = Path(__file__).resolve().parents[1]
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from marine_kb.model_json import model_json
from marine_kb.replay import _unwire


RULES = {
    'method': '人工阅读实际回答、原始生成稿、typed/coverage原始判定及其选中正文；不是业务专家验收。',
    'forbidden_shortcuts': [
        '不以HTTP200、引用数量、模型full标签、单元测试通过代替回答完整或事实正确。',
        '不把旧judge意见套到新草稿；新判定缺失、来源缺失或模型失败均标明未核实。',
        '不把多次重复试验当成不同业务问题；不把网页时变差异全部归因于代码。',
        '不凭已知常识补造来源支持；明确区分程序硬规则与人工语义核对。',
    ],
    'retention': [
        '把实际原子、所选正文、保留/删除决策逐项对齐；复合句只删错配部分，核查是否仍丢掉原文支持的兄弟分句。',
        '子问归属根据实际对象、标题和代词承接；冬春程序不能挂到过渡期，申请人和审批人不能混写。',
        '文件身份可由精确DocCard字段支持，金额、职责、条件、流程仍须正文；文档卡不能替代条款。',
        '归纳核对全部必要前提及关系、否定、条件，不能仅因名字重复而接受；过度严格语法误删也需记录。',
        '只保留日期、来源目录、笼统待核对等属于没有实质交付；记录为缺项/退化，不算完整回答。',
    ],
    'gaps': [
        '通用步骤中的地名通常是背景，不要求当地专用规程；渠道题不要求当前是否已经发布预警。',
        '蚊虫多是否证明暴发是关系题；不能据此说当地没有疫情，也不为解释关系强加当前疫情清单缺项。',
        '实际个人赔付、救助资格、具体数值、当日状态仍需个人核定/业务数据，不因答案变短而删真缺项。',
        '区分答案已交付而标签仍报缺项，和答案确实缺正文证据；分别记录，不混成缺数据。',
    ],
    'family_deliverables': {
        'M1': ['对蚊媒传播风险的有条件解释，病种/媒介对应正确', '清积水孳生地和个人防叮咬措施，有所引正文'],
        'M2': ['花盆/容器/庭院积水处置', '个人和室内防叮咬措施', '不臆造药物浓度或当地疫情'],
        'M3': ['不能由蚊虫多直接推断当地已暴发登革热', '有据防蚊措施', '不把缺疫情实况当作解释关系不能交付'],
        'C1': ['气象部门官方台风预警渠道', '海洋部门官方海洋预警渠道', '渠道入口与当前预警状态区分'],
        'C2': ['宁波可用气象台风预警入口', '宁波可用海洋海浪警报入口', '两个发布体系分别回答'],
        'C3': ['台风路径不能替代正式台风预警的区别结论', '路径信息查询渠道', '正式气象预警查询渠道'],
        'A1': ['网箱加固措施', '池塘加固措施', '赔付依据和个人保单/核定条件，不能保证赔付'],
        'A2': ['缆绳检查加固', '框架检查加固', '网衣检查加固', '实质技术步骤，不以温州专用规程缺失否定通用手册'],
        'A3': ['池塘堤埂检查加固', '进排水设施检查加固', '备用设备检查准备'],
        'G1': ['政府救助类别及适用情境', '本人能否获助需地方或个人核定的范围', '不以保险替代政府救助'],
        'G2': ['政府倒损住房救助先向村居/政府体系申请，保险另行理赔', '申请、评议审核及确定/发放的程序', '地方具体材料/窗口缺项不得吞掉已取得国家程序'],
        'G3': ['三项制度并非一回事的结论及依据', '过渡期生活救助文件身份和正文依据', '冬春救助文件身份和正文依据', '保险赔付法律/合同或方案依据', '程序各归其对象，不额外承诺可同时领取或必赔'],
    },
    'review_schema': {
        'review_status': ['reviewed', 'unverifiable'],
        'subquestion_delivery': ['complete', 'partial', 'missing', 'unverifiable'],
        'overall_outcome': ['improved', 'regressed', 'mixed', 'unchanged', 'unverifiable'],
        'finding_codes': ['supported_clause_deleted','unsupported_clause_retained','facet_misattributed',
                          'requested_output_missing','unasked_gap_retained','real_gap_removed',
                          'label_delivery_mismatch','date_only_fragment','judge_unavailable',
                          'source_unavailable','comparison_sources_changed'],
    },
}


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def model_steps(path):
    rows = []
    errors = []
    for line_no, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        try:
            row = json.loads(line)
        except ValueError:
            errors.append({'line':line_no,'error':'incomplete_or_invalid_jsonl'})
            continue
        if row.get('record_type') != 'interaction' or row.get('kind') != 'model':
            continue
        request = _unwire(row['request'])
        args = request.get('args') or []
        payload = {}
        if len(args) > 2:
            try:payload = json.loads(args[2])
            except (ValueError, TypeError):pass
        response = None
        if row.get('outcome') == 'return':
            wire = _unwire(row['response'])
            raw = wire[0] if isinstance(wire, tuple) else wire
            try:response = model_json(raw)
            except (ValueError, TypeError):response = {'unparsed_actual_response':raw}
        stage = ('typed_judge' if 'atoms' in payload else
                 'legacy_judge' if 'claims' in payload else
                 'coverage_review' if 'review_targets' in payload else
                 'facet_regeneration' if 'frozen_siblings' in payload else
                 'generation' if isinstance(response, dict) and 'answer' in response else
                 'retrieval_review' if isinstance(response,dict) and 'evaluations' in response else
                 'question_normalization' if isinstance(response,dict) and 'normalized_question' in response else 'other')
        rows.append({'seq':row['seq'],'stage':stage,'outcome':row.get('outcome'),
                     'payload':payload,'response':response,'request_sha256':row.get('request_sha256'),
                     'response_sha256':row.get('response_sha256')})
    return rows, errors


def build_workbench(run_dir):
    run_dir = Path(run_dir).resolve()
    cases = []
    for path in sorted(run_dir.glob('*.result.json')):
        actual = json.loads(path.read_text(encoding='utf-8'))
        case = actual.get('case') or {}
        case_id = case.get('case_id') or path.name.removesuffix('.result.json')
        reply = actual.get('reply') or {}
        cassette = path.with_name(case_id+'.cassette.jsonl')
        steps, errors = model_steps(cassette) if cassette.exists() else ([], [{'error':'cassette_missing'}])
        phase2 = (reply.get('evidence_diagnostics') or {}).get('phase2') or {}
        case_base = case_id.split('_extra_')[0]
        cases.append({'case_id':case_id, 'question':case.get('question'), 'http_status':actual.get('http_status'),
                      'actual_answer':reply.get('answer'), 'answer_sha256':_sha(str(reply.get('answer') or '')),
                      'model_label_not_human_result':reply.get('answerability'),
                      'actual_missing_information':reply.get('missing_information'),
                      'actual_subquestion_coverage':reply.get('subquestion_coverage'),
                      'expected_reading_points':RULES['family_deliverables'].get(case_base, []),
                      'phase2_audit':phase2, 'steps':steps,'cassette_errors':errors,
                      'human_review_status':'unverifiable','human_review_not_yet_done':True})
    return {'schema_version':1,'run_dir':str(run_dir),'contains_private_raw_data':True,
            'notice':'原始工作台，仅用于本地人工审读；全部review_status初始为未核实，不是评测结果。',
            'rules':RULES,'cases':cases,'new_model_calls':0,'new_network_calls':0,'new_service_calls':0}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    root=(APP/'tmp').resolve()
    output=args.output.resolve()
    if root not in output.parents:
        raise ValueError('raw_review_workbench_must_remain_in_app_tmp')
    result=build_workbench(args.run_dir)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(output),'recorded_cases':len(result['cases']),'human_reviews_completed':0,
                      'new_model_calls':0,'new_network_calls':0},ensure_ascii=False))


if __name__=='__main__':main()
