"""Build read-only audit deliverables from the applied 2026-10-21 review evidence.

This exporter never opens a database, calls a model, fetches a page, or applies a
review decision. Re-running it only refreshes its own report directory. All QA
answers are copied verbatim from recorded responses, with model self-evaluation
kept separate from deterministic diagnostic observations.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


APP = Path(__file__).resolve().parents[1]
BASE = APP / "tmp/source_review_followup_20261021"
OUT = APP / "exports/文字资料续核与业务口径核查_20261021"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def joined(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "；".join(joined(x) for x in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def csv_write(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows({k: joined(row.get(k)) for k in fields} for row in rows)
    with path.open(encoding="utf-8-sig", newline="") as f:
        assert len(list(csv.DictReader(f))) == len(rows), path


def href(path: str | Path, label: str | None = None) -> str:
    target = str(path)
    url = target if re.match(r"https?://", target) else Path(target).resolve().as_uri()
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(label or target)}</a>'


def table(headers: list[str], rows: list[list], cls: str = "") -> str:
    body = "".join("<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>" for row in rows)
    return '<div class="scroll"><table class="' + cls + '"><thead><tr>' + "".join(
        f"<th>{html.escape(x)}</th>" for x in headers
    ) + "</tr></thead><tbody>" + body + "</tbody></table></div>"


def e(value) -> str:
    return html.escape(joined(value))


def render_answer(text: str) -> str:
    """Render the application's small Markdown subset without executing HTML."""
    lines = (text or "未取得答案文本，请查看原始记录。").splitlines()
    blocks, paragraph = [], []

    def inline(value: str) -> str:
        return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(value))

    def flush():
        if paragraph:
            blocks.append("<p>" + "<br>".join(inline(x) for x in paragraph) + "</p>")
            paragraph.clear()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            flush()
        elif line.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1]):
            flush()
            header = [x.strip() for x in line.strip("|").split("|")]
            i += 2
            values = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                values.append([inline(x.strip()) for x in lines[i].strip().strip("|").split("|")])
                i += 1
            blocks.append(table(header, values))
            continue
        elif line.startswith("**") and line.endswith("**"):
            flush()
            blocks.append("<h3>" + inline(line[2:-2]) + "</h3>")
        elif re.match(r"^#{1,4}\s", line):
            flush()
            blocks.append("<h3>" + inline(re.sub(r"^#{1,4}\s+", "", line)) + "</h3>")
        else:
            paragraph.append(line)
        i += 1
    flush()
    return "".join(blocks)


def evidence_paths(item: dict) -> list[str]:
    found = []
    def walk(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in {"path", "artifact_path", "evidence_image_path", "reviewed_text_path", "raw_path", "official_raw_path"} and isinstance(value, str):
                    found.append(value)
                elif isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)
    walk(item)
    return list(dict.fromkeys(found))


