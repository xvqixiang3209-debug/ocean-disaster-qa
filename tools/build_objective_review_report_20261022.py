"""Build the private, read-only HTML delivery from executed regression records."""
from collections import Counter
from datetime import datetime
import html
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'tmp/claude_review_20261022'
OUT=ROOT/'exports/客观预报复审修复与实测_20261022'
esc=lambda x:html.escape(str(x))


def build():
    summary=json.loads((WORK/'real_questions/summary.json').read_text('utf8'))
    baseline=json.loads((WORK/'p2_baseline_actual_dependencies.json').read_text('utf8'))
    fixed=json.loads((WORK/'p2_fixed_actual_dependencies.json').read_text('utf8'))
    masks=json.loads((WORK/'p2_real_nc_mask_audit.json').read_text('utf8'))
    OUT.mkdir(parents=True,exist_ok=True)
    sections=[]
    for row in summary['cases']:
        response=row['response'];artifacts=[]
        for artifact in row['artifacts']:
            name=artifact['filename'];identifier=row['report_id']
            origin=WORK/'real_questions/reports'/identifier/name
            target=OUT/'products'/identifier/name
            if origin.is_file():
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(origin,target)
                artifacts.append(f'<a href="products/{esc(identifier)}/{esc(name)}">{esc(name)}</a>')
        sections.append(f'<section id="{esc(row["id"])}"><h2>{esc(row["id"])} · {esc(row["question"])}</h2>'
            f'<p>实际状态：{esc(row["state"])}；地图要素：{esc(row["map_fields"])}；范围：{esc(row["scope"])}；'
            f'用时：{row["seconds"]}秒；检查：{esc(row["checks"])}。</p>'
            f'<details><summary>规范化查询与动作组</summary><pre>{esc(row["normalized_question"])}</pre>'
            f'<pre>{esc(json.dumps(response["answer_meta"].get("map_actions") or response["answer_meta"].get("map_action"),ensure_ascii=False,indent=2))}</pre></details>'
            f'<h3>系统实际回答（逐字）</h3><pre class="answer">{esc(response["answer"])}</pre>'
            f'<p>{" · ".join(artifacts)}</p></section>')
    overview=''.join(f'<tr><td><a href="#{esc(r["id"])}">{esc(r["id"])}</a></td><td>{esc(r["question"])}</td>'
                     f'<td>{esc(r["state"])}</td><td>{esc("、".join(r["map_fields"]))}</td></tr>' for r in summary['cases'])
    maskrows=''.join(f'<tr><td>{esc(r["level"])}</td><td>{r["first_frame_sea_cells"]}</td>'
                     f'<td>{r["static_source_sea_cells"]}</td><td>{r["changed_cell_count"]}</td></tr>' for r in masks)
    images=[]
    for name in ('zhujiajian_wind_switch.png','ningbo_own_peak.png','parent_scope_notice.png'):
        if (WORK/name).is_file():
            shutil.copy2(WORK/name,OUT/name)
            images.append(f'<figure><img src="{name}" alt="{esc(name)}"><figcaption>{esc(name)}</figcaption></figure>')
    for name in ('summary.json','p2_baseline_actual_dependencies.json','p2_fixed_actual_dependencies.json','p2_real_nc_mask_audit.json'):
        origin=WORK/'real_questions/summary.json' if name=='summary.json' else WORK/name
        shutil.copy2(origin,OUT/name)
    pytest_log=(WORK/'full_pytest.txt').read_bytes()
    pytest_text=pytest_log.decode('utf-16') if pytest_log.startswith(b'\xff\xfe') else pytest_log.decode('utf8',errors='replace')
    resultline=next((line for line in reversed(pytest_text.splitlines()) if 'passed' in line),'未读取到计数')
    body=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>客观预报复审修复与真实NC实测</title>
