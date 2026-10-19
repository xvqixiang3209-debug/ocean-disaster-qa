"""Validate and combine preserved HTTP replies with separate AI assessments."""
from collections import Counter,defaultdict
from datetime import datetime,timezone,timedelta
import hashlib,json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'tmp/qa_full160_phase161_20261018_final'
LABELS=('完整回答','部分回答','未回答')
GAPS={'数据未接入','资料已有但未调用','证据时效或版本未核','关键数值口径未核','问题输入仍不足',
      '程序路由或时段错误','生成或请求失败','答案事实或引用需核对'}


def main():
    load=lambda name:json.loads((RUN/name).read_text(encoding='utf8'))
    manifest=load('manifest.json');answers=load('answers.json');identity=load('run_identity.json')
    expected={r['id']:r for r in manifest['items']}
    actual={r['id']:r for r in answers['items']}
    if len(answers['items'])!=160 or len(actual)!=160 or set(actual)!=set(expected):raise ValueError('160 response IDs do not match')
    reviews={}
    for path in (RUN/'reviews').glob('*.json'):
        value=json.loads(path.read_text(encoding='utf8'))
        if value['id'] in reviews:raise ValueError('Duplicate review')
        reviews[value['id']]=value
    if set(reviews)!=set(expected):raise ValueError('Missing or extra reviews: '+str(set(expected)^set(reviews)))
    rows=[]
    for item in manifest['items']:
        row=actual[item['id']];review=reviews[item['id']]
        if row['original_question']!=item['original_question'] or row['question']!=item['question']:raise ValueError('Changed question')
        if row['run_id']!=identity['run_id'] or row['base_url']!=identity['base_url']:raise ValueError('Mixed runtime responses')
        if review['label'] not in LABELS:raise ValueError('Invalid label')
        if review.get('reviewed_answer_sha256')!=hashlib.sha256(row.get('reply',{}).get('answer','').encode()).hexdigest():
            raise ValueError('Review does not match this preserved answer '+item['id'])
        for key in ('required_points','answered_points','missing_points','gap_types','specific_gaps','evidence_checks'):
            if not isinstance(review.get(key),list):raise ValueError('Invalid review '+item['id']+' '+key)
        if set(review['gap_types'])-GAPS:raise ValueError('Unknown gap categories '+item['id']+': '+str(review['gap_types']))
        if review['label']=='完整回答' and any(review[key] for key in ('missing_points','gap_types','specific_gaps')):
            raise ValueError('Complete answer has unresolved gaps '+item['id'])
        if not review.get('reason') or review.get('expert_accepted') is not False:raise ValueError('Unclear assessment provenance')
        for relative,digest in identity['runtime_source_sha256'].items():
            if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()!=digest:raise ValueError('Application changed during run '+relative)
        rows.append({**row,'assessment':review})
    counts=Counter(r['assessment']['label'] for r in rows)
    by_category=defaultdict(Counter)
    for row in rows:by_category[row['category']][row['assessment']['label']]+=1
    summary={'created_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),
        'base_url':identity['base_url'],'run_id':identity['run_id'],'question_count':160,
        'first_request_at':min(r['started_at'] for r in rows),'last_request_at':max(r['completed_at'] for r in rows),
        'http_200':sum(r['http_status']==200 for r in rows),'actual_answer_count':sum(bool(r.get('reply',{}).get('answer')) for r in rows),
        'mode_counts':dict(Counter(r.get('reply',{}).get('mode','unknown') for r in rows)),
        'answer_kind_counts':dict(Counter(r.get('reply',{}).get('answer_kind','unknown') for r in rows)),
        'label_counts':{key:counts[key] for key in LABELS},
        'label_ids':{key:[r['id'] for r in rows if r['assessment']['label']==key] for key in LABELS},
        'gap_type_counts':dict(Counter(g for r in rows for g in set(r['assessment']['gap_types']))),
        'category_counts':{name:{key:values[key] for key in LABELS} for name,values in by_category.items()},
        'assessment_method':'逐题辅助AI独立内容复核，非业务专家验收；完整性按实际提问及返回证据，不按HTTP成功或模型标签推定。',
        'expert_accepted':False,'runtime_sources_unchanged':True}
    for name,value in [('review_summary.json',summary),('reviewed_answers.json',{
        'summary':summary,'manifest':manifest,'run_identity':identity,'items':rows,'expert_accepted':False})]:
        (RUN/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({key:summary[key] for key in ('question_count','http_200','actual_answer_count','label_counts')},ensure_ascii=False))


if __name__=='__main__':main()
