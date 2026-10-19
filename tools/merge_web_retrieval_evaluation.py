"""Merge blind paired judgments and observable retrieval/runtime metrics."""
import argparse
from collections import Counter,defaultdict
import hashlib,json,re
from pathlib import Path

LABELS=('完整回答','部分回答','未回答')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--baseline',type=Path,required=True);args=parser.parse_args()
    run=args.run;old=args.baseline
    load=lambda p:json.loads(p.read_text(encoding='utf8'))
    raw=load(run/'answers.json');manifest=load(run/'manifest.json');identity=load(run/'run_identity.json')
    previous=load(old/'answers.json');before_map={r['id']:r for r in previous['items']}
    assert len(raw['items'])==len(before_map)==160
    reviews=run/'reviews';reviews.mkdir(exist_ok=True)
    rows=[];counts={'before':Counter(),'after':Counter()};by_category=defaultdict(lambda:{'before':Counter(),'after':Counter()})
    winners=Counter();transitions=Counter();changes=[]
    for actual in raw['items']:
        ident=actual['id'];pair=load(run/'paired_reviews'/(ident+'.json'))
        versions={letter:('改前' if side=='before' else '改后') for letter,side in zip(('A','B'),pair['order'])}
        def human(value):
            if isinstance(value,str):return re.sub(r'(?<![A-Za-z])([AB])(?![A-Za-z])',lambda m:versions[m[1]],value)
            if isinstance(value,list):return [human(v) for v in value]
            if isinstance(value,dict):return {k:human(v) for k,v in value.items()}
            return value
        assert actual['question']==before_map[ident]['question']
        for side,record in (('before',before_map[ident]),('after',actual)):
            assert pair[side+'_answer_sha256']==hashlib.sha256(record['reply'].get('answer','').encode()).hexdigest()
            counts[side][pair[side]['label']]+=1
            by_category[actual['category']][side][pair[side]['label']]+=1
        judged=human(pair['after']);old_label=pair['before']['label']
        review={'id':ident,'label':judged['label'],'required_points':pair['required_points'],
            'reviewed_answer_sha256':pair['after_answer_sha256'],
            'answered_points':[p['point']+'：'+p['reason'] for p in judged['required_point_results'] if p['status']=='answered'],
            'missing_points':judged['missing_points'],'gap_types':judged['gap_types'],'specific_gaps':judged['missing_points'],
            'reason':judged['reason'],'evidence_checks':judged['evidence_limits'],
            'review_method':pair['review_method'],'expert_accepted':False,
            'previous_label':old_label,'change_note':human(pair['comparison_reason']),
            'required_point_results':judged['required_point_results']}
        (reviews/(ident+'.json')).write_text(json.dumps(review,ensure_ascii=False,indent=2),encoding='utf8')
        rows.append({**actual,'assessment':review});winners[pair['winner']]+=1
        transitions[old_label+' → '+judged['label']]+=1
        if old_label!=judged['label']:
            changes.append({'id':ident,'before':old_label,'after':judged['label'],'reason':human(pair['comparison_reason'])})
    app=Path(__file__).resolve().parents[1]
    for name,digest in identity['runtime_source_sha256'].items():
        assert hashlib.sha256((app/name).read_bytes()).hexdigest()==digest, name
    def observed(items):
        sources=[s for row in items for s in row['reply'].get('sources',[])]
        web=[s for s in sources if s.get('source_type')=='public_web' or (s.get('metadata') or {}).get('source_type')=='public_web']
        applications=[(s.get('metadata') or {}).get('applicability') or {} for s in web]
        return {'http_200':sum(row.get('http_status')==200 for row in items),
            'modes':dict(Counter(row['reply'].get('mode') for row in items)),
            'returned_sources':len(sources),'returned_web_sources':len(web),
            'questions_using_web':sum(any(s in web for s in row['reply'].get('sources',[])) for row in items),
            'web_applicability_labels':dict(Counter(a.get('use','unlabelled') for a in applications)),
            'web_temporal_labels':dict(Counter(a.get('temporal','unlabelled') for a in applications)),
            'computed_fallback_ids':[row['id'] for row in items if (row['reply'].get('validation') or {}).get('computed_table_fallback')],
            'mean_seconds':round(sum(row['elapsed_seconds'] for row in items)/len(items),2)}
    summary={'question_count':160,'label_counts':{k:counts['after'][k] for k in LABELS},
        'comparison_counts':{side:{k:counts[side][k] for k in LABELS} for side in counts},
        'category_comparison':{category:{side:dict(values[side]) for side in values} for category,values in by_category.items()},
        'pair_preference':dict(winners),'transitions':dict(transitions),'changed':changes,
        'observed_metrics':{'before':observed(previous['items']),'after':observed(raw['items'])},
        'original_previous_review_counts':load(old/'review_summary.json')['label_counts'],
        'assessment_method':'同一DeepSeek评阅设置逐题盲评两份真实回答，随机A/B顺序、相同核心要求；辅助AI复核，非业务专家验收。',
        'comparison_limitations':['外部网站搜索结果、页面和网络可用性可能随实测时间变化；仅一轮，不是统计显著性结论。',
            '改前和改后按相同评阅口径重新复核；历史报告的辅助AI分类另列，不能混算。',
            '返回的证据适用性标签由本轮程序判断，不代表专家核验网页全文或业务结论。'],
        'runtime_sources_unchanged':True,'expert_accepted':False}
    for name,value in [('review_summary.json',summary),('reviewed_answers.json',{'summary':summary,'manifest':manifest,'run_identity':identity,'items':rows,'expert_accepted':False})]:
        (run/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