def question_records(base: Path) -> list[dict]:
    root = base / "questions"
    records = []
    if not root.exists():
        return records
    phases = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: (
        0 if p.name == "before" else 1 if p.name == "after" else 2, p.name
    ))
    for phase in phases:
        for path in sorted(phase.glob("*.json")):
            if path.stem == "summary":
                continue
            case = load(path)
            if not isinstance(case, dict) or "question" not in case:
                continue
            reply = case.get("actual_reply") or case.get("response") or {}
            if not isinstance(reply, dict):
                continue
            answer = reply.get("answer") or ""
            diag = reply.get("evidence_diagnostics") or {}
            observations = []
            # These are presentation/route checks, not an automatic truth grade.
            if case.get("id") not in {"numeric"} and "试算结果（假设口径）" in answer:
                observations.append("规范条款问题出现试算标题：应核对业务路由。")
            if case.get("id") in {"temperature", "current", "directions"} and "待补齐" in answer and any(x in answer for x in ("适用地点或海域", "对应日期和时刻")):
                observations.append("已给规范数值后仍追补地点/日期：应核对子问类型与额外缺项。")
            if case.get("id") == "province" and not all(x in answer for x in ("提示单", "指令单", "督办单")):
                observations.append("三张单名称未全部进入实答；对应正式正文已在库，属于召回/覆盖问题。")
            if case.get("id") == "numeric":
                observations.append("原值可核查；单位、年月、基准未定是实际数据口径缺项，不因回答返回成功而消失。")
            if case.get("id") == "general" and "基本任务" in answer and "未以" in answer:
                observations.append("“基本任务/观测目的”措辞对应被列为限制，需区分术语解释与实际缺数据。")
            records.append({
                "phase": phase.name, "id": case.get("id", path.stem), "question": case["question"],
                "http_status": case.get("http_status"), "seconds": case.get("seconds"),
                "answerability_model_self_report": reply.get("answerability"),
                "answer_kind": reply.get("answer_kind"), "business_state": reply.get("business_state"),
                "answer": answer, "sources": reply.get("sources") or [],
                "subquestion_coverage": reply.get("subquestion_coverage"),
                "diagnostic_observations": observations,
                "answer_budget": diag.get("answer_budget"),
                "record_path": str(path.resolve()), "record_sha256": sha(path),
                "raw_record": case,
            })
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=BASE)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--qa-assessments", type=Path, help="Optional recorded human assessment JSON; no new model calls")
    args = parser.parse_args()
    base, out = args.base.resolve(), args.output.resolve()
    if out == base or base in out.parents:
        raise ValueError("Report output must remain separate from source evidence")
    out.mkdir(parents=True, exist_ok=True)
    initial = load(base / "initial_text_sources.json")
    applied = load(base / "application_result.json")
    plan = load(base / "application_plan.json")
    integrity = load(base / "integrity_after.json")
    fulltext = load(base / "fulltext/followup_fulltext_review_manifest.json")
    versions = load(base / "versions/version_followup.json")
    numeric = load(base / "numeric/numeric_semantics_followup.json")
    additions = load(base / "excerpt_additions.json")
    by_plan = {x["source_id"]: x for x in plan["items"]}
    by_applied = {x["source_id"]: x for x in applied["items"]}
    by_fulltext = {x["source_id"]: x for x in fulltext["documents"]}
    by_version = {x["source_id"]: x for x in versions["items"]}
    by_excerpt = {x["parent_source_id"]: x for x in additions}
    assert len(initial) == 39
    assert len(by_fulltext) == 28 and len(by_version) == 11
    rows, remaining_pages, confirmations = [], [], []
    for n, original in enumerate(initial, 1):
        sid = original["source_id"]
        change = by_applied[sid]
        decision = by_plan[sid]
        meta = decision.get("metadata_patch", {})
        doc, version = by_fulltext.get(sid), by_version.get(sid)
        group_before = "全文待核对" if doc else "版本待确认"
        if change["status"] == "approved":
            group_after = "已批准（完整规范性业务正文及附录）"
            result = "已核准校录的完整规范性中文业务正文及相关附录；前言、参考文献、英文对照等留原件，不作新增业务依据。"
        elif change["status"] == "archived":
            group_after = "已归档（有已批准正式替代正文）"
            result = version["reason"]
        elif doc:
            group_after = "部分内容已审核"
            result = "仅所列业务条款逐页核对并建立已批准节选；原件其余内容保持待审。"
        else:
            group_after = "版本待确认"
            result = version["reason"]
        scope = meta.get("approved_text_scope") or meta.get("review_followup_20261021", {}).get("scope", "")
        locators = [x["locator"] for x in doc.get("reviewed_excerpts", [])] if doc else []
        issues = []
        if doc and change["status"] != "approved":
            for page in doc.get("page_specific_remaining_issues", []):
                remaining_pages.append({
                    "来源ID": sid, "资料名称": original["filename"], "原件SHA256": original["sha256"],
                    "物理页": page["page"], "仍需核对事项": joined(page.get("issues")),
                    "本页已核范围": joined([x["locator"] for x in doc.get("reviewed_excerpts", []) if f'page:{page["page"]};' in x.get("locator", "")]),
                    "整页已批准": "否", "原件路径": original["raw_path"],
                    "核查记录": str(base / "fulltext/followup_fulltext_review_manifest.json"),
                })
            issues = ["未列入已核节选的正文、公式、图表仍需逐页/逐单元核对；详见页面清单。"]
        if version:
            issues.extend(version.get("unresolved_items", []))
            if change["status"] != "archived":
                confirmations.append({
                    "类别": "正式全文缺失" if version.get("formal_standard_number") else "公报正式稿缺失" if sid == "5d63b002eca050766926d500" else "版本与业务适用性",
                    "对象": original["filename"], "覆盖数量": 1, "现已取得": result,
                    "还需取得或确认": joined(version.get("unresolved_items", [])),
                    "提供方": "资料提供方/业务主管单位；标准全文可通过单位授权或正规出版渠道取得",
                    "确认前处理": "维持版本待确认；已核节选只按原批准范围使用，不冒充正式全文。",
                    "来源ID": sid, "证据路径或模板": str(base / "versions/version_followup.json"),
                })
        artifact_list = evidence_paths(decision.get("verification", {}))
        if doc:
            artifact_list += evidence_paths(doc.get("reviewed_excerpts", []))
        if version:
            artifact_list += evidence_paths(version)
        rows.append({
            "序号": n, "原分组": group_before, "资料名称": original["filename"], "来源ID": sid,
            "本轮处理结果": group_after, "实际执行": result,
            "已核范围": scope or joined(locators), "已核条款定位": joined(locators),
            "物理总页数": doc.get("physical_page_count", "") if doc else "",
            "已目视页": joined(meta.get("verified_physical_pages") or (doc.get("actual_visually_inspected_pages", []) if doc else [])),
            "新增或复用节选ID": by_excerpt.get(sid, {}).get("source_id", ""),
            "正式替代来源ID": joined(version.get("formal_replacement_source_ids", [])) if version else "",
            "剩余事项": joined(issues), "需要业务方确认": "是" if version and change["status"] != "archived" else "否（未核页面另列，不自动批准）" if doc and change["status"] != "approved" else "否",
            "原件SHA256": original["sha256"], "原件路径": original["raw_path"],
            "证据路径": joined(list(dict.fromkeys(artifact_list))),
            "外发权限": "原有显式权限不变；测试只使用另行授权的已核切片。",
        })
    assert Counter(x["本轮处理结果"] for x in rows) == {
        "已批准（完整规范性业务正文及附录）": 5,
        "部分内容已审核": 23,
        "已归档（有已批准正式替代正文）": 2,
        "版本待确认": 9,
    }
    templates = numeric["confirmation_templates"]["template_outputs"]
    template_map = {}
    for item in templates:
        path = Path(item["csv"])
        assert sha(path) == item["csv_sha256"], path
        target = out / path.name
        shutil.copy2(path, target)
        assert sha(target) == item["csv_sha256"]
        template_map[path.name] = item
    numeric_kinds = ["产品量纲与集合含义", "站点/节点/岸段对应", "完整有效时间", "岸段阈值版本"]
    numeric_quantities = ["4种产品×11种模板＝44组，覆盖880本", "11个模板；40个别名候选覆盖548列，另有同名歧义", "220批次（可由可验证的统一规则覆盖）", "198岸段；4岸段13项数值冲突"]
    numeric_template_names = [templates[i]["csv"] for i in (0, 2, 1, 3)]
    for i, task in enumerate(numeric["external_must_confirm"]):
        confirmations.append({
            "类别": numeric_kinds[i], "对象": task["unit_of_confirmation"], "覆盖数量": numeric_quantities[i],
            "现已取得": "880本ZIP/XML读取无失败，原值/精确表头/批次序列/冲突位置已归档。",
            "还需取得或确认": joined(task["required_fields"]), "提供方": "产品生产方/岸段阈值维护单位",
            "确认前处理": "保持unknown或明确assumed；不得写为verified，不自动改写880本/岸段阈值。",
            "来源ID": "881份结构化资料", "证据路径或模板": str(out / Path(numeric_template_names[i]).name),
        })
    csv_write(out / "39份资料逐项处理.csv", rows, list(rows[0]))
    csv_write(out / "仍需确认事项.csv", confirmations, list(confirmations[0]))
    csv_write(out / "未全文核对页面清单.csv", remaining_pages, list(remaining_pages[0]))

    qa = question_records(base)
    assessments = load(args.qa_assessments.resolve()) if args.qa_assessments else None
    assessment_rows = assessments.get('rows', []) if isinstance(assessments, dict) else []
    assessment_table = table(['题类', '逐题核查', '实际交付', '仍需完善或确认'], [
        [e(x.get('题类')), e(x.get('核查结论')), e(x.get('已核实际内容')), e(x.get('仍需完善或确认'))]
        for x in assessment_rows]) if assessment_rows else ''
    dump(out / "实际问答与前后对照.json", {"generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "3道资料续核前基线；8类续核后实答及已落盘的代码修正复测。不是160题全量验收。",
        "model_self_report_is_not_accuracy_grade": True, "records": qa, "manual_assessments": assessments})
    phase_counts = Counter(x["phase"] for x in qa)
    latest = {}
    for record in qa:
        latest[record["id"]] = record
    self_counts = Counter(str(x["answerability_model_self_report"]) for x in latest.values())
    # Files/results are links to exact recorded artifacts, not a claim of recency.
    tests = []
    for path in sorted((APP / "tmp").glob("*review*20261021*.txt")):
        text = path.read_text(encoding="utf-8", errors="replace")
        tests.append({"path": str(path), "tail": "\n".join(text.splitlines()[-8:]), "sha256": sha(path)})
    file_rows = [f'<li>{href(out / fn, fn)}</li>' for fn in (
        "39份资料逐项处理.csv", "仍需确认事项.csv", "未全文核对页面清单.csv", "实际问答与前后对照.json")]
    file_rows += [f'<li>{href(out / name, name)}（{item["rows"]}行）</li>' for name, item in template_map.items()]
    source_table = table(["原分组", "资料", "本轮结果", "剩余事项"], [[
        e(x["原分组"]), '<strong>'+e(x["资料名称"])+f'</strong><br><small>{e(x["来源ID"])}</small>',
        e(x["本轮处理结果"])+"<br>"+e(x["已核范围"]), e(x["剩余事项"]) or "已列范围内完成",
    ] for x in rows], "filterable")
    confirmation_table = table(["类别", "对象/数量", "需要提供或确认", "处理边界"], [[
        e(x["类别"]), e(x["对象"])+"<br>"+e(x["覆盖数量"]), e(x["还需取得或确认"]), e(x["确认前处理"]),
    ] for x in confirmations])
    qa_table = table(["题类", "阶段", "HTTP", "模型自报", "程序/阅读诊断"], [[
        '<a href="#qa-'+e(x["phase"])+"-"+e(x["id"])+ '">'+e(x["id"])+"</a>",
        e(x["phase"]), e(x["http_status"]), e(x["answerability_model_self_report"]), e(x["diagnostic_observations"]) or "此项未触发所列确定性诊断；不等于已人工确认所有事实。",
    ] for x in qa])
    qa_details = []
    for record in qa:
        source_items = []
        for source in record["sources"]:
            title = source.get("filename") or source.get("title") or source.get("name") or source.get("source_id", "资料")
            locator = source.get("locator") or source.get("location") or ""
            url = source.get("source_url") or source.get("url")
            source_items.append("<li>" + (href(url, title) if url and re.match(r"https?://", url) else e(title)) + " " + e(locator) + "</li>")
        qa_details.append(f'<details class="qa" id="qa-{e(record["phase"])}-{e(record["id"])}"><summary>{e(record["phase"])} · {e(record["id"])} · {e(record["question"])}</summary>'
            f'<p>HTTP {e(record["http_status"])}；耗时{e(record["seconds"])}秒；模型自报{e(record["answerability_model_self_report"])}；回答类型{e(record["answer_kind"])}</p>'
            '<div class="answer">'+render_answer(record["answer"] or "未取得答案文本，请查看原始记录。")+'</div>'
            '<details><summary>来源及原始记录</summary><ul>'+"".join(source_items)+'</ul><p>'+href(record["record_path"], "完整原始请求结果 JSON")+'</p></details></details>')
    official_rows = []
    for item in versions["prior_public_sources_do_not_duplicate"]:
        official_rows.append([e(item.get("title")), e(item.get("existing_source_id")), e(item.get("existing_status")), "复用已有批准文本，未重复导入"])
    enrich = versions["existing_source_enrichments"][0]
    official = enrich["official_evidence"]
    normative_rows = [[e(x["资料名称"]), e(x["已核范围"]), e(x["已目视页"])] for x in rows if x["本轮处理结果"].startswith("已批准")]
    styles = """
    :root{--ink:#19313a;--muted:#526b73;--teal:#087c82;--line:#d7e3e6;--bg:#f2f7f8;--warn:#fff4df}
    *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 'Microsoft YaHei','Noto Sans CJK SC',sans-serif}
    main{max-width:1280px;margin:auto;padding:36px 30px 80px}h1{font-size:31px;margin:0 0 10px}h2{font-size:23px;margin:34px 0 12px}h3{font-size:18px;margin:22px 0 8px}p{margin:10px 0}
    a{color:#006b92;word-break:break-all}small,.muted{color:var(--muted)}.eyebrow{color:var(--teal);font-weight:700;letter-spacing:1px}.panel{background:white;border:1px solid var(--line);border-radius:12px;padding:20px;margin:16px 0}
    .cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:22px 0}.card{background:white;border:1px solid var(--line);border-top:4px solid var(--teal);padding:16px;border-radius:9px}.card strong{display:block;font-size:32px;color:var(--teal)}
    .callout{background:var(--warn);border-left:4px solid #b88120;padding:16px 20px;margin:18px 0}.scroll{overflow:auto}table{border-collapse:collapse;background:white;width:100%;font-size:14px}th,td{border:1px solid var(--line);padding:11px 13px;vertical-align:top;text-align:left}th{background:#e8f2f4;color:#163f49}td{min-width:110px}td:nth-child(2){min-width:240px}
    details{border:1px solid var(--line);border-radius:8px;background:white;padding:14px 17px;margin:12px 0}summary{cursor:pointer;font-weight:700}.qa{scroll-margin-top:16px}.answer{overflow-wrap:anywhere;font:15px/1.8 'Microsoft YaHei',sans-serif;background:#f6f9fa;padding:16px;border-radius:8px}.answer h3{font-size:16px}.answer td{min-width:60px}pre{white-space:pre-wrap;overflow-wrap:anywhere}input{width:100%;padding:12px;font:inherit;border:1px solid #8fb0ba;border-radius:7px;margin:8px 0 14px}nav{display:flex;gap:16px;flex-wrap:wrap;margin:20px 0}ul{padding-left:24px}.pill{display:inline-block;background:#e6f4f4;color:#086b70;padding:2px 8px;border-radius:4px}
    @media(max-width:720px){main{padding:22px 15px}.cards{grid-template-columns:repeat(2,1fr)}h1{font-size:25px}}@media print{body{background:white}main{padding:0}.scroll{overflow:visible}input,nav{display:none}details{break-inside:avoid}.cards{grid-template-columns:repeat(4,1fr)}a{color:inherit}}
    """
    report = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>文字资料续核与业务口径核查 · 2026-10-21</title><style>{styles}</style></head><body><main>
    <p class="eyebrow">海洋灾害知识问答 · 资料治理记录</p><h1>文字资料续核与业务口径核查</h1>
    <p class="muted">核查日：2026年10月21日。报告生成：{e(datetime.now().astimezone().isoformat(timespec="seconds"))}。依据已执行批次与已落盘问答记录，不进行新查询或修改数据库。</p>
    <p>原28份全文待核对资料已逐份处理：5份标准的完整规范性中文业务正文及相关附录已校录准入；另外23份只批准所列业务条款，原件转入“部分内容已审核”。11份版本资料中，2份有正式替代正文的旧稿已归档，9份仍需正式全文或版本依据。</p>
    <div class="cards"><div class="card">规范性业务正文准入<strong>5</strong></div><div class="card">原件只核部分条款<strong>23</strong></div><div class="card">旧版本已归档<strong>2</strong></div><div class="card">版本仍待确认<strong>9</strong></div></div>
    <div class="callout">“全文待核对”变为0，不表示28份原PDF每一行都已批准。23份原件仍有未核正文、公式或表格；已经核准的条款通过22份独立节选进入问答，1对同SHA原件别名复用同一节选。5份标准的前言、参考文献、英文对照等留在原件，未作为新增业务条款。</div>
    <nav><a href="#scope">核查与状态</a><a href="#sources">39份逐项处理</a><a href="#numeric">881份数值资料</a><a href="#confirm">需要确认</a><a href="#qa">实际问答</a><a href="#files">下载与追溯</a></nav>
    <section id="scope"><h2>核查范围与已执行结果</h2><div class="panel"><p>28份PDF合计461个物理页完成双引擎提取和风险定位；461页是机械处理页数。5份较短标准合计35个物理页实际目视检查，完整校录规范性业务正文及附录；其他23份只逐项核对所列条款。HY/T 059—2002原文层大量为购书水印，新增10页本地OCR；这些OCR结果仍需逐页核对，未整体升级为批准。</p>
    <p>版本补查复用了已有2023年修订条例、2020年正式公报等来源。浙江2024年防汛防台抗旱预案的本地已批准版本，与官方PDF的38页原生文字逐页完全相同，本轮仅补版本证据，未重复导入。{href(official['source_url'], '浙江省2024年预案官方PDF')}。</p>
    <p>生产专业资料库现有{integrity['records_after']:,}条来源、{integrity['chunks']:,}个切片，其中已批准112、归档2。待复核共922：原值已恢复/口径待确认881、部分内容已审核32、全文待核对0、版本待确认9。历史台风库不在本轮修改范围。</p></div>
    {table(['已核准标准','批准范围','实际目视物理页'],normative_rows)}
    <h3>原件与权限完整性</h3><p>原有{integrity['original_records_retained']:,}条来源记录全部保留，对{integrity['original_files_hashed']:,}份不同原文件重新核对字节和SHA。原件身份变更{len(integrity['original_identity_changes'])}、原有显式外发权限变更{len(integrity['explicit_permission_changes'])}、孤立切片关联{integrity['orphan_links']}；切片与FTS均为{integrity['chunks']:,}条。新增22份已核节选不覆盖原件。{href(base/'integrity_after.json','查看完整性核对结果')}。</p></section>
    <section id="sources"><h2>39份资料逐项处理</h2><p>筛选资料名、来源ID或处理状态。原件详细路径、SHA、目视页、准入范围和剩余事项见CSV。</p><input id="sourceFilter" placeholder="例如：海温 / 版本待确认 / 2014" aria-label="筛选39份资料">{source_table}</section>
    <section id="numeric"><h2>881份“原值已恢复、业务口径待确认”能完成哪些工作</h2><div class="panel"><p>本轮完成880本非空工作簿的全部ZIP/XML读取、4类产品与11种精确表头归组、220批次四产品时间序列一致性核查，以及站点名称和岸段阈值差异定位。技术核查可自动完成；现有原件没有提供单位、完整年月/时区、高程基准、集合统计定义，不能凭数值像厘米就标为业务已核。</p>
    <p>4类产品各220本，共880本。按产品×表头形成44组确认项；生产方如提供覆盖全部批次的统一产品字典和时间规则，可一次核定后批量应用，无需逐本人工审核。来源名称别名40项覆盖548列，仅是候选，不是已核定的测站或模型节点身份；健跳、石塘仍存在同名站歧义。</p>
    <p>198岸段资料仍有4岸段13项有效阈值差异：武原、西塘桥、乍浦、独山港。旧56岸段公开阈值表与本地198乡镇岸段边界尺度不同，不能直接替代。大陈已确认的无效值继续按无效处理，无需重复确认。</p>
    <p>文件名不等于有效日期：同一历史场景多批文件导出日期变化，但内部DD/HH:mm时间序列相同。天文潮TXT的厘米/日期规则只适用于该格式，不能移用到这些Surge/Waterlevel工作簿。</p></div></section>
    <section id="confirm"><h2>仍需资料提供方或业务同事确认</h2><p>9份版本资料及4组数值口径任务如下。未核页面另有逐页清单，可继续按业务优先级完成技术复核；表中业务版本、数据字典及阈值授权来源无法靠OCR自行创造。</p>{confirmation_table}</section>
    <section id="qa"><h2>相关真实问答测试</h2><p>已记录阶段：{e(dict(phase_counts))}。最新一条/题的模型自报：{e(dict(self_counts))}。这是模型返回状态，不是事实准确率或人工验收通过率。before为续核前3题；after为资料准入后的8类初测；verified及后续目录为代码修正复测，保留各次实际回答。</p>
    <p>初测确认海冰条款和历史工作簿原值可查；也暴露海温、海流规范条款误进预报试算、额外追补地点时间，以及“三张单”已在库却未进入回答的问题。这些属于路由、子问归属或召回缺陷，不能归成缺数据。以下保存各次原文，便于核对改善是否真正进入答案。</p>
    {('<p>'+e(assessments.get('scope'))+'</p><h3>最新实际回答逐题核查</h3>'+assessment_table) if assessment_rows else ''}
    <p>本轮同步修复了规范条款与实际预报的路由区分、规范性频次/时效的交付字段、指定文号与正文短术语召回，以及表格“列名/单位”和开闭区间的数值校验。实际地点日期的业务查询仍要求相应数据；未确认的单位和基准仍保留unknown。</p>
    {qa_table}{''.join(qa_details)}
    {('<details><summary>人工/专项核查记录</summary><pre>'+e(assessments)+'</pre></details>') if assessments is not None else '<p class="muted">尚未附加独立人工总评分；本报告不把HTTP成功、带引用或模型自报full直接等同于全部正确。</p>'}
    <h3>代码验证记录</h3>{''.join('<details><summary>'+href(x['path'],Path(x['path']).name)+'</summary><pre>'+e(x['tail'])+'</pre></details>' for x in tests) or '<p>尚未提供测试日志，请查看后续更新。</p>'}</section>
    <section id="files"><h2>下载与追溯</h2><ul>{''.join(file_rows)}</ul><p>审计证据已保留本机：{href(APP/'data/marine_kb/review_evidence/20261021_followup','持久化核查证据目录')}。执行前数据库备份：{href(applied['backup'],'本轮执行前备份')}。</p><details><summary>本轮原始审核依据</summary><ul>{''.join('<li>'+href(base/fn,fn)+'</li>' for fn in ['application_plan.json','application_result.json','excerpt_additions.json','lineage_application_result.json','integrity_after.json','fulltext/followup_fulltext_review_manifest.json','versions/version_followup.json','numeric/numeric_semantics_followup.json'])}</ul></details>
    <p class="muted">报告构建器仅导出已有证据与问答记录，不重应用审核，不改原文件、资料状态、外发权限或阈值。SHA清单用于核对文件是否变化。</p></section>
    </main><script>document.getElementById('sourceFilter').addEventListener('input',function(){{let v=this.value.toLowerCase();document.querySelectorAll('table.filterable tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(v));}});</script></body></html>'''
    report_path = out / "续核与问答测试报告.html"
    report_path.write_text(report, encoding="utf-8")
    manifest = {"generated_at": datetime.now(timezone.utc).isoformat(), "source_records": len(rows),
        "source_outcomes": dict(Counter(x["本轮处理结果"] for x in rows)),
        "remaining_page_rows": len(remaining_pages), "confirmation_task_rows": len(confirmations),
        "question_record_count": len(qa), "question_phases": dict(phase_counts),
        "files": [{"name": x.name, "sha256": sha(x), "bytes": x.stat().st_size} for x in sorted(out.iterdir()) if x.is_file() and x.name != "交付文件校验清单.json"]}
    dump(out / "交付文件校验清单.json", manifest)
    print(json.dumps({"report": str(report_path), **{k: v for k, v in manifest.items() if k != "files"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