<style>body{{max-width:1180px;margin:30px auto;padding:0 24px;font:16px/1.75 "Microsoft YaHei",sans-serif;color:#173846;background:#f1f6f8}}section,.card{{background:white;border:1px solid #cfdee4;border-radius:10px;padding:24px;margin:24px 0}}h1{{font-size:28px}}h2{{font-size:21px}}h3{{font-size:17px}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{border:1px solid #ccdce2;padding:8px;text-align:left}}th{{background:#e7f2f4}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.8 "Microsoft YaHei",sans-serif}}a{{color:#007e95}}img{{width:100%;height:auto}}figure{{margin:20px 0}}.answer{{border-left:3px solid #40a8b6;padding:12px 16px;background:#f7fbfc}}</style>
<h1>客观预报复审修复与真实NC实测</h1><p>phase1.12.0-objective-review · 2026-10-22 · <a href="http://127.0.0.1:8775/">打开新版无模型预览</a></p>
<div class="card"><h2>完成内容</h2><p>本轮修复地点四态解析、无新坐标的父级明示回退、未知/越界清图、完整源时间轴静态海点域、缺测统计、风浪动作组与各要素峰值时间。新增晚期异议的一次定向复核，报告解读改为默认按需启用。</p>
<p>Python本轮实际重跑：<b>{esc(resultline)}</b>。三个Node文件实际通过。13条真实本地NC问答检查通过；模型调用0、下载/检索网络调用0。另有浏览器无模型实测，截图如下。未重跑160题，未重新录制旧24次，未将程序测试算成业务专家正确率。</p>
<p>本次使用本地最新已登记L5：{esc(summary['dataset_id'])}；起报{esc(summary['batch'])}，测试原生时次{esc(summary['selected_native_time'])}。这不是本轮重新联网下载数据的测试。</p></div>
<div class="card"><h2>修复前后：相同合成NC、真实依赖</h2><table><tr><th>检查</th><th>旧代码</th><th>新代码</th></tr>
<tr><td>报告波高峰值</td><td>{baseline['stats72_wave_peak']['value']} m</td><td>{fixed['stats72_wave_peak']['value']} m</td></tr>
<tr><td>海点数 / 缺测样本</td><td>{baseline['stats72_sea_count']} / {baseline['stats72_missing_count']}</td><td>{fixed['stats72_sea_count']} / {fixed['stats72_missing_count']}</td></tr>
<tr><td>大于5米样本</td><td>{baseline['stats72_threshold_hits_gt5']}</td><td>{fixed['stats72_threshold_hits_gt5']}</td></tr>
<tr><td>恢复点的风</td><td>{esc(baseline['wind_frame_at_recovered_point'])}</td><td>{esc(fixed['wind_frame_at_recovered_point'])} m/s</td></tr>
<tr><td>报告状态</td><td>{esc(baseline['report_state'])}</td><td>{esc(fixed['report_state'])}</td></tr></table>
<p>新报告、过程、逐格最大值和峰值单帧一致；永久无效点透明。不是用h5py空壳代测，实际使用本机h5py和scipy。</p>
<h3>真实NC全源域差异</h3><table><tr><th>层级</th><th>首帧海点</th><th>全源静态海点</th><th>新增候选海点</th></tr>{maskrows}</table><p>新增格点的各时次仍分别检查缺测；不把全源某时有效误当成整窗都有数。真实L2/L5风浪跨出口峰值及时间一致；详细JSON随报告保留。</p></div>
<div class="card"><h2>实际问题一览</h2><table><tr><th>编号</th><th>实际问题</th><th>返回状态</th><th>地图要素</th></tr>{overview}</table>
<p>partial表示暴露真实缺测，no_data表示目标时段确无覆盖，越界/待范围表示明确阻断代答；这些预期行为与检查失败分开。</p></div>
<div class="card"><h2>页面实测</h2>{''.join(images)}</div>
{''.join(sections)}
<div class="card"><h2>仍需后续验证</h2><p>原问答P1–P6的子问归属、首轮归纳证明、旧录制正文哈希问题继续跟踪；本轮未新增付费模型验证。精细岸线、带底图图片导出、风浪同屏叠加属于扩展。本地内部交接包仍是上一快照，未冒称已重打包。变量和时区沿用临时解释，L2/L5没有气压，MWD未画。</p>
<p><a href="summary.json">13条原始问答JSON</a> · <a href="p2_real_nc_mask_audit.json">真实NC掩膜核查</a> · <a href="p2_baseline_actual_dependencies.json">旧合成结果</a> · <a href="p2_fixed_actual_dependencies.json">新合成结果</a></p></div></html>'''
    (OUT/'实测报告.html').write_text(body,encoding='utf8')
    print(str(OUT/'实测报告.html'))


if __name__=='__main__':build()
