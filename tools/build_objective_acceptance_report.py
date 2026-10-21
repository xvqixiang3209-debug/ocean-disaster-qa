"""Export sanitized actual answers and real-NC product links, no raw cassettes."""
import argparse
from datetime import datetime
import html
import json
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
CASE_IDS = ('KNOWLEDGE', 'ANALOGUE', 'REPORT', 'FOLLOWUP')
BEFORE_NOTES = {
    'KNOWLEDGE': '海浪、风暴潮与响应概念归属混淆，并把未问阈值和转换表列为缺项。',
    'ANALOGUE': '工具已返回三条正确排名；语言校验却误删名称、年份和分数。',
    'REPORT': '程序数值保住，但语言段误报图件和下载缺失，并删去部分要素解释。',
    'FOLLOWUP': '追问未继承产品批次和时段，误转普通地点查询并要求补地点。',
}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sanitized(record):
    reply = record.get('reply') or {}
    return {'id': record['id'], 'question': record['question'], 'http_status': record['http_status'],
            'elapsed_seconds': record.get('elapsed_seconds'), 'answer': reply.get('answer'),
            'mode': reply.get('mode'), 'answerability': reply.get('answerability'),
            'resolved_question': reply.get('resolved_question'), 'context_used': reply.get('context_used'),
            'route': (reply.get('evidence_diagnostics') or {}).get('route'),
            'missing_information': reply.get('missing_information') or [],
            'objective_dataset_id': reply.get('objective_dataset_id'),
            'objective_artifacts': reply.get('objective_artifacts') or [],
            'sources':[{key:row.get(key) for key in ('citation_id','title','filename','url','locator')}
                       for row in reply.get('sources') or []],
            'cassette_summary': record.get('cassette_summary')}


