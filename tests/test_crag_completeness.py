"""Regression tests for failures found in real CRAG family questions."""
import json,sqlite3,tempfile,unittest
from contextlib import closing
from pathlib import Path
from datetime import datetime,timedelta
from unittest.mock import patch
import h5py,numpy as np
from forecast_runtime.service import ForecastRuntime
from forecast_runtime.grids import inspect_grid
from forecast_runtime.storage import BEIJING
from forecast_runtime.official_warnings import warning_question
from forecast_runtime.routing import numeric_request
from forecast_runtime.answer_facts import delivery_facts
from marine_kb.history_statistics import monthly,matches
from marine_kb.business_validation import validate_business_delivery
from marine_kb.claim_grounding import draft_claims,review_claim_batches

class CompletenessTests(unittest.TestCase):
 def test_long_answer_attribution_checks_every_claim_in_bounded_batches(self):
  raw=json.dumps({'answer':'\n'.join(f'第{i}项陈述提供对应资料中的事实。' for i in range(60))})
  _,claims=draft_claims(raw);sizes=[]
  def check(packet):
   sizes.append(len(packet['claims']))
   return json.dumps({'claims':[{'id':r['id'],'status':'unsupported','refs':[]} for r in packet['claims']]})
  result=review_claim_batches({'claims':claims,'evidence':[]},check)
  self.assertEqual(sizes,[24,24,12]);self.assertEqual([r['id'] for r in result['claims']],list(range(1,61)))
  with self.assertRaisesRegex(ValueError,'incomplete_batch'):
   review_claim_batches({'claims':claims},lambda _:json.dumps({'claims':[{'id':1}]*24}))
  with self.assertRaisesRegex(ValueError,'claim_limit'):
   draft_claims(json.dumps({'answer':'\n'.join('这是一段足够长的事实陈述。' for _ in range(97))}))
 def test_numeric_clause_before_date_survives_definition_subquestion(self):
  self.assertTrue(numeric_request('查询2026年10月18日08时至18时东经122.4北纬29.9海域浪高和波向；请区分波向角度与来向定义。'))
  self.assertFalse(numeric_request('2026年版风暴潮定义是什么意思？'))
 def test_method_and_channels_do_not_query_released_alerts(self):
  for q in ['浙江海洋灾害预警有哪些官方查询渠道？','海浪预警专报怎么写，有没有模板？','风暴潮预警从哪里收到？']:
   self.assertFalse(warning_question(q),q)
  self.assertTrue(warning_question('截至2026年10月17日浙江已发布哪些海浪预警，官方查询渠道是什么？'))

 def test_archive_counts_are_monthly_unique_not_landfall(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'h.sqlite3'
   with closing(sqlite3.connect(p)) as db,db:
    db.executescript('CREATE TABLE storms(start_time TEXT,end_time TEXT);CREATE TABLE track_points(time TEXT,sid TEXT);CREATE TABLE metadata(key TEXT,value TEXT);')
    db.executemany('INSERT INTO storms VALUES (?,?)',[('1981-01-31','1981-02-02'),('2020-06-01','2020-06-02')])
    db.executemany('INSERT INTO track_points VALUES (?,?)',[('1981-01-31','A'),('1981-01-31','A'),('1981-02-01','A'),('2020-06-01','B')])
   r=monthly(p);self.assertEqual(len(r['monthly']),12);self.assertEqual(r['sample_count'],2)
   self.assertEqual(r['monthly'][0]['active_unique_storms'],1);self.assertEqual(r['monthly'][1]['active_unique_storms'],1)
   self.assertEqual(r['monthly'][1]['first_record_count'],0)
   with self.assertRaises(ValueError):monthly(p,1981,2026)
  self.assertFalse(matches('浙江台风一般几月登陆？'))

 def test_complete_series_cannot_be_described_as_missing(self):
  meta={'tool_series_coverage':[{'state':'ready','complete':True,'available_samples':73}]}
  with self.assertRaisesRegex(ValueError,'series_claim'):validate_business_delivery('未提供逐时完整序列',meta,[])
  validate_business_delivery('已取得73个样本，以下展示峰值。',meta,[])
  validate_business_delivery('未提供逐时完整序列',{'tool_series_coverage':[{'state':'no_data','complete':False}]},[])

 def test_direction_unit_guard_and_cyclic_samples(self):
  with tempfile.TemporaryDirectory() as d:
   r=ForecastRuntime(Path(d));start=datetime(2026,10,18,0,tzinfo=BEIJING);end=start+timedelta(hours=2)
   def register(level,unit,values):
    path=Path(d)/f'PZJ_BEN_OSM_ZJS_202610172000_WAV_00_{level}.nc'
    with h5py.File(path,'w') as f:
     f.create_dataset('lon',data=[122.,122.1]);f.create_dataset('lat',data=[30.,30.1])
     t=f.create_dataset('time',data=[4,5,6]);t.attrs['units']='hours since 2026-10-17 20:00:00'
     x=f.create_dataset('MWD',data=np.asarray([np.full((2,2),v) for v in values],dtype='float32'));x.attrs['unit']=unit
     w=f.create_dataset('WAV',data=np.asarray([np.full((2,2),v) for v in [1.,3.,2.]],dtype='float32'));w.attrs['unit']='m'
    r.store.register_grid(inspect_grid(path))
   register('L2','m',[1,2,3])
   with patch('forecast_runtime.service.local_now',return_value=end):
    bad=r.grid('wave_direction',122,30,start,end)
   self.assertEqual(bad['state'],'no_data');self.assertIn('角度',bad['reason'])
   register('L5','degree',[359,1,3])
   with patch('forecast_runtime.service.local_now',return_value=end):
    good=r.grid('wave_direction',122,30,start,end)
    combined=r.query('2026年10月18日00时至10月18日02时 东经122 北纬30 浪高和波向是多少？')
   self.assertEqual(good['state'],'ready');self.assertEqual(good['series_coverage']['available_samples'],3)
   self.assertIsNone(good['maximum']);self.assertIsNone(good['minimum'])
   self.assertIn('零点',good['direction_convention']);self.assertEqual(good['direction_samples'][0]['value'],359)
   direction=next(p for p in combined['products'] if p['product']=='MWD')
   self.assertEqual(direction['at_wave_peak']['value'],1)
   facts=delivery_facts(combined);self.assertTrue(any(f.get('unit')=='°' and f.get('value')==1 for f in facts))

 def test_as_of_brief_starts_forecast_next_day_and_keeps_all_tools(self):
  with tempfile.TemporaryDirectory() as d:
   r=ForecastRuntime(Path(d));calls=[]
   original=r.query
   def child(q,_only=None,_point=None):
    if _only:
     calls.append((q,_only));return {'kind':_only,'state':'ready'}
    return original(q,_only=_only,_point=_point)
   r.query=child
   with patch('forecast_runtime.service.local_now',return_value=datetime(2026,10,19,12,tzinfo=BEIJING)):
    result=child('请生成截至2026年10月17日台风对浙江影响研判报告')
   self.assertEqual(result['start'],'2026-10-18T00:00:00+08:00')
   self.assertEqual(result['end'],'2026-10-21T00:00:00+08:00')
   self.assertEqual([x[1] for x in calls].count('grids'),5)
   self.assertEqual([x[1] for x in calls].count('coastal'),1)

if __name__=='__main__':unittest.main()
