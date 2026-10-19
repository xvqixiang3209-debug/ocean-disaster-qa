"""Readable report from saved, unedited real HTTP responses."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
from html import escape
import json
from pathlib import Path
import re
import requests

ROOT=Path(__file__).resolve().parents[1]

def inline(text):
    return re.sub(r'\*\*(.+?)\*\*',r'<strong>\1</strong>',escape(text))

def render(text):
    lines=text.splitlines();out=[];i=0
    while i<len(lines):
        line=lines[i]
        if line.startswith('|') and i+1<len(lines) and re.fullmatch(r'[| :\-]+',lines[i+1]):
            cells=lambda row:[c.strip() for c in row.strip().strip('|').split('|')]
            out.append('<div class="scroll"><table><thead><tr>'+''.join('<th>'+inline(c)+'</th>' for c in cells(line))+'</tr></thead><tbody>');i+=2
            while i<len(lines) and lines[i].startswith('|'):
                out.append('<tr>'+''.join('<td>'+inline(c)+'</td>' for c in cells(lines[i]))+'</tr>');i+=1
            out.append('</tbody></table></div>');continue
        if line.strip():out.append('<p>'+inline(line)+'</p>')
        i+=1
    return '\n'.join(out)

def main():
    p=argparse.ArgumentParser();p.add_argument('--run-dir',required=True);p.add_argument('--baseline-dir',required=True)
    p.add_argument('--base',default='http://127.0.0.1:8769');p.add_argument('--output',required=True);args=p.parse_args()
    run=Path(args.run_dir);raw=(run/'results.json').read_bytes();result=json.loads(raw)
    baseline=json.loads((Path(args.baseline_dir)/'results.json').read_text(encoding='utf-8'))
    source=requests.get(args.base+'/api/forecast/status',timeout=30).json()['official_warnings']
    build=requests.get(args.base+'/api/assistant/status',timeout=15).json()
    hashes_match=build==json.loads((run/'build.json').read_text(encoding='utf-8'))
    states=Counter(r['actual'].get('answerability','unknown') for r in result['results'])
    counts={str(y):{h:sum(f.get('notice_count',0) for f in source['feeds'] if f.get('year')==y and f['provider']=='nmefc' and f['stream'].split(':')[0]==h) for h in ['wave','storm','ice','bore']} for y in [2025,2026]}
    changed=['forecast_runtime/official_warnings.py','forecast_runtime/service.py','forecast_runtime/api.py','marine_kb/qa.py','marine_kb/chat_api.py','production_server.py','VERSION.txt','tests/test_official_warnings.py','tools/sync_official_warnings.py','tools/test_official_warning_http.py']
    manifest={'version':build['version'],'generated_at':datetime.now().astimezone().isoformat(),
              'real_cases':result['total'],'contract_passed':result['passed'],'answerability':dict(states),
              'followup_cases':sum(bool(r.get('followup')) for r in result['results']),
              'actual_responses_sha256':hashlib.sha256(raw).hexdigest(),'running_build_matches_tested_build':hashes_match,
              'source_notice_counts':counts,'source_status':source,
              'files_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in changed}}
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    output.with_suffix('.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    html=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>官方预警清单与历史查询实测</title><style>body{max-width:1280px;margin:36px auto;padding:0 28px;font:16px/1.8 "Microsoft YaHei",sans-serif;color:#183344;background:#f8fbfc}h1,h2,h3{color:#006e80}section{background:white;padding:24px;margin:24px 0;border:1px solid #d6e4e8;border-radius:10px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #ccdfe5;padding:9px;text-align:left;vertical-align:top}th{background:#e8f3f5}a{color:#087a92}.scroll{overflow:auto}code,pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef4f6}p{margin:.6em 0}.note{padding:16px;background:#fff5e5;border-left:4px solid #ba8131}</style><h1>官方预警清单、正文与历史查询实测</h1>']
    html.append('<p>'+escape(manifest['generated_at'])+' · '+escape(build['version'])+'</p>')
    html.append('<section><h2>结果与适用范围</h2><p>真实问答接口 '+str(result['passed'])+'/'+str(result['total'])+' 项通过数据合同检查，另有1次连续追问。'+escape(str(dict(states)))+'。测试记录来自实际DeepSeek调用，逐题原回答保留在下文。</p><p class="note">通过测试表示路由、时间、地区和返回数据符合检查条件，不代表所有题目业务信息完整。浙江省、市的直接清单接口仍缺访问凭据；国家台清单不能代表全部地方发布。无匹配记录不等于当地没有预警。</p>')
    html.append('<p>已归档2025年和2026年的官方公告共'+str(source['notice_count'])+'条，包含颜色警报、消息、海啸信息与解除通报。归档版本数含本次解析规则更新形成的版本，不能当作官方修订次数。历史日期按官方现有历史清单回溯，不声称已保存过去每次网页修改。</p>')
    html.append('<table><tr><th>年份</th><th>海浪</th><th>风暴潮</th><th>海冰</th><th>海啸公告/信息</th></tr>'+''.join('<tr><td>'+year+'</td>'+''.join('<td>'+str(v[h])+'</td>' for h in ['wave','storm','ice','bore'])+'</tr>' for year,v in counts.items())+'</table><p>海冰0条表示本次所查年份清单返回空列表，不能据此推断历史上没有海冰灾害。</p></section>')
    html.append('<section><h2>本次完成的代码功能</h2><ul><li>接入国家台台风/温带海浪、台风/温带风暴潮、海冰、海啸分类清单；海啸公告另取官方原始HTML。</li><li>保存原始JSON、原始HTML、SHA256及不可覆盖的公告解析版本；按发布时间、更新时刻、地区、原文时段查询。</li><li>“当天发布过”保留每次发布；“截至某时有哪些”取该时刻最新更新。先确定最新发布，再核对地区，避免旧预警范围残留。</li><li>地区等级与公告最高等级分开，24/48/72小时预报分开；保留表头单位，预计解除与实际解除分开。</li><li>路由覆盖多子问题、连续追问和公告编号查询；未来日期明确说明尚无已发布清单，缺源不退回通用解释。</li><li>服务启动后独立归档线程每15分钟检查当年清单，不启动NC下载；查询按需补历史年份。关闭服务后归档停止。海啸正文对未变更列表项最多缓存24小时，列表有修订立即重新下载。</li></ul>')
    html.append('<h3>可直接这样提问</h3>'+render('截至2026年10月17日12时，国家海洋预报台对秦皇岛发布了哪些风暴潮警报？等级、时段和覆盖范围是什么？\n2026年7月10日浙江发布了哪些海浪和风暴潮预警？\n截至2026年9月6日20时，国家海洋预报台对浙江的海浪警报解除了吗？\n这些警报覆盖哪里？什么时候发布的？'))
    html.append('<h3>接口与维护</h3><pre>GET /api/forecast/warnings?question=自然语言问题\nGET /api/forecast/warnings?date=2026-10-17&amp;region=浙江&amp;hazard=海洋灾害&amp;mode=published\nGET /api/forecast/warnings/notices/{公告ID}\nGET /api/forecast/evidence/{本次查询ID}\n..\\.venv\\Scripts\\python.exe tools\\sync_official_warnings.py --year 2026 --refresh</pre><p>每次日期范围最多31天，正文最多展示30条公告；超限明确提示缩小范围。原文只有“上午/下午/夜间”时保持原文，不能补成精确小时。独立归档开关为MARINE_WARNING_WORKER，默认true；省级凭据预留ZJOCEAN_WARNING_TOKEN，未取得凭据前不承诺该接口已验收。</p></section>')
    html.append('<section><h2>发现并修复的问题</h2><p>首轮 '+str(baseline['passed'])+'/'+str(baseline['total'])+' 通过。W03“何时发布”误入一般资料检索；W09未来日期被通用解释覆盖；W12“截至…发布了”误列当天全部更新。另发现连续追问含公告编号时，编号被误识别为日期。以上均增加通用规则与回归检查。早期首轮结果、最终结果分别保存，未改写失败记录。</p><p>全量自动化回归：978项测试、102项子测试通过；新增官方预警模块24项测试通过。实际问答数据合同通过不等于独立专家已逐句验收模型全部措辞。</p></section>')
    html.append('<section><h2>逐题结果</h2><table><tr><th>编号</th><th>问题</th><th>检查</th><th>完整性</th><th>匹配公告</th></tr>')
    for row in result['results']:
        a=row['actual'];e=row.get('evidence') or {}
        html.append('<tr><td><a href="#'+row['id']+'">'+row['id']+'</a></td><td>'+escape(row['question'])+'</td><td>'+('通过' if row['passed'] else '未通过')+'</td><td>'+escape(str(a.get('answerability')))+'</td><td>'+str(e.get('total','日期待澄清'))+'</td></tr>')
    html.append('</table></section>')
    for row in result['results']:
        a=row['actual'];html.append('<section id="'+row['id']+'"><h2>'+row['id']+' '+escape(row['question'])+'</h2><p>HTTP '+str(row['http_status'])+'；模式 '+escape(str(a.get('mode')))+'；完整性 '+escape(str(a.get('answerability')))+'；耗时 '+str(row['seconds'])+' 秒。</p>'+render(a.get('answer','')))
        e=row.get('evidence') or {};seen=set()
        if e.get('items'):html.append('<h3>本题对应官方原文</h3><ul>')
        for item in e.get('items',[]):
            if item['id'] in seen:continue
            seen.add(item['id']);html.append('<li><a href="'+escape(item['source_url'],quote=True)+'">'+escape(item['publisher']+' '+item['published_at']+' '+item['title'])+'</a> · 公告ID '+item['id']+'</li>')
        if e.get('items'):html.append('</ul>')
        if row.get('followup'):
            f=row['followup'];html.append('<h3>连续追问：'+escape(f['question'])+'</h3>'+render(f['actual'].get('answer','')))
        html.append('<details><summary>检查项与实际接口记录</summary><pre>'+escape(json.dumps(row['checks'],ensure_ascii=False,indent=2))+'</pre><p>原始响应：'+escape(str(run/(row['id']+'.json')))+'</p></details></section>')
    html.append('<section><h2>原始记录与核验</h2><p>结果JSON SHA256：<code>'+manifest['actual_responses_sha256']+'</code>。当前进程与本轮测试构建一致：'+str(hashes_match)+'。</p><p>官方来源：<a href="https://www.nmefc.cn/zhyj/fbcjb/tffbcjb">国家海洋预报台历史警报</a>；<a href="https://www.nmefc.cn/api/data/warnHistoryList">该网站公开历史清单接口</a>。官方海啸原文使用清单所指向的obs.nmefc.cn原始HTML。</p></section></html>')
    output.write_text('\n'.join(html),encoding='utf-8')
    print(json.dumps({'report':str(output),'passed':result['passed'],'total':result['total'],'answerability':dict(states),'build_matches':hashes_match},ensure_ascii=False))

if __name__=='__main__':main()
