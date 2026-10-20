"""Public terminology searches, kept separate from private corpus retrieval."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import os
import re
import threading
import time
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from .retrieval import STORM_ALIASES
from .query_intents import DEFENSIVE_ACTION_QUESTION
from .answer_design import is_policy_lookup
from .evidence_selection import select_evidence
from .public_service_intents import public_queries, focus_words, focus_groups
from .public_sources import ACADEMIC_DOMAINS, primary_routes, research_question,research_focus,QA_EVIDENCE_RE
from .evidence_applicability import (assess_page, coverage_matrix, leaf_places, DATE,
                                     OBJECTS, question_context, parse_date, reconcile_notices, named_objects)


# Include the regional marine service and the operator's own announcements.
# Trust here permits reading a primary source; it does not establish that an
# announcement is in force, or make an operator a government authority.
TRUSTED_DOMAINS = ('cma.gov.cn', 'mnr.gov.cn', 'nmefc.cn', 'nmc.cn', 'weather.com.cn', 'gov.cn',
                   'wmo.int', 'noaa.gov', 'zjocean.org.cn', 'hxzs.com.cn','nmemc.org.cn','95598.cn')
TERMS = ('近岸海域', '近海海域', '风暴增水', '天文潮', '风暴潮', '有效波高', '警戒潮位', '热带气旋', '海平面',
         '高潮位', '低潮位', '风暴潮位', '潮位基准', '波周期', '预报时效', '海洋灾害', '海水倒灌',
         '离岸流', '潮汐表', '潮差', '潮高', '风速', '风级', '海冰',
         '海啸', '海浪', '台风', '赤潮', '涌浪', '风浪', '潮位', '潮汐', '增水')

# A source index, not stored answers: every selected URL is read at runtime.
# These primary pages prevent a popular news article displacing a definition.
REFERENCE_PAGES = {
    '台风': ('台风的定义', 'https://www.cma.gov.cn/2011xzt/kpbd/typhoon/2018050901/201807/t20180717_473579.html'),
    '热带气旋': ('台风的定义', 'https://www.cma.gov.cn/2011xzt/kpbd/typhoon/2018050901/201807/t20180717_473579.html'),
    '风暴潮': ('什么是天文潮？什么是风暴潮？', 'https://www.weather.com.cn/zt/kpzt/1084392.shtml'),
    '风暴增水': ('什么是天文潮？什么是风暴潮？', 'https://www.weather.com.cn/zt/kpzt/1084392.shtml'),
    '天文潮': ('什么是天文潮？什么是风暴潮？', 'https://www.weather.com.cn/zt/kpzt/1084392.shtml'),
    '海啸': ('海啸避险常识', 'https://www.cma.gov.cn/kppd/kppdqxwq/kppdfzbd/201211/t20121126_192398.html'),
    '海浪': ('Why does the ocean have waves? — NOAA', 'https://oceanservice.noaa.gov/facts/wavesinocean.html'),
    '叠加': ('风暴潮及其危害', 'https://www.weather.com.cn/index/lssj/06/21568.shtml'),
    '台风季节': ('新闻分析：立冬后为何仍有台风？', 'https://www.cma.gov.cn/2011xwzx/2011xmtjj/202411/t20241115_6694486.html'),
    '台风月份': ('大数据盘点台风活跃规律', 'https://news.weather.com.cn/2023/08/3642140.shtml'),
    '五停杭州': ('杭州市应对极端天气灾害“五停”工作指导意见（试行）', 'https://safety.hangzhou.gov.cn/art/2022/6/6/art_1682790_58925516.html'),
    '海洋响应程序': ('自然资源部2026年海洋灾害应急预案印发通知', 'http://www.hunan.gov.cn/zqt/zcsd/202606/t20260629_34014765.html'),
    '上海响应程序': ('上海市处置海洋灾害专项应急预案（2022）', 'https://www.shanghai.gov.cn/yjya/20210105/0001-32023_858101.html'),
    '海冰': ('海洋灾害知多少？—应急管理部', 'https://www.mem.gov.cn/kp/zrzh/201904/t20190401_366117.shtml'),
}

# Additional primary pages by intent; still fetched and ranked at runtime.
REFERENCE_INTENT_PAGES = {
    '台风': (r'台风眼|家庭应急物资|门窗', '台风安全攻略 福建省人民政府',
             'http://www.fujian.gov.cn/zwgk/ztzl/fxftfkpzs/kpzs/202407/t20240724_6488728.htm'),
    '离岸流': (r'自救|救援', '海边玩水警惕离岸流 国家消防救援局',
             'https://www.119.gov.cn/kp/zjts/qtzh/2024/44711.shtml'),
    '洪涝': (r'清洁|消毒|饮水', '洪涝灾害后如何清洁消毒 云南省应急管理厅',
             'https://yjglt.yn.gov.cn/html/2023/yingjizhinan_0807/27403.html'),
    '海冰': (r'成因|形成|原理', 'Thermohaline Circulation — NOAA',
             'https://oceanservice.noaa.gov/education/tutorial_currents/05conveyor1.html'),
}

PUBLIC_TOPICS = TERMS + ('气象数据', '海洋数据', '海温', '海流', '潮流', '海洋预报', '海洋观测',
                         '防灾减灾', '应急响应', '灾害公报', '台风路径', '热带风暴', '拉尼娜', '厄尔尼诺',
                         '五停', '停课', '停工', '停产', '停运', '停业', '停止户外活动')

# Public intent vocabulary, not user text or internal document names. Domain
# questions must not all degrade to the unrelated query “海洋灾害 知识解答”.
SERVICE_TOPICS = {
    '海洋观测 浮标 潮位 数据质量控制 规范':r'质控|异常值|观测设备|补测|补录',
    '海洋预报 产品格式 产品制作规范 业务标准':r'产品.*格式|产品.*标准|预警预报产品',
    '海洋灾害 灾情统计 调查评估 直接经济损失 报送':r'灾情.*统计|灾情快报|核报|计损口径|经济损失.*核算|死亡.*认定',
    '风暴潮 灾害风险评估 区划 技术导则 承灾体':r'风险评估|区划|承灾体|脆弱性|水深.*损失率|风险普查|三区三线',
    '风暴潮 数据同化 数值模式 误差订正':r'同化|误差特征|怎么订正|浅水.*误差|地面沉降',
    '海堤 设计高潮位 波浪爬高 设计规范':r'设计高潮位|波浪爬高|海堤防潮标准',
    '海洋灾害 警报发布 技术判据 警戒潮位 核定':r'代表站|警戒潮位.*核定|警报.*判据|红色警报.*提前',
    '台风眼 防台避险':r'台风眼|风眼|眼区|眼壁|风突然停|中心过境|风小了',
    '台风 家庭应急物资 储备清单':r'物资|囤水|囤粮|储水|储粮|三口之家',
    '台风 门窗 加固 米字胶带':r'窗户|门窗|胶带',
    '离岸流 自救 救援':r'离岸流|被卷出去',
    '潮位 基准面 高程基准':r'基准面|潮位基准|高程基准',
    '台风 强度等级 风速':r'热带风暴|超强台风|强度等级|强台风怎么分',
    '重现期 百年一遇 概率':r'重现期|百年一遇|年一遇',
    '台风 海上搜救 求救':r'求救|12395|遇险',
    '洪涝 灾后 清洁消毒 饮水安全':r'消毒|清理|自来水|饮水|水泡过|泡水',
    '台风 停水停电 报修 恢复':r'停水|停电|修好|抢修',
    '台风 保险 理赔':r'保险|理赔|赔付|能赔',
    '自然灾害 生活救助 申请 恢复重建':r'救助|重建项目|项目申报',
    '水产养殖 网箱 池塘 防台 加固 技术':r'网箱|鱼排|养殖塘|池塘|养殖设施',
    '洪涝 灾后 防蚊 蚊媒传染病':r'蚊子|蚊虫|防蚊|灭蚊|登革热|蚊媒',
    '台风 就医 孕产妇 透析 取药':r'生孩子|孕妇|透析|拿药|取药',
    '台风 避灾安置场所 转移 特殊群体':r'安置|避护|转移|老人|小孩|宠物',
    '台风 航班 铁路 船票 退改签':r'机票|高铁票|船票|退改|民宿|酒店|订房',
    '台风 交通管制 停复航':r'跨海大桥|轮渡|客运|复航|停航|恢复开放|景区|出海',
    '台风 劳动关系 停工 旷工':r'上班|旷工|工地|处罚',
    '海洋灾害 预警 检验 评分 命中率 空报率 TS':r'命中率|空报率|漏报率|TS|提前量',
    '海洋灾害 淹没模拟 DEM 风险评估':r'DEM|淹没|承灾体|脆弱性|损失率|情景矩阵',
    '人工智能 气象 海洋 RAG 知识库':r'智能体|RAG|知识库|大模型|幻觉|数据治理|'+QA_EVIDENCE_RE,
}
PUBLIC_INTENTS = {
    '活动季节 防范月份': r'夏天|冬天|春天|秋天|夏季|冬季|春季|秋季|几月|月份|季节|全年|常年',
    '防御措施 避险': r'防御|防范|避险|怎么办|怎么做|安全|撤离|' + DEFENSIVE_ACTION_QUESTION,
    '历史灾情 经济损失': r'灾情|损失|死亡|受灾|灾损',
    '最大增水 观测': r'最大增水|最高潮位|观测|实测',
    '预警 发布条件': r'预警|警报|发布条件',
    '标准 规范 条款': r'标准|规范|条款|法定|阈值',
    '路径 登陆': r'路径|登陆|经过',
    '数据 产品 查询': r'产品|查询|数据|获取|下载|接口',
    '成因 影响': r'为什么|成因|形成|原理|影响|变化',
    '决定主体 决策权限': r'由谁|谁来|谁决定|决定|权限|主体|责任单位',
    '公告发布 通告': r'公告|通告|公布|告知|发布方式',
    '启动条件 工作指引': r'启动|什么条件|什么情况下|执行条件',
    '响应调整 降级 终止': r'降级|终止|结束响应|解除响应|调整响应',
    '办理程序 审批 发布': r'程序|流程|手续|审批|报批|走什么',
}
PUBLIC_PLACES = ('中国', '浙江', '福建', '广东', '海南', '台湾', '广西', '上海', '江苏', '山东',
                 '河北', '天津', '辽宁', '宁波', '舟山', '温州', '台州', '杭州湾', '海门', '北仑',
                 '杭州', '绍兴', '嵊州', '余姚', '武义',
                 '渤海', '黄海', '东海', '南海', '西北太平洋')

FACET_WORDS = {
    '防御措施 避险': ('防御', '避险', '远离', '停止', '撤离', '转移', '听从', '救生', '不要', '避免'),
    '启动条件 工作指引': ('启动', '条件', '响应', '预警', '标准'),
    '决定主体 决策权限': ('决定', '建议', '政府', '报备', '批准', '权限', '规范工作流程'),
    '公告发布 通告': ('公告', '通告', '发布', '广播', '电视', '短信', '微信', '网站', '信息渠道'),
    '响应调整 降级 终止': ('调整', '降低', '降级', '终止', '解除', '响应级别', '警报', '条件', '有效控制', '继续的必要'),
    '办理程序 审批 发布': ('签发', '审批', '会商', '通知', '抄送', '发送', '信息公开', '工作总结'),
}

# Operative safety instructions may sit away from the title/definition. Match
# the family of the question without requiring its literal wording in a page.
SAFETY_FOCUS = (
    (DEFENSIVE_ACTION_QUESTION, ('防御', '避险', '远离', '停止', '撤离', '转移', '听从', '不要', '避免')),
    # Event comparisons need dated loss/casualty facts, not only a broad
    # definition whose introduction repeats "economic loss". Preserve source
    # wording: a candidate event still does not establish an all-time maximum.
    (r'(?:历史|曾经|哪一次|哪一年|历次).{0,50}(?:严重|损失|死亡|灾害)|(?:死亡|损失|伤亡).{0,50}(?:历史|比较|最大|最多)',
     ('死亡','淹死','失踪','损失','亿元','万人','历史灾害','单次')),
    (r'直接经济损失.*(?:核算|口径|范围|包括)|计损口径',
     ('直接经济损失','实际价值','损毁率','不纳入','停产','营业额','计算')),
    (r'死亡.*(?:认定|核报|统计)|失踪.*(?:认定|核报|统计)|灾情.*核报',
     ('因灾死亡人口','因灾失踪人口','7日','先报后核','在地原则','初报','核报','续报')),
    (r'家庭.*(?:受灾|救助)|保险.*(?:赔|理赔)|赔付',
     ('保障时间','进水','赔付','救助对象','申请','公示','查勘','赔款','损失程度')),
    (r'(?:室内|地面|墙壁|泡过).{0,30}(?:清理|清洁|消毒)|清理.*(?:地面|住宅)',
     ('清污','淤泥','个人住宅','室内','地面','墙壁','过度消毒','防护')),
    (r'海洋灾害.*(?:损失|死亡|失踪)|(?:损失|灾种).*海洋灾害',
     ('因海洋灾害', '海洋灾害造成', '直接经济损失', '风暴潮灾害', '海浪灾害', '死亡失踪', '分灾种')),
    (r'台风眼|风眼|眼区|眼壁|中心过境|风突然停|风小了',
     ('台风眼', '眼区', '眼壁', '暂时平静', '减弱', '外出', '再次', '突然')),
    (r'离岸流|被卷出去',
     ('离岸流', '逆流', '平行', '横向', '漂浮', '游出', '救生员', '呼救', '救援',
      'rip current', 'parallel', 'swim', 'float', 'fight', 'lifeguard')),
    (r'12395|求救|遇险',
     ('12395', '报警', '经纬度', '位置', '人数', '船名', 'VHF', '频道', '通信', '自救')),
    (r'物资|囤水|囤粮|储备|水和粮|储水|储粮|几口|一家.*口|三口之家',
     ('每人', '饮用水', '食品', '升', '天', '清单', '储备')),
)


def _source_identifiers(text):
    return [m[0] for m in re.finditer(r'(?<!\d)\d{3,8}(?!\d)',text)
            if not (re.fullmatch(r'(?:19|20)\d{2}',m[0]) and
                    re.match(r'\s*(?:年|[-/]\d)',text[m.end():]))]


def focused_public_text(body, question, subquestions=None, limit=2400):
    """Budget space to each requested facet, including passages near the end."""
    if len(body) <= limit:
        return body
    paragraphs = [part for part in body.split('\n\n') if len(part.strip()) >= 8]
    # PDF extraction often yields one entire page as a paragraph. Separate
    # numbered definitions before ranking, so a sought definition near the
    # bottom is not displaced by the page's unrelated opening sentences.
    expanded = []
    for part in paragraphs:
        if len(part) > 700 and re.match(r'^第\d+页\n', part):
            expanded.extend(piece for piece in re.split(
                r'(?m)(?=^\d{1,3}[．、.]\s*[^\n：:]{2,35}[：:]|^[（(]\d{1,3}[）)][^\n。]{2,18}。)', part) if len(piece.strip()) >= 8)
        else:
            expanded.append(part)
    paragraphs = expanded
    # Conditions often live in numbered paragraphs after a short heading and
    # do not repeat “终止响应”. Keep that section together for facet ranking.
    grouped, index = [], 0
    while index < len(paragraphs):
        part = paragraphs[index]
        section_heading = (re.search(r'(?:终止|降级|调整|结束).{0,12}(?:条件|程序|标准)', part)
                           or any(term in part for term in PUBLIC_TOPICS) and re.search(r'什么是|定义|成因|危害|影响', part))
        if len(part) < 100 and section_heading:
            section = [part]
            index += 1
            while index < len(paragraphs) and sum(map(len, section)) < 1000:
                following = paragraphs[index]
                if re.match(r'^\s*\d+(?:[.．]\d+)+|^\s*\d+[.．、]\s*\D', following) or (
                        len(following) < 100 and re.search(r'(?:终止|降级|调整|结束).{0,12}(?:条件|程序|标准)', following)):
                    break
                section.append(following)
                index += 1
            grouped.append('\n\n'.join(section))
        else:
            grouped.append(part)
            index += 1
    paragraphs = grouped
    targets = subquestions or [question]
    groups = []
    for target in targets:
        labels = [label for label, pattern in PUBLIC_INTENTS.items() if re.search(pattern, target)]
        words = [word for label in labels for word in FACET_WORDS.get(label, tuple(label.split()))]
        # A marine umbrella term does not identify the operative paragraph:
        # seek rescue, supplies, verification, etc. within the fetched body.
        words += [word for topic,pattern in SERVICE_TOPICS.items() if re.search(pattern,target,re.I)
                  for word in topic.split() if word not in {'台风','海洋灾害'}]
        words += _source_identifiers(target)
        words += research_focus(target)
        words += focus_words(target)
        words += [word for pattern, focus in SAFETY_FOCUS if re.search(pattern, target, re.I)
                  for word in focus]
        focused_terms = [word for pattern, focus in SAFETY_FOCUS
                         if re.search(pattern, target, re.I) for word in focus]
        terms = [term for term in PUBLIC_TOPICS if term in target]
        if not terms:
            terms = [term for term in PUBLIC_TOPICS if term in question]
        if re.search(PUBLIC_INTENTS['活动季节 防范月份'], target):
            words += ['月', '夏', '秋', '冬', '春', '季', '全年', '四季']
        ranked = sorted(((sum(word.lower() in part.lower() for word in words) * 3 +
                          sum(word.lower() in part.split('：', 1)[0].lower() for word in focused_terms) * 40
                          * bool(re.match(r'^\d{1,3}[．、.]\s*[^\n：:]{2,35}：',part)) +
                          sum(word in part.splitlines()[0] and word in target for word in focused_terms) * 60
                          * bool(re.match(r'^[（(]\d{1,3}[）)][^\n。]{2,18}。',part)) +
                          sum(number in part for number in _source_identifiers(target))*12+
                          sum(term in part for term in terms), index, part)
                         for index, part in enumerate(paragraphs)), key=lambda row: (-row[0], row[1]))
        groups.append([(index, part, words) for score, index, part in ranked[:3] if score])
    selected, seen, used = [], set(), 0
    for index, part in enumerate(paragraphs):
        if re.search(r'本预案适用|本办法适用|本指引适用|同时废止|自.{0,30}起(?:施行|实施)|有效期为', part):
            context = part[:350]
            if used + len(context) + 2 > min(900, limit // 2):
                continue
            selected.append(context)
            seen.add(index)
            used += len(context) + 2
    # Preserve distinct operative sections before generic lexical ranking.
    # Source text remains verbatim; this is extraction, never a stored answer.
    needs = focus_groups(' '.join([question] + list(subquestions or [])))
    for pattern in dict.fromkeys(needs):
        choices = [(i, part) for i, part in enumerate(paragraphs) if i not in seen and re.search(pattern, part)]
        if not choices or used >= limit - 100:continue
        index, part = max(choices, key=lambda row: (min(3,len(re.findall(pattern,row[1]))), -row[0]))
        size = min(400, limit - used)
        if len(part) > size:
            match = re.search(pattern,part)
            start = max(0, min(match.start()-60, len(part)-size))
            part = part[start:start+size]
        elif len(part) < 100 and index + 1 < len(paragraphs):
            part += '\n\n' + paragraphs[index+1][:max(0,size-len(part)-2)]
        selected.append(part);seen.add(index);used+=len(part)+2
    for offset in range(3):
        for group in groups:
            if offset >= len(group) or used >= limit:
                continue
            index, part, words = group[offset]
            if index in seen:
                continue
            seen.add(index)
            size = min(700, limit - used)
            if size < 50:
                continue
            if len(part) > size:
                sentences = list(re.finditer(r'[^。！？]+[。！？]?', part))
                best = max(sentences, key=lambda match: sum(word in match.group() for word in words))
                start = max(0, min(best.start(), len(part) - size))
                part = part[start:start + size]
            selected.append(part)
            used += len(part) + 2
    return '\n\n'.join(selected) if selected else body[:limit]


def focused_public_spans(body, question, subquestions=None, limit=4800):
    """Exact offsets selected per facet, emitted in original document order.

    No dates/implementation clauses are moved to the front. Section ancestry
    remains separately available even when an operative paragraph is far below
    its heading (notably winter/spring relief in section 6.3.1).
    """
    from .evidence_units import build_passages
    rows = build_passages({'source_text': body})
    parts = [p.get('question', '') if isinstance(p, dict) else str(p)
             for p in (subquestions or [question])]
    selected, used = {}, 0
    rankings = []
    for part in parts:
        words = focus_words(part) + research_focus(part) + _source_identifiers(part)
        words += [word for label, pattern in PUBLIC_INTENTS.items() if re.search(pattern, part)
                  for word in FACET_WORDS.get(label, tuple(label.split()))]
        words += [word for pattern, focus in SAFETY_FOCUS if re.search(pattern, part, re.I) for word in focus]
        for pattern in focus_groups(part):
            words += [m[0] for row in rows for m in re.finditer(pattern, row.text)]
        phrases = re.findall(r'[\u4e00-\u9fff]{2,}', part[:240])
        words += [phrase[start:start+size] for phrase in phrases for size in range(2,min(7,len(phrase)+1))
                  for start in range(len(phrase)-size+1)
                  if phrase[start:start+size] not in {'什么','哪些','怎么','如何','是不是','是否','办理','程序'}]
        words = list(dict.fromkeys(word for word in words if len(word) >= 2))
        def score(row):
            content = (' '.join(row.section_path)+' '+row.text).lower()
            return sum(min(3, content.count(word.lower())) * min(12, len(word)) for word in words)
        rankings.append(sorted(((score(row), row) for row in rows), key=lambda item: (-item[0], item[1].start)))
    # Reserve the first useful unit for each facet before filling any facet.
    for offset in range(3):
        for ranked in rankings:
            candidates = [row for score, row in ranked if score > 0 and row.passage_id not in selected]
            if not candidates: continue
            row = candidates[0]
            if used + len(row.text) > limit: continue
            selected[row.passage_id] = row; used += len(row.text)
    if not selected:
        for row in rows:
            if used + len(row.text) > limit: break
            selected[row.passage_id] = row; used += len(row.text)
    return [row.to_dict() for row in sorted(selected.values(), key=lambda row: row.start)]


def irrelevant_interactive_path(question, row):
    """Avoid known JS maps for unrelated tasks; never blanket-drop alert channels."""
    host = (urlsplit(row.get('url', '')).hostname or '').lower()
    if host not in {'typhoon.nmc.cn', 'typhoon.slt.zj.gov.cn'}:
        return False
    return not bool(re.search(r'路径|预警|警报|当前台风|台风动态|活动台风|台风位置|台风.{0,8}(?:在哪|现在|强度|移动)', question))


def _base_public_search_queries(question, dynamic=False, subquestions=None, default_jurisdiction='浙江'):
    """Search public identities/intents across marine questions, not just definitions."""
    question = question.replace('天文大潮', '天文潮')
    subject_queries = public_queries(question)
    if subject_queries:
        # Keep the specialist object/action even when the broad marine lens
        # also matches. Other explicit siblings still receive their own query.
        return subject_queries
    from .intent_lenses import MARINE_RESEARCH,HOUSEHOLD,WAVE_GRADING,WARNING_COMPARE,WARNING_LEVELS,WARNING_TECHNICAL,ALERT_CHANNELS,STANDARDS_CATALOG
    if re.search(STANDARDS_CATALOG,question):
        return ['海洋 观测 预报 防灾减灾 国家标准 现行 site:std.samr.gov.cn',
                '海洋 观测 预报 防灾减灾 行业标准 现行 site:mnr.gov.cn']
    if re.search(ALERT_CHANNELS,question):
        return ['官方 预警 发布 接收 渠道 订阅 微信 短信 应急广播 site:gov.cn',
                '预警 接收 发布 渠道 微信 短信 订阅 site:cma.gov.cn']
    if re.search(WARNING_LEVELS,question) and not re.search(WARNING_TECHNICAL,question):
        return ['浙江 海洋灾害公报 四色警戒潮位说明 蓝色 黄色 橙色 红色 site:gov.cn',
                '风暴潮 警戒潮位 公众防御 戒备 site:gov.cn']
    if re.search(HOUSEHOLD,question):
        return ['全国基础版 家庭应急物资 储备 建议清单 饮用水 site:mem.gov.cn',
                '家庭 应急物资 储备 饮用水 每人 site:gov.cn']
    if re.search(WAVE_GRADING,question):
        return ['有效波高 海浪级别 划分 巨浪 狂浪 site:gov.cn',
                '有效波高 最大波高 定义 site:mnr.gov.cn']
    if re.search(WARNING_COMPARE,question):
        return ['台风预警信号 风力 含义 site:cma.gov.cn',
                '风暴潮警报 警戒潮位 含义 site:gov.cn']
    if re.search(MARINE_RESEARCH,question):
        # Search a research frontier as literature, not as today's warning.
        # Dates are enforced against each paper's publication below; an
        # earlier paper is relevant progress, never proof of being the latest.
        return ['storm surge sea level forecast deep learning observations site:gmd.copernicus.org',
                'storm surge prediction neural networks validation site:nhess.copernicus.org',
                '海洋灾害 预警 技术进展 监测 预报 site:mnr.gov.cn']
    if re.search(r'历史|曾经|哪一次|哪一年',question) and re.search(r'被淹|淹没|漫堤|倒灌|最严重',question):
        scope=('中国全国' if re.search(r'全国|我国|中国', question)
               else ' '.join(leaf_places(question)) or default_jurisdiction)
        return [f'{scope} 历史 台风 风暴潮 淹没 漫堤 灾情 site:gov.cn',
                f'{scope} 风暴潮 灾害 历史 潮位 site:mnr.gov.cn']
    annual=re.search(r'(20\d{2})年',question)
    if annual and '海洋灾害' in question and re.search(r'损失|灾种|公报|年度统计',question) and not re.search(r'台风[“"]|评分|TS',question):
        scope='浙江省' if '浙江' in question else '中国'
        return [f'{annual[1]}年{scope}海洋灾害公报 直接经济损失 site:mnr.gov.cn',
                f'"{annual[1]}年{scope}海洋灾害公报" site:gov.cn']
    service_topics=[topic for topic,pattern in SERVICE_TOPICS.items() if re.search(pattern,question,re.I)]
    if service_topics and not re.search(r'五停|停课|停产|停业',question):
        places=[place for place in PUBLIC_PLACES if place in question]
        # Local operational notices retain location; generic methods and
        # hypothetical questions also search the nationwide official guidance.
        identity=' '.join(dict.fromkeys(places))
        if dynamic:
            identity=' '.join(dict.fromkeys(re.findall(r'(?<!\d)20\d{2}(?!\d)',question)+places))
        queries=[f'{identity} {topic} site:gov.cn'.strip() for topic in service_topics[:3]]
        # The operator is outside gov.cn. Search it explicitly for local
        # operating notices, while retaining the maritime/government queries.
        if ('台风 交通管制 停复航' in service_topics
                and re.search(r'舟山|朱家尖|普陀|沈家门|岱山|嵊泗', question)):
            queries.insert(0, f'{identity} 交通管制 停航 复航 公告 site:hxzs.com.cn'.strip())
        queries += [f'{topic} site:gov.cn' for topic in service_topics[:3]]
        return list(dict.fromkeys(queries))[:6]
    subjects = [term for term in PUBLIC_TOPICS if term in question and not any(
        term != longer and term in longer and longer in question for longer in PUBLIC_TOPICS)]
    storms = [name for name, english in STORM_ALIASES.items() if name in question or english in question.lower()]
    if not subjects and not storms:
        subjects = ['海洋灾害']
    years = re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', question)
    places = [place for place in PUBLIC_PLACES if place in question]
    agencies = [name for name in ('自然资源部', '国家海洋预报台', '国家海洋环境预报中心') if name in question]
    identity = ' '.join(dict.fromkeys(years + storms + places + agencies))
    policy = any(term in question for term in ('五停', '停课', '停工', '停产', '停运', '停业'))
    if policy:
        scope = ' '.join(places) or default_jurisdiction
        targets = subquestions or [question]
        facets = []
        for target in targets:
            for label in ('启动条件 工作指引', '决定主体 决策权限', '公告发布 通告'):
                if re.search(PUBLIC_INTENTS[label], target) and label not in facets:
                    facets.append(label)
        facets = facets or ['工作指引']
        domains = ['zj.gov.cn', 'gov.cn'] if scope == '浙江' else ['gov.cn']
        words = {'启动条件 工作指引':'启动条件', '决定主体 决策权限':'决定', '公告发布 通告':'通告'}
        return [f'{scope} "五停" {words.get(facet, facet)} site:{domain}' for domain in domains for facet in facets][:6]
    # Definitions/comparisons require one query per concept; truncating a list
    # of four hazards to three silently lost the typhoon definition.
    if not dynamic and not storms and not years and not re.search(DEFENSIVE_ACTION_QUESTION, question) and any(
            re.search(r'定义|区别|什么是|是什么|含义|概念|叠加', part) for part in (subquestions or [question])):
        definitions = terminology_queries(question)
        if definitions:
            return definitions
    if not dynamic and not storms and not years and re.search(r'为什么|成因|形成|原理', question):
        base = ' '.join(part for part in (identity, ' '.join(subjects[:3])) if part)
        return [f'{base} 成因 形成 原理 site:cma.gov.cn',
                f'{base} 危害 影响 site:weather.com.cn', f'{base} 成因 影响 site:gov.cn']
    if not dynamic and re.search(PUBLIC_INTENTS['活动季节 防范月份'], question):
        base = ' '.join(part for part in (identity, ' '.join(subjects[:3])) if part)
        return [f'{base} 全年 四季 site:cma.gov.cn',
                f'{base} 登陆 活跃月份 site:weather.com.cn',
                f'{base} 活动季节 防范月份 site:gov.cn']
    if subquestions and len(subquestions) > 1:
        queries = []
        # Each gap gets its own first search, rather than the first intent
        # crowding out the remaining subquestions in a long combined query.
        for part in subquestions[:4]:
            selected = [term for term in subjects if term in part] or subjects[:2]
            intents = [label for label, pattern in PUBLIC_INTENTS.items() if re.search(pattern, part)]
            if any(label == '响应调整 降级 终止' for label in intents):
                intents = [label for label in intents if label != '启动条件 工作指引']
            intent_words = {'响应调整 降级 终止':'响应终止 降级 条件', '办理程序 审批 发布':'终止程序 审批 签发 通知',
                            '决定主体 决策权限':'决定 批准 终止程序'}
            intent = ' '.join(intent_words.get(label, label) for label in intents[:2]) or ('定义' if re.search(r'什么|定义|区别|概念', part) else '知识 解答')
            topic = ' '.join(selected[:3])
            policy_hint = '应急预案' if re.search(r'响应|审批|五停', question) else ''
            queries.append(f'{identity} {topic} {policy_hint} {intent} site:gov.cn'.strip())
        return list(dict.fromkeys(queries))
    intents = [label for label, pattern in PUBLIC_INTENTS.items() if re.search(pattern, question)]
    if not dynamic and not storms and not years and not intents:
        definitions = terminology_queries(question)
        if definitions:
            return definitions
    if (not dynamic and not storms and not years and not re.search(DEFENSIVE_ACTION_QUESTION, question)
            and re.search(r'定义|区别|什么是|是什么|含义|概念|叠加', question)):
        definitions = terminology_queries(question)
        if definitions:
            return definitions
    topic = ' '.join(subjects[:3])
    if not dynamic and re.search(PUBLIC_INTENTS['活动季节 防范月份'], question):
        base = ' '.join(part for part in (identity, topic) if part)
        return [f'{base} 全年 四季 site:cma.gov.cn',
                f'{base} 登陆 活跃月份 site:weather.com.cn',
                f'{base} 活动季节 防范月份 site:gov.cn']
    intent = ' '.join(intents[:2]) or '知识 解答'
    policy_question = is_policy_lookup(question)
    recency = ('最新 现行 预案' if policy_question else '最新 预报 预警') if dynamic else ''
    base = ' '.join(part for part in (identity, topic, intent, recency) if part)
    queries = [f'{base} site:cma.gov.cn', f'{base} site:weather.com.cn', f'{base} site:gov.cn']
    if (dynamic and not policy_question and re.search(r'预警|警报|预报', question)
            and re.search(r'浙江|舟山|朱家尖|普陀|宁波|温州|台州', question)):
        queries.insert(0, f'{base} site:zjocean.org.cn')
    return queries


def public_search_queries(question, dynamic=False, subquestions=None, default_jurisdiction='浙江'):
    """Bounded public vocabulary queries, preserving each facet's date/object.

    Internal filenames, arbitrary conversation text and private corpus excerpts
    never become search queries. Dates/places and service terms are public slots.
    """
    from .intent_lenses import HOUSEHOLD,STANDARDS_CATALOG
    base = _base_public_search_queries(question, dynamic, subquestions, default_jurisdiction)
    parts = [p.get('question', '') if isinstance(p, dict) else p for p in (subquestions or [question])]
    identity = ' '.join(leaf_places(question))
    dates = ' '.join(dict.fromkeys(match.group().strip() for match in DATE.finditer(question)))
    objects = ' '.join(dict.fromkeys(re.findall('|'.join(OBJECTS.values()), question)))
    named = ' '.join(named_objects(question))
    focused = []
    if len(parts) > 1:
        parent_topics = '五停' if re.search(r'五停|停课|停工|停产|停运|停业', question) else ' '.join(
            term for term in PUBLIC_TOPICS if term in question)
        for part in parts[:6]:
            inherited = ' '.join((identity, dates, parent_topics, part))
            choices = _base_public_search_queries(inherited, dynamic, [part], default_jurisdiction)
            if choices:
                focused.append(choices[0])
    queries = focused + base
    result = []
    for query in queries:
        # Preserve full dates for operational requests rather than just a year.
        catalog_query=bool(re.search(STANDARDS_CATALOG,question) and re.search(r'国家标准|行业标准|标准.*目录|标准.*清单',query))
        generic=(not dynamic and (any(query.startswith(topic+' site:') for topic in SERVICE_TOPICS)
                 or query in public_queries(question) and re.search(r'site:(?:nhc|moa|mem)\.gov\.cn', query))) or (bool(re.search(HOUSEHOLD,question)) and '清单' in query) or catalog_query
        prefix = '' if generic else ' '.join(value for value in (identity, named, objects, dates if dynamic else '') if value and value not in query)
        if prefix:
            query = prefix + ' ' + query
        if query not in result:
            result.append(query)
    return result[:10]


def public_page_date(page):
    value = str(page.get('published_at') or '')
    if not value:
        # Some older official templates expose an article date directly below
        # the heading rather than in metadata. Do not use sidebar dates.
        heading = str(page.get('page_title') or page.get('title') or '').split('-')[0].strip()
        body = str(page.get('content') or '')
        position = body.find(heading) if len(heading) >= 4 else -1
        if position >= 0:
            value = body[position:position + len(heading) + 250]
    return parse_date(value)


def fresh_public_page(page, now=None):
    """Reading today does not turn an old forecast into a current observation."""
    now = now or datetime.now(timezone.utc)
    stamp = public_page_date(page)
    if stamp is None:
        return False
    return -86400 <= (now - stamp).total_seconds() <= 72 * 3600


def trusted_url(url, allow_academic=False, allow_health=False):
    try:
        value = urlsplit(url)
        host = (value.hostname or '').lower()
        return (value.scheme in {'http', 'https'} and not value.username and not value.password
                and value.port in {None, 80, 443}
                and any(host == domain or host.endswith('.' + domain)
                        for domain in TRUSTED_DOMAINS+(ACADEMIC_DOMAINS if allow_academic else ())+
                        (('chinacdc.cn',) if allow_health else ())))
    except (ValueError, TypeError):
        return False


def canonical_public_url(url):
    parsed = urlsplit(url)
    query = urlencode([(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                       if key.lower() != 'eqid' and not key.lower().startswith('utm_')])
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ''))


def terminology_queries(question):
    """Only public term names leave for search, never corpus text or history."""
    question = question.replace('天文大潮', '天文潮')
    if not re.search(r'什么|定义|区别|含义|概念|怎么理解|是指|叠加|关系|成因|形成|原理', question):
        return []
    terms = [term for term in TERMS if term in question and not any(term in longer and longer != term and longer in question for longer in TERMS)]
    queries = []
    if '天文潮' in terms and any(term in terms for term in ('风暴增水', '风暴潮')):
        queries.append('风暴增水 天文潮 叠加 site:weather.com.cn')
    for term in terms:
        domain = 'cma.gov.cn' if term in {'台风', '热带气旋', '海啸'} else 'weather.com.cn'
        queries.append(f'{term} 定义 site:{domain}')
    return queries[:4]


class OfficialWebSupplement:
    def __init__(self, search_client, reader, enabled=False, reference_pages=None, default_jurisdiction='浙江',
                 evidence_units=None, connector_v2=None):
        self.search_client, self.reader, self.enabled = search_client, reader, bool(enabled)
        self._cache, self._lock = {}, threading.Lock()
        self.reference_pages = REFERENCE_PAGES if reference_pages is None else reference_pages
        self.default_jurisdiction = default_jurisdiction
        self.evidence_units = (os.getenv('MARINE_EVIDENCE_UNITS', 'false').lower() in {'1','true','on'}
                               if evidence_units is None else bool(evidence_units))
        self.connector_v2 = (os.getenv('MARINE_CONNECTOR_V2', 'false').lower() in {'1','true','on'}
                             if connector_v2 is None else bool(connector_v2))

    def fetch(self, question, *, dynamic=False, subquestions=None, refresh=False, _gap_retry=False, corrective=False):
        from .phase2 import current_budget
        budget = current_budget()
        if budget is not None and budget.remaining() <= 0:
            return [], {'web_supplement_state':'budget_exhausted','web_search_executed':False,
                        'web_search_queries':[],'web_body_read_failures':[]}
        subquestions = [part.get('question','') if isinstance(part,dict) else part for part in (subquestions or [])]
        queries = public_search_queries(question, dynamic=dynamic, subquestions=subquestions,
                                        default_jurisdiction=self.default_jurisdiction) if self.enabled else []
        academic=research_question(question)
        if corrective and queries:
            missing_text=' '.join(subquestions)
            topics=[topic for topic,pattern in SERVICE_TOPICS.items() if re.search(pattern,missing_text,re.I)]
            scope=' '.join(leaf_places(question)) if dynamic or is_policy_lookup(question) else ''
            extras=public_queries(' '.join((scope, missing_text)))
            extras += [f'{scope} {topic} site:gov.cn'.strip() for topic in topics[:4]]
            queries=list(dict.fromkeys(extras+queries))[:10]
        if academic and queries:
            topics=[label for label,pattern in SERVICE_TOPICS.items() if re.search(pattern,question,re.I)]
            if topics:queries=list(dict.fromkeys(queries+[topics[0]+' site:arxiv.org']))[:10]
        if not queries:
            return [], {'web_supplement_state': 'not_requested'}
        cache_key = (question, dynamic, tuple(subquestions or []), tuple(queries),corrective)
        from .phase2 import current_turn_key
        if current_turn_key():cache_key=cache_key+(current_turn_key(),)
        with self._lock:
            cached = self._cache.get(cache_key)
        if not refresh and cached and time.monotonic() - cached[0] < (60 if dynamic else 3600):
            return cached[1], {**cached[2], 'web_supplement_cache_hit': True}

        def lookup(query):
            if budget is not None and budget.remaining() <= 0:
                return None
            try:
                storms = [name for name, english in STORM_ALIASES.items() if name in query or english in query.lower()]
                # Generic topics are not entity identities. Requiring 台风 in
                # every title discarded national supply lists and rescue pages.
                subject = storms[0] if storms else ''
                return self.search_client.search_with_status(query, max_results=4, subject=subject)
            except Exception:
                return None
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix='marine-terms') as pool:
            from .qa_observability import submit_observed
            futures = [submit_observed(pool, lookup, query) for query in queries]
            outcomes = [future.result() for future in futures]
        # One result per query first, so a compound question keeps topic diversity.
        groups = []
        for query, outcome in zip(queries, outcomes):
            term = ('上海响应程序' if '上海' in query and re.search(r'响应|降级|终止|审批', query) else
                    '海洋响应程序' if '海洋灾害' in query and re.search(r'响应|降级|终止|审批', query) else
                    '五停杭州' if '"五停"' in query and ('浙江' in query or '杭州' in query) else
                    '台风季节' if '台风' in query and ('全年' in query or '活动季节' in query) else
                    '台风月份' if '台风' in query and '活跃月份' in query else
                    '叠加' if '叠加' in query else query.split()[0] if '定义' in query else '')
            if '定义' in query and term not in self.reference_pages and term not in REFERENCE_PAGES:
                term = next((topic for topic in sorted(self.reference_pages, key=len, reverse=True) if topic in query), '')
            group = []
            reference = self.reference_pages.get(term) if not dynamic or (
                term in {'海洋响应程序', '上海响应程序'} and is_policy_lookup(question)) else None
            if reference:
                group.append({'title': reference[0], 'url': reference[1], '_supplement_term': term})
            for topic, (pattern, title, url) in REFERENCE_INTENT_PAGES.items():
                if not dynamic and topic in query and re.search(pattern, query) and self.reference_pages is REFERENCE_PAGES:
                    group.insert(0, {'title':title, 'url':url, '_supplement_term':topic})
            for row in outcome.results if outcome else []:
                if trusted_url(row.get('url', ''),academic,allow_health=self.connector_v2):
                    row = dict(row)
                    row['_supplement_term'] = term
                    row['url'] = canonical_public_url(row['url'])
                    row['_generic_scope']=any(query.startswith(topic+' site:') for topic in SERVICE_TOPICS)
                    group.append(row)
            groups.append(group)
        candidates, seen = [], set()
        if self.reference_pages is REFERENCE_PAGES:
            for row in primary_routes(question,connector_v2=self.connector_v2):
                candidates.append(row);seen.add(row['url'])
        for offset in range(3):
            for group in groups:
                if offset < len(group) and group[offset]['url'] not in seen:
                    row = dict(group[offset])
                    candidates.append(row)
                    seen.add(row['url'])
        # Prefer an actual article over a home/column page before paying for
        # body reads; retain round-robin facet diversity among article results.
        skipped = []
        if self.connector_v2:
            skipped = [{'url': row['url'], 'state': 'skipped_irrelevant',
                        'reason': 'interactive_typhoon_path_unrelated_to_question'}
                       for row in candidates if irrelevant_interactive_path(question, row)]
            candidates = [row for row in candidates if not irrelevant_interactive_path(question, row)]
        candidates.sort(key=lambda row: len(urlsplit(row['url']).path.strip('/')) < 5)
        candidates = candidates[:12]
        try:
            pages = self.reader.enrich(candidates) if candidates and (budget is None or budget.remaining()>0) else []
            unread=[page for page in pages if page.get('read_status')=='not_attempted']
            if unread and (budget is None or budget.remaining()>0):
                completed={page['url']:page for page in self.reader.enrich(unread)}
                pages=[completed.get(page['url'],page) if page.get('read_status')=='not_attempted' else page for page in pages]
        except Exception:
            pages = []
        read_failures=[{'url':p.get('url'),'state':p.get('read_status'),'reason':p.get('read_error'),
                        'http_status':p.get('http_status'),'final_url':p.get('final_url')}
                       for p in pages if p.get('read_status') not in {'success','partial'}]
        # A notice often puts the operative clauses in an official attachment.
        # Read at most two, using the same pinned/public reader and size limits.
        attachments = []
        if (is_policy_lookup(question) or (not dynamic and re.search(r'标准|规范', question))
                or self.connector_v2 and re.search(r'方案|细则|指南|手册|规范|通知附件|防蚊|灭蚊|防台|加固', question)):
            for page in pages:
                parent_url = page.get('final_url') or page.get('url', '')
                if not trusted_url(parent_url,allow_health=self.connector_v2) or page.get('read_status') not in {'success', 'partial'}:
                    continue
                for link in page.get('document_links', []):
                    url = link.get('url', '')
                    if (trusted_url(url,allow_health=self.connector_v2) and re.search(r'\.(?:pdf|docx)$', urlsplit(url).path, re.I)
                            and url not in seen and re.search(
                                r'预案|标准|规范|办法|正文|附件|方案|细则|指南|手册' if self.connector_v2 else r'预案|标准|规范|办法|正文|附件',
                                link.get('title', ''))):
                        parent_body = page.get('content', '')
                        issued = re.search(r'(?:自然资源部办公厅|自然资源部)\s*(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日', parent_body)
                        issued_at = '-'.join([issued[1], issued[2].zfill(2), issued[3].zfill(2)]) if issued else None
                        attachments.append({'url': url, 'title': link['title'], 'parent_url': parent_url,
                                            'published_at': page.get('published_at'),
                                            '_document_issued_at': issued_at,
                                            '_parent_notice': parent_body if len(parent_body) <= 900 else focused_public_text(parent_body, question, subquestions, limit=900)})
                        if self.connector_v2:
                            from .evidence_units import build_doc_card
                            parent_card = build_doc_card({**page, 'source_text': parent_body}).to_dict()
                            attachments[-1]['_parent_doc_card'] = parent_card
                        seen.add(url)
            if attachments and (budget is None or budget.remaining()>0):
                try:
                    pages += self.reader.enrich(attachments[:2])
                except Exception:
                    pass
        hits, stale_count, excluded = [], 0, []
        for page in pages:
            url = page.get('final_url') or page.get('url', '')
            body = str(page.get('content') or '')
            # Search summaries discover sources; only fetched official bodies
            # become answer evidence. Re-check after redirects.
            if not trusted_url(url,academic,allow_health=self.connector_v2) or page.get('read_status') not in {'success', 'partial'} or len(body.strip()) < 30:
                continue
            url = canonical_public_url(url)
            research_source=academic and any((urlsplit(url).hostname or '')==domain for domain in ACADEMIC_DOMAINS)
            if research_source:page={**page,'_primary_scope':'research_method'}
            title = page.get('page_title') or page.get('title') or ''
            # Article titles commonly end with “_福建省人民政府门户网站”.
            # A site suffix is not evidence that the article is a homepage.
            title_head=re.split(r'[_|—]',title,maxsplit=1)[0].strip()
            if (not urlsplit(url).path.strip('/') or re.fullmatch(
                    r'首页|网站首页|门户网站|[\u4e00-\u9fffA-Za-z]{2,25}(?:人民政府|海洋局|部门)(?:门户)?网站',title_head)):
                excluded.append({'url':url,'reason':'index_page_not_article'})
                continue
            policy_question = is_policy_lookup(question)
            # An enacted plan is not a 72-hour forecast. Version/effectiveness
            # evidence is retained below and must be qualified in the answer.
            general_scope=page.get('_generic_scope') is True
            published_value = page.get('published_at') or (public_page_date(page).isoformat() if public_page_date(page) else None)
            assessment = assess_page(question, {**page, 'published_at': published_value}, dynamic)
            from .crag import facet_context
            assessment['facets'] = [dict(assess_page(facet_context(question,part,dynamic)[0], {**page,'published_at':published_value},
                facet_context(question,part,dynamic)[1], subquestion=part), question=part) for part in (subquestions or [question])]
            if assessment['use']=='excluded' and any(part['use']!='excluded' for part in assessment['facets']):
                assessment['use']='reference'
                assessment['reasons'].append('仅支持部分子问题，按各子问题适用性使用')
            if assessment['use'] == 'excluded':
                stale_count += assessment['temporal'] in {'after_cutoff', 'expired', 'not_yet_effective','event_not_matched'}
                excluded.append({'url':url,'title':page.get('title'), **assessment})
                continue
            # Undated background cannot answer a current observation query.
            if (dynamic and not policy_question and not general_scope
                    and assessment['kind']=='knowledge' and not published_value):
                stale_count += 1
                excluded.append({'url':url, 'reason':'undated_background_for_dynamic_query'})
                continue
            requested_years = re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', question)
            requested_storms = [(name, english) for name, english in STORM_ALIASES.items()
                                if name in question or english in question.lower()]
            identity_text = (str(page.get('page_title') or page.get('title') or '') + ' ' + body).lower()
            if any(term in question for term in ('五停', '停课', '停工', '停产', '停运', '停业')) and not any(
                    term in identity_text for term in ('五停', '停课', '停工', '停产', '停运', '停业')):
                continue
            if requested_storms and not general_scope and not any(name in identity_text or english in identity_text for name, english in requested_storms):
                continue
            title_years = re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', str(page.get('page_title') or page.get('title') or ''))
            if requested_storms and requested_years and title_years and not set(title_years)&set(requested_years):
                excluded.append({'url':url,'reason':'storm_year_mismatch'})
                continue
            if requested_years and not general_scope and not policy_question and not research_source and not any(year in identity_text for year in requested_years):
                continue
            key = hashlib.sha256(url.encode()).hexdigest()
            text = focused_public_text(body, question.replace('天文大潮', '天文潮'), subquestions)
            if page.get('_primary_scope')=='service_directory' and page.get('page_title'):
                # Fetched page identity and its actual navigation support a
                # query-channel answer, never the existence of a current alert.
                text='已读取页面标题：'+page['page_title']+'\n访问地址：'+url+'\n页面文字：\n'+text
            if research_source:
                from .research_excerpt import research_excerpt
                text=research_excerpt(body)
            if page.get('_parent_notice'):
                text = '附件所属印发通知（仅说明版本和适用范围）：\n' + page['_parent_notice'][:600] + '\n\n附件正文相关段落：\n' + text
            if page.get('_supplement_term') == '叠加':
                # This query asks about coincident water levels, not the old
                # article's unrelated numerical examples or terminology aliases.
                sentences = re.split(r'(?<=[。！？])', body)
                related = [sentence.strip() for sentence in sentences
                           if re.search(r'叠加|相叠|未遇天文', sentence)
                           and not re.search(r'\d{4}年|\d{4}.*号台风', sentence)]
                if related:
                    text = '\n\n'.join(related[:5])
            title = page.get('page_title') or page.get('title') or '权威机构公开资料'
            published = page.get('published_at') or (public_page_date(page).isoformat() if public_page_date(page) else None)
            hits.append({'source_id': 'web:' + key, 'chunk_id': 'web:' + hashlib.sha256(text.encode()).hexdigest(),
                         'title': title, 'filename': title, 'text': text[:3000], 'locator': '官方附件文字相关段落' if page.get('parent_url') else '联网网页正文相关段落',
                         'category': '公开业务与知识资料', 'status': 'public_web', 'quality_status': 'parsed', 'score': 1,
                         'url': url, 'sids': [], 'retrieval': 'official_web_search_and_body',
                         'retrieval_details': {'direct_evidence': True},
                         'metadata': {'source_type': 'public_web', 'external_allowed': True,
                                      'primary_scope':page.get('_primary_scope'),
                                      'publication_date': published,
                                      'retrieved_at': page.get('read_at') or datetime.now(timezone.utc).isoformat(),
                                      'read_status': page.get('read_status'), 'url': url,
                                      'read_from_cache':page.get('read_from_cache',False),
                                      'evidence_scope':'research_method' if page.get('_primary_scope')=='research_method' else 'general_guidance' if general_scope else 'source_document',
                                      'parent_url': page.get('parent_url'),
                                      'document_issued_at': page.get('_document_issued_at'),
                                      'applicability': assessment,
                                      'text_is_excerpt': text[:3000] != body}})
            if self.evidence_units:
                from .evidence_units import build_doc_card
                unit_hit = hits[-1]
                unit_hit['source_text'] = body
                unit_hit['passages'] = focused_public_spans(body, question, subquestions,
                    limit=6000 if assessment['kind'] == 'policy' else 4800)
                if page.get('_parent_doc_card'):
                    unit_hit['metadata']['parent_notice_doc_card'] = page['_parent_doc_card']
                unit_hit['doc_card'] = build_doc_card({**page, **unit_hit}).to_dict()
                # Inheritance remains explicit, not silently asserted as the
                # attachment's own content or identity.
                unit_hit['metadata']['evidence_view_version'] = 2
                unit_hit['metadata']['extraction_method'] = page.get('extraction_method')
                unit_hit['metadata']['read_from_reviewed_archive'] = bool(page.get('read_from_reviewed_archive'))
                unit_hit['metadata']['body_sha256'] = hashlib.sha256(body.encode()).hexdigest()
                unit_hit['metadata']['selected_original_passage_ids'] = [p['id'] for p in unit_hit['passages']]
        reconcile_notices(hits)
        available_count = len(hits)
        hits = select_evidence(question, hits, subquestions, limit=6)
        # A single bounded second round targets uncovered subquestions. It
        # never treats search success or a result title as semantic coverage.
        before_coverage = coverage_matrix(question, subquestions, hits)
        gaps = [row['question'] for row in before_coverage if row['state']!='candidate_evidence']
        retry_meta = None
        if gaps and subquestions and not _gap_retry and (len(gaps)<len(subquestions) or len(subquestions)>1) and (budget is None or budget.remaining()>0):
            retry_hits, retry_meta = self.fetch(question, dynamic=dynamic,
                subquestions=gaps, refresh=True, _gap_retry=True)
            hits = select_evidence(question, reconcile_notices(hits + retry_hits), subquestions, limit=6)
        meta = {'web_supplement_state': 'success' if hits else 'unavailable',
                'web_search_states':[outcome.state if outcome else 'error' for outcome in outcomes],
                'web_search_executed': any(outcome and outcome.executed for outcome in outcomes),
                'web_search_queries': queries, 'web_supplement_source_count': len(hits),
                'web_body_candidate_count': available_count,
                'web_body_read_failures':read_failures,
                'web_relevance_excluded': available_count - len(hits),
                'web_stale_pages_excluded': stale_count,
                'web_excluded_sources': excluded,
                'web_evidence_coverage': coverage_matrix(question, subquestions, hits),
                'web_gap_retry': bool(retry_meta),
                'web_gap_retry_queries': retry_meta.get('web_search_queries', []) if retry_meta else [],
                'web_supplement_cache_hit': False}
        meta['web_corrective_pass']=corrective
        if self.connector_v2:
            meta['web_candidates_skipped'] = skipped
        if any(term in question for term in ('五停', '停课', '停工', '停产', '停运', '停业')) and not any(place in question for place in PUBLIC_PLACES):
            meta['web_default_jurisdiction'] = self.default_jurisdiction
        if hits:
            with self._lock:
                if len(self._cache) >= 128:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[cache_key] = (time.monotonic(), hits, meta)
        return hits, meta
