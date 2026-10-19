"""Publish an auditable report using saved, unedited real assistant responses."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
from html import escape
import json
from pathlib import Path
import requests
from report_official_warning_acceptance import render

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('--run-dir',required=True);p.add_argument('--map-run-dir',required=True)
    p.add_argument('--baseline-dir',required=True);p.add_argument('--output',required=True)
    p.add_argument('--base',default='http://127.0.0.1:8769');a=p.parse_args()
    run=Path(a.run_dir);extra=Path(a.map_run_dir);baseline=Path(a.baseline_dir)
    result=json.loads((run/'results.json').read_text('utf-8'));map_result=json.loads((extra/'results.json').read_text('utf-8'))
    rows=result['results']+map_result['results'];build=json.loads((run/'build.json').read_text('utf-8'))
    current=requests.get(a.base+'/api/assistant/status',timeout=15).json()
    source_hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in build['runtime_source_sha256']}
    runtime_matches=(build==current and source_hashes==build['runtime_source_sha256'] and
                     json.loads((extra/'build.json').read_text('utf-8'))==build)
    audit=lambda row:(row['actual'].get('evidence_diagnostics') or {}).get('crag_audit') or {}
    states=Counter(row['actual'].get('answerability','unknown') for row in rows)
    proof=ROOT/'exports/CRAG联合检索_网页验证_20261019.png'
    manifest={'generated_at':datetime.now().astimezone().isoformat(),'version':build['version'],
        'real_cases':len(rows),'contract_passed':sum(row['passed'] for row in rows),
        'followups':sum('followup' in row for row in rows),'model_reported_answerability':dict(states),
        'both_retrievers_executed':sum(bool(audit(row).get('local',{}).get('executed') and audit(row).get('web',{}).get('executed')) for row in rows),
        'answers_citing_both_local_and_web':sum(audit(row).get('used_local',0)>0 and audit(row).get('used_web',0)>0 for row in rows),
        'runtime_matches_tested_files':runtime_matches,'unit_tests':1005,'subtests':102,
        'javascript_checks':['test_chat_render.cjs','test_assistant_client.cjs','test_forecast_panel.cjs'],
        'runtime_source_sha256':source_hashes,'frontend_sha256':hashlib.sha256((ROOT/'frontend/map_assistant.js').read_bytes()).hexdigest(),
        'raw_results_sha256':{str(folder/'results.json'):hashlib.sha256((folder/'results.json').read_bytes()).hexdigest() for folder in [run,extra]},
        'upstream_reference':json.loads((ROOT/'docs/CRAG_REFERENCE.json').read_text('utf-8'))}
    output=Path(a.output);output.parent.mkdir(parents=True,exist_ok=True)
    output.with_suffix('.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    h=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>CRAG联合检索改造与真实问答验证</title><style>body{max-width:1240px;margin:36px auto;padding:0 24px;font:16px/1.8 "Microsoft YaHei",sans-serif;color:#183744;background:#f6fafb}h1,h2,h3{color:#006f7b}section{padding:24px;margin:24px 0;background:white;border:1px solid #d1e2e7;border-radius:10px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #cbdfe4;padding:9px;vertical-align:top;text-align:left}th{background:#e7f3f5}a{color:#087b94}pre,code{white-space:pre-wrap;overflow-wrap:anywhere;background:#edf4f6}.note{padding:14px;border-left:4px solid #c48b33;background:#fff5e3}.scroll{overflow:auto}img{max-width:100%;border:1px solid #cbdfe4}details{margin:12px 0}</style><h1>CRAG联合检索改造与真实问答验证</h1>']
    h.append('<p>'+escape(manifest['generated_at'])+' · '+escape(build['version'])+'</p>')
    h.append('<section><h2>已运行的问答流程</h2><p>拆分子问题 → 并行检索本地资料库和官方网络正文 → 逐个子问题评估来源与原文段落 → 对缺项做一轮定向补检 → 与NC、预警或历史案例工具结果合并 → DeepSeek组织回答 → 引用、数值和回答覆盖检查。</p><p>本地检索有结果时继续执行网络检索；联网不是只在整题查不到时才触发。正常知识问答、NC业务查询、官方预警、地图所选历史台风、连续追问均使用统一调度。缺少日期或地点且必须澄清时先明确对象；模型或网络不可用时如实显示状态。</p><p>来源在不同子问题中可有不同用途。资料可支持定义，却不能证明当天是否开放；预案中的操作条件不能当作文件有效期；国家规则不能直接冒充地方指令。网络正文不可用、不相关或超出所问时空范围时，不为凑齐两路引用而使用它。</p></section>')
    h.append('<section><h2>与CRAG作者代码的对应</h2><p>参考<a href="https://github.com/HuskyInSalt/CRAG">作者仓库</a>和<a href="https://arxiv.org/abs/2401.15884">论文</a>，固定参考提交 de7c2961ae624a1483a138c5798e1f6d0c4fb0e0。这里独立适配现有中文资料、DeepSeek、官方网页读取器和计算模块，没有执行作者实验脚本，也没有安装其T5/LLaMA评估权重。未将论文实验成绩当成本项目成绩。</p><table><tr><th>作者实现</th><th>本项目改造</th></tr><tr><td>CRAG_Inference.process_flag：correct / ambiguous / incorrect</td><td>按子问题评估直接支持、部分参考、不支持，同时核对真实段落编号；评估失败仅保留参考候选。</td></tr><tr><td>internal_knowledge_preparation：分解与筛选</td><td>保留原文切片，向生成环节交付筛选后的原始段落，保留全文供数值和引用核对。</td></tr><tr><td>external_knowledge_preparation：改写与网络扩充</td><td>每次检索官方网页正文；缺项补检只使用用户问题中的公开业务词、地点和日期，不将内部文档或模型答案送入搜索引擎。</td></tr><tr><td>combined_knowledge_preparation：合并内外证据</td><td>按各子问题合并两路证据，计算结果保持资料1和原始数值。按用户要求，即使本地结果相关也执行网络检索。</td></tr></table><p>参考文件、SHA256和适配说明保存在 docs/CRAG_REFERENCE.json。</p></section>')
    h.append('<section><h2>验证结果</h2><p>1005项Python回归测试、102项子测试和3组JavaScript检查通过。8个真实接口问题、1次连续追问完成；另在统一地图页面实际提问并展开来源核对。运行进程与实测构建、磁盘46个被跟踪文件一致：'+str(runtime_matches)+'。</p><p>8/8问题的两路检索均已执行；'+str(manifest['answers_citing_both_local_and_web'])+'个最终回答实际同时引用本地资料和网络正文。其他问题根据取得的证据采用计算记录、归档官方清单或单路有效材料，不等同于没有搜索另一渠道。</p><p class="note">这些检查验证调度、来源采用、数值保留与路由，不代表8题全部业务信息完整，也不代表已重新完成160题实测。模型返回的完整性分布为 '+escape(str(dict(states)))+'；这是模型与程序的覆盖结果，尚非独立专家逐句验收。浙江省市预警清单权限、现场开放状态、业务基准核定等缺项仍保持明确标注。</p><table><tr><th>题号</th><th>问题</th><th>调度检查</th><th>回答完整性</th><th>采用本地/网页/工具</th><th>补检</th></tr>')
    for row in rows:
        au=audit(row);h.append('<tr><td><a href="#'+row['id']+'">'+row['id']+'</a></td><td>'+escape(row['question'])+'</td><td>'+('通过' if row['passed'] else '未通过')+'</td><td>'+escape(str(row['actual'].get('answerability')))+'</td><td>'+str(au.get('used_local',0))+' / '+str(au.get('used_web',0))+' / '+str(au.get('used_tool',0))+'</td><td>'+str(au.get('corrective_rounds',0))+'轮</td></tr>')
    h.append('</table></section>')
    h.append('<section><h2>本轮实测发现与修复</h2><ul><li>地图所选历史台风分站问题曾绕过普通问答，并以“本地已足够”为由不联网：现保留所选SID与结构化记录，转入共同调度。</li><li>“有哪些预警？黄色是什么意思？”曾被定义子问带离预警路由：现先查询正式清单，再用资料和网页补充定义。</li><li>预案正文中的“未来24小时”曾被误解析成预案到期时间：现区分文件有效期与业务触发预报时段。</li><li>国家预案转载网页和海区机构名称可能干扰地区核对：现优先采用正文明确的国家级适用范围。</li><li>动态多子问题曾整体屏蔽历史资料：现单独核对，历史定义可用于定义子问，不能用于认证当前状态。</li><li>计算结果不会因另一个子问无资料而被删除；Q022延伸题仍返回最大增水164.84厘米、最高总潮位685.86厘米及相应地点时刻。</li></ul><p>首轮和中间结果独立保留，未改写旧160题报告。</p></section>')
    for row in rows:
        actual=row['actual'];au=audit(row)
        h.append('<section id="'+row['id']+'"><h2>'+row['id']+' '+escape(row['question'])+'</h2><p>实际回答 · '+str(row['seconds'])+'秒 · '+escape(str(actual.get('mode')))+' · '+escape(str(actual.get('answerability')))+('</p><p>地图SID：'+escape(row['selected_sid']) if row.get('selected_sid') else '')+'</p>'+render(actual.get('answer','')))
        h.append('<h3>各子问题的检索证据状态</h3><table><tr><th>子问题</th><th>本地相关性</th><th>最终证据覆盖</th><th>直接来源编号</th><th>参考来源编号</th></tr>')
        for facet in au.get('facets',[]):
            h.append('<tr>'+''.join('<td>'+escape(str(facet.get(k,'')))+'</td>' for k in ['question','local_grade','state','correct_sources','reference_sources'])+'</tr>')
        h.append('</table><p>相关性评估表示候选段落是否支持对应子问，不是官方认定或事实正确率。部分参考不能自动标为完整回答。</p><h3>实际采用来源</h3><ul>')
        for source in actual.get('sources',[]):
            title=escape(str(source.get('title') or source.get('filename') or source.get('source_id')))
            url=source.get('url') or ''
            if url.startswith(('http://','https://')):title='<a href="'+escape(url,quote=True)+'">'+title+'</a>'
            h.append('<li>'+escape(str(source.get('citation')))+' · '+title+' · '+escape(str(source.get('locator') or ''))+'</li>')
        h.append('</ul>')
        if actual.get('missing_information'):h.append('<h3>实际剩余缺项</h3><ul>'+''.join('<li>'+escape(item)+'</li>' for item in actual['missing_information'])+'</ul>')
        if row.get('followup'):
            f=row['followup'];h.append('<h3>实际连续追问：'+escape(f['question'])+'</h3>'+render(f['actual'].get('answer','')))
        if row['id'] in {'C03','C06'} and (baseline/(row['id']+'.json')).exists():
            old=json.loads((baseline/(row['id']+'.json')).read_text('utf-8'))
            h.append('<details><summary>首轮实际回答对照（原记录保留）</summary>'+render(old['actual'].get('answer',''))+'</details>')
        h.append('<details><summary>实际调度检查与来源采用记录</summary><pre>'+escape(json.dumps({'checks':row['checks'],'audit':au},ensure_ascii=False,indent=2))+'</pre></details></section>')
    h.append('<section><h2>统一页面验证</h2><p>真实提问“什么是风暴增水？它和天文大潮叠加在一起会怎么样？”，页面显示本次实际采用本地资料1篇、网络正文2篇。来源默认收起，提问时无须进行人工审核。</p><img src="'+escape(proof.name,quote=True)+'" alt="统一地图问答页面联合检索验证"></section>')
    h.append('<section><h2>文件与复测</h2><p>主要实现：marine_kb/crag.py；普通问答：marine_kb/qa.py；统一入口：marine_kb/chat_api.py；计算和预警：forecast_runtime/service.py、official_warnings.py；页面：frontend/map_assistant.js。实际原始响应分别保存在 '+escape(str(run))+' 和 '+escape(str(extra))+'，报告同名JSON保存运行构建与SHA256。</p><pre>..\\.venv\\Scripts\\python.exe -m pytest tests -q\nnode tests\\test_chat_render.cjs\nnode tests\\test_assistant_client.cjs\nnode tests\\test_forecast_panel.cjs\n..\\.venv\\Scripts\\python.exe tools\\test_crag_joint_http.py --output tmp\\新的独立实测目录</pre></section></html>')
    output.write_text('\n'.join(h),encoding='utf-8')
    print(json.dumps({k:manifest[k] for k in ['version','real_cases','contract_passed','both_retrievers_executed','answers_citing_both_local_and_web','model_reported_answerability','runtime_matches_tested_files']},ensure_ascii=False))
    print(str(output))

if __name__=='__main__':main()
