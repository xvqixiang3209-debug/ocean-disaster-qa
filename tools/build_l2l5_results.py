"""Publish sanitized local acceptance results and actual generated products."""
import hashlib, html, json, re, shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'tmp/replay/l2l5_20261020_231606_1e7fec74'
OUT=ROOT/'exports'
NAME='L2L5自动获取与地图联动_修改及真实测试结果_20261020'
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def inline(text):
 text=html.escape(text)
 text=re.sub(r'\*\*(.+?)\*\*',r'<strong>\1</strong>',text)
 return re.sub(r'\[([^\]]+)\]\((/api/objective/reports/[a-f0-9]{32}/[\w.]+)\)',r'<a href="http://127.0.0.1:8774\2">\1</a>',text)
def render(text):
 output=[];table=False
 for line in text.splitlines():
  if line.startswith('|'):
   if not table:output.append('<table>');table=True
   if re.fullmatch(r'[| :\-]+',line):continue
   output.append('<tr>'+''.join('<td>'+inline(x.strip())+'</td>' for x in line.strip('|').split('|'))+'</tr>');continue
  if table:output.append('</table>');table=False
  if line.strip():output.append('<p>'+inline(line)+'</p>')
 if table:output.append('</table>')
 return '\n'.join(output)
def main():
 rows=[]
 for cid in ['MAP','WIND','TIME','REPORT','PRESSURE']:
  record=read(RUN/(cid+'.result.json'));reply=record['reply'];tool=record.get('tool_result') or {}
  rows.append({'id':cid,'question':record['question'],'answer':reply['answer'],'http_status':record['http_status'],
   'resolved_question':reply.get('resolved_question'),'business_state':reply.get('business_state'),
   'answerability':reply.get('answerability'),'mode':reply.get('mode'),'map_action':reply.get('map_action'),
   'context_used':reply.get('context_used'),'seconds':record['elapsed_seconds'],
   'model_calls':record['cassette_summary']['model_calls'],'tool_result':tool,
   'sources':reply.get('sources',[]),'artifacts':reply.get('objective_artifacts',[])})
  assert record['http_status']==200 and reply.get('objective_dataset_id','').startswith('zjocean_l5_')
 assert rows[0]['map_action']['valid_time']=='2026-10-21T08:00:00+08:00'
 assert rows[1]['map_action']['field']=='wind' and rows[1]['context_used']
 assert rows[2]['map_action']['valid_time']=='2026-10-21T10:00:00+08:00'
 assert rows[-1]['map_action']['field_state']=='not_provided' and rows[-1]['business_state']=='no_data'
 report=rows[3]['tool_result'];assert report['state']=='ready'
 assert report['requested_fields']==['wave','wind'] and report['fields']['pressure']['state']=='not_provided'
 assert report['fields']['wave']['maximum']['value']==7.563476
 assert report['fields']['wind']['maximum']['value']==24.548779
 target=OUT/'浙江72小时L5风浪报告_20261020';target.mkdir(exist_ok=True)
 for item in rows[3]['artifacts']:
  source=ROOT/'data/forecast_runtime/objective/reports'/report['report_id']/item['filename']
  assert hashlib.sha256(source.read_bytes()).hexdigest()==item['sha256']
  shutil.copy2(source,target/item['filename'])
 current={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in read(RUN/'source_after.json')}
 assert current==read(RUN/'source_after.json'),'release_source_differs_from_recording'
 payload={'version':'phase1.10.0-l2l5-map-dialogue','run':RUN.name,'preview':'http://127.0.0.1:8774/',
  'test_summary':{'python_passed':1966,'subtests_passed':134,'node_groups_passed':5,
   'http_transactions':5,'model_calls':sum(row['model_calls'] for row in rows),'source_stability':read(RUN/'run_completed.json')['source_stability']},
  'acquisition':read(ROOT/'tmp/l2_acquisition_verified.json'),'cases':rows,
  'limits':['WAV暂作波高，u10/v10暂作10米阵风；MWD不绘制。','L2/L5没有气压，未借用ECMWF。',
   '本次报告DeepSeek补充段未发布，保留程序完整数值报告。通用模型补充仍有流程/主体误分类缺陷。',
   '15分钟轮询已启动并实测目录检查；最新文件已有，未宣称发生新的数据字节下载。',
   '本次未重跑160题，也未重新验收预警知识及相似台风；其上一轮测试保留为历史记录。']}
 (OUT/(NAME+'.json')).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
 sections=[]
 for row in rows:
  sections.append('<section><h2>'+html.escape(row['id']+' · '+row['question'])+'</h2><p class="meta">HTTP '+str(row['http_status'])+'；业务状态 '+row['business_state']+'；程序交付 '+row['answerability']+'；耗时 '+str(row['seconds'])+'秒；模型调用 '+str(row['model_calls'])+'次。</p><details><summary>实际解析的输入与地图动作</summary><pre>'+html.escape(json.dumps({'resolved_question':row['resolved_question'],'map_action':row['map_action']},ensure_ascii=False,indent=2))+'</pre></details>'+render(row['answer'])+'</section>')
 page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>L2/L5自动获取与地图联动实测</title><style>body{max-width:1080px;margin:36px auto;padding:0 22px;color:#163b4a;background:#f4f8fa;font:16px/1.8 "Microsoft YaHei",sans-serif}h1{font-size:28px}h2{font-size:21px}section,.card{background:white;padding:24px;margin:20px 0;border:1px solid #d4e3e7;border-radius:10px}p{margin:10px 0}table{border-collapse:collapse;width:100%;font-size:14px}td,th{border:1px solid #c9dadd;padding:8px;text-align:left}tr:first-child{background:#e9f3f5}a{color:#007b91}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}.meta{color:#52717d}img{max-width:100%}</style><body><h1>L2/L5自动获取与地图联动：修改及真实测试结果</h1><p>版本 phase1.10.0-l2l5-map-dialogue · 2026年10月20日 · 本机 <a href="http://127.0.0.1:8774/">打开新版地图问答</a></p><div class="card"><h2>已交付的完整流程</h2><p>服务器按15分钟检查浙江海洋L2/L5目录，只获取WAV、SSW；查询时亦可触发补取。已存在的文件复用，新的文件下载后校验并登记。原始文件保持不变，解析结果保留来源文件名、哈希、层级、起报和有效时刻。</p><p>同一页面提问后自动选择地图批次、要素和实际原生时刻。连续追问只继承服务器生成的输入条件，每次重新读取NC；更换时刻或要素不会沿用旧图数值。不存在的时刻、要素会清除旧图并说明缺项。</p><p>浙江72小时报告优先选择覆盖完整的L5，输出风浪极值、发生时间与位置、三参考分区、逐时CSV和PNG/JSON/HTML。L2空间不足时标明局部覆盖；不跨级、跨批次拼接。</p><p><a href="浙江72小时L5风浪报告_20261020/report.html">打开真实生成的72小时报告</a> · <a href="浙江72小时L5风浪报告_20261020/series.csv">逐时CSV</a> · <a href="浙江72小时L5风浪报告_20261020/report.json">计算JSON</a></p></div><div class="card"><h2>真实结果与验证范围</h2><p>2026年10月20日20时L5批次，21日00时至24日00时，73个原生时次：最大波高7.563476m（23日19时，123.5°E、26.5°N）；最大风速24.548779m/s（23日23时，120.45°E、26.5°N，临时阵风解释）。两项与独立直接读取NC的结果一致。</p><p>1966项Python测试、134项子测试、5组Node测试通过。最终5次真实连续HTTP问答记录完整，录制期间288份代码/静态输入文件未变化；总计10次模型调用。首次检查的四个最新NC均已存在，目录ready，未将文件复用称为新下载。这里的ready表示程序数据交付完整，不代表业务方已经核定变量。</p><p>报告的DeepSeek补充未通过最终校验，最终答案为程序完整报告，数值和下载文件未丢失。通用补充环节仍出现‘流程/主体’误分类，保留为后续改进项。本次未重新跑160题，预警知识和相似台风沿用此前结果。</p></div><div class="card"><h2>使用方法</h2><p>直接输入“显示浙江近海2026年10月21日08时的海浪分布图”，接着问“同一时刻换成海面风分布图”，或“明天10时呢”。生成报告可问“从2026年10月21日00时起生成浙江未来72小时风浪影响报告”。运行服务后保持终端开启，自动更新才会持续。</p><p>启动：<code>powershell -ExecutionPolicy Bypass -File E:\\ocean_qa\\ocean_phase1_20261006\\app\\start_objective_products.ps1 -Port 8774</code>。原有模型、天地图和ZJOCEAN凭据继续由本机.env读取。</p><p>当前L2/L5均约0.05°，L5范围更大。WAV暂作海浪波高，u10/v10暂作10米阵风；MWD浪向尚未使用。L2/L5没有气压，系统明确显示缺项，并保留手工导入ECMWF的独立批次。</p><img src="L2L5连续问答与二维风场_20261020.png" alt="连续追问与自动风场联动实测"></div>'''+''.join(sections)+'</body></html>'
 (OUT/(NAME+'.html')).write_text(page,encoding='utf-8')
 print(json.dumps({'html':str(OUT/(NAME+'.html')),'json':str(OUT/(NAME+'.json')),'report':str(target/'report.html'),'verified':True},ensure_ascii=False))
if __name__=='__main__':main()
