"""Build the public, source-checked 2026-10-21 actual NC acceptance package."""
import hashlib, html, json, shutil
from pathlib import Path
import requests
from build_l2l5_results import render
ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'tmp/today_l2l5_20261021_092510'
BEFORE=ROOT/'tmp/today_l2l5_20261021_092043'
DOWNLOAD=ROOT/'tmp/l2_download_reuse_20261021_092036/summary.json'
OUT=ROOT/'exports/今日最新NC自动下载复用与72小时报告实测_20261021'
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
def main():
 OUT.mkdir(exist_ok=True)
 audit=read(ROOT/'tmp/l2_latest_20261021_initial_audit.json');download=read(DOWNLOAD)
 native=read(ROOT/'tmp/l2_20261021_independent_native.json')
 cases=[read(RUN/(name+'.result.json')) for name in ['WAVE','WIND','REUSE','REPORT']]
 report=cases[-1]['tool_result'];checks=[]
 for name,row in [('wave',cases[0]),('wind',cases[1])]:
  expected=native['map_20261021_1000'][name]['maximum'];actual=row['frame_stats']['maximum']
  checks.append({'test':name+'_native_frame','pass':abs(actual['value']-expected['value'])<1e-6 and actual['time']==expected['time']})
  assert str(actual['value']) in row['reply']['answer']
 for name in ['wave','wind']:
  expected=native['report72'][name]['maximum'];actual=report['fields'][name]['maximum']
  okay=abs(actual['value']-expected['value'])<1e-6 and actual['time']==expected['time'] and abs(actual['lon']-expected['lon'])<1e-5 and abs(actual['lat']-expected['lat'])<1e-5
  checks.append({'test':name+'_native_72h','pass':okay});assert okay
  assert len(report['fields'][name]['series'])==73 and str(actual['value']) in cases[-1]['reply']['answer']
 assert all(x['pass'] for x in checks)
 assert report['state']=='partial' and all(report['fields'][f]['spatial_partial'] for f in ['wave','wind'])
 for filename in ['report.html','report.json','series.csv','wave_peak.png','wind_peak.png','WAVE.png','WIND.png']:
  shutil.copy2(RUN/filename,OUT/filename)
 for item in cases[-1]['reply']['objective_artifacts']:
  assert hashlib.sha256((OUT/item['filename']).read_bytes()).hexdigest()==item['sha256']
 stages=[{k:s[k] for k in ['stage','state','directory_gets','oss_body_gets','oss_bytes_read','cache_hit','files'] if k in s}
         | {'identical_sha_size_mtime':s.get('identical_sha_size_mtime')} for s in download['stages']]
 assert stages[0]['oss_body_gets']==4 and stages[0]['oss_bytes_read']==63373375
 assert stages[1]['oss_body_gets']==0 and stages[1]['identical_sha_size_mtime'] is True
 assert stages[2]['oss_body_gets']==0 and stages[2]['cache_hit'] is True
 service=requests.get('http://127.0.0.1:8774/api/assistant/status',timeout=10).json()
 qa_hash=hashlib.sha256((ROOT/'marine_kb/qa.py').read_bytes()).hexdigest()
 assert service['runtime_source_sha256']['marine_kb/qa.py']==qa_hash
 summary={'tested_at':read(RUN/'summary.json')['tested_at'],'latest_batches':download['latest_batches'],
  'batch':report['batch'],'dataset_id':report['dataset_id'],'download_and_reuse':stages,
  'production_reuse':read(RUN/'summary.json'),'independent_native_checks':checks,
  'report':{k:report[k] for k in ['state','start','end','hours','bounds','missing_information']},
  'bug_fixed':'Verified partial NC program delivery now bypasses generic guidance replacement.',
  'patched_qa_sha256':qa_hash,'running_qa_sha256':service['runtime_source_sha256']['marine_kb/qa.py'],
  'limits':['最新L2为今日08时；L5仍为10月20日20时，未混批。',
   '今日L2不完整覆盖浙江参考框，报告按交集统计并标partial。','风浪变量与时区继续采用已授权临时解释。',
   '程序报告已完整保留；本次DeepSeek补充未发布，未将其称为通过模型验收。']}
 regression=(ROOT/'tmp/today_nc_regression_20261021_final.txt').read_text(encoding='utf-8')
 assert '1973 passed, 134 subtests passed' in regression
 summary['full_regression']='1973 passed, 134 subtests passed'
 (OUT/'测试结果.txt').write_text(regression,encoding='utf-8')
 write(OUT/'verification.json',summary);write(OUT/'independent_native_check.json',native)
 write(OUT/'actual_questions_answers.json',[{k:r[k] for k in ['id','question','reply','seconds','http_status']} for r in cases])
 tables='<table><tr><th>阶段</th><th>目录GET</th><th>NC文件GET</th><th>下载字节</th><th>结果</th></tr>'
 for s in stages:
  tables+=f'<tr><td>{html.escape(s["stage"])}</td><td>{s["directory_gets"]}</td><td>{s["oss_body_gets"]}</td><td>{s["oss_bytes_read"]:,}</td><td>{html.escape(s["state"])}</td></tr>'
 tables+='</table>'
 sections=[]
 for row in cases:
  sections.append('<section><h2>'+html.escape(row['id']+' · '+row['question'])+'</h2><p>HTTP '+str(row['http_status'])+'；业务状态 '+row['reply']['business_state']+'；'+str(row['seconds'])+'秒。partial对应真实空间覆盖缺口，非请求失败。</p>'+render(row['reply']['answer'])+'</section>')
 bad=read(BEFORE/'WAVE.result.json')['reply']['answer']
 page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>今日最新NC下载、复用和风浪产品实测</title><style>body{max-width:1080px;margin:30px auto;padding:0 20px;background:#f2f7f9;color:#153946;font:16px/1.8 "Microsoft YaHei",sans-serif}h1{font-size:27px}h2{font-size:21px}.card,section{background:#fff;border:1px solid #cbdde2;border-radius:9px;padding:24px;margin:20px 0}table{width:100%;border-collapse:collapse;font-size:14px}td,th{border:1px solid #cbdde2;padding:9px;text-align:left}th{background:#e8f1f4}a{color:#007c92}img{max-width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}.note{background:#fff5e4;padding:14px}</style><body><h1>2026年10月21日最新NC：自动下载、本地复用与风浪产品实测</h1><p>北京时间09:20—09:30实测 · <a href="http://127.0.0.1:8774/">打开地图问答</a></p><div class="card"><h2>实测结论</h2><p>已验证真实网络下载、文件保存与登记、后续零NC下载复用、今日指定时刻风浪图，以及未来72小时报告。最新可取得的L2为今天08时批次；L5目录最新仍为10月20日20时。今天的报告明确使用L2，未把旧L5冒充成今日新批次。</p><p class="note">L2源范围120—125°E、27—31°N，没有完整覆盖浙江参考框。报告按实际交集120—123.5°E、27—31°N统计，状态partial；它不是浙江全部近海范围的极值报告。</p><p><a href="report.html">打开今天新批次生成的72小时报告</a> · <a href="series.csv">逐时CSV</a> · <a href="report.json">完整计算JSON</a> · <a href="verification.json">下载复用验证JSON</a> · <a href="actual_questions_answers.json">实际问题与回答JSON</a></p></div><div class="card"><h2>自动下载与复用证据</h2><p>生产目录保留的09:02取数记录显示今日L2 WAV、SSW均为downloaded；对应文件分别15,654,213及7,268,541字节，文件SHA与数据库、客观预报索引一致。09:17记录为existing。该记录没有调用者字段，单凭记录不能区分定时worker或查询ensure触发。</p><p>另外从全新空目录运行同一自动获取代码，真实访问官方目录和OSS，验证如下；没有复制生产文件、删除原文件或模拟网络响应。首次四个文件包括今日L2两份和目录当前最新的昨晚L5两份。</p>'''+tables+'''<p>第二轮强制检查只查询目录，不发生NC文件GET；文件SHA、大小、修改时间完全一致。第三轮命中缓存，目录与NC文件GET均为0。刷新可能更新数据库登记时间，这不代表NC本体被重新下载。</p><p>实际生产问答再次查询前后，38个已有风浪文件SHA、大小和修改时间保持一致；相同问题的地图动作及数值统计一致。原始NC仍保存在app/data/forecast_runtime/grids中。</p></div><div class="card"><h2>今天新批次的数值与图件</h2><table><tr><th>时段与实际范围</th><th>波高最大值</th><th>海面风最大值（临时阵风解释）</th></tr><tr><td>10月21日10时，120—125°E、27—31°N</td><td>2.469727 m</td><td>13.927134 m/s</td></tr><tr><td>21日10时至24日10时，120—123.5°E、27—31°N</td><td>6.885742 m，24日10时，122.25°E、27°N</td><td>22.559499 m/s，24日08时，120.8°E、27°N</td></tr></table><p>72小时包含73个原生时次，每帧3849个有效海点，风浪各280977个有效海点-时间样本，窗口内无海点缺测。以上四组极值均由另一段直接h5py读取原始NC的计算独立核对一致，不仅比较系统自己生成的引用。</p><p>两张下图由系统读取今日NC计算绘制。</p><p><a href="WAVE.png">下载21日10时海浪图</a> · <a href="WIND.png">下载21日10时风场图</a> · <a href="wave_peak.png">下载72小时波高峰值时刻图</a> · <a href="wind_peak.png">下载72小时风速峰值时刻图</a></p><img src="WAVE.png" alt="今日NC指定时刻海浪分布"><img src="WIND.png" alt="今日NC指定时刻风场分布"></div><div class="card"><h2>测试中发现并修复的问题</h2><p>数据已算出，但L2空间覆盖不完整时，公共回答出口会启动通用解释兜底，将已有数字和准确缺项覆盖掉。修复后，经过程序验证并带有数据记录的NC交付保留原文；普通缺证据问答仍可使用通用解释。新增公共入口回归测试覆盖局部数据、无气压、时刻无数据和普通问答，7项通过。</p><p>重启新版后，四次真实HTTP问答重新测试并断言最终文字含真实极值，而非仅检查data_url或地图。重测海浪、风场、重复查询分别约0.215、0.036、0.030秒；报告约49秒（包含模型补充尝试），最终保留完整程序报告。本次模型补充未发布，不称作语言模型验收通过。</p><details><summary>修复前实际错误回答，保留供核查</summary>'''+render(bad)+'''</details><p>变量和时区仍采用已授权的临时解释，气压当前数据源未提供。没有改动NC数值、临时变量配置或地图统计算法。</p></div>'''+''.join(sections)+'</body></html>'
 page=page.replace('7项通过。','7项通过。全量回归1973项测试及134项子测试通过。')
 (OUT/'实测记录.html').write_text(page,encoding='utf-8')
 print(json.dumps({'folder':str(OUT),'report_id':report['report_id'],'checks':checks},ensure_ascii=False))
if __name__=='__main__':main()
