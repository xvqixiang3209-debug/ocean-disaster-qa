"""Question-specific presentation and bounded readability checks for grounded QA.

These rules arrange evidence; they neither supply facts nor certify correctness.
"""
import re


RESPONSE_CHANGE = r'降级|降低|终止|结束|解除|调整'
POLICY_QUESTION = r'应急响应|响应|预案|五停|停课|停工|停产|停运|停业|审批|公告|权限'


def is_policy_lookup(question):
    """A current response status is live data, unlike a current procedure."""
    return bool(re.search(r'预案|规程|审批|条款|法定|现行标准|启动条件|终止条件|程序|流程|权限|由谁|谁决定|什么时候可以|何时可以', question))


def response_parts(question):
    """Keep independently answerable response facets even if planning fails."""
    if not re.search(RESPONSE_CHANGE, question) or '响应' not in question:
        return []
    if re.search(r'是否(?:已|已经)?(?:降级|终止|结束|解除)|(?:降级|终止|结束|解除)(?:了)?[吗么]', question):
        return []
    parts = []
    asks_conditions = bool(re.search(r'条件|标准|什么时候|何时|什么情况|何种情况|能否|可否', question))
    change = bool(re.search(r'降级|降低|调整', question))
    ending = bool(re.search(r'终止|结束|解除', question))
    action = '调整或终止' if change and ending else '调整' if change else '终止'
    narrow_step = bool(re.search(r'第[一二三四五六七八九十\d]+步',question)) and not bool(
        re.search(r'全部|完整|整个|所有步骤',question))
    if change and asks_conditions:
        parts.append('什么时候可以降级或降低应急响应？')
    if ending and asks_conditions:
        parts.append('什么时候可以终止应急响应？')
    if re.search(r'由谁|谁来|谁决定|主体|权限|批准', question):
        parts.append(f'由谁决定{action}应急响应？')
    if re.search(r'程序|流程|手续|审批|报批|怎么|如何|步骤', question) and not narrow_step:
        parts.append(f'{action}应急响应要走什么程序？')
    return parts


def _question_kind(question):
    live = re.search(r'当前|(?<!出)现在|目前|实时|今天|今日|明天|未来|此刻|这次|本次|最新', question)
    if live and not is_policy_lookup(question):
        return 'current_situation'
    if re.search(r'区别|不同|对比|相比|差别', question):
        return 'comparison'
    if re.search(r'什么是|是什么|是什么意思|定义|含义|概念|怎么理解', question):
        return 'definition'
    if re.search(POLICY_QUESTION, question) or is_policy_lookup(question):
        return 'policy_procedure'
    if re.search(r'夏天|夏季|冬天|冬季|春季|秋季|几月|月份|季节', question):
        return 'season'
    if re.search(r'怎么计算|如何计算|计算公式|计算方法|怎么算', question):
        return 'calculation'
    if re.search(r'多少|多大|最大|最高|最低|统计|数量|哪些站|各站|分站|分别.*数值', question):
        return 'data_lookup'
    if re.search(r'(?:19|20)\d{2}年|当时|历史|曾经', question):
        return 'historical_case'
    if re.search(r'预报产品|哪里.{0,4}查|在哪.{0,4}查|获取|下载|接口', question):
        return 'product_lookup'
    if re.search(r'避险|防范|防御|怎么办|怎么做|应对|措施|安全|出海', question):
        return 'action_advice'
    if re.search(r'为什么|成因|形成|原理|影响|关系|叠加', question):
        return 'cause_effect'
    return 'explanation'


