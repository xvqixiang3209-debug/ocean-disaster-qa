"""Audit preserved responses without generating or replacing any answer."""
from collections import Counter
import argparse
import hashlib,json,re
from datetime import datetime,timedelta
from decimal import Decimal
from unittest.mock import patch
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from forecast_runtime.answer_facts import delivery_facts
from forecast_runtime.service import period
from forecast_runtime.storage import BEIJING
from marine_kb.business_validation import validate_business_delivery
from marine_kb.quantities import quantities,UNITS

RUN=ROOT/'tmp/qa_full160_phase161_20261018_final'
CHECKED_KINDS={'computed','coverage','marine_product','analogue','elevation','population',
               'impact_scope','typhoon_scope','historical_metric','comparison','comparison_scope'}
MEASUREMENT_KINDS={'computed','marine_product','elevation','population','historical_metric','comparison'}


def _label_match(line,label):
    match=re.fullmatch(r'(\d{4})年(.+)',label)
    return re.search(re.escape(match[1])+r'年.{0,6}'+re.escape(match[2]),line) if match else re.search(re.escape(label),line)


def _operand_present(segment,value,unit):
    if unit not in UNITS:return False
    category,factor=UNITS[unit]
    expected=Decimal(str(value))*factor
    return any(UNITS[u][0]==category and abs(number*UNITS[u][1]-expected)<=Decimal('0.00000001')*factor
               for number,u,_,_ in quantities(segment))


def _comparison_operands_present(answer,fact):
    """A correct cited delta alone does not deliver both compared measurements."""
    from marine_kb.business_validation import _comparison_present
    for line in answer.splitlines():
        if not _comparison_present(line,fact):continue
        left=_label_match(line,fact['left']);right=_label_match(line,fact['right'])
        if not left or not right:continue
        left_end=right.start() if right.start()>left.start() else len(line)
        right_end=left.start() if left.start()>right.start() else len(line)
        if (_operand_present(line[left.end():left_end],fact['left_value'],fact['unit'])
                and _operand_present(line[right.end():right_end],fact['right_value'],fact['unit'])):return True
    return False


def audit_typed_facts(answer,facts,citations,returned):
    rows=[]
    for index,fact in enumerate(facts,1):
        required=fact.get('required') is not False
        row={'index':index,'kind':fact.get('kind'),'required':required,
             'metric':fact.get('metric'),'location':fact.get('location'),
             'event_name':fact.get('event_name'),'season':fact.get('season'),
             'left':fact.get('left'),'right':fact.get('right'),
             'left_value':fact.get('left_value'),'right_value':fact.get('right_value'),'difference':fact.get('difference'),
             'value':fact.get('value'),'unit':fact.get('unit'),'check':'optional_not_required'}
        if required:
            try:
                if fact.get('kind') not in CHECKED_KINDS:raise ValueError('unsupported_required_fact_kind')
                expected_citation=fact.get('citation',1)
                if expected_citation not in citations or expected_citation not in returned:
                    raise ValueError('required_fact_missing_returned_citation')
                validate_business_delivery(answer,{'required_business_facts':[fact]},citations)
                if fact['kind']=='comparison':
                    difference=Decimal(str(fact['left_value']))-Decimal(str(fact['right_value']))
                    if abs(difference-Decimal(str(fact['difference'])))>Decimal('0.00050001'):
                        raise ValueError('comparison_record_arithmetic_mismatch')
                    if not _comparison_operands_present(answer,fact):
                        raise ValueError('comparison_operand_values_not_delivered_with_objects')
                row['check']='pass'
            except (ValueError,TypeError,KeyError) as exc:
                row['check']='fail:'+str(exc)
        rows.append(row)
    return rows


