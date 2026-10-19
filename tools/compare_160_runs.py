"""Compare identical live question runs without treating generation as accuracy."""
import argparse
import json
import re
from collections import Counter
from pathlib import Path


def load_run(directory):
    report=json.loads((directory/'answers.json').read_text(encoding='utf-8'))
    rows={r['id']:r for r in report['items']}
    if len(rows)!=160 or len(report['items'])!=160:
        raise ValueError('Expected 160 distinct completed questions')
    return report,rows


def metrics(rows):
    metas=[r['result'].get('answer_meta',{}) for r in rows.values()]
    return {
        'question_count':len(rows),
        'mode_counts':dict(Counter(m.get('used_mode','unknown') for m in metas)),
        'model_reported_answerability':dict(Counter(m.get('answerability','unspecified') for m in metas)),
        'grounded_answerability_before_general_supplement':dict(Counter(
            m.get('grounded_answerability',m.get('answerability','unspecified')) for m in metas)),
        'general_explanations_used':sum(bool(m.get('general_knowledge_used')) for m in metas),
        'general_explanation_without_cited_sources':sum(
            bool(r['result'].get('answer_meta',{}).get('general_knowledge_used'))
            and not (r['result'].get('rag_sources') or r['result'].get('web_sources')) for r in rows.values()),
        'web_search_executed':sum(bool(m.get('web_search_executed')) for m in metas),
        'official_web_source_returned':sum(bool(r['result'].get('web_sources')) for r in rows.values()),
        'business_query_ready':sum(m.get('business_state')=='ready' for m in metas),
        'expert_accuracy_accepted':False,
    }


def consistency_issues(rows):
    issues=[]
    for key,row in rows.items():
        result=row['result'];meta=result.get('answer_meta',{});answer=result.get('answer','')
        ids={str(s.get('citation_id')) for s in result.get('rag_sources',[])+result.get('web_sources',[])}
        unknown=set(re.findall(r'\[资料\s*(\d+)\]',answer))-ids
        if unknown:
            issues.append({'id':key,'issue':'unknown_citation_ids','values':sorted(unknown)})
        if meta.get('general_knowledge_used') and (
                '通用解释与处理思路' not in answer or '未作为已核验的当前业务事实' not in answer):
            issues.append({'id':key,'issue':'general_explanation_label_missing'})
    return issues


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('before',type=Path)
    parser.add_argument('after',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    before,b=load_run(args.before);after,a=load_run(args.after)
    if b.keys()!=a.keys() or any(b[k]['question']!=a[k]['question'] for k in b):
        raise ValueError('Question identity mismatch')
    if before.get('manifest_sha256')!=after.get('manifest_sha256'):
        raise ValueError('Scenario manifest mismatch')
    rows=[]
    for key,r in a.items():
        old=b[key]['result'].get('answer_meta',{});meta=r['result'].get('answer_meta',{})
        rows.append({'id':key,'question':r['question'],
            'before_mode':old.get('used_mode'),'after_mode':meta.get('used_mode'),
            'after_answerability':meta.get('answerability','unspecified'),
            'grounded_answerability':meta.get('grounded_answerability',meta.get('answerability','unspecified')),
            'general_explanation_used':bool(meta.get('general_knowledge_used')),
            'business_state':meta.get('business_state'),
            'missing_information':meta.get('missing_information',[]),
            'subquestion_coverage':meta.get('subquestion_coverage',[]),
            'original_data_dependencies':r.get('dependencies','')})
    report={'description':'同题真实接口运行结果比较；生成成功与模型自报覆盖程度不等于专家正确率。',
        'manifest_sha256':after['manifest_sha256'],
        'before':metrics(b),'after':metrics(a),
        'recovered_generation_failures':[k for k in b if b[k]['result'].get('answer_meta',{}).get('used_mode')=='model_error'
            and a[k]['result'].get('answer_meta',{}).get('used_mode')=='llm'],
        'remaining_non_model_replies':[r['id'] for r in rows if r['after_mode']!='llm'],
        'automatic_consistency_issues':consistency_issues(a),
        'items':rows}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='items'},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