DESIGNS = {
    'calculation': (
        ['直接说明计算方法', '公式及输入条件', '必要的计算步骤或限制'],
        '先说明有据的计算方法，公式写清变量、单位和基准，再按步骤计算；缺少必要输入时指出具体缺项。'
        '不得用互联网上其他站点、时次或基准的数据代替业务输入，不编造公式、阈值或计算结果。'),
    'policy_procedure': (
        ['直接结论', '用户所问条件或主体', '有序办理步骤', '文件与地区说明'],
        '先说明有证据支持的判断框架，再解释适用文件中的具体规定。降级与终止分开；办理程序用编号步骤，写明有据的建议、审批、通知主体。'
        '优先用一份适用预案把问题讲完整，不同地区、灾种和机构的规则单独标明，不能拼成全国通用规则。'
        '降级条件下不放终止条款；启动程序不作为终止程序。没有证据的主体、期限、阈值不填写。'
        '只在引用该文件首次出现时说明地区和版本，避免每句重复归档标签；未核实现行状态的限制只针对对应文件。'),
    'comparison': (
        ['一句话核心区别', '按相同维度比较', '回答用户的选择或关注重点'],
        '先概括核心区别，再按相同维度比较。两项简单差异可用短段落，三项以上或多个比较维度优先用表格。'
        '解释概念本质、成因、特点及关系，最后回答用户的选择或关注重点。概念比较不混入无关警报门槛和历史灾例。'),
    'definition': (
        ['通俗定义', '必要解释或例子', '用户另外问到的关系与影响'],
        '第一句用通俗中文定义概念，再解释关键术语；只有帮助理解时才举有据的例子。涉及差值说明方向与参照量。'
        '复合提问继续回答关系、影响或应用，不能只定义第一个词。产品数值、警报模板不能代替定义。'),
    'season': (
        ['直接回答季节限制', '常年时段和地区', '必要防范重点'],
        '先回答是否只限某季节，再说明有据的常年月份及地区，区分生成和登陆。只在用户问到时讨论今年或当前情况。'),
    'data_lookup': (
        ['直接给出有据数值', '时间地区单位及口径', '必要差异说明'],
        '先给用户问到的数值与地点；一个数值可直接用一句话，多指标或多站用简洁表格。'
        '保留原始数字、单位、时间与基准，按引用对应原文，不任意换算、取平均、选最大或扩展为总体统计。'
        '来源有差异时分别写出出处与数值；unit_equivalences指出的等价数值不能称为数值冲突，也不代表站点和口径相同。'
        '没有依据就不推测差异原因，不自行计算差值；不要把其他灾情数据一并堆进答案。'),
    'historical_case': (
        ['直接回答事件问题', '与提问有关的事实或行动', '必要历史口径说明'],
        '先回答指定事件中用户关心的结果、原因或应对行动。措施题按实际采取的行动归纳，事件过程题按时间顺序写。'
        '不要将灾害后果当作应对措施，也不要添加用户没问的气压、风速、损失等统计。明确历史时间，不变成当前行动判断。'),
    'action_advice': (
        ['当前问题的直接建议', '按优先级排列的行动', '适用场景与必要限制'],
        '根据有据的官方指导，先说明最重要的行动，再按先后或风险场景列出少量可执行步骤。'
        '通用避险知识可解释；具体地点当前是否安全需要有效预警，不用通用建议替代当前安全判断。'),
    'current_situation': (
        ['截至何时的可确认结论', '地点及有效时段', '用户所问的行动或下一步'],
        '第一段说明资料截至时间、适用地区和能确认的情况。历史档案和网页读取时间不能证明当前状态。'
        '缺少当前有效资料时直接指出缺口，并说明可查询的官方发布渠道；不得作出可以出海或已经解除风险的保证。'),
    'product_lookup': (
        ['直接指出查询入口或产品', '查询步骤', '产品时间和适用范围'],
        '先告诉用户从哪里查询或获取什么产品，再给必要步骤。说明产品发布时间、有效时间与区域；不编造地址、接口或实时数据。'),
    'cause_effect': (
        ['直接说明原因或关系', '按因果顺序解释', '适用条件与实际影响'],
        '先说清原因或关系，再按因果顺序解释，必要时用有据例子。可能性与必然性分清，不以无关历史记录替代机制解释。'),
    'explanation': (
        ['直接结论', '围绕用户问题解释', '必要适用说明'],
        '先直接回答，再用短段落解释。复合题按实际子问题依次回答，简单题一两段即可，不强加固定四段模板。'),
}