def plain_source_mentions(answer):
    """Read prose source IDs separately; these never satisfy required citations."""
    prose=re.sub(r'\[资料\d+\]', ' ', answer)
    found=set()
    pattern=r'资料\s*(\d+)(?!\d|年|份|条|页|个)((?:\s*(?:[、,，]|至|到|[-—–])\s*(?:资料\s*)?\d+(?!\d|年|份|条|页|个))*)'
    for match in re.finditer(pattern,prose):
        previous=int(match[1]);found.add(previous)
        for item in re.finditer(r'([、,，]|至|到|[-—–])\s*(?:资料\s*)?(\d+)',match[2]):
            current=int(item[2])
            if item[1] in {'至','到','-','—','–'} and previous<=current and current-previous<=100:
                found.update(range(previous,current+1))
            else:
                found.add(current)
            previous=current
    return sorted(found)


def main():
    global RUN
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,default=RUN,help='Preserved test-run directory')
    parser.add_argument('--output',type=Path,help='Optional report path; preserved input responses are never modified')
    args=parser.parse_args();RUN=args.run.resolve()
    records=json.loads((RUN/'answers.json').read_text(encoding='utf8'))['items']
    rows=[]
    for r in records:
        reply=r.get('reply') or {};answer=reply.get('answer') or '';evidence=r.get('business_evidence') or {}
        citations={int(n) for n in re.findall(r'\[资料(\d+)\]',answer)}
        returned={s.get('citation_id') for s in reply.get('sources',[])}
        plain_mentions=plain_source_mentions(answer)
        row={'id':r['id'],'mode':reply.get('mode'),'business_kind':evidence.get('kind'),
             'business_state':reply.get('business_state'),'validation':reply.get('validation'),
             'answer_sha256':hashlib.sha256(answer.encode()).hexdigest(),
             'missing_returned_citation_ids':sorted(citations-returned),
             'plain_source_mentions':plain_mentions,
             'missing_mentioned_source_ids':sorted(set(plain_mentions)-returned),
             'typed_delivery_check':'not_applicable','forecast_period_check':[], 'typhoon_snapshot_cutoff_check':[]}
        if evidence:
            facts=delivery_facts(evidence)
            row['required_typed_fact_count']=sum(f.get('required') is not False for f in facts)
            row['required_measurement_fact_count']=sum(f.get('required') is not False and f.get('kind') in MEASUREMENT_KINDS for f in facts)
            row['required_fact_counts_by_kind']=dict(Counter(f.get('kind') for f in facts if f.get('required') is not False))
            row['typed_fact_checks']=audit_typed_facts(answer,facts,citations,returned)
            try:
                validate_business_delivery(answer,{'required_business_facts':facts,
                    'official_activity_rules':evidence.get('activity_rule_mode')=='official_catalog'},citations)
                row['typed_delivery_check']='pass' if facts else 'no_typed_facts'
            except ValueError as exc:row['typed_delivery_check']='fail:'+str(exc)
            individual_failures=[check for check in row['typed_fact_checks'] if check['check'].startswith('fail:')]
            if individual_failures:row['typed_delivery_check']=individual_failures[0]['check']
            def scan(value,question):
                if value.get('kind')=='bundle':
                    tasks={t.get('id'):t.get('question') for t in (value.get('task_plan') or {}).get('tasks',[])}
                    for item in value.get('items',[]):
                        scan(item,tasks.get(item.get('task_id')) or question)
                elif value.get('kind')=='typhoons' and value.get('requested_as_of'):
                    def time_value(raw):
                        value=datetime.fromisoformat(raw)
                        return value if value.tzinfo else value.replace(tzinfo=BEIJING)
                    cutoff=time_value(value['requested_as_of'])
                    captured=time_value(value['retrieved_at']) if value.get('retrieved_at') else None
                    points=[point for storm in value.get('list',[]) for point in storm.get('points',[])]
                    observed=[time_value(point['time']) for point in points if point.get('time')]
                    row['typhoon_snapshot_cutoff_check'].append({'requested_as_of':value['requested_as_of'],
                        'retrieved_at':value.get('retrieved_at'),'historical_snapshot':value.get('historical_snapshot'),
                        'retrieval_before_cutoff':captured<=cutoff if captured else None,
                        'all_observed_points_before_cutoff':all(t<=cutoff for t in observed),
                        'forecast_valid_times_may_be_future':True})
                elif value.get('kind') in {'grids','coastal','tide','activity'}:
                    with patch('forecast_runtime.service.local_now',return_value=datetime.fromisoformat(r['started_at'])):
                        start,end=period(question)
                    period_basis='explicit_or_standard_query_period'
                    cutoff=re.search(r'截至\s*(20\d{2})年(\d{1,2})月(\d{1,2})日(?!\d|[零一二三四五六七八九十]+时)',question)
                    if cutoff and re.search(r'影响研判报告|影响研判专报|台风影响报告',question) and not re.search(r'未来|(?<!截)至|到|小时|日[0-9零一二三四五六七八九十]*时',question):
                        # An as-of publication cutoff is not a requested past
                        # forecast interval. Verify the declared default brief
                        # horizon independently, keeping explicit ranges strict.
                        start=datetime(*map(int,cutoff.groups()),tzinfo=BEIJING)+timedelta(days=1)
                        end=start+timedelta(hours=72)
                        period_basis='default_brief_72_hours_after_date_cutoff'
                    row['forecast_period_check'].append({'kind':value['kind'],'requested_start':start.isoformat(),
                        'requested_end':end.isoformat(),'actual_start':value.get('start'),'actual_end':value.get('end'),
                        'period_basis':period_basis,'match':value.get('start')==start.isoformat() and value.get('end')==end.isoformat()})
            scan(evidence,r['question'])
        rows.append(row)
    result={'note':'分别校验保存记录的方括号引用编号与正文提及的资料编号（正文提及不满足正规引用要求）、逐项程序事实交付和时段一致性；历史比较同时检查两侧对象绑定的数值和差值，未识别的必需事实类型不判通过。不代替实况误差检验、逐题内容复核或专家验收。',
            'expert_accepted':False,'input_answers_sha256':hashlib.sha256((RUN/'answers.json').read_bytes()).hexdigest(),
            'audit_runtime_hashes':{str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in
                [Path(__file__),ROOT/'forecast_runtime/answer_facts.py',ROOT/'marine_kb/business_validation.py',ROOT/'marine_kb/quantities.py']},
            'question_count':len(rows),'typed_delivery_counts':dict(Counter(x['typed_delivery_check'] for x in rows)),
            'required_fact_counts_by_kind':dict(sum((Counter(x.get('required_fact_counts_by_kind',{})) for x in rows),Counter())),
            'required_measurement_fact_count':sum(row.get('required_measurement_fact_count',0) for row in rows),
            'scope_or_count_only_delivery_ids':[row['id'] for row in rows if row.get('required_typed_fact_count') and not row.get('required_measurement_fact_count')],
            'typed_fact_check_counts':dict(Counter(f['check'] for x in rows for f in x.get('typed_fact_checks',[]))),
            'validation_error_counts':dict(Counter(x['validation']['error'] for x in rows if x.get('validation') and x['validation'].get('error'))),
            'computed_table_fallback_ids':[x['id'] for x in rows if (x.get('validation') or {}).get('computed_table_fallback')],
            'forecast_period_mismatch_ids':[x['id'] for x in rows if any(not p['match'] for p in x['forecast_period_check'])],
            'citation_id_mismatch_ids':[x['id'] for x in rows if x['missing_returned_citation_ids']],
            'source_mention_mismatch_ids':[x['id'] for x in rows if x['missing_mentioned_source_ids']],
            'typhoon_snapshot_cutoff_mismatch_ids':[x['id'] for x in rows if any(
                c['retrieval_before_cutoff'] is False or not c['all_observed_points_before_cutoff']
                for c in x['typhoon_snapshot_cutoff_check'])],
            'items':rows}
    output=args.output.resolve() if args.output else RUN/'computation_audit.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k!='items'},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