def render_answer(text):
    """Small escaped renderer for the saved answers' headings and data tables."""
    def inline(value):
        value=html.escape(value,quote=True)
        value=re.sub(r'\*\*(.+?)\*\*',r'<strong>\1</strong>',value)
        def link(match):
            label,target=match.groups()
            if re.fullmatch(r'/api/objective/reports/[a-f0-9]{32}/[A-Za-z0-9_.-]+',target):
                return '<a href="http://127.0.0.1:8773'+target+'">'+label+'</a>'
            return match[0]
        return re.sub(r'\[([^\]]+)\]\(([^\s)]+)\)',link,value)
    lines=str(text or '').splitlines();output=[];index=0
    while index<len(lines):
        line=lines[index].strip()
        if line.startswith('|'):
            rows=[]
            while index<len(lines) and lines[index].strip().startswith('|'):
                cells=lines[index].strip().strip('|').split('|');index+=1
                if all(re.fullmatch(r'\s*:?-+:?\s*',cell) for cell in cells):continue
                rows.append(cells)
            output.append('<div style="overflow:auto"><table>'+''.join('<tr>'+''.join(
                ('<th>' if pos==0 else '<td>')+inline(cell.strip())+('</th>' if pos==0 else '</td>')
                for cell in cells)+'</tr>' for pos,cells in enumerate(rows))+'</table></div>')
            continue
        if line.startswith('**') and line.endswith('**'):output.append('<h3>'+inline(line[2:-2])+'</h3>')
        elif line:output.append('<p>'+inline(line)+'</p>')
        index+=1
    return ''.join(output)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--before', required=True, type=Path)
    p.add_argument('--after', required=True, type=Path)
    p.add_argument('--checks', required=True, type=Path, help='Reviewed program assertions and manual answer review')
    args = p.parse_args()
    checks = read(args.checks)
    pairs = [{'before': sanitized(read(args.before/(cid+'.result.json'))),
              'after': sanitized(read(args.after/(cid+'.result.json'))),
              'before_problem': BEFORE_NOTES[cid], 'review': checks.get(cid, {})} for cid in CASE_IDS]
    report_record = read(args.after/'REPORT.result.json')
    result = report_record.get('tool_result') or {}
    report_id = result.get('report_id')
    products = ROOT/'exports/浙江72小时客观海况报告_20261020'
    if report_id:
        source = ROOT/'data/forecast_runtime/objective/reports'/report_id
        products.mkdir(parents=True, exist_ok=True)
        for filename in ('report.html', 'report.json', 'series.csv', 'wave_peak.png', 'wind_peak.png', 'pressure_peak.png'):
            if (source/filename).is_file():
                shutil.copy2(source/filename, products/filename)
    junit = ROOT/'tmp/objective_final_validation.xml'
    tests = ET.parse(junit).getroot() if junit.is_file() else None
    suites = list(tests.iter('testsuite')) if tests is not None else []
    test_summary = {key: sum(int(s.get(key, 0)) for s in suites) for key in ('tests', 'failures', 'errors', 'skipped')}
    value = {'generated_at': datetime.now().astimezone().isoformat(), 'version': 'phase1.9.0-objective-products',
             'before_run': args.before.name, 'after_run': args.after.name,
             'source_stability': read(args.after/'run_completed.json').get('source_stability'),
             'scope': '4次同一对话真实专项复测；不是新一轮160题或专家验收。两次联网结果可能变化，不视为严格因果消融。',
             'python_tests': test_summary, 'cases': pairs, 'checks': checks,
             'product_directory': products.name, 'dataset_id': result.get('dataset_id')}
    out = ROOT/'exports/客观预报与同页问答_修改及真实测试结果_20261020'
    out.with_suffix('.json').write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    esc = lambda text: html.escape(str(text), quote=True)
    rows, sections = [], []
    for item in pairs:
        before, after, review = item['before'], item['after'], item['review']
        cid = after['id']
        rows.append('<tr>'+''.join('<td>'+esc(x)+'</td>' for x in (cid, item['before_problem'],
            review.get('status', '未审核'), review.get('conclusion', ''), after['answerability'], after['elapsed_seconds']))+'</tr>')
        sections.append(f'<section id="{cid}"><h2>{cid}：{esc(after["question"])}</h2>'
            f'<p>{esc(review.get("conclusion", ""))}</p><p>实际路由：{esc(after["route"])}；模型/程序状态：{esc(after["mode"])}；'
            f'HTTP {after["http_status"]}；上下文继承：{esc(after["context_used"])}</p>'
            f'<details><summary>改进前实际回答</summary><pre>{esc(before["answer"])}</pre></details>'
            f'<h3>改进后实际回答</h3><div class="answer">{render_answer(after["answer"])}</div>'
            f'<details><summary>改进后原始文本（逐字）</summary><pre>{esc(after["answer"])}</pre></details>'
            '<h3>本次回答来源</h3><ul>'+''.join('<li>'+esc('资料'+str(source.get('citation_id'))+'：'+str(source.get('title'))+'；'+str(source.get('locator') or ''))+
                (f' · <a href="{esc(source["url"])}">官方原文</a>' if str(source.get('url') or '').startswith('https://') else '')+'</li>' for source in after['sources'])+'</ul>'+
            f'<details><summary>解析后的查询及逐项核对</summary><pre>{esc(after["resolved_question"])}\n'
            f'{esc(json.dumps(review, ensure_ascii=False, indent=2))}</pre></details></section>')
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    page += '<title>客观预报与同页问答：修改及真实测试结果</title><style>body{background:#eef3f6;color:#183446;font:16px/1.7 system-ui,"Microsoft YaHei";margin:0}main{max-width:1160px;margin:auto;padding:26px}section,header{background:white;padding:26px;margin:18px 0;border-radius:12px}table{border-collapse:collapse;width:100%;font-size:14px}td,th{border:1px solid #dce6ec;padding:10px;text-align:left}th{background:#eaf3f5}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;background:#f8fafb;padding:18px}a{color:#057b88}img{max-width:100%}.note{border-left:4px solid #d29b39;padding-left:16px}</style><main>'
    page += '<header><h1>客观预报与同页问答：修改及真实测试结果</h1><p>版本 phase1.9.0-objective-products · 2026年10月20日</p>'
    page += '<p>数据接入、二维图层、72小时客观海况报告与原地图问答共用一个页面；原有历史台风地图继续保留。</p>'
    page += '<p><a href="http://127.0.0.1:8773/">打开新版地图问答</a> · <a href="浙江72小时客观海况报告_20261020/report.html">打开真实NC完整报告</a> · <a href="浙江72小时客观海况报告_20261020/series.csv">原生时次CSV</a></p></header>'
    page += '<section><h2>实现内容</h2><ul><li>读取经典CDF和HDF5三份NC，保存文件SHA并按区域/时刻切片；swh按有效波高、10u/10v按10米风、msl从Pa转hPa，时间暂按UTC转北京时间。</li><li>海浪色斑、风箭头、气压等压线、原生格点查询、有效时刻切换、PNG下载。</li><li>原生海点统计、北中南参考框、72小时连续窗口、极值位置与时刻、HTML/CSV/JSON/三张空间图。</li><li>相似台风明确已发生/机构预报路径；名称、年份和实际分数由程序校核后保留。移除了无实测时仅按强度距离推定严重风暴潮的逻辑。</li><li>官方预警概念按对象和问项组织；当日清单仍走正式预警档案查询。报告追问继承服务端输入槽并重查源文件。</li></ul></section>'
    page += '<section><h2>真实数据结果</h2><p>数据集 '+esc(result.get('dataset_id'))+'；起报 '+esc(result.get('batch'))+'。窗口为北京时间10月20日20时至23日20时。</p>'
    page += '<p>主参考框最大有效波高6.74 m（122°E，26.5°N，23日20时），最大海上10米风速23.48 m/s（120.4°E，26.5°N，23日20时），最低海平面气压1003.07 hPa（122.5°E，26.5°N，23日17时）。</p>'
    page += '<p class="note">以上是119.5°E—123.5°E、26.5°N—31.5°N近海参考框内的模式统计，包含邻近海域，不能称为浙江行政海域或各站实测极值。变量及时区暂按临时配置，数字由原文件计算，未手工指定。无波向变量，未生成波向图；未计算淹没、灾损或活动许可。</p></section>'
    page += '<section><h2>验证范围和结果</h2><p>'+esc(value['scope'])+'</p><p>Python共1876项常规测试及134项子测试通过；JUnit合计（含子测试）：'+esc(test_summary)+'。浏览器实测三图切换、有效时刻保持、地图点选和报告文件入口；四组Node前端合同测试通过。</p><p>实际复测4次完整问答，共19次模型调用。报告及追问的程序数据已完整交付；未通过核对的额外模型解读被隔离，后续仍可改善语言精简与稳定性。界面最后仅整理主查询按钮、折叠自定义JSON及分级标签；后端与录制版本一致。</p><div style="overflow:auto"><table><tr><th>专项</th><th>首轮发现</th><th>人工复核</th><th>结论</th><th>系统状态</th><th>耗时秒</th></tr>'+''.join(rows)+'</table></div></section>'
    page += '<section><h2>浏览器实际界面</h2><img src="同页地图风场_最终实测_20261020.png" alt="同页地图海面风及问答入口"><img src="浙江72小时报告_网页实测_20261020.png" alt="真实问题生成的72小时报告"></section>'
    page += ''.join(sections)
    page += '<section><h2>仍需后续完善</h2><p>业务方核定变量与时区后统一调整解析配置；用正式海域边界替换参考矩形；接入后续NC批次与发布流程。官方预警档案不能保证覆盖所有浙江地方发布机构，当前可用来源与覆盖情况按查询结果说明。通用知识的其他类型仍沿用现有CRAG，这4道专项不代表所有问题已经完整解决。</p><p>原始模型请求与响应仅在本机tmp/replay保存，本文不包含API密钥或原始录制。代码入口与启动方式见docs/客观预报产品与同页问答使用说明.md。</p></section></main></html>'
    out.with_suffix('.html').write_text(page, encoding='utf-8')
    print(json.dumps({'html':str(out.with_suffix('.html')), 'json':str(out.with_suffix('.json')), 'products':str(products)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