def answer_design(question, parts):
    """Shared global contract, with structure adapted to each actual intent."""
    kind = _question_kind(question)
    outline, guidance = DESIGNS[kind]
    return {
        'kind': kind, 'outline': outline, 'guidance': guidance,
        'subquestions': parts,
        'subquestion_layout': [{'id':part['id'], 'kind':_question_kind(part['question']),
                               'question':part['question']} for part in parts],
        'style': ('所有回答先给核心答案，再解释依据。简单题通常一两段，复合题按子项使用短标题，程序用编号步骤。'
                  '通常300至600字，复杂问题可适当增加；不要为了凑字数或栏目扩写。引用放在相应事实旁。'),
        'synthesis': ('资料库与官方网页共同提供证据，择相关、适用、版本明确的内容综合组织，不能把网页摘要当作正文。'
                      '不要写成“资料库说一遍、联网又说一遍”。相同结论合并，实质冲突说明出处和范围；'
                      '未问到的全国所有地区、所有年份或完整统计不列为缺项。'),
        'self_check': [
            '每个实际子问题是否都有结论或具体缺口，结论和引用是否对应；缺项不能标为full。',
            '删掉无关案例、重复限制、检索过程；缺口只对应尚未回答的部分。',
            '条件与程序、降级与终止、灾害后果与应对措施没有错放或混淆。',
            '一般归纳与具体条款分开；数值、机构、地域、时段、版本及条件逻辑有依据。',
            '正文能直接读懂，长段落拆开；同一文件的版本限制只说明一次，不扩展到其他来源。',
        ],
    }


def readability_issues(answer, coverage, question=''):
    """Only high-confidence editorial faults; never block a grounded answer."""
    if coverage == 'insufficient':
        return []
    issues = []
    # Multiple reports about absent clauses are retrieval diagnostics, not an answer.
    diagnostics = re.findall(r'[^。\n]*(?:节选|任务清单|文件|预案)[^。\n]*(?:未涉及|未覆盖|未提供|未给出|未明确|只涉及|仅涉及)[^。\n]*', answer)
    if len(diagnostics) >= 2:
        issues.append('不要逐份报告文件缺少什么；合并成末尾一句具体限制，正文解释有据结论。')
    if len(re.findall(r'缺项|尚缺|未提供|未覆盖|未涉及|缺少依据', answer)) >= 4:
        issues.append('缺项提示重复过多；只对实际未回答的子问题简短说明一次。')
    if re.search(r'^(?:现有证据只|现有资料只|本次资料只|未提供适用于)', answer.strip()):
        issues.append('已有可回答部分时，开头先回答核心问题，再交代适用范围和限制。')
    if len(answer) > 1000:
        issues.append('回答过长；合并重复规定，保留直接结论、必要条件与步骤，尽量控制在600字左右。')
    if len(re.findall(r'按库中归档版本|按归档版本|版本状态未核实', answer)) >= 3:
        issues.append('版本标签重复过多；同一文件首次引用时说明一次，末尾仅保留必要限制，不能把本地归档限制套在联网来源上。')
    if any(len(paragraph) > 350 for paragraph in answer.split('\n') if not paragraph.strip().startswith('|')):
        issues.append('段落太长；按意思拆成短段，条件列要点、程序列步骤，避免把多个主体或多份文件挤在一段。')
    if (re.search(r'应对|防御措施|采取.*措施', question)
            and not re.search(r'损失|风速|气压|增水|潮位|灾情', question)
            and sum(term in answer for term in ('最大风速', '最低气压', '最大增水', '直接经济损失')) >= 2):
        issues.append('用户问应对措施，删掉无关的风速、气压、增水和损失统计；解释已找到的实际行动，缺失措施只简短说明。')
    return issues
